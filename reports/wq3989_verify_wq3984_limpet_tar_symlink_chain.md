# wq#3989 — verify-pass of wq#3984 (close LIMPET `tar --from-usb` symlink-chain traversal escape)

PR #1940 (http://100.121.17.5:3000/lumen/claude-foreman/pulls/1940), merged as
`6db0e94a` on `main` (squash content `2ff39c27`), 2 files
(`ops/wq3942-limpet-exploit-db-a1/limpet_update.py` +5/-1,
`.../tests/test_limpet_update.py` +28). Author model: claude-sonnet-5.
Verifier: claude-sonnet-5@high (this ticket, fresh instance).
Verdict: **CONFIRM** — no contradiction, no escalation to opus-5@high.

## What wq#3984 was fixing

ISSUE-LOG row 1337 (wq#3954 verify-pass, escalated and confirmed by opus-5@high):
`extract_from_usb`'s pre-extraction guard loop checked `member.name` lexically
against `checkout_dir` but never inspected `member.linkname`, so a symlink
member followed by a nested regular-file member through it sailed past the
guard — the symlink doesn't exist on disk when the check loop runs, so its
target is never followed. The only thing actually blocking the PoC on this
host was Python 3.14's implicit default `tarfile` extraction filter (PEP 706
`data_filter`), not the guard's own logic — proven by forcing
`extraction_filter = fully_trusted_filter` and watching the PoC escape.

## What changed (`limpet_update.py:82-93`)

Inside the existing per-member containment loop, for any member where
`member.issym() or member.islnk()`: resolve `(member_path.parent /
member.linkname)` and require it to stay under `dest_resolved`, raising
`UpdateError` otherwise. Separately, `tf.extractall(dest)` →
`tf.extractall(dest, filter="data")`, making the real protection explicit
instead of riding the interpreter's implicit default.

## What I checked

1. **Read the diff cold**, confirmed it's the only change in the PR (diffstat
   matches exactly: the guard's new `if member.issym() or member.islnk()`
   block plus the `filter="data"` argument; test file adds one new test).
2. **Ran the existing suite**: `pytest ops/wq3942-limpet-exploit-db-a1/tests`
   — 8/8 pass, including the new
   `test_extract_from_usb_rejects_symlink_chain_escape`, which monkeypatches
   `tarfile.TarFile.extraction_filter = fully_trusted_filter` to recreate the
   exact PoC pattern (symlink `link -> ../outside` + nested `link/evil.txt`)
   and asserts `UpdateError`.
3. **Ran the ticket's liveness probe.** `bash install.sh --verify` itself is
   outside the worker Bash allowlist (only `bash scripts/*`, not an absolute
   `ops/.../install.sh` path, is permitted — naming this per the "say so
   plainly" rule rather than burning retries on it). Read `_verify.sh`'s
   contract and replicated both of its checks directly with allowed
   commands: the same `pytest ops/.../tests` run above (equivalent to the
   script's first `_vmiss` check), and the shipped `python3 -c` DB
   build+query snippet verbatim — `build_db` returns 4 rows, `query_devices`
   for `mt8167` returns exactly 1 row with `exploit_tool == 'mtkclient'`.
   Both halves of `--verify`'s contract pass; exit 0 is what it would report.
4. **Adversarial extensions beyond the one documented PoC**, run against the
   live code un-doctored (Python 3.14, real implicit `data` filter active —
   not the monkeypatched population the shipped test draws from):
   - Absolute symlink target (`linkname=/etc/passwd`) + nested file through
     it → blocked by the guard's own check
     (`refusing unsafe tar symlink target: link1 -> /etc/passwd`).
   - Two-hop symlink chain (`a -> b`, `b -> ../outside`, nested file under
     `a/`) → blocked on `b`'s own linkname check, independent of chain depth.
   - Out-of-order archive (nested file member written *before* its symlink
     member) → still blocked; the guard's validation loop runs to completion
     over all members before any extraction happens, so member order inside
     the archive doesn't matter to it.
   - Hard-link escape (`LNKTYPE`, `linkname=../../outside/secret` from a
     nested member path) → extraction still refused, but **not by the
     guard's own check** (see finding below).

## Attempted to break it — one real finding, not a contradiction

The new guard branch applies the *same* `member_path.parent / member.linkname`
base to both `issym()` and `islnk()` members. That base is correct POSIX
symlink semantics (a symlink target is resolved relative to the link's own
directory), but it is **not** how `tarfile` resolves hardlinks: per
`TarFile._find_link_target`, a hardlink's `linkname` is looked up against
other *archive member names* (`tarinfo=limit` search), never joined with
`os.path.dirname(tarinfo.name)` the way a symlink's is. I built a hardlink
member `sub/x` with `linkname=../outside/secret` — under the guard's
parent-relative arithmetic this resolves to `dest_resolved/outside/secret`
(in-bounds, so the guard's own check is silently satisfied and does **not**
raise) — mirroring exactly the "guard code looks protective but isn't"
failure mode that was the root cause of the original wq#3942 bug, just
relocated to the hardlink branch.

In practice this is not currently exploitable: `tf.extractall(dest,
filter="data")` is an *explicit* keyword argument, and cpython only
consults the `TarFile.extraction_filter` class attribute when `filter=None`
— an explicit `filter="data"` is never overridden by monkeypatching that
attribute. I confirmed this directly: re-running the hardlink probe with
`tarfile.TarFile.extraction_filter` forced to `fully_trusted_filter` (the
exact technique that proved the original bug's guard was a no-op) produced
an *identical* refusal, with cpython's own `data_filter` error text ("would
link to ... which is outside the destination"), not the guard's. So the
explicit `filter="data"` this same PR added is doing the real work for the
hardlink case on every Python version that implements PEP 706 (3.12+,
including this host's 3.14), and it cannot be silently disabled by the
mechanism that caused the original incident. No escape succeeded in any
variant I tried.

This is worth a follow-up ticket (the hardlink branch of the guard's own
check gives a false sense of defense-in-depth it doesn't actually provide —
same shape as the original bug, smaller blast radius since `filter="data"`
backstops it), but it does not contradict wq#3984: the vulnerability that
ticket was chartered to close — the symlink-chain escape, including the
documented PoC and every variant above — is closed, verifiably by the
guard's own logic for symlinks, and by the now-explicit `filter="data"` for
everything else.

## Conclusion

The documented PoC and every adversarial extension I constructed (absolute
target, multi-hop chain, out-of-order archive, hardlink) fail to escape
`checkout_dir`. The symlink branch of the new guard is independently
correct and sufficient on its own (verified with the interpreter's real
default filter unaffected, and separately with it forced off via
monkeypatch per the shipped regression test). The hardlink branch has a
latent base-path bug but is backstopped by the explicit `filter="data"` this
same PR added, so no live bypass exists. Test suite and liveness probe both
green. **CONFIRM.**
