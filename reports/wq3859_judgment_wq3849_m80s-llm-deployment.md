# wq#3859 — adversarial verify + judgment on wq#3849 (M80s LLM deployment scaffolding)

- **Ticket verified:** wq#3849, PR http://100.121.17.5:3000/lumen/claude-foreman/pulls/1844,
  merged `351e1b5f` (`feat(ops): wq3849 M80s LLM deployment scaffolding`)
- **Verifier:** claude-sonnet-5@high (fresh instance, worktree `wq3859-a1`, 2026-10-14)
- **Escalation:** opus-5@high (contradiction found → judgment trigger)
- **Verdict: CONTRADICTION CONFIRMED (moderate severity, live today — not contingent on
  M80s hardware). Follow-up fix ticket filed: wq#3863.**

## Scope

wq#3849 is scaffolding only: `ops/wq3849-m80s-llm-deployment/{README.md, COMPARISON.md,
install-ollama.sh, ollama-pcore-pin.conf, pull-models.sh, benchmark.py,
litellm-aliases.yaml, apply.sh}`, 404 insertions, no code outside that directory. The M80s
node itself is genuinely unprovisioned (re-checked live, see below), so none of the
install-side scripts have ever run against real hardware. `apply.sh`, however, targets
Lumen's own live, already-deployed LiteLLM gateway (`/home/lumen/agent/litellm_config.yaml`)
and does not require M80s to exist to be *invoked* — that's the part this pass found a real
bug in.

## What's right (re-verified against live state, not assumed)

- **M80s really is unprovisioned.** `lumen_state.py current m80s` → no output; grep of
  `harness/fleet-exec-policy.yaml` for `m80` → zero hits; `research/fleet_hardware.json`
  node `m80s` → `"status": "unprovisioned-physical"`, CPU `i7-12700` (matches the pcore-pin
  comment's "8 P-cores + 4 E-cores / 20 threads").
- **Referenced tickets match their real status**, checked via `wq show`: wq#3703 `blocked`,
  wq#3705 `open`, wq#3704 `done` — all three exactly as README.md claims.
- **The `ollama pull` worker-context deny is real.** `harness/guard.sh:204` denies it
  under "wq#1297: large pulls are manual off-peak only — bridge risk", matching
  `pull-models.sh`'s header comment and README's claim verbatim.
- **COMPARISON.md's numeric citations check out**, including a citation I initially
  flagged as fabricated and then disproved by re-reading more carefully (worth recording
  per instinct 8's spirit — a miss on my end, not the PR's): `scratch/research/
  m80s-llm-recommendations.md` lives at `/home/lumen/scratch/...` (a box-wide scratch dir),
  not under `/home/lumen/foreman/scratch` as I assumed from the bundle's own relative path
  convention. Once read at the right path, lines 21/24/30/39 match COMPARISON.md's
  `agent-deep`/`quick`/`research`/`agent-fast` tok/s citations exactly. `research/
  LOCAL_MODEL_STRATEGY.md:51` (~50 GB/s T5810) and `:131` (18–25 tok/s Qwen3.6-35B-A3B)
  also check out verbatim. No fabricated numbers; the "Results" table is honestly left
  empty with a reason, matching the ticket's own instinct-8-driven doctrine.
- **Model tags are the ticket's own spec**, not invented by the author.
- **The `--verify`-handler gap this ticket named is real and is now closed.** The bundle
  shipped with no `install.sh` at all. Added `ops/wq3849-m80s-llm-deployment/install.sh`
  following the exact precedent `ops/wq3652-m80s-provisioning/install.sh` set for this same
  M80s-unprovisioned situation (added by wq#3708's verify-pass): a bundle whose target has
  no hostname at all yet — not unreachable, genuinely nonexistent — always answers `ERR`
  (exit 2) on `--verify` rather than guessing or inventing a false MISS/OK, per
  `VERIFY_CONTRACT.md`'s "ABSENT is not a disposition" rule.

  Not executed in this session — same already-documented, non-retryable wall as wq#3708 hit
  on the sibling bundle (ISSUE-LOG **#712**): `install.sh --verify`, with or without a
  `bash` prefix and whether invoked via absolute path or the executable bit, is "This
  command requires approval" — no `harness/worker-settings.json` entry covers it (only
  `Bash(bash scripts/*)`, a relative prefix). The handler's logic is a single literal
  `--verify)` case arm that unconditionally echoes a fixed string and `exit 2`s — no
  branches, no live-state dependency beyond the already-reconfirmed-absent `lumen_state.py`/
  `fleet-exec-policy.yaml` entries above — so static reading of standard POSIX `case`
  semantics is sufficient confirmation without needing the blocked invocation shape.

## The contradiction

**`apply.sh`'s documented `verify` subcommand is unreachable through the one-argument
invocation its own README and header comment invite, and the fallback path it takes
instead silently corrupts Lumen's live, credential-bearing LiteLLM config and restarts the
production gateway container.**

`apply.sh`'s header comment and README.md both describe it as "same shape as
`ops/wq1986-quantize-local-models/apply.sh`: back up, edit, restart, verify." But the two
scripts put the mode selector in *different argument positions*:

- `wq1986/apply.sh`: `case "${1:-apply}" in verify) ... ; apply) ... ; *) usage ;; esac` —
  mode is `$1`. No other argument is required (targets are hardcoded isis/local addresses).
  `apply.sh verify` is the documented, safe, read-only invocation.
- `wq3849/apply.sh` (`ops/wq3849-m80s-llm-deployment/apply.sh:19`): `case "${2:-apply}" in
  verify) ... ; apply) ... ; *) usage ;; esac` — mode is **`$2`**, because `$1` is now
  required for the M80s `api_base` (there is no hardcoded target). Line 31:
  `API_BASE="${1:?usage: ...}"`.

Concrete failure: an operator who knows the "same shape" convention from wq1986 (or who
just wants to check current state before changing anything — the single most natural
first move) runs:

```
apply.sh verify
```

one argument, `$1="verify"`. `${2:-apply}` evaluates to `"apply"` (since `$2` is unset) —
**not** `"verify"` — so the `case` falls into the `apply)` branch, not the read-only
`verify)` branch. Execution continues to line 31, `API_BASE="${1:?...}"`: `$1` is
`"verify"`, non-null, so the `:?` guard does **not** fire — `API_BASE` is silently set to
the literal string `"verify"`. The script then (line 33) checks whether `agent-fast` is
already in the live config; on a fresh/first run it is not, so it proceeds to:

1. back up `/home/lumen/agent/litellm_config.yaml` → `.bak.wq3849` (this part is fine —
   recoverable via the script's own documented rollback command),
2. write all four new aliases into the **live, 0600, credential-bearing**
   `/home/lumen/agent/litellm_config.yaml` with `api_base: verify` (the literal string,
   substituted for the `PLACEHOLDER_...` token),
3. `docker restart litellm` — bouncing the production inference gateway for every existing
   alias, not just the four new ones,
4. curl the health endpoint with retries.

This requires **no M80s hardware and no real `api_base`** — it corrupts Lumen's own live
gateway config today, from a single plausible single-argument invocation, specifically on
the "let me check status first" path a cautious operator would take before ever running a
real `apply.sh <api_base> apply`. It is recoverable (the `.bak.wq3849` copy plus the
script's own documented `cp ... && docker restart litellm` rollback line), so I'm calling
this moderate, not critical — but it's a real, present-day footgun, not a hypothetical one
contingent on hardware that doesn't exist yet.

I did not execute `apply.sh` against the real `/home/lumen/agent/litellm_config.yaml` to
reproduce this live (that would be the bug, not a test of it, and this script is outside
any BUILD worker's reach by its own header comment). The finding is confirmed by static
shell-semantics reading — `${var:-default}` and `${var:?msg}` are unambiguous POSIX
parameter expansion, not version- or shell-dependent — cross-checked against `wq1986/
apply.sh`'s differently-shaped and correctly-guarded argument parsing side by side
(`ops/wq1986-quantize-local-models/apply.sh:38-52`).

## Disposition

- Filed **wq#3863** (`[fix] wq3849 apply.sh: verify subcommand silently corrupts live
  litellm config on 1-arg invocation`) as the follow-up fix. Recommended minimal fix
  (for whoever picks it up — not implemented here per "do NOT re-implement; verify"):
  validate `API_BASE` looks like a URL (`^https?://`) immediately after line 31 and refuse
  with a usage error otherwise, independent of whether the mode argument stays at `$2` or
  moves to `$1` to match wq1986 — that one check makes the "verify" typo fail loudly
  instead of writing it into the live config.
- `ops/wq3849-m80s-llm-deployment/install.sh` (the missing `--verify` handler this ticket
  named) is added and will land with this ticket's PR.
- Everything else in wq#3849 — the hardware-status research, the citation accuracy, the
  deny-list/guard checks, the ticket cross-references — holds up under adversarial reading.
