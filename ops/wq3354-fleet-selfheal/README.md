# wq#3354 — fleet self-healing: detect, fix, verify, close

Audits kept finding the same failures because nothing acted between audits. This
bundle is the layer that does. Every class below either fixes the failure itself or
files a build-lane ticket, which is the queue the orchestrator dispatches fworkers
from: filing the ticket *is* the auto-dispatch. Each open failure gets one ticket,
aq row, and page per episode, and the episode closes on its own when the condition
clears.

| class | trigger | automatic fix | escalation |
| --- | --- | --- | --- |
| 1 guard timer down (`sh_guards.py`) | a named guard timer, or any loaded `*guard*/*sentinel*/*drift*/*watchdog*/pat-scan/mask-at-rest` timer, not active | `systemctl start`, up to 3 consecutive sweeps | ticket + page when it still won't stay up, when a named guard isn't installed, or when its service has been failed >30 min (wq#1705 owns service restarts, and it only raises aq rows) |
| 2 crash-loop (`sh_crashloop.py`) | NRestarts rose by >3 within 5 min | stop the unit and write a wq#967 holddown flag (12 h), so lumen-self-healer neither fights the stop nor forgets to restore it later | ticket + urgent page. `never_stop` units (cloudflared, Commander/MCP gateway, tailscaled, ssh, docker, healers) get the alert and ticket but keep running |
| 3 credential leak (`leakwatch.py`, daemon) | inotify close-write/rename in the watched dirs | secret stores (`.env*`, `claude_credentials.md*`, config backups) have world access stripped; group access is reported | literal credential in `claude_memory.md` / live `litellm_config.yaml` → rotation ticket naming label + sha12, urgent page. The file is not edited (instinct 7, and a masked config would break its rung) |
| 4 disk pressure (`sh_disk.py`) | `/`, `/home`, or `/data/lumen` ≥90% | npm cache, pip cache, cargo registry cache, `journalctl --vacuum-time=7d`, worktrees wq#2620 proves merged + clean + unclaimed + 7 d landed. At most once an hour | ticket + page if still ≥90% afterwards |
| 5 provider key dead (`sh_providers.py`) | LiteLLM host answers 401/403 with a provider error for ≥10 min | its model_names go into `dispatcher/runtime/backend_cooldown.json` (`source: fleet-selfheal`); `router.py` now skips a cooled rung in every pool | rotation ticket + page; rungs re-enabled on the first good probe |
| 6 node offline (`sh_nodes.py`) | lumen_state fleet node (matched by tailscale IP) offline ≥30 min | none (physical) | ticket with tailscale LastSeen, lumen_state `power_*`/`status` facts, live `plug-ctl <p> state` |
| 7 ollama pin (`sh_ollama.py`, wq#3558) | a model in Lumen's `/api/ps` expires >120 s out, which only a client-set `keep_alive` can cause | `POST /api/generate {model, keep_alive: 0}` (ollama's own unload call). In-flight requests finish first, and no restart is needed | ticket + page when it is pinned again after 3 breaks in a row |

Why class 7 exists: with `OLLAMA_MAX_LOADED_MODELS=1`, loading a chat model marks the
resident model for unload and blocks the scheduler until that unload lands. A request
for the resident model that carries its own `keep_alive` overwrites the mark (fast
path `useLoadedRunner`, ollama v0.34.4 `server/sched.go`). Chat then hangs while embed
and `/api/ps` keep answering. minerva's embed calls sent `keep_alive: '30m'`, which
explains the 2026-10-01 and 2026-10-07 wedges. The client fix is minerva PR
fix/wq3558-embed-no-keepalive-pin. Class 7 breaks any pin that comes back.

`selfheal_sweep.py` runs classes 1, 2, 4, 5, 6 and 7 from a 1-minute root timer. Class 5
paces itself: it probes every host every 30 min (one 5-token completion per host) and
re-probes failing hosts every 5 min. Class 3 is `lumen-leakwatch.service`, a
long-running daemon (as lumen) that uses libc inotify through ctypes, so there is no
new dependency. It re-checks every file at start-up so it catches anything written
while it was down.

The nightly `lumen-mask-at-rest` stays as is. Its slow, OOM-prone part is the sweep of
every CC transcript (wq#3207 capped its memory). This bundle takes over the known
sensitive files in real time; it does not shrink the transcript sweep.

## Guard rails

- `--dry-run` on both entry points touches nothing: no systemctl, no file writes, no
  tickets, no state save. It prints what it would do.
- Each class is isolated. A crash in one sends an aq row and the other classes still
  run. Every class can be switched off with `enabled: false` in the live
  `/home/lumen/agents/fleet-selfheal/selfheal.yaml`, which is re-read on every sweep.
- The disk pruners are hard-coded. Config can turn a pruner off but cannot add a path.
  The cargo clear refuses anything outside `~/.cargo/registry/cache`.
- Holddown flags (wq#967) are honoured by class 1 and written by class 2, so a
  deliberate pause is never fought and a crash-loop stop is never undone early.
- Tickets are filed through `runuser -u lumen -- wq add`, so wq's sqlite never picks up
  root-owned journal files.
- Thor is a node like any other here: class 6 only files tickets and never sends a
  power action.

## Deploy (root)

`sudo bash install.sh --no-enable` first: it installs the code, runs the suite, and
prints both dry-runs. Then `sudo bash install.sh` enables them.
`bash install.sh --verify` is the read-only live check (ops/VERIFY_CONTRACT.md).
Rollback is in the install.sh header.

## Evidence

`python3 -m pytest -q ops/wq3354-fleet-selfheal/tests` passes 29 tests, including
real-kernel inotify, the real `harness/masking.scan`, and `main()` end to end over a
temp dir outside the bundle. `orchestrator/tests/test_router_availability.py::
test_cooled_cloud_rung_is_skipped` fails against the pre-wq#3354 router
(`'claude-sonnet-5@medium' == 'claude-haiku-4-5'`) and passes against this one. A
live `selfheal_sweep.py --dry-run --only guards,crashloop,disk,nodes` on Lumen
(2026-10-01) started no guard timer (every named guard was loaded and active) and,
after the node baseline fix (ISSUE-LOG #1152), would file one baseline ticket for
the 7 nodes already offline.
