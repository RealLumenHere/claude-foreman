# wq#2200 — verify of wq#2107 (PR #756): CONFIRMED

Verifier: claude-sonnet-5@high, fresh instance. Scope: `ops/wq1252-lumen-public-guardian`
commit 80c7d0d (S1/S2/S4 skipped while the stream unit is masked/not-found, E1-version
names a rejected deploy token) and the claim that the timer is live again.

## Probes

| check | result |
|---|---|
| `pytest ops/wq1252-lumen-public-guardian/tests` (worktree at 802c3c4) | 34 passed |
| `systemctl is-enabled/is-active lumen-public-guardian.timer` | enabled / active, fired 18:26:08 and 18:28:08 UTC |
| `systemctl show -p LoadState` | `lumen-pokemon-live.service` masked, `lumen-lpp-brb.service` not-found — the premise for pruning holds |
| journal of the 18:28:08 pass | `S1-public/S2-unit/S4-local not run — lumen-pokemon-live.service is masked (retired)`; E1-schema, E1-version (`live == validated`), E4-sync, S3-tunnel all OK; no RED, no fire |
| `guardian.py status` | no `S1`/`S2`/`S4` entries (pruned from state), S3-tunnel kept, no open `fire_ticket` |
| new `install.sh --verify` via `lumen-run start` on the live box | `VERIFY OK`, exit 0: 5 code files, mail-probe files, service + timer unit files byte-identical, timer enabled and active |
| negative: appended a line to `checks_stream.py` in the worktree, re-ran `--verify` | `MISS differs: .../checks_stream.py`, `VERIFY FAIL (1)`, exit 1; perturbation reverted |

## Attempted breaks

- Prune runs only when `not only`, and state is written only when not `dry_run`, so a
  `--only` or `--dry-run` invocation cannot drop persisted state.
- `unit_load_state` returns None when systemctl cannot answer, and None is not in
  `RETIRED_LOAD_STATES`, so an unreadable unit is never treated as retired.
- Only 401/403 map to `DeployTokenRejected`; other HTTP errors still raise and surface as a
  failed check, so a Cloudflare 5xx is not downgraded to UNCONFIGURED.
- Registry is re-evaluated each pass, so unmasking the unit re-arms S1/S2/S4.

## Residual notes (not defects)

- Pruning also discards the persisted `fire_ticket` of a check that leaves the registry. The
  guardian never auto-closes fires (`guardian.py:322` only suppresses duplicates), so nothing
  is lost that it acted on; an already-open S-fire would need closing by hand.
- The masked-unit skip means the public LPP stream has no guardian coverage until the unit
  returns; intended, and logged each pass.
- `tests/failure_drills.py` still fails at HEAD per issue-log row 486; not part of this PR.

## Gap closed

The bundle had no `--verify` handler (wq#1696), so "merged is live" could not be checked by
tooling. This ticket adds it (`ops/wq1252-lumen-public-guardian/install.sh`). The sudoers
grant is root 0440 and unreadable to the lumen user, so it is not checked.
