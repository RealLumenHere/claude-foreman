# PILOT REPORT — dab847127347: Tune KV-cache settings for local long-context models in LiteLLM/Ollama

Ticket: wq#2432 | Card: `/home/lumen/foreman/pilots/dab847127347/CARD.md`

## What was attempted

Per the card, the baseline arm requires a second ollama 0.30.7 instance on
hathor (127.0.0.1:11435, no flash attention / no KV-cache override), then
treatment arms B (`q8_0`) and C (`q4_0`) on the same node, all against the
same fixed synthetic 4k-token sample (`random.Random(1234)`, 5 planted
facts, 5 questions). The runner (`pilots/dab847127347/run_pilot.py`,
committed on this branch) implements `build-sample` and `run-arm` exactly
to that spec: deterministic sample generation, per-round prefill/decode
throughput via ollama's `prompt_eval_count/prompt_eval_duration` and
`eval_count/eval_duration`, and needle-accuracy scoring against the five
planted vault codes. `build-sample` was run locally and confirmed
deterministic (sha256 `e2d36cfee33b1a742cb9b8c60888144dc19265f6c2c83a7c37b508f60ae1b3d7`
for seed 1234).

## Why no measured numbers are reported

The execution step could not run:

- `fleet-exec run hathor ...` denies with `grant:grant_missing_env` —
  wq#2432 was opened with `needs:` empty, so `fworker.resolve_exec_grant`
  never wrote a `FOREMAN_GRANT_FILE` for this attempt (same defect as
  ISSUE-LOG #257/#265 on wq#1560/wq#1570; recorded as ISSUE-LOG #778).
- A bare `ssh hathor ...` is refused outright ("This command requires
  approval") — `ssh` is not on this worker's per-command Bash allowlist,
  by design (the exec path is meant to go through `fleet-exec`, which is
  the thing the missing grant blocks).
- Lumen's own ollama (127.0.0.1:11434) is not a substitute: it is a live
  production instance serving embedding traffic under a 4-core / no-GPU
  systemd cap (`systemctl status ollama`: `ram-cap.conf`, `no-gpu.conf`),
  and it does not have `llama3.1:8b` pulled (`/api/tags` lists only small
  embedding/instruct models). Running an 8B-model prefill benchmark there
  would both contaminate production traffic — exactly what the card's own
  contamination check (production CPU-seconds before/after) exists to
  catch — and measure the wrong hardware/model entirely.

Per the card's kill criteria: "Baseline does not reproduce -> stop, report
the baseline defect. Do not proceed." That is what happened: the baseline
arm (Arm A on hathor) never started, so no round of any arm ran and there
are no throughput or accuracy numbers to report.

This is reported as WATCH rather than SKIP: the hypothesis (flash
attention + `q8_0` KV cache gives >=10% higher prefill throughput with no
needle-accuracy loss) is untested, not falsified. Nothing here should be
read as evidence against the paper's findings.

## What unblocks a real run

`pilots/dab847127347/run_pilot.py` is ready to execute unmodified once a
worker attempt on this ticket carries `needs: ssh(hathor)` so
`fleet-exec` gets a valid grant (or a runner with pre-authorized SSH
access runs it directly): `build-sample`, then three `run-arm` calls per
arm (baseline against the plain port-11435 instance, then `q8_0`, then
`q4_0`, restarting the second instance between arms per the card).

RECOMMENDATION: WATCH — baseline arm did not reproduce (no hathor exec
grant on this ticket attempt; ISSUE-LOG #778). No throughput or accuracy
numbers were collected. Re-run once wq#2432 (or a successor ticket) is
dispatched with `needs: ssh(hathor)`.
