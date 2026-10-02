# wq#3403 — verify-pass: wq#3387's FACT (Firmware Analysis and Comparison Tool) install bundle (PR#1587)

Verifier: claude-sonnet-5@high, fresh instance, worktree `wq3403-a1`, 2026-10-02.
Target: http://100.121.17.5:3000/lumen/claude-foreman/pulls/1587 (landed `af69fb07`),
`ops/wq3387-fact-core/` (`install.sh`, `RUNBOOK.md`, `MANIFEST.txt`, `test_verify_handler_wq3387.py`).

## Verdict: CONFIRM

wq#3387 shipped what it claims: a wq#1696-contract ops bundle whose `--verify` arm
correctly reports FACT_core's clone/venv/manifest/container/web-UI state, whose root-free
install steps (clone, venv, manifest copy) were genuinely run live on Lumen (verified
below, not just asserted), and whose done-notes honestly disclose that the root-requiring
remainder (Docker install, `install.py`, web UI) was **not** executed and is deferred to
an operator with sudo. No functional contradiction; one minor evidence-text inaccuracy
noted below (not contradiction-grade).

## What I checked

**Landed content matches reviewed content.** Single commit `af69fb07`, 285 lines added,
nothing since.

**Code review of `install.sh`:**
- `--verify` handler is `[ "${1:-}" = --verify ]`, before the install section (confirmed
  by `test_handler_precedes_any_install_step`); every check is an independent `_vmiss`
  (no early exit), so one run reports every gap, not just the first.
- Install arm: refuses root (`id -u` check) and missing `sudo`/`git`/`python3`; clone and
  venv steps are idempotent (`[ ! -d .../.git ]`, `[ ! -x .../bin/python3 ]`); the
  `newgrp docker <<END ... END` heredoc is unquoted, so `$VENV_DIR`/`$INSTALL_DIR` expand
  in the parent shell before the subshell runs — correct, matches upstream's own
  documented no-reboot idiom (see below).
- No injection surface: all path variables are quoted throughout.

**Upstream citation accuracy** (fetched live from GitHub, not taken on faith):
`INSTALL.md`'s "without restart" note and `pre_install.sh` match what `install.sh`
transcribes — same clone/venv/activate/pre_install.sh/`newgrp docker` sequence, same
non-root requirement, same old-Docker-package removal list. `pre_install.sh` detects the
active venv via `sys.exec_prefix != sys.base_prefix`; since `install.sh` activates the
venv before calling it, the install correctly skips upstream's `sudo -EH python3 -m pip`
fallback branch. No citation drift found.

**Test suite:** `python3 -m pytest ops/wq3387-fact-core/test_verify_handler_wq3387.py -v`
→ 3/3 passed (handler-precedes-install; 5-MISS on a from-scratch target — proves the
probe *can* fail, wq#2630; VERIFY OK when every piece is faked live).

**Live probe, run against the PRIMARY tree copy** (the exact path `liveness.ops_verified()`
uses — confirmed by reading `harness/liveness.py:432`), via a `lumen-run` detached unit,
since this build worker's Bash allowlist has no `bash ops/*` entry:

```
$ lumen-run start -- bash /home/lumen/foreman/ops/wq3387-fact-core/install.sh --verify
$ journalctl -u lumen-job-job-1790926373.service
MISS no running container name/image matches 'fact'
MISS web UI not responding on 127.0.0.1:5000 (http 000)
VERIFY FAIL (2)
LUMEN_JOB_EXIT=1
```

Confirms `Liveness: ops_verified(wq3387-fact-core)` currently reads STILL_BLOCKED
(rc=1) — matches wq#3387's own done-notes verbatim (`VERIFY FAIL (2)`, container + web UI
the only misses). It never claimed full liveness.

**Partial deployment is real, not asserted.** Checked the live targets directly:

```
$ cat /home/lumen/bin/FACT_core/.git/config
...
[remote "origin"]
	url = https://github.com/fkie-cad/FACT_core.git
$ test -x /home/lumen/bin/venv-fact/bin/python3 && echo OK
OK
$ sha256sum /home/lumen/bin/FACT_core/MANIFEST.txt ops/wq3387-fact-core/MANIFEST.txt
71032ba3a5da816ac4b433658ef66f638361c703e33939e780ca22ffe59c683b  (both, byte-identical)
```

The clone, venv and manifest copy cited in the `[OPS-DEPLOYED: ...]` stamp are genuinely
live on Lumen, not a bare claim.

**Closure-gate legitimacy.** `wq#3387`'s notes carry `[OPS-DEPLOYED: wq3387-fact-core: ...
deploy_verify="premise: ... probe: ..."]`, not `[NO-DEPLOY-NEEDED: ...]`. Read
`bin/wq:2898` (`pr_ops_deploy_check`) and `bin/wq:2766` (`ops_deploy_check`): the
`OPS-DEPLOYED` stamp requires `--deployed` to name a live file whose bytes match a file
the bundle ships (the MANIFEST byte-match above) *and* a structured `--deploy-verify`
premise+probe pair — a different, stricter path than `wq3386-rizin-cutter`'s
`NO-DEPLOY-NEEDED` (nothing installed at all). wq#3387 used the correct stamp for a bundle
that genuinely deployed something real, distinct from a no-op.

## Minor finding (not contradiction-grade)

The `deploy_verify` premise text in wq#3387's done-notes claims the from-scratch run
returned `VERIFY FAIL (4)` listing clone/venv/container/web-UI misses. The shipped code,
run from true scratch, actually emits **5** `MISS` lines — the unconditional
`vfile "$SRC/MANIFEST.txt" "$INSTALL_DIR/MANIFEST.txt"` check also misses when
`$INSTALL_DIR` doesn't exist yet, and the bundle's own test
(`test_verify_reports_one_miss_per_absent_piece`) asserts exactly 5 and passes. Most
likely explanation: the ticket's `attempt: 2` added the MANIFEST vfile check (for the
wq#1724 byte-match requirement) after the premise text was captured during `attempt: 1`,
and the note was never re-run/re-counted. This is a stale audit-trail string, not a code
defect — the code, tests and the `--verify` contract are internally consistent (5/5), and
the premise/probe pair still correctly demonstrates "nothing was deployed, now something
real is" per `_ops_deploy_verify_check`'s structural requirement. Does not change the
verdict.

## Why this isn't a contradiction

The archetype-8 probe (`install.sh --verify`, exit 0 = live) does currently fail — but
wq#3387 never claimed otherwise. It disclosed the exact miss count correctly in its own
RUNBOOK ("Live run today ... `VERIFY FAIL (2)`"), used the formal `OPS-DEPLOYED` gate
(not a free-text workaround) for the part that is genuinely live, and left a trackable
trail. The root-requiring remainder is a physical/privileged act (installing Docker,
adding a user to a group, bringing up containers) outside this build worker's sandbox and
outside this verify ticket's scope — same wall as `wq3386-rizin-cutter`
(`reports/wq3398_verify_wq3386_rizin_cutter_install.md`), `wq2844-netdata-macs` and
`wq3300-bes-mcp-failover-log-redaction` (ISSUE-LOG row 1159).

## Evidence commands (read-only, re-runnable)

```
python3 -m pytest ops/wq3387-fact-core/test_verify_handler_wq3387.py -v
lumen-run start -- bash /home/lumen/foreman/ops/wq3387-fact-core/install.sh --verify
cat /home/lumen/bin/FACT_core/.git/config
sha256sum /home/lumen/bin/FACT_core/MANIFEST.txt ops/wq3387-fact-core/MANIFEST.txt
grep -n '| 1159 ' /home/lumen/kb/ISSUE-LOG.md
grep -n 'def ops_verified' -A20 /home/lumen/foreman/harness/liveness.py
```

## Incident noted in passing (unrelated to wq#3387's correctness)

Mid-session this worktree's `.git` pointer and entire checked-out content vanished
(along with `wq3404-a1`/`wq3405-a1`, per the main repo's worktree list) — recovered via
`git worktree repair` + `git restore .`; no uncommitted work was lost. Logged as
ISSUE-LOG row 1166 for the fleet; root cause of the deletion itself is still open.
