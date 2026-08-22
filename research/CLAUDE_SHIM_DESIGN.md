# claude-shim — OpenAI-compatible endpoint over the Claude Max 20x session (wq#726)

Author: fleet BUILD worker (sonnet-5@high), 2026-08-22. Spec: wq#726.
Refs: `/home/lumen/messenger/agent_bridge.py` `_try_claude_sync`/`_brain_env`/`_brain_model_args`
(the proven no-tools CLI-call pattern, confirmed working 2026-08-21), `research/CLAUDE_CLI_INTERNALS.md`
§E (result-frame taxonomy), `research/FOREMAN_RESEARCH.md` §8/§9 (concurrent-worker/local-rung
wiring), `dispatcher/DESIGN.md` + `dispatcher/systemd/INSTALL.md` (staged-install precedent this
doc follows), `/home/lumen/agent/litellm_config.yaml` (live LiteLLM config), `/home/lumen/docker/
docker-compose.yml` (litellm container networking).
Status: DESIGN FOR REVIEW. Frank reads this before the shim is wired into anything that spends
real traffic. Nothing in this document flips LPP's model env — that is a separate, Frank-gated
ticket per wq#726's own acceptance criteria.

## 0. TL;DR

A small stdlib-only Python HTTP server (`claude-shim/claude_shim.py`) implements just enough of
`POST /v1/chat/completions` for LiteLLM to treat it as an OpenAI-compatible provider. Each request
becomes one `claude -p` subprocess call, using the exact no-tools/no-session/no-MCP flag set
messenger's `_try_claude_sync` already proved live against the Max 20x OAuth session — no new
CLI incantation is being invented here, only wrapped in HTTP. Two LiteLLM model entries
(`claude-max-sonnet`, `claude-max-opus`) point at it. The shim is additive: it does not touch
LPP, does not touch DeepSeek routing, and ships with zero LiteLLM traffic pointed at it until
Frank approves the follow-up ticket.

## 1. Why this and not something else

The alternative (LiteLLM's native `anthropic/` provider hitting `api.anthropic.com` with an API
key) costs per token. The Max 20x session is a flat-rate subscription already paying for the
`claude` CLI's OAuth session — `_try_claude_sync` demonstrates it answers no-tools completions for
$0/call marginal cost. The shim's only job is to make that session reachable as
`POST /v1/chat/completions` so LiteLLM (and therefore anything that already speaks
OpenAI-compatible client code, including the LPP brain layer once Frank approves the flip) can
address it exactly like any other model row.

## 2. Concurrency model (ticket point 1)

`claude -p` is one subprocess per call — there is no persistent server process holding the
session open, no batching, no HTTP keep-alive to Anthropic. Two constraints stack:

- **Shared account, shared rate limits.** FOREMAN_RESEARCH §8: the fleet already runs headless
  `claude -p` workers (BUILD lane, this very session) against the same OAuth session, with a
  cloud concurrency ceiling of 3 (`dispatcher/config.json cloud_ceiling`, `models.yaml
  defaults.concurrency.cloud_max: 3`). The shim adds a *second* population of `claude -p`
  processes drawing from the same 5-hour/7-day rolling windows. It must not, by itself, be able
  to push the account over that ceiling.
- **No documented parallel-session limit from Anthropic** beyond the account-global rate limit
  itself (CLAUDE_CLI_INTERNALS.md §D: `rate_limit_event`/`api_error.rate_limits` are the only
  in-band signal; there is no separate "max concurrent CLI processes" number in any doc found).
  Treat the fleet's own empirical ceiling (3) as the safe upper bound for the whole account, not
  per-consumer.

**Decision:** the shim enforces its own hard concurrency cap via a `threading.Semaphore`,
default **`CLAUDE_SHIM_MAX_CONCURRENCY=2`** (env-overridable), independent of and *not*
coordinated with the BUILD-lane dispatcher's ceiling (no shared IPC between the two — see §9
open question). A request that cannot acquire a slot within `CLAUDE_SHIM_QUEUE_WAIT_S`
(default 10s) gets **HTTP 429** immediately — no unbounded queue, no silent blocking past the
caller's own timeout. This is deliberately conservative for a first landing: LPP's three call
sites (escalate/actRetry/summarize) are low-QPS turn-by-turn calls, not a burst workload, so 2
concurrent slots is headroom, not a bottleneck, and the number is one env var to raise once
real traffic is observed.

Each request also gets a hard subprocess timeout (`CLAUDE_SHIM_TIMEOUT_S`, default 120s,
`subprocess.run(..., timeout=...)`) — a hung CLI process cannot hold a concurrency slot forever
and starve the other slot.

## 3. Model mapping (ticket point 2)

```
claude-max-sonnet -> claude -p --model sonnet ...
claude-max-opus   -> claude -p --model opus   ...
```

Straight alias passthrough, matching `_brain_model_args`'s `anthropic`-backend branch (chat->
sonnet, re->opus). Any other `model` value in the request body is a **400** (`unknown model
"<x>", must be claude-max-sonnet or claude-max-opus`) — no default substitution, no "closest
match" guessing. Adding a third alias later (e.g. `claude-max-haiku`) is a one-line dict entry.

## 4. What the CLI does NOT expose, and what gets dropped (ticket point 3)

`claude -p` has no flag for `max_tokens`, `temperature`, `top_p`, `frequency_penalty`,
`presence_penalty`, `stop`, `n`, `logprobs`, or `seed` — the OpenAI chat-completions request
schema's sampling controls simply don't exist on the CLI surface (it's an agent harness, not a
raw completion API). The shim's contract:

- Accepted and honored: `model`, `messages` (system message -> `--system-prompt`, rest flattened
  to a transcript prompt — see §5), a synchronous, non-streaming response.
- Accepted and **silently ignored, logged once per process at WARNING**: `max_tokens`,
  `temperature`, `top_p`, `stop`, `n`, `presence_penalty`, `frequency_penalty`, `seed`,
  `logprobs`, `user`. These are not errors — a well-behaved OpenAI client sends `max_tokens` and
  `temperature` on every call, and 400-ing on their mere presence would make the shim unusable
  by anything not written specifically for it. The line is: *silently drop unsupported knobs the
  caller can't verify took effect either way, but never silently drop or reinterpret the
  message content itself.*
- Rejected with **400**: `stream: true` (no SSE/chunked implementation — see §9), `tools`/
  `functions`/`tool_choice` (this is the no-tools completion path by design; a caller that wants
  tool execution wants the real Claude Code harness, not this shim), `n > 1`.

This asymmetry (drop sampling params, reject structural params) is the same shape LPP's own
config already assumes: `pokemon/src/config.ts` reads `maxTokensOut` per brain tier but that
value has never actually bounded a Max-session call (it's meaningful only against DeepSeek/
LiteLLM providers that honor `max_tokens`) — so dropping it here changes nothing LPP currently
depends on working.

## 5. Proven flags (ticket point 4) and request shaping

Lifted verbatim from `_try_claude_sync` (agent_bridge.py:2505-2516), the flag set that is live
and confirmed working against the Max session today:

```
claude -p
  --output-format json
  --model <sonnet|opus>
  --system-prompt "<first system message content, or a generic default>"
  --strict-mcp-config
  --mcp-config '{"mcpServers":{}}'
  --tools ""
  --no-session-persistence
  "<flattened prompt>"
```

`--strict-mcp-config --mcp-config '{"mcpServers":{}}'` blocks user MCP servers from loading (no
side-effecting tools reachable from a pure-completion call). `--tools ""` disables built-in
tools. `--no-session-persistence` means every call is stateless — no transcript file, no
`~/.claude/projects/<cwd>` growth, matching "one call in, one answer out" semantics an OpenAI
client expects. `subprocess.run(..., stdin=subprocess.DEVNULL)` (also copied) prevents the CLI
from ever blocking on stdin it will never receive.

**Env for the child process** (mirrors `_brain_env`'s `anthropic`-backend branch, i.e. *env
unchanged* — no `ANTHROPIC_BASE_URL`/`ANTHROPIC_API_KEY` override): the shim inherits its own
environment verbatim into the subprocess, so the CLI authenticates exactly the way an
interactive `claude` session on this box does — OAuth session in `~/.claude/.credentials.json`.
The shim's own environment must never carry `ANTHROPIC_API_KEY` (guard.sh already denies
in-command injection of that var; the shim additionally asserts at startup that
`ANTHROPIC_API_KEY` is unset in its own env and refuses to start if it is set — fail closed
against accidentally billing the API key path instead of OAuth).

**Prompt shaping:** messages flattened with the same transcript format as
`_format_history_as_prompt` (system message extracted separately, `role=user` -> `User: ...`,
`role=assistant` -> `Assistant: ...`, joined by blank lines) — no LPP-specific marker stripping
(`[pro]`/`[local]`/etc. are a messenger-persona concern, not this shim's).

## 6. Failure semantics — fail LOUD (ticket point 5, wq#722 class)

wq#722 was a silent-fallback bug: a degraded primary route quietly fell through to a paid
DeepSeek path with no visibility, and the resulting spend spike went unnoticed for a full
postmortem cycle. The shim's entire error contract exists to make that class of bug structurally
harder here: **every non-success path is a non-2xx HTTP response with a body describing why.
There is no code path that returns HTTP 200 with an empty, truncated, or apologetic
completion.**

Mapping `claude -p --output-format json` result frames (CLAUDE_CLI_INTERNALS.md §E) to HTTP:

| CLI outcome | HTTP status | body `error.type` |
|---|---|---|
| `is_error:false`, `subtype:"success"` | 200 | (n/a — normal chat-completion body) |
| rate-limit signal seen (`rate_limit_event.status=="rejected"`, or `api_error.rate_limits!=null`, or retry error-category `"rate_limit"`) | 503 | `rate_limited` (+ `resets_at` if parsed) |
| `subtype:"error_max_turns"` | 500 | `cli_max_turns` (should never fire — the shim passes no `--max-turns`; a hit means something upstream changed) |
| `subtype:"error_max_budget_usd"` | 500 | `cli_max_budget` (shim passes no `--max-budget-usd`; same "should never fire" logic) |
| any other `is_error:true` | 502 | `cli_error` (raw `result`/`errors[]` text included, capped) |
| subprocess non-zero exit, unparseable stdout, or empty stdout | 502 | `cli_crash` |
| subprocess `TimeoutExpired` | 504 | `cli_timeout` |
| concurrency slot not acquired in time | 429 | `overloaded` |
| unknown `model`, `stream:true`, `tools` present, `n>1` | 400 | `invalid_request` |
| `ANTHROPIC_API_KEY` set in shim env at startup | shim refuses to start | (logged, process exits 1) |

Every rejection is logged (stderr -> journald under systemd) with the full CLI stderr tail
(capped, same 300-char cap pattern `_try_claude_sync` uses for its warning log) so a human
debugging "why did LPP get a 502" has the actual CLI complaint one `journalctl` away, not a
guess.

## 7. Docker networking — the gap the ticket's "localhost bind only" doesn't resolve on its own

This is the part not spelled out in the ticket text but load-bearing for "verification (round-
trip via litellm :4000)" to be possible at all: **LiteLLM does not run on the host.** It's a
Docker container (`/home/lumen/docker/docker-compose.yml` `litellm:` service), port-mapped
`127.0.0.1:4000:4000`, on the `docker_default` bridge network, with
`extra_hosts: host.docker.internal:host-gateway`. If the shim binds strictly to `127.0.0.1` on
the host network namespace, it is **unreachable from inside the litellm container** — loopback
does not cross network namespaces, and `host.docker.internal` resolves to the bridge gateway
IP (this host has multiple custom bridges, all in `172.16.0.0/12` — `ip -4 addr` confirms
`docker0`=172.17.0.1 plus several `br-*` at 172.18-172.26.0.1), not `127.0.0.1`. A literal
"bind 127.0.0.1" would build a shim LiteLLM's container can never reach, and the acceptance
criterion's round-trip smoke test would fail structurally — this is very plausibly related to
why prior attempts on this ticket stalled.

**Resolution, preserving the spirit of "localhost bind only" (no LAN/WAN exposure):** the shim
listens on `0.0.0.0:<port>` (default port **8091** — confirmed free via `ss -ltn`, no collision
with the 40+ services already bound on this host) but enforces an **application-level source-IP
allowlist** before doing any work: `127.0.0.0/8` (host-local callers) and `172.16.0.0/12`
(every Docker bridge subnet on this host, since the litellm container's actual bridge wasn't
individually confirmed — `docker network inspect` is not in the guard's docker
allowlist (ps/logs only), so the coarser range is used deliberately rather than guessing one
`br-*` and risking silent unreachability if compose recreates the network on a different
subnet). Any request from outside that range gets **403**, connection-level, before touching
the CLI. Net effect: no different from a literal loopback bind as far as the outside network is
concerned (nothing on `192.168.8.0/24`, Tailscale `100.x`, or the public interface can reach
it), but the one caller that actually needs to reach it — the litellm container via
`host.docker.internal` — can.

**Auth, defense in depth on top of the IP allowlist:** the shim also requires
`Authorization: Bearer <token>` matching `CLAUDE_SHIM_API_KEY`. If that env var is unset at
startup, the shim generates one (`secrets.token_urlsafe(32)`), writes it to
`claude-shim/.api_key` (0600, gitignored — never committed, mirrors how
`/home/lumen/agent/.env` holds `LITELLM_MASTER_KEY` outside git), and logs its path once. This
costs nothing and matches the existing convention for the other locally-hosted OpenAI-compatible
rungs in `litellm_config.yaml` (e.g. `lumen-lfm2.5-2.6b`'s `api_key: sk-1234` placeholder —
except here the key is a real per-install secret, not a shared placeholder, since this one sits
in front of a paid subscription rather than free local compute).

## 8. LiteLLM wiring

Two new entries in `/home/lumen/agent/litellm_config.yaml`, following the existing
`openai/<name>` + `api_base` pattern used by `lumen-lfm2.5-2.6b`/`kimik3-0.4b-local`:

```yaml
- model_name: claude-max-sonnet
  litellm_params:
    model: openai/claude-max-sonnet
    api_base: http://host.docker.internal:8091/v1
    api_key: os.environ/CLAUDE_SHIM_API_KEY
    timeout: 130
  model_info:
    mode: chat
- model_name: claude-max-opus
  litellm_params:
    model: openai/claude-max-opus
    api_base: http://host.docker.internal:8091/v1
    api_key: os.environ/CLAUDE_SHIM_API_KEY
    timeout: 130
  model_info:
    mode: chat
```

`os.environ/CLAUDE_SHIM_API_KEY` requires that var in the litellm container's env (added to
`/home/lumen/agent/.env`, which is already the container's `env_file`). `timeout: 130` gives
10s of margin over the shim's own 120s CLI timeout so LiteLLM doesn't cut the connection first
and mask the shim's own 504.

**Why the yaml edit alone is not enough, and what the build phase actually does about it:** the
running `litellm` container only reads `config.yaml` at startup — there is no config-reload
mechanism wired here, and restarting the container is out of scope for a worker session (the
guard denies all `docker` subcommands except `ps`/`logs`, and it is a shared service other
live consumers — messenger, librechat — depend on; restarting it is Frank's call, not a headless
build's). The build phase commits the yaml entries above so they're live on the *next* natural
restart, backed up first with the existing `litellm_config.yaml.bak.<timestamp>` convention
already used for every other edit to this file.

**Tried and ruled out — live registration without restart:** the plan going in was to also
register both models at runtime via LiteLLM's Model Management API (`POST /model/new`,
master-key auth) to prove the port-4000 round trip inside this same session. Tried it live
against the running container: `POST /model/new` returns `500 {"error": "Set
STORE_MODEL_IN_DB='True' in your env to enable this feature."}` — that flag isn't set in this
deploy's `litellm_config.yaml`/env, and turning it on requires a restart to take effect anyway,
which is the exact thing this path was trying to avoid. There is no restart-free way to make the
live container aware of a new model row. **Consequence:** the litellm-:4000 leg of "round-trip
via litellm" is proven by config + direct-shim evidence, not by an actual packet through port
4000 — see §10 for exactly what was and wasn't demonstrated, and the one command Frank runs to
close that last gap.

## 9. systemd unit, staged (ticket point 6)

Follows the AUTO-3 precedent exactly (`dispatcher/systemd/INSTALL.md`): **units ship repo-only**
(`claude-shim/systemd/claude-shim.service`), because installing to `/etc/systemd/system`
requires `sudo`, which the guard denies outright (`sudo` is in the denied-verb list, no
ps/logs-style carve-out). The build phase writes the unit file into the repo and documents the
Frank-run install/enable steps in `claude-shim/systemd/INSTALL.md`; it does not and cannot copy
anything into `/etc` itself.

```ini
[Service]
Type=simple
User=lumen
Environment=PATH=/home/lumen/.npm-global/bin:/usr/local/bin:/usr/bin:/bin
Environment=CLAUDE_SHIM_HOST=0.0.0.0
Environment=CLAUDE_SHIM_PORT=8091
WorkingDirectory=/home/lumen/foreman/claude-shim
ExecStart=/usr/bin/python3 /home/lumen/foreman/claude-shim/claude_shim.py run
Restart=on-failure
RestartSec=5
MemoryMax=512M
MemoryHigh=384M
NoNewPrivileges=true
PrivateTmp=true
```

`MemoryMax=512M` bounds a leaked/runaway subprocess pileup (max 2 concurrent `claude -p`
children plus the parent server, generously sized). No `[Install]` section — same rationale as
`lumen-foreman-dispatch.service`: this cannot be systemd-enabled at boot, only started by hand
or, later, by whatever supervises it; Frank decides persistence. For this ticket's verification
step the shim runs as a **plain background process** (not via systemd — no sudo path to install
it), which is sufficient to prove the HTTP contract and the direct Max-session round trip; the
unit file is the durable artifact Frank installs afterward.

## 10. Verification plan and results (what "smoke evidence" means for this ticket)

Run 2026-08-22, shim as a background process on the host, port 8091:

1. **Startup self-check:** `GET /health` -> 200 `{"status": "ok"}`; log confirms the
   `ANTHROPIC_API_KEY`-unset self-check passed (shim would have exited 1 otherwise) — CONFIRMED.
2. **Direct completions, both models:**
   `claude-max-sonnet` with a system+user prompt -> 200, `{"content": "Pineapple"}` for a
   "reply with exactly one word" prompt, 2.8s wall time. `claude-max-opus` similarly -> 200,
   `{"content": "banana"}`. Both real `claude -p` round trips against the Max session —
   CONFIRMED.
3. **Negative smoke:** unknown model -> 400 `invalid_request`; `stream:true` -> 400
   `invalid_request`; wrong bearer token -> 401 `unauthorized` — CONFIRMED (allowlist 403 path
   verified by unit test against the real CIDR list, not a live off-net request, since this
   worker session has no reachable non-allowlisted source to test from).
4. **LiteLLM round trip via port 4000: NOT achieved live, and here's exactly why.** Config
   entries are committed and YAML-validated (`yaml.safe_load` confirms both `model_name` rows
   parse). Runtime registration via `POST /model/new` was attempted and failed
   (`STORE_MODEL_IN_DB` not set — §8) — LiteLLM has no restart-free path to pick up either the
   yaml or a `/model/new` call in this deploy's current configuration. A direct
   `POST :4000/v1/chat/completions` with `model: claude-max-sonnet` against the *unmodified*
   live container confirms the negative baseline (`400 Invalid model name`), i.e. proves the gap
   is real and not a test mistake, not that it's closed. **Closing it needs one restart Frank
   runs by hand** (`docker compose -f /home/lumen/docker/docker-compose.yml restart litellm` —
   outside worker scope per the guard's docker allowlist, and it's a shared container other live
   consumers depend on, so a worker shouldn't be the one deciding when it bounces). After that
   restart, steps 2-3 above (repeated through port 4000 instead of 8091) are the confirmation
   Frank or a follow-up ticket should run.
5. **Zero-API-key confirmation:** the shim's own env had no `ANTHROPIC_API_KEY` (`env | grep -c
   ANTHROPIC_API_KEY` -> 0) for the entire test run, and the startup self-check (§6/§10.1) would
   have refused to launch it if one were present — the `claude -p` children in steps 2-3
   inherited that same key-free env, so every completion above ran OAuth-only. This is the part
   of the acceptance bar that's fully proven regardless of the port-4000 gap.
6. Evidence above (request/response transcripts) goes in the PR description and
   `wq done --notes`, including the honest state of item 4.

## 11. What this build does NOT do (explicit non-goals, per ticket's FRANK-GATED line)

- Does not change `LPP_BRAIN_ESCALATE`/`LPP_BRAIN_ACTRETRY`/`LPP_BRAIN_SUMMARIZE`. A follow-up
  ticket proposes the flip (escalate->`claude-max-opus`, actRetry/summarize->`claude-max-sonnet`)
  for Frank's approval, per this ticket's acceptance criteria.
- Does not enable the systemd unit or make it boot-persistent.
- Does not restart the live `litellm` container; the yaml edit is inert until the next natural
  restart (§10.4 — the one verification gap this build could not close itself).
- Does not add `claude-max-sonnet`/`claude-max-opus` to any existing fallback chain
  (`router_settings.fallbacks` in litellm_config.yaml) — no other caller is silently redirected
  onto the Max session by this change.

## 12. Open questions for Frank

1. Is `CLAUDE_SHIM_MAX_CONCURRENCY=2` (§2) too conservative once LPP traffic actually lands, and
   should the shim and the BUILD-lane dispatcher share a single account-wide concurrency
   accounting mechanism (e.g. a slock-style counter both sides decrement/increment) instead of
   two independently-configured, uncoordinated ceilings? Not built here — flagged, not guessed.
2. Should the shim's `.api_key` auto-generation (§7) instead be a Frank-provisioned secret from
   day one (consistent with `/home/lumen/.secrets/` conventions), rather than self-generated on
   first run?
3. `172.16.0.0/12` as the Docker-bridge allowlist (§7) is coarse — it trusts every container on
   this host, not just `litellm`. Tightening it to the exact `docker_default` subnet requires
   `docker network inspect`, which the guard's docker allowlist (ps/logs only) doesn't permit a
   worker session to run; a human with `docker network inspect docker_default` can narrow this
   in five minutes if the coarser range is judged too broad.
