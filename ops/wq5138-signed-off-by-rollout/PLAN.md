# Signed-off-by rollout for the Anubis commit-check (wq#5138, follows wq#5014)

`ops/wq5014-gitea-commit-check` shipped with `require_signed_off_by = false`. If it were turned on
today it would refuse every `lumen-pr land`, for two reasons: no fleet commit carries a sign-off, and
the token-engine squash body (`_pr_footers`) forwarded only `Refs/Assisted-by/Generated-by/BREAKING CHANGE`.

## Phase 1: carry the trailer (this PR, plus lumen-bin)

| Piece | Change | State |
|---|---|---|
| token-engine `_pr_footers` (this PR) | `FOOTER_RE` forwards `Signed-off-by:`, deduped. A PR whose commits carry none gets `Signed-off-by: Lumen Industries <lumen@lumennode.uk>` (`FOOTER_SIGNOFF`). Tests: 2 new, 74/74 pass. | merged to repo. **Not deployed.** See below. |
| lumen-pr `cmd_commit` (lumen-bin) | appends the same trailer after `Assisted-by:` | merged and live (bin is the checkout) |

Deploy blocker: the installed `/usr/local/lib/token-engine/token_engine_daemon.py` already differs from
repo main (`token-engine/check_deploy_drift.py` reports DRIFT). The installed copy carries mask work
(`_SHAPES_BY_LABEL`, foreign-prefix full-shape stop, orphan-prefix backstop: a0077baa, 71f4e066 and
siblings on other branches) that main does not have. Main has `f3ff1714` (refuse Co-authored-by on land),
which is not installed. Running `token-engine/install.sh` from main would roll back masking in the
broker. This change must wait until the drift is reconciled (separate ticket). Then:

```
ssh lumen@100.117.66.102 '/home/lumen/bin/lumen-run start --root -- bash /home/lumen/foreman/token-engine/install.sh /home/lumen/foreman/token-engine'
ssh lumen@100.117.66.102 'python3 /home/lumen/foreman/token-engine/check_deploy_drift.py'
```

Run it when no `lumen-pr land` holds a gitlock. `install.sh` ends in `systemctl restart token-engine.service`.

Gitea web-UI squash merges (Frank) do not go through token-engine. Before Phase 2, choose one:
(a) add `.gitea/default_merge_message/SQUASH_TEMPLATE.md` carrying the trailer to each of the 13 repos, or
(b) accept that web-UI squashes are refused and merge with `lumen-pr land` only. Recommendation: (a). It costs
one file per repo, and Frank keeps the UI button.

Phase 1 is done when: token-engine is deployed with no drift, and 3 consecutive `lumen-pr land`s on main
show `Signed-off-by:` in `git log -1 --format=%B`.

## Phase 2: warn for 2 weeks

The hook has only a boolean (`require_signed_off_by`). It has no warn mode, so a warn period needs
a small hook change first:

1. `hook.py`: add `signed_off_by = "off" | "warn" | "enforce"`, keeping the old boolean as an alias.
   In `warn` mode a missing trailer prints a `remote: WARNING ... (wq#5138, enforced from <date>)`
   line, writes a row to `/var/log/lumen-commit-check/signoff-warn.jsonl` (repo, sha, pusher), and still
   accepts the push. Test both modes in `test_hook.py`.
2. Set `signed_off_by = "warn"` in `cchk.toml` and re-run `install.sh` on Anubis.
3. Replay the last 7 days of main pushes on all 13 repos with the enforce logic (ticket item 4).
   Every miss must be explained (pre-Phase-1 commits, a web-UI squash) before the warn clock starts.
4. Daily: count warn rows. Each new row is a path we missed: fix the path, not the row.

Exit criterion: 14 consecutive days with zero warn rows from lumen-pr lands, and either zero from
web-UI squashes or option (b) chosen.

## Phase 3: enforce

1. `signed_off_by = "enforce"`, run `install.sh` on Anubis, then `install.sh --verify`.
2. Probe: push a commit without the trailer to a scratch branch on a test repo. It must be refused with
   the wq#5138 message. A `lumen-pr land` must go through.
3. Rollback: set `signed_off_by = "warn"` and re-run `install.sh`, which takes one minute. The hook is
   fail-closed, so keep the rollback command in the ticket close-out.

Owner per phase: Phase 1 deploy and Phase 2 hook change are agent work, with the root steps through `lumen-run --root`.
Choosing (a) or (b) for web-UI squashes is Frank's call. The Phase 3 flip needs Frank to say "enforce".
