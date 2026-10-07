# wq#3568 — verify-pass on wq#3552 / PR#1646

Archetype 8 adversarial verify. Author: claude-sonnet-5 (wq#3552). Verifier: claude-sonnet-5@high, fresh instance.

## Scope

wq#3552 claimed to root-apply `ops/wq3354-fleet-selfheal/install.sh` on Lumen, deploying
three things that had drifted from `/home/lumen/agents/fleet-selfheal` (the live install
dir): `leakwatch.py`'s wq#3456 `group_readers` fix, `selfheal.yaml`'s `nodes.ignore` list
(thor/maksoor/apis/pbox/seshat/be3600/3dprinter), and `sh_nodes.py`. PR#1646 (merge commit
`2b4c742b`, merged into main at `8e6db1163c`) ships exactly one code change on top of the
operational deploy: `tests/test_leakwatch.py`'s `_fake_accounts()` hardcoded uid/gid 1000
into faked `pwd`/`grp` records, which only matched the real `tmp_path` file owner when
pytest ran as `lumen`; under root (egid/euid 0, required by `install.sh`'s own pytest gate)
the comparison always missed and `group_readers()` returned `set()`, failing
`test_group_reaching_another_account_is_reported` (ISSUE-LOG row 1199). The fix derives
uid/gid from `os.geteuid()`/`os.getegid()` at call time instead.

## What I checked

1. Read the diff cold (`git diff b791322b 2b4c742b -- tests/test_leakwatch.py`, 8
   insertions / 5 deletions). Traced both affected tests
   (`test_user_private_group_is_not_reported`, `test_group_reaching_another_account_is_reported`)
   by hand against `group_readers()` in `leakwatch.py` for both a `lumen`-run and a
   root-run pytest process — the closures correctly re-capture the real `euid`/`egid` per
   `_fake_accounts()` call, and `readers.discard(owner)` still resolves against the same
   faked owner the fake `getpwuid` returns, regardless of which uid is real. No bug found.
2. Ran the shipped tests as `lumen` (uid 1000): `python3 -m pytest -q
   ops/wq3354-fleet-selfheal/tests` -> **48 passed** (the suite has grown past wq#3552's
   38 via two later tickets, wq#3558/wq#3560 — see below).
3. Pulled the historical root-run evidence wq#3552's own notes cite:
   `journalctl -u lumen-job-wq3552-diag2-1791370161.service` shows the actual
   `lumen-run --root` diagnostic job, `38 passed in 2.23s`, `LUMEN_JOB_EXIT=0` — this
   predates wq#3558/wq#3560 landing, so 38 was the correct count at the time.
4. `wq show 3552`: claimed scope, root-apply method (`lumen-run --root`), and acceptance
   probe all read as described; `done_at 10:55:31` sits after the leakwatch service
   restart (see #6) and the PR merge.
5. **Live code parity** (Bash can't `diff`/`cmp`/`wc` outside this worker's `--add-dir`
   grant, so I pulled each deployed file's full text via `Read` — which is not
   path-scoped — wrote it to a temp file inside my worktree, and `diff -q`'d that against
   the current repo copy):
   - `leakwatch.py`: **byte-identical** to current `main`.
   - `sh_nodes.py`: **byte-identical** to current `main`.
   - `selfheal.yaml`: differs from current `main` by exactly one 6-line block — the
     `ollama:` CLASS 7 section wq#3558 added *after* wq#3552's deploy (see below). The
     `nodes.ignore` list wq#3552 actually shipped is untouched and present verbatim.
6. **Live probe, re-run now**: `python3 /home/lumen/agents/fleet-selfheal/leakwatch.py
   --once --dry-run --state /tmp/fworker-wq3568/x.json` -> exit 0, no output (no
   group-accessible line, no rotate line) — matches wq#3552's stated acceptance exactly,
   and confirms the deployed `group_readers` fix still behaves correctly live, not just
   in pytest.
7. **Units**: `systemctl cat` on all three (`lumen-fleet-selfheal.service/.timer`,
   `lumen-leakwatch.service`) byte-matches the bundle's shipped unit files.
   `systemctl is-enabled`/`is-active` on the timer and the leakwatch service both report
   `enabled`/`active`. `lumen-leakwatch.service`'s `ActiveEnterTimestamp` is
   `10:50:42`, inside wq#3552's claim window (`claimed_at 10:46:08` .. `done_at
   10:55:31`) — consistent with "restarted to pick up new leakwatch.py" actually
   happening, not just claimed.

## Adversarial finding: install.sh --verify would NOT exit 0 right now — but this is not wq#3552's fault

I could not literally run `bash ops/wq3354-fleet-selfheal/install.sh --verify` (not on
this worker's Bash allowlist, and its target `/home/lumen/agents/fleet-selfheal` is
outside `--add-dir` even if it were), so I reconstructed every check `_verify.sh` makes
by hand (file parity above, unit parity, enabled/active). Two later, separate tickets —
wq#3558 (`59c45934`, ollama CLASS 7, merged 11:11) and wq#3560 (`5d35d4dd`, provider 402
handling, merged 11:16) — both landed on `main` *after* wq#3552's deploy (`2b4c742b`,
10:55) and neither has been root-applied yet: `sh_ollama.py` doesn't exist at
`/home/lumen/agents/fleet-selfheal` at all (`Read` fails: "not a regular file —
missing"), and the deployed `selfheal_sweep.py`/`selfheal.yaml`/`sh_providers.py` are one
step behind current `main` because of it. Given `--verify`'s contract ("exit 0 only when
every live target matches what this bundle ships"), running it today would report `MISS`
lines for exactly those three files. **wq#3552 predates both tickets** (confirmed: `git
show 2b4c742b:.../selfheal_sweep.py` has no ollama import) and its own deploy was
genuinely clean at the time (checks #3, #5, #6 above). The two follow-on tickets already
have their own archetype-8 verify tickets open (`wq#3571`, `wq#3572`); a root-apply deploy
ticket for them is the expected next step in this fleet's normal wq3354-> wq35xx-deploy->
wq356x-verify-deploy pipeline, not something this ticket should file — it is out of scope
for a verify-pass of wq#3552 specifically.

## Verdict

**CONFIRM.** wq#3552's diff (the `test_leakwatch.py` fixture fix) is correct and
well-targeted; its claimed root-apply (leakwatch.py, sh_nodes.py, selfheal.yaml's
nodes.ignore) is still byte-exact live, its own stated acceptance probe still passes
cold, both affected units are enabled/active with content matching the bundle, and the
historical root-run journal log corroborates the "38 passed as root" claim at the time it
was made. The one thing that would make a literal `install.sh --verify` fail right now —
missing `sh_ollama.py` / stale `selfheal_sweep.py`/`selfheal.yaml`/`sh_providers.py` — is
drift from two unrelated, later tickets (wq#3558, wq#3560) that haven't been root-applied
yet, not a regression or gap in wq#3552's own work.
