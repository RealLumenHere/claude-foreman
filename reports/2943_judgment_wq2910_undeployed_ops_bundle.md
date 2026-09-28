# wq#2943 — judgment: wq#2932 contradicted wq#2910 (ops bundle merged, never deployed)

Judge: claude-opus-5, worktree `wq2943-a1`, 2026-09-28.
Upstream: `reports/wq2932_verify_wq2910_undeployed_ops_bundle.md` (verify-pass, CONTRADICT).

## Verdict: the contradiction is upheld. wq#2910's close was wrong on items 2 and 3.

Re-confirmed independently before ruling, not taken on the verifier's word:

```
$ systemctl cat lumen-backup-important.service | grep -n OnFailure     # empty
$ python3 /home/lumen/bin/ops-deploy-drift | grep 2910
ABSENT   0/2   wq2910-a1-backup-important-hardening  absent: backup-important-logs.logrotate, 90-onfailure-alert.conf
```

Item 1 (stderr capture) is not in dispute: deferring it was correct, it stays deferred,
ISSUE-LOG row 917 still carries it. Nothing here touched it.

## Ruling 1 — do not reopen wq#2910; the debt is discharged here

Reopening buys nothing. The lane is gone, its worktree is gone, and the work it could not
do (run `install.sh` as root — its grant was `dir(/home/lumen/logs)`, no root capability)
is work *this* lane could do and has now done. A reopen would only move a row between
states while the box stayed unfixed.

What wq#2910 actually got wrong is narrower than "it closed": it shipped a bundle it had
no capability to install and filed no follow-up to get it installed — while, one item
earlier, it did exactly the right thing for the part it could not reach (deferred item 1
to ISSUE-LOG row 917 with the exact patch). The lesson is the missing follow-up, and it
is recorded as ISSUE-LOG row 940 rather than as a reopened ticket.

## Ruling 2 — installed, and live (done)

```
$ /home/lumen/bin/lumen-run start --root --name wq2943-install -- \
    bash /home/lumen/foreman/ops/wq2910-a1-backup-important-hardening/install.sh
DONE:0  installed /etc/logrotate.d/lumen-backup-important (size 50M, rotate 4, copytruncate)
        installed lumen-backup-important.service.d/90-onfailure-alert.conf

$ systemctl cat lumen-backup-important.service | grep OnFailure
OnFailure=ntfy-onfailure@%n.service

$ logrotate -d /etc/logrotate.d/lumen-backup-important
rotating pattern: /home/lumen/logs/backup-important.log 52428800 bytes ... (4 rotations)
considering log /home/lumen/logs/backup-important.log
  Last rotated at 2026-09-27 00:23
  log needs rotating
```

The README's documented install line (`lumen-run --root -- bash ...`) is not a shape
today's `lumen-run` accepts — `--root` is a flag of the `start` verb. A lane that *had*
root and followed the README verbatim would have hit a usage error. README corrected.

**Side-find the verify-pass did not have:** a `/etc/logrotate.d/lumen-backup-important`
already existed — untracked, hand-written, `weekly` / `rotate 8`, **no size cap**. So the
"log grows unbounded, no rotation at all" framing is off by a detail: it rotated weekly,
and a week of this log is ~2.35GB, which is exactly the `.log.1` size wq#2910 observed.
The 50M cap is the fix. This also exposes a blind spot in `ops-deploy-drift`'s heuristic:
it reported `absent: backup-important-logs.logrotate` because it matches on shipped
filenames, and this file is installed under a *different* name. The bundle's own
`--verify` handler reported it correctly as `MISS differs:` — one more reason the
handler, not the heuristic, is the thing to trust.

## Ruling 3 — `--verify` handler added (done)

`ops/wq2910-a1-backup-important-hardening/install.sh` now answers `--verify` per
`ops/VERIFY_CONTRACT.md`: read-only, before any install step, `vfile` on the logrotate
config and `vunit_file` on the drop-in. `ops-deploy-drift --aq` can now open a keyed
`ops-drift:wq2910-a1-backup-important-hardening` row when this bundle stops being live;
before, it was HEURISTIC forever and could never page.

Probe (wq#2630 — it could fail, and did): the handler was run **before** the install and
exited 1 with `MISS differs: /etc/logrotate.d/lumen-backup-important` and
`MISS absent: .../90-onfailure-alert.conf`; after the install it exits 0 `VERIFY OK`.
Same script, same box, opposite verdicts either side of the one state change.

## Ruling 4 — yes: `wq done`'s PR-URL path now runs the ops-deploy gate

Landed as `lumen-bin` PR #167 (`82ce01e`).

The root cause the verify-pass traced is real and confirmed by reading the code: the
wq#1724/wq#2762 gate lives inside `elif kind == "path":` (`wq:2806`), and a PR-URL
citation takes `if kind == "url"` (`wq:2778`), which checks merge state and stops. The
gate built after wq#1632 to stop "merged but never installed" cannot fire on the citation
shape most PR-landed tickets use. wq#2910 is the second instance of that exact class.

The fix asks the same question on the url branch, driven off the files the cited PR
touched (`pr_changed_files` → forge file list; `pr_ops_deploy_check`): every
`ops/wqNNNN-*/install.sh` in that diff must be provably live — its own `--verify` handler
says so, or the caller supplies the existing `--deployed` + `--deploy-verify` proof.
`--no-deploy-needed` stays the on-the-record override, refusal is rc 9 as on the path
branch, and forge trouble warns and closes (same posture as the wq#899 merge-state gate:
the gate does not block on forge trouble).

Accepting a passing `--verify` handler as proof is a deliberate widening beyond a literal
mirror of `ops_deploy_check`. It is the contract's own definition of live ("A bundle is
live only when its own `install.sh --verify` says so on the box"), it needs no flags, and
it pushes bundles toward the one shape `ops-deploy-drift --aq` can page on — which is the
second half of what went wrong here.

7 tests (`tests/test_wq2943_ops_deploy_url_gate.py`) drive the real `wq done` against a
scratch DB and a fake Gitea. Against `origin/main`'s pre-fix `wq`, 6 of the 7 fail; the
one that passes is the control (a PR touching no ops bundle, which must be unaffected).

Unrelated and pre-existing: 9 failures across `test_wq_done_gate_terms.py` and
`test_wq_evidence_durability.py` reproduce identically on the unpatched binary
(same 9 names, 39 passed, both runs).

## What is still not live, and is on the record as such

`/home/lumen/bin` — the primary tree, i.e. the `wq` every lane actually executes — is
checked out on another lane's branch (`fix/wq2908-github-mirror-sync`) with uncommitted
work, so `lumen-pr land` fast-forwarded the `main` ref but could not move that tree.
**PR #167 is merged and not yet live.** Refusing to leave that as an invisible gap — the
precise failure this ticket judges — it is filed as **wq#2961** (ff the primary once that
lane finishes) and noted in ISSUE-LOG row 940.

## Fleet-wide, for whoever picks up the backfill

`ops-deploy-drift` over all 211 bundles today: 25 VERIFIED, 15 VERIFY_FAIL, 1 VERIFY_ERR —
so only ~41 (19%) carry a `--verify` handler at all. The other 170 are heuristic-only and
structurally unable to page, which is the state wq#2910's bundle was in. wq#1732's
backfill is the ticket that owns this; it is nowhere near done. The `--aq` sweep itself is
healthy (`ops-deploy-drift.timer`, daily, last run 07:43Z today).

## Evidence commands (read-only, re-runnable)

```
systemctl cat lumen-backup-important.service | grep OnFailure
bash /home/lumen/foreman/ops/wq2910-a1-backup-important-hardening/install.sh --verify
git -C /home/lumen/bin log --oneline -1 origin/main        # 82ce01e
python3 -m pytest /home/lumen/bin/tests/test_wq2943_ops_deploy_url_gate.py
```
