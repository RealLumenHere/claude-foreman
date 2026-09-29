# wq#3168 — dead local rungs eat a 60s+ timeout before falling back

[audit parent wq#3162] Four LiteLLM rungs were confirmed dead by direct probe
(`probe2.out`, cited in the ticket) with real completions running through
`localhost:4000`:

| rung | backend | symptom |
|---|---|---|
| `claude-max-opus` | `host.docker.internal:8091` (claude-shim) | `ss` shows 0 listeners on :8091 |
| `claude-max-sonnet` | `host.docker.internal:8091` (claude-shim) | same — same backend as claude-max-opus |
| `kimik3-0.4b-local` | `host.docker.internal:8000` | `ss` shows only `127.0.0.1:8000` — a docker bridge container reaching `host.docker.internal` cannot land on a host-loopback-only bind |
| `lumen-lfm2.5-2.6b` | `host.docker.internal:8000` | same class as `kimik3-0.4b-local` — likely the same local server process serving both aliases |

`claude-opus-5` (anthropic/) 401s on a missing API key — that one is an
intentional defusal (per `tools/council/council.yaml` and multiple ISSUE-LOG
rows) and is explicitly kept, not touched here.

## Why this BUILD worker can't apply the litellm_config.yaml edit directly

Same convention as `ops/wq1617-litellm-fragment-merge`, `ops/wq1593-rung-retirement-price-check`,
and `ops/wq1591-glm53flash-pricing`: `/home/lumen/agent/litellm_config.yaml` lives
outside this repo, and this worker's sandbox has no read or write access to
`/home/lumen/agent` (the directory also holds `agent/.env` secrets — policy-refused).
No out-of-band snapshot was staged at `/tmp/fworker-wq3168/inputs/` for this ticket
(checked — empty), so unlike wq#1617 there is no live copy of the current
`model_list` to diff against in this sandbox. The decisions and snippets below are
built from what's independently verifiable inside this repo (ISSUE-LOG, the
`claude-shim` design doc and its staged systemd unit, `tools/council/council.yaml`)
plus the ticket's own probe evidence — not from guessing the live file's exact
current contents.

## Decisions (retire vs restore), applied where this sandbox has write access

### claude-max-opus / claude-max-sonnet — RETIRE (from the in-repo fallback chain)

`claude-shim` — the process meant to listen on `:8091` — was never installed.
`claude-shim/systemd/INSTALL.md` ships the unit **repo-only**: install requires
`sudo cp ... /etc/systemd/system/` + `sudo systemctl start`, both outside this
worker sandbox (guard denies `sudo` outright). ISSUE-LOG wq#2628 (2026-09-25)
already found `curl http://127.0.0.1:8091/health` refused and no
`claude-shim.service` unit on the box; this ticket's probe (2026-09-29) confirms
the same dead state four days later — nobody with root has installed it in that
window.

Applied in this branch: `tools/council/council.yaml` now ships the `claude` seat
with `enabled: false`, and `tools/council/council.py::load_config` was fixed so a
chairman-list entry for a disabled seat is dropped at load time instead of being
retried for a real synthesis call (see "The chairman-list bug" below). This was
the one place in this repo with live code on the request path to `claude-max-opus`;
after this change no council run ever spends a timeout on it.

**Restore path** (for whoever has root): install `claude-shim.service` per its
`INSTALL.md`, confirm `curl http://127.0.0.1:8091/health` → `{"status": "ok"}`,
then flip `enabled: true` back in `tools/council/council.yaml`. The
`litellm_config.yaml` entries for `claude-max-sonnet`/`claude-max-opus` (exact
shape in `research/CLAUDE_SHIM_DESIGN.md` section 8) are inert while the shim is
down and need no edit either way — `claude-shim/systemd/INSTALL.md`'s own
rollback note already says so.

### kimik3-0.4b-local / lumen-lfm2.5-2.6b — RETIRE (recommend; needs a gateway-node edit this sandbox can't make)

No systemd unit, compose service, or bind config for the local server(s) behind
`host.docker.internal:8000` exists anywhere in this repo to restore from (checked:
no hits for `kimik3`/`lumen-lfm2.5` outside prose docs and pilot proposal cards —
`grep -rl` over the worktree, excluding `pilots/`). Restoring means rebinding
that server to the docker bridge or `0.0.0.0` (behind ufw) or switching the litellm
container to host networking — an infra change on the gateway node this sandbox
has no visibility into and no write access for (same `/home/lumen/agent` boundary
as above). Absent a concrete restore path to verify, and per
`research/LOCAL_RUNG_AUDIT_2576.md`'s fleet-wide finding that local rungs deliver
near-zero value against their operational cost, retire is the lower-risk default.

No in-repo fallback chain references either name (checked `tools/council/`,
`research/models.yaml`, `dispatcher/`, `governor/` — the only hits are prose in
`pilots/*/CARD.md` and `research/CLAUDE_SHIM_DESIGN.md`), so there is nothing else
in this repo to fix for these two beyond documenting the decision here.

**For whoever has `/home/lumen/agent` access:**
1. Back up: `cp /home/lumen/agent/litellm_config.yaml /home/lumen/agent/litellm_config.yaml.bak.$(date +%Y%m%d)`
2. Precheck with the existing guard tool before touching the file:
   `python3 ops/wq1617-litellm-fragment-merge/merge_litellm_fragment.py check <live> <candidate>`
   against a candidate with the `kimik3-0.4b-local` / `lumen-lfm2.5-2.6b` /
   `claude-max-sonnet` / `claude-max-opus` `model_list` entries removed, to confirm
   no pricing key on a live model is dropped incidentally.
3. Remove the four `model_list` entries.
4. `lumen_model_watchdog.py`'s `CLOUD`/`LOCAL` rosters (`ops/wq2903-model-ref-drift/lumen_model_watchdog.py`)
   already do **not** list any of these four names — verified by reading the file
   in this branch — so no watchdog-roster edit is needed; removing them from
   `litellm_config.yaml`'s `model_list` will not create a wq#2903-style roster-drift
   alert.
5. Restart/redeploy `litellm` to pick up the edit (`docker compose ... restart litellm`
   — this worker's guard denies all `docker` subcommands except `ps`/`logs`, so this
   step needs a session with that access, same as `claude-shim/systemd/INSTALL.md`
   step 4).

## The chairman-list bug this ticket's fix closes generally

`tools/council/council.py::load_config` (before this branch) filtered `members:`
by `enabled`, so `council.seats` never included a disabled seat — but
`council.chairman` was built straight from the yaml list with no such filter.
`synthesize()` (`tools/council/council.py:241-264`) only skips a chairman entry
whose model already failed as a fanned-out seat (`failed = {a.model for a in
result.answers if not a.ok}`); a model that was never a seat at all (disabled, or
never enabled) was not in `failed`, so `synthesize()` called it directly with a
real `chat()` timeout (`council.timeout_s`, default 300s). Disabling a seat via
`enabled: false` — the exact mechanism this ticket uses to retire `claude-max-opus`
— did **not** actually take that model out of the request path if it was also
named as a chairman fallback, which `claude-max-opus` is
(`tools/council/council.yaml:31`, unchanged). Fixed at `tools/council/council.py:143-148`
(`load_config`, the `seat_models`/`chairman` lines added in this branch): the
chairman list is now filtered to enabled seat models before being stored on
`Council`, so a retired rung can never be reached from either path. Covered by
`tools/council/tests/test_council.py::test_load_config_drops_disabled_seat_from_chairman_list`.
