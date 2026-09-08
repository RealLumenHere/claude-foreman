# wq#1501 — Minerva verify judge: phantom fallback, timeout, factpack cap

Attempt a2, 2026-09-08 10:39-11:05 UTC. Attempt a1 had already written the
live drop-in (`40-judge-model.conf`, dated wq#1501) and patched the judge code
(the journal gained `judge_ms=[...]` at the 06:45 run) but landed nothing in a
repo and died with the ticket claimed. This pass makes the config a tracked,
replayable artifact, measures what it actually does, and files the two pieces
that need the minerva source.

## Evidence

### Before (journal, 09-04 .. 09-08 06:44, old code)

| verify lines | `judge_err:ReadTimeout` | `fallback:judge_http_429` |
|---|---|---|
| 34 | 23 | 23 |

Every failure went ReadTimeout -> phantom fallback -> 429 -> fail-closed REJECT.
(The ticket's 129/325 is the proposals-table count for the same window; the
journal only retains the last four days.)

### Live config (systemctl show, effective after the drop-in chain)

```
VERIFY_JUDGE_MODEL=qwen36-local
VERIFY_JUDGE_MODEL_2=
VERIFY_JUDGE_TIMEOUT=230
VERIFY_JUDGE_MAX_TOKENS=600
VERIFY_JUDGE_SAMPLES=1
JUDGE_CTX_CHARS=16000
```

### After (10:45 run, timeout 230, n=1, fallback empty)

| proposal | judge_ms | judge | outcome |
|---|---|---|---|
| openbmb/MiniCPM5-2B-GGUF | [230078, 1995] | 0.33 | REJECT judge_low |
| The Search Engine Reverse Engineering Co | [230101, 2276] | 0.00 | REJECT |
| Why Better Models Can Create Riskier Sys | [230101, 1981] | 0.00 | REJECT |

`verify_judge.py journal --since "2026-09-08 10:45"`:
`journal OK verify=3 readtimeout=0 phantom4xx=0 judge_ms[0] max=230101` (exit 0).
The same check over the 06:45 run (a1's 150s config) fails: 2 phantom 4xx, 22%
ReadTimeout.

Phantom fallback: gone. Fail-closed ReadTimeout: gone. But the primary call is
pinned at the timeout on 3/3 calls (9/9 at 150s in the 06:45 run) and a retry
answers in ~2s.

### Where the 2s answer comes from

Isis `:8095/slots` polled during the run (`/health` answers in 3ms, `/slots`
blocks while the main loop is busy):

| time | slot 0 | slot 1 |
|---|---|---|
| 10:51 | 14-token probe, max_tokens 5, processing | - |
| 10:53-10:56 | `/slots` blocked 20s, 40s, 90s (main loop busy) | |
| 10:58:45 (after 110s wait) | id_task 97294, **n_prompt_tokens 6144**, n_decoded 2, processing | 15-token probe |

The verify line for that proposal landed at 10:54:30 with judge_ms[1]=2276.
Slot 0 was still on the 6144-token judge prompt four minutes later, and a
6144-token prefill cannot take 2s on the box that needs >230s for it. So the
answer that scores the proposal is not from qwen36-local. `VERIFY_JUDGE_MODEL_2`
is empty; the rung is whatever `minerva_verify.py` or the LiteLLM router picks
by default (a1's own comment names deepseek-v4-flash as the previous primary).
That rung cannot be named from this sandbox (ISSUE-LOG #233) — wq#1529.

Two consequences recorded, not fixed here:
- the "free judge" is currently the paid rung plus a 230s wait per proposal;
- llama-server does not cancel abandoned non-streaming requests, so every
  timeout leaves a full 6144-token job running on Isis, which is also the LPP
  box. Isis was saturated enough that 15-token probes took minutes.

### Factpack cap (design §2e)

`FACTPACK_CAP = JUDGE_CTX_CHARS - reserve = 15500 < digest 17096`, the third
occurrence of the wq#1383 window class. The judge prompt on Isis is already
6144 tokens of an 8192 window, so the fix is a budget split inside
`build_context` (facts reserved before the digest), not just a bigger window.
That code is `/home/lumen/minerva/minerva_factpack.py`, outside this worker's
sandbox — wq#1528, with `verify_judge.py window --strict` as its replay.

## What landed

- `ops/wq1501-minerva-judge-rung/40-judge-model.conf` — the drop-in, one
  `VERIFY_JUDGE_TIMEOUT` line (a1's live copy carried 150 then 230), reasoning
  and rollback in the header.
- `ops/wq1501-minerva-judge-rung/apply.sh` — root install via `lumen-run
  --root`, backup to `/home/lumen/backups/wq1500-1501/`, daemon-reload, prints
  the effective env. Applied 10:59:56Z (job `lumen-job-wq1501-judge-1788865196`,
  DONE:0, backup `20260908T105956Z.40-judge-model.conf.bak`); second run
  reports `unchanged` (idempotent).
- `ops/wq1501-minerva-judge-rung/verify_judge.py` — env / journal / window
  checks, exit 0 only when all pass.
- ISSUE-LOG #233: minerva paths are outside the foreman worker sandbox.
- Follow-ups: wq#1528 (factpack budget, §2e), wq#1529 (name the retry rung).

## Sandbox walls hit (for the allowlist owner)

- `grep`/`ls` on `/home/lumen/minerva/*`, `/tmp/<agent-scratch>`,
  `/home/lumen/backups/*`: refused as Read() deny (path). Not retried.
- `getent hosts isis`: not allowlisted; Isis's address came from
  `foreman/research` (100.100.129.24).
