# wq#4200: oh-wq-runner — wq ticket → OpenHands 1.8 bridge (shadow mode first)

Design: `/home/lumen/scratch/research/openhands-autonomous.md` §3.5 (runner design),
§3.3 (bounds the server lacks), §3.7 (acceptance gates). Derived from the
live-verified driver `/home/lumen/scratch/openhands-test/oh_run_task.py`
(API calls verified against the running stack 2026-10-18).

Script only, run via `lumen-run` — **no service, no timer** (stabilization freeze).

## What's here

| File | What |
|---|---|
| `oh_wq_runner.py` | The runner. Subcommands `shadow` (enabled) and `live` (gated). |
| `test_oh_wq_runner.py` | 25 pure-logic tests, no network/wq writes/docker. All pass. |
| `live-gate.json` | NOT present by design. Live mode refuses until it exists with evidence for §3.7 (a)-(g) + `wq4152_item6`. |

## Invariants implemented (ticket requirements → code)

- `wq claim --id` only; FENCE parsed from claim output and passed to every
  `done`/`block`/`heartbeat`; a claim that prints no FENCE is released and
  aborted (`wq_claim`). A crash mid-ticket ends in `wq block`, never a left
  claim (`run_one_live` finally-block).
- Wall-clock deadline + per-task cost cap read from `metrics.accumulated_cost`
  on every poll (`breach`, `extract_cost`); the server has neither bound (§3.3).
- Sandbox reaped in a `finally` block via
  `DELETE /api/v1/sandboxes?sandbox_id=<id>` — `sandbox_id` is a **query**
  param; the path form 422s (`reap_url`, quoted with `safe=""`).
- `idle` and `waiting_for_confirmation` are terminal-needs-human (`classify`);
  `idle` confirmed on 2 consecutive polls to dodge the startup race.
- Host-side verification of the ticket's acceptance command; the agent's
  `finish` is never trusted, and no configured command = NOT verified
  (`host_verify`, fail closed).
- Landing is host-side only: artifacts pulled via the file API into a worktree,
  committed with `lumen-pr`; the sandbox never holds Gitea creds (`land_artifacts`).
- `session_api_key` scrubbed from every dump (`scrub`).

## Rollout (per ticket; gates are hard-coded)

1. **Shadow** — replay ≥10 already-closed tickets, score against recorded
   outcomes (`score_shadow`: a recorded-blocked ticket the agent "finishes"
   counts as a MISMATCH). Queue stays read-only. Example, detached:

   ```
   lumen-run start --name oh-shadow --mem 1G -- \
     python3 /home/lumen/ops/wq4200-oh-wq-runner/oh_wq_runner.py shadow \
     --out-dir /home/lumen/scratch/openhands-test/shadow \
     --count 10 --max-seconds 1200 --max-cost 0.25 --total-cost-ceiling 2.0
   ```

   Emits `shadow-report.json` with per-ticket rows + accuracy.
2. **Gate** — run §3.7 (a)-(g) and wq#4152 item 6 (T1–T8 on the 1.8 stack);
   record each result as non-empty evidence text in `live-gate.json`
   (keys `a`..`g`, `wq4152_item6`). `live` refuses (rc 3) until all present.
3. **Live** — explicit ids only, per-task caps + `--verify-cmd` required to close.
   Untrusted-content tickets additionally wait on the sandbox egress ticket
   (wq#4198) — the sandbox network is NOT isolated (§3.4).

## Tests

```
python3 -I ops/wq4200-oh-wq-runner/test_oh_wq_runner.py
```

25/25 pass (2026-10-18). The suite follows wq#2630: probes outside the happy
path (unknown execution status, slash-bearing sandbox id, FENCE-less claim
output, gate file with (a)-(f) but not (g)) — the sandbox-id probe caught a
real quoting defect during development.
