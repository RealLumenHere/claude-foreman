# wq#3606 — burned Langfuse INIT project secret (sk-key `e04dbc05b403`)

## What burned

`mask_replay.py 3513` (exit 1): one unaccounted value, sk-key len 51, sha256-12 `e04dbc05b403`,
seen in wq#3513 attempt 5's transcript. Context line: `LANGFUSE_INIT_PROJECT_SECRET_KEY:` in the
langfuse compose file. The env key name and the 2026-10-01 rotation (rotated_ledger.json, wq2975:
"new pair created via INIT reseed") mean it is the live `lumen-pipeline` project key, the one
litellm and the langfuse-evaluator/idle-alert consumers use. Hash-matching it against
`agent/.env` was not possible from a BUILD worker: guard.sh denies that path (wq#2980).

## Leak path (closed in this bundle)

wq#3513 a5 ran `lumen-run start -- bash -c '... compose file redacted ...'` with its own
hand-written redaction, which missed the INIT secret, then `lumen-run log <unit>` printed
the journal unmasked. `lumen-run log` now pipes through the sibling `mask-secrets` and
exits 3 without printing when that is missing (`lumen-run`, `log)` case).

Deploy: `bash install.sh`; verify: `bash install.sh --verify`; test:
`python3 -m pytest -q test_lumen_run_log_mask.py` (includes a control showing the old line leaks).

## Rotation (needs a container-capable operator lane)

BUILD workers cannot do this step: guard.sh refuses the container CLI except ps/logs, and no
`needs:` tag grants it (needs.py TAG_NAMES; ISSUE-LOG rows 914/928/964). It is the same
procedure burnlist-1 ran on 2026-10-01 for `6cf8c0a3e6a1`:

1. `slock` litellm + langfuse. Run `cred-consumers LANGFUSE_PUBLIC_KEY LANGFUSE_SECRET_KEY` and keep the list.
2. Mint a new pair: set new `LANGFUSE_INIT_PROJECT_PUBLIC_KEY` / `LANGFUSE_INIT_PROJECT_SECRET_KEY`
   in the langfuse compose/.env (0600, back up first).
3. Delete the old pair's row from `api_keys` in langfuse-postgres (match on public key).
4. Recreate langfuse-web so the INIT reseed creates the new pair, then flush the langfuse redis
   api-key cache (the old key keeps returning 200 from cache until it is flushed, wq2756).
5. Write the new pair to `agent/.env` and every consumer from step 1. Recreate litellm and restart the listed units.
6. Verify: old pair returns 401, new pair returns 200, and a litellm-acompletion trace lands in clickhouse.
7. Record it: `python3 /home/lumen/foreman/harness/mask_replay.py record-rotated --sha256-12 e04dbc05b403 --ticket 3606`.
   After that, `mask_replay.py 3513` exits 0, which is this ticket's AC1.

wq#3513 (move the remaining compose literals to `.env`) touches the same files and the same
restart window. Doing both in one window means one restart instead of two.
