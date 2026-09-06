# wq#1373 — Cloudflare token liveness monitor

## Problem

The fleet holds several scoped Cloudflare API tokens (D1, Workers, DNS, R2,
Access — inventory tracked in `claude_credentials.md`, which this BUILD
worker's sandbox denies reading outright: it matches the guard hook's
credential-file deny pattern). Earlier this cycle the Access-mgmt token was
dead and gated silently until Frank noticed and re-minted it by hand
(`/home/lumen/kb/ISSUE-LOG.md` #37). Nothing was watching token liveness.

`GET /user/tokens/verify` cannot fill that gap: it needs a User-level read
permission most scoped tokens don't carry, so it returns `Invalid` for
healthy narrowly-scoped tokens as readily as for actually-dead ones — false
positive and true positive look identical. The only reliable signal is
probing each token with a real operation drawn from its own scope.

## What this adds

`cf_token_liveness.py` — a manifest-driven prober. For each entry in
`tokens.json` it performs the one real, read-only Cloudflare API call that
token's scope was minted for:

| scope     | call                                             |
|-----------|--------------------------------------------------|
| `d1`      | `POST .../d1/database/{id}/query` (`SELECT 1`)   |
| `workers` | `GET .../workers/scripts`                        |
| `dns`     | `GET .../zones/{id}/dns_records?per_page=1`       |
| `r2`      | `GET .../accounts/{id}/r2/buckets`                |
| `access`  | `GET .../accounts/{id}/access/apps`               |

A non-2xx/`success:false` response, or a missing token file, is a failure and
triggers `ntfy-send -p urgent`. If a manifest entry also carries `minted` +
`ttl_days`, the script warns (`ntfy-send -p high`, does not affect exit code)
once fewer than `TTL_WARN_DAYS` (3) remain.

### Exit codes (wq#1434)

| exit | meaning                                                       |
|------|---------------------------------------------------------------|
| 0    | every configured token probed live                             |
| 1    | at least one token is dead/missing/misconfigured — pages `urgent` |
| 2    | the pass itself could not complete — tokens left unchecked     |

A malformed manifest entry is charged to *that* token (exit 1, alert fires,
the loop keeps going) rather than aborting the pass: the resource-id lookups
live inside the `probe_*` functions, so an entry missing `account_id_file` /
`database_id_file` / `zone_id_file` — precisely the config-only edit this
README prescribes for each pending token below — used to raise an uncaught
`KeyError` out of `run()`, killing the pass before any alert fired and
skipping every later entry (wq#1433). `check_one()` now catches config-shape
errors, and `main()` backstops anything else with exit **2**, which
`SuccessExitStatus=0 1` deliberately does not whitelist — otherwise a crash
(Python's default status is also 1) would look identical to a healthy pass to
systemd *and* to `lumen-cron-deadman`.

The D1 and Workers request shapes are reused verbatim from code already
proven live in this fleet: `mailbot/mail_recall.py:38-46` and
`foreman/ops/wq1252-lumen-public-guardian/checks_email.py:47-65`.

## Token inventory: what's wired up vs. what's pending

Per instinct 7 (never fabricate a value you can't verify), `tokens.json`
lists only the tokens whose files this worker could directly confirm exist
inside its allowed directories:

- `/home/lumen/mailbot/.cf_d1_token` (D1 Read, minted 2026-09-01, per
  `mailbot/mail_recall.py:11`)
- `/home/lumen/mailbot/.cf_d1_write_token` (D1 write-scoped, minted
  2026-09-02, not yet wired into any caller as of this ticket)

Both were probed live during this work (`python3 cf_token_liveness.py
--dry-run` → `cf_token_liveness: 2/2 tokens live`).

The remaining fleet tokens named in the ticket (the #37 Access-mgmt token,
plus Workers/DNS/R2 tokens elsewhere) live in `claude_credentials.md` and/or
on hosts outside this worker's `--add-dir` scope
(`/home/lumen/worktrees/wq1373-a1`, `agents/work-queue`, `mailbot`,
`pokemon`, `bin`, `foreman`, `kb`, `projects/nest`) — the guard hook denies
reading the inventory file directly, and no other `.cf_*`-style token file
turned up in a search of the accessible directories. Guessing paths for
tokens this worker never confirmed would violate instinct 7, so they are
deliberately left out rather than fabricated. Adding each is mechanical once
its `token_file`/resource-id paths are known: append one object to
`tokens.json`'s `tokens` array (schema documented inline via the `_comment`
key and the two existing entries) — no code change required. This does not
block shipping: the monitor is real, tested, and immediately closes the gap
for the tokens it does cover, and every future token is a config-only add.

## Alerting

Reuses `/home/lumen/bin/ntfy-send`, the same fleet-wide publisher every other
probe in this repo uses (see `foreman/ops/wq901-ntfy-oob-429/README.md`,
`foreman/ops/wq897-litellm-auth-monitor-probe/README.md`). `urgent` pages on a
dead token; `high` warns on approaching TTL. No token value is ever logged or
printed — alert text carries only the manifest `name` and the HTTP/scope
error.

## Scheduling

`cf-token-liveness.timer` runs the probe every hour (`OnBootSec=5min`,
`OnUnitActiveSec=1h`), matching the oneshot + `ExecStartPost=lumen-cron-stamp`
pattern from `lumen-public-guardian.service` so a dead prober is itself
caught by `lumen-cron-deadman` (wq#1184).

## Install

```
sudo -u lumen /home/lumen/foreman/ops/wq1373-cf-token-liveness/install.sh
```

(via `lumen-run --root`, per repo convention — this BUILD worker's sandbox
cannot write `/etc/systemd/system` or run `systemctl enable`, so installation
itself is out of scope for this run; the units and installer are the
reviewable, ready-to-apply artifact.)

## Verification run (this ticket)

```
$ python3 cf_token_liveness.py --dry-run
cf_token_liveness: 2/2 tokens live

$ python3 cf_token_liveness.py --manifest /tmp/wq1373-test/tokens_test.json --dry-run
[dry-run] would alert priority=urgent title='CF token liveness: DEAD token(s)'
test-dead-token: d1 probe failed: HTTP Error 404: Not Found
test-missing-file: token file missing (/tmp/wq1373-test/does_not_exist)
[dry-run] would alert priority=high title='CF token liveness: nearing TTL'
mailbot-d1-read-ttl-check: expires in -6.2d (minted 2026-08-01, ttl 30d)
cf_token_liveness: 1/3 tokens live — FAILED: ... — TTL warnings: ...
exit=1
```

Confirms: real live tokens pass via a real scoped op, a garbage token/missing
file both fail loudly with exit 1 (would page `urgent`), and an artificially
back-dated `minted` correctly trips the TTL warning path (would alert `high`)
independent of the pass/fail result.
