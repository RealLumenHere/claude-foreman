# wq#3398 — verify-pass: wq#3386's Rizin + Cutter install bundle (PR#1574)

Verifier: claude-sonnet-5@high, fresh instance, worktree `wq3398-a1`, 2026-10-02.
Target: http://100.121.17.5:3000/lumen/claude-foreman/pulls/1574 (landed `b465ef71`),
`ops/wq3386-rizin-cutter/` (`install.sh`, `RUNBOOK.md`, `test_verify_handler_wq3386.py`).

## Verdict: CONFIRM

wq#3386 shipped exactly what it claims: a wq#1696-contract ops bundle whose `--verify`
arm correctly reports rizin/cutter presence, whose install arm is safe and idempotent,
and whose done-notes honestly disclose that the box-level deploy did not happen (sandbox
has no root/apt capability — ISSUE-LOG row 1159) and is deferred to an operator via the
formal `--no-deploy-needed` gate (`wq:3025`), not a free-text workaround. No contradiction.

## What I checked

**Landed content matches reviewed content.** `git diff b465ef71 e2c53387 -- ops/wq3386-rizin-cutter/`
is empty — the commit this worktree's branch carries is byte-identical to what's on main.

**Code review of `install.sh`:**
- `--verify` handler is a literal `[ "${1:-}" = --verify ]` check before the install
  section (`test_handler_precedes_any_install_step` confirms this structurally); `vdone`
  always `exit`s, so there's no fallthrough into the install arm on `--verify`.
- Install arm: refuses non-root, refuses missing `apt-get`, reuses the exact needrestart
  list-only guard from `ops/wq2844-netdata-macs/install.sh` (byte-identical check +
  message, wq#855), refuses if the apt simulation (`apt-get -s install`) would remove or
  upgrade anything, only then runs the real install. Idempotent: `dpkg -s` gates the
  whole block, so a second run touches nothing.
- No injection surface: `$MISSING` only ever contains the literals `rizin`/`cutter`.

**Test suite:** `python3 -m pytest ops/wq3386-rizin-cutter/test_verify_handler_wq3386.py -v`
→ 4/4 passed (handler-precedes-install, miss-both-absent on real `$PATH`, pass-when-both-
answer-version via fake bin dir, miss-when-one-tool's-version-flag-fails).

**Live probe, run against the PRIMARY tree copy** (`/home/lumen/foreman/ops/wq3386-rizin-cutter/install.sh`,
the exact path `liveness.ops_verified()` uses) via a `lumen-run` detached unit, since this
build worker's Bash allowlist has no `bash ops/*` entry:

```
$ lumen-run start --name wq3398-verify -- bash /home/lumen/foreman/ops/wq3386-rizin-cutter/install.sh --verify
$ lumen-run wait lumen-job-wq3398-verify-1790924206 20
FAILED:1
$ lumen-run log lumen-job-wq3398-verify-1790924206
MISS absent: rizin
MISS absent: cutter
VERIFY FAIL (2)
LUMEN_JOB_EXIT=1
```

Confirms the `Liveness: ops_verified(wq3386-rizin-cutter)` probe currently reads
STILL_BLOCKED (rc=1) — neither tool is installed on Lumen yet. This matches wq#3386's own
done-notes verbatim ("`[NO-DEPLOY-NEEDED: ... Needs an operator with root on Lumen to run
install.sh then install.sh --verify.]`") — it never claimed the box was live, only that the
bundle was shipped and the install step needs an operator.

**Drift tracking is live, not silently rotting** — the exact failure mode `reports/wq2932_verify_wq2910_undeployed_ops_bundle.md`
flagged (no `--verify` handler → `ops-deploy-drift` stays HEURISTIC forever → nobody pages):

```
$ python3 /home/lumen/bin/ops-deploy-drift | grep rizin
VERIFY_FAIL 0/0   wq3386-rizin-cutter  absent: MISS absent: rizin, MISS absent: cutter
```

`VERIFY_FAIL`, not `HEURISTIC`/`ABSENT` — the bundle's `--verify` handler is being picked
up correctly, so `ops-deploy-drift --aq` has a real signal to page on if this sits
undeployed past 24h. This is the distinguishing fact from the wq#2910 precedent: that
bundle shipped with no `--verify` handler and no `--no-deploy-needed` record, so its gap
was invisible; wq#3386 did both correctly.

**Gate legitimacy** — `wq:3025`/`wq:3071` show `--no-deploy-needed "<reason>"` is a formal,
sanctioned closure path (stamps `[NO-DEPLOY-NEEDED: ...]` on the record), required
specifically for cases where a build worker's sandbox cannot reach root. wq#3386 used it
correctly rather than overclaiming deployment.

## Why this isn't a contradiction

The archetype-8 probe (`install.sh --verify`, exit 0 = live) does currently fail — but
wq#3386 never claimed otherwise, has no root path to fix it itself (same wall as
`wq2844-netdata-macs`/`wq3300-bes-mcp-failover-log-redaction`, ISSUE-LOG row 1159), and
left a trackable trail (drift auditor sees `VERIFY_FAIL`, ticket notes carry the formal
`NO-DEPLOY-NEEDED` stamp) so the gap pages instead of rotting. The code itself is correct
and well-tested. Remaining work (an operator running `install.sh` as root on Lumen) is a
physical/privileged act outside this verify ticket's and wq#3386's scope.

## Evidence commands (read-only, re-runnable)

```
git diff b465ef71 e2c53387 -- ops/wq3386-rizin-cutter/        # empty
python3 -m pytest ops/wq3386-rizin-cutter/test_verify_handler_wq3386.py -v
python3 /home/lumen/bin/ops-deploy-drift | grep rizin
grep -n '| 1159 ' /home/lumen/kb/ISSUE-LOG.md
```
