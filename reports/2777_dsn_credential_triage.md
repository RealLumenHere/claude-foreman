# wq#2777 -- triage 7 DSN-shaped credentials found in clear (wq#2760 sweep)

## Source
`python3 ops/wq2760-dsn-mask/sweep_dsn_history.py /home/lumen/logs` found 7 distinct url-auth
values in the clear in `/home/lumen/logs/supergateway/desktop-commander-mcp.log` (+ `.log.1`).
The current log had grown past the sweep's 64MB read cap since wq#2760 ran; re-scanned with the
cap lifted (read-only, `ops/wq2760-dsn-mask/sweep_dsn_history.py`'s own logic, `MAX_BYTES`
monkeypatched in a throwaway `/tmp` script) to recover the `cur:` hits the original run missed.

## Disposition of all 7 (`--context` mode used throughout -- masked line context, sha256/12
compared programmatically, plaintext never printed except where noted)

| value_sha12 | scheme/len | Disposition |
|---|---|---|
| `e13443e9b736` | http len14 | **LIVE** -- `minioadmin2026`, the langfuse-minio S3 secret. Confirmed by hash match against the running `langfuse-worker` container's env. **Rotated via wq#2754** (report `reports/2754_langfuse_minio_clickhouse_rotate.md`); dead as of this ticket's close. |
| `9db44f3cd56d` | pg len24 | Dead. Matches `agent/.env.bak.20260910_025133_pre_zai` and 30+ other `.env`/`.env.bak` snapshots April-2026 through 2026-09-25 04:18 -- the pgvector password for that whole window, superseded before the wq#2512 rotation. |
| `0cc16ed01e2c` | pg len32 | Dead. Matches `agent/.env.bak.20260925_051634_pre_rotate` and `.bak.20260926_005935Z_pre_wq2700_pg_rotate` -- pgvector password live 09-25 05:16 through 09-26 00:59 (the wq#2512 rotation's output), superseded by wq#2700. |
| `0a3d9929c59b` | pg len18 | Benign -- not a real credential. Hash matches the literal `realsecretvalue123` test constant in `ops/wq2316-mask-secrets-boundary/test_mask_secrets_boundary.py:214/295` (masker's own doc/self-test example). |
| `d307bc925500` | http len30 | Benign -- not a real credential. Hash matches the literal `oldtok123456789012345678901234` sandbox fixture in `bin/tests/test_gitea_rotate_finish.sh:40` (the gitea-rotate-finish test harness's synthetic pre-state). |
| `63a853c94653` | pg len9 | Unresolved, not live. Context shows it embedded in a `re.compile(...)` DSN-match/replace pattern from an interactive rotation snippet (never saved to disk -- not found in any file on this box). Does not match any recorded pgvector password across 6+ months of `.env`/`.env.bak` snapshots checked (April 2026 -> present, incl. `/var/backups/lumen/wq523_pre_redaction_*` and `/home/lumen/backups/wq523_pre_redaction_*` archives). Most likely a short throwaway/test value from an in-progress rotation script, never applied. |
| `d5699343e649` | pg len32 | Unresolved, not live. Context shows a "confirm both use the same value" sanity-check line around a 09-25-era rotation. Does not match the `.env` value actually written at any recorded point (`9db44f3cd56d` -> `0cc16ed01e2c` -> current `38010aeaaeb2`, verified via the same backup sweep) -- most likely a rotation candidate that was generated, echoed to compare, then discarded/regenerated before being written to disk. |

Current live `agent/.env` `DATABASE_URL`/`LUMEN_PGVECTOR_PASSWORD` (hash `38010aeaaeb2`, confirmed
both fields match each other) does **not** appear anywhere in either log file -- clean.

## Decision: rotate, don't scrub
All 7 values are now dead (6 already were; the 7th closed by wq#2754). Scrubbing/truncating the
~175MB of supergateway operational log for zero remaining live-secret benefit destroys
diagnostic history for no security gain, so the log itself is left in place.

## Follow-up filed separately (class-level, not fixed here)
wq#2806 -- `mask-secrets`/the sweep's url-auth rule only catches DSN/URL-auth-shaped values. It
does not catch `<password>value</password>` XML or plain `KEY: value` config lines, and nothing
masks the supergateway desktop-commander-mcp log pipe itself -- every command (and its raw
output) is logged verbatim, unmasked, by the gateway, independent of whether the operator
remembers to pipe through `mask-secrets`. This exact gap fired live during wq#2754's own
rotation (`cat`-ing ClickHouse's `users.d/default-user.xml` printed `langfuse2026` in the clear,
since `<password>...</password>` isn't a URL-auth shape). The next credential anyone echoes into
a Commander session will leak into this same log the same way wq#2760/wq#2777 found these.
