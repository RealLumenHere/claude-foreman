# wq#4128 verify-pass: CONFIRM wq#4116 (LIMPET ISO: 5 core RE tools never installed)

Archetype 8 adversarial verify of PR #2063 (http://100.121.17.5:3000/lumen/claude-foreman/pulls/2063),
author claude-sonnet-5, verifier claude-sonnet-5@high (fresh instance). Shipped as commit
6e0a1dbb5bc37535f58cd346aeffd6682bbe7f18, already merged into `main` (confirmed
`git merge-base --is-ancestor 6e0a1dbb5b... origin/main`).

## Claim under test

wq#4116: `limpet/iso-builder/gen_tool_packages.py`'s `resolve()` only handled
`install_method` apt/cargo; unblob, frida-tools, mtkclient, edl, and ghidra all fell
through to `unresolved`, so stage4 logged-and-skipped them and the built ISO shipped
without 5 of its core RE tools. The fix adds `PIP_RECIPES`/`PIP_GIT_RECIPES`/
`BINARY_RECIPES` tables (sha256- or 40-hex-commit-pinned, fail-closed like the existing
`CARGO_RECIPES`), `_resolve_pip()`, binary-method handling in `resolve()`, manifest
partitioning, and `build.sh` stages `stage4c_build_pip_tools` / `stage4d_fetch_binary_tools`.
It also corrects `tool-catalog.json`'s frida `version` (stale `17.18.0` — actually
frida-tools' pinned `frida` dependency range, not a frida-tools release — corrected to
`14.11.0`) and adds commit pins for mtkclient/edl.

## What I checked (independently, not re-running the author's reasoning)

1. **The real catalog actually resolves now.** Loaded the live
   `limpet/tool-catalog.json` through `gen_tool_packages.load_tools()`/`resolve()` myself
   (not just reading the PR's own test): unblob → `pip`, frida → `pip`,
   mtkclient → `pip-git`, edl → `pip-git`, ghidra → `binary`. None fall to `unresolved`.
   `build_manifest()` totals: `catalog=44, baked=15, unresolved=27, cargo=1, pip=4,
   binary=1` — the 27 remaining unresolved tools are a disjoint set (fwanalyzer, jadx,
   cutter, …), not the 5 this ticket targets.
2. **Every pinned digest/commit checked against the live upstream, not trusted from the
   diff's comments:**
   - PyPI JSON API `pypi.org/pypi/unblob/26.6.4/json`: the `manylinux_2_17_x86_64` wheel's
     real sha256 is `0fc16e9c7dc9...3584e` — byte-for-byte match with `PIP_RECIPES["unblob"]`.
   - PyPI JSON API `pypi.org/pypi/frida-tools/14.11.0/json`: only one artifact exists
     (sdist, no wheel at all) — confirms the diff's comment that frida-tools ships sdist-only
     and that `pip download` can't ambiguously pick a different artifact than the one
     hashed. Its sha256 (`b8c772e5...88da921`) matches `PIP_RECIPES["frida-tools"]` exactly.
   - GitHub releases API `repos/NationalSecurityAgency/ghidra/releases/tags/Ghidra_12.1.4_build`:
     asset `ghidra_12.1.4_PUBLIC_20260921.zip` digest `sha256:ddac49f9...87d2d4db` — matches
     `BINARY_RECIPES` exactly.
   - `git ls-remote https://github.com/bkerler/mtkclient HEAD` → `7cb40e727727dfe3faa065eefb29b16ed29bbdf1`;
     `git ls-remote https://github.com/bkerler/edl HEAD` → `1cda1a616748d608667b2d13b73a28d3197ceb45`.
     Both are the literal HEAD today, matching the catalog's new `commit` fields exactly
     (not stale, not guessed).
3. **Tried to break the pip-wheel-ambiguity angle specifically.** unblob publishes 5
   wheels (2 macOS, 2 Linux, 1 musllinux) plus an sdist; a naive sha256 pin could mismatch
   if `pip download` on the build host picked a different one. Checked: the ISO builder
   is x86_64-only (`qemu-system-x86_64`, no `docker run --platform` override, i.e. host
   arch), and glibc-based Ubuntu satisfies `manylinux_2_17_x86_64` over musllinux/macOS —
   pip's platform-tag resolution deterministically picks the pinned wheel. No ambiguity on
   the actual target platform.
4. **Tried to break the shared-venv design for `pip_git_packages`.** `stage4c`'s second
   loop runs a separate `docker run` per package (mtkclient, then edl), each doing
   `python3 -m venv /out/opt/re-venv` against the same host-bind-mounted, already-populated
   directory. Reproduced this locally: created a venv, dropped a marker file under
   `lib/`, re-ran `python3 -m venv` on the same (non-empty, no `--clear`) directory — the
   marker survived. Confirms re-running venv creation on an existing venv doesn't wipe the
   first package's install; the "shared venv at /opt/re-venv" comment's claim holds.
5. **The commit-pin check is real, not cosmetic.** `build.sh`'s pip-git block does
   `git clone --no-checkout` → `checkout --detach "$commit"` → byte-compares
   `rev-parse HEAD` against the pin before installing, and fails closed on mismatch —
   same pattern as the cargo stage it mirrors.
6. **Binary fetch is fail-closed and extraction is scoped correctly.** `stage4d`
   curls, sha256-verifies before any `unzip`, extracts to a scratch dir, resolves exactly
   one top-level directory via `find -mindepth 1 -maxdepth 1 -type d`, and copies that into
   `$CHROOT/opt/ghidra` — matches ghidra's actual release zip layout and the catalog's
   `health_check` path.
7. **Regression tests, run cold:** `python3 -m pytest limpet/iso-builder/test_gen_tool_packages.py -q`
   → **32 passed**, including `test_real_catalog_resolves_the_five_previously_unresolved_core_tools`
   (asserts unblob/frida→pip, mtkclient/edl→pip-git, ghidra→binary against the live catalog —
   this is the ticket's own falsifiable acceptance probe, and it's a real probe: it fails
   loudly with the tool name and got/want method if any of the 5 regress to `unresolved`).
8. **ISSUE-LOG.md row 1381** documents this issue/fix per doctrine, consistent with the
   diff — not a fabricated or mismatched reference.

## One pre-existing inconsistency noted, not attributable to this PR

`tool-catalog.json`'s ghidra entry still carries top-level `"version": "12.1.3"` while
`BINARY_RECIPES`/`package` point at `ghidra_12.1.4_PUBLIC_20260921.zip`. Checked
`git show 6e0a1dbb^:limpet/tool-catalog.json` — this mismatch predates wq#4116 (inherited
from wq#4081) and the diff never touches ghidra's catalog entry, only `BINARY_RECIPES` in
`gen_tool_packages.py`. `resolve()`'s binary-method branch never reads `tool["version"]`,
so this does not affect correctness of the fix under test. Flagging for a future catalog
cleanup ticket, not a contradiction of wq#4116.

## Verdict

**CONFIRM.** All 5 tools resolve from the real catalog; every pinned sha256/commit
checked against live upstream (PyPI JSON API, GitHub releases API, `git ls-remote`)
matches exactly; the build-script install paths are fail-closed and internally
consistent; 32/32 tests pass including the ticket's own real-catalog acceptance probe.
No escalation to opus-5@high.

Verify repo: `/home/lumen/foreman` (commit 6e0a1dbb5bc37535f58cd346aeffd6682bbe7f18,
already on `main` and already on the branch this worktree reads from — no code changes
made by this verify-pass).
