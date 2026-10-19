# wq#4346 — verify wq#4345 (PR#2243, `ops_bundles_cited` requires `install.sh`)

**Verdict: CONFIRM.** The fix matches its own stated bug, is minimal, doesn't regress the
adjacent "legacy bundle missing a `--verify` handler" case, and holds up against the real
production directory (`ops/wq4329-direct-mcp-commander`) that triggered the original report.

## What shipped (re-read cold)

`git show 8a0c7110` (merged to main, PR#2243): `orchestrator/judgment.py` — one-line
behavior change in `ops_bundles_cited()`:

```
- if b not in seen and os.path.isdir(os.path.join(OPS_ROOT, b)):
+ if b not in seen and os.path.isfile(os.path.join(OPS_ROOT, b, "install.sh")):
```

plus a docstring note and one new test, `test_doc_only_bundle_dir_is_not_cited`, added to
`orchestrator/tests/test_judgment_ops_verify.py`.

## Bug this closes

wq#4345's own detail traces to wq#4342's judgment finding: wq#4341 shipped a doc-only
`ops/wq4329-direct-mcp-commander/README.md` (the bundle's real `install.sh` is stranded on
a still-unmerged sibling PR, wq#4344). Because `ops_bundles_cited()` only checked
`os.path.isdir()`, that doc-only dir got cited as a "bundle" and `file_verify_pass()`
(judgment.py:150-154) appended a "Bundle(s) without a `--verify` handler ... Add one before
confirming" line to wq#4342's own verify ticket — a gate that can't be honestly satisfied
without fabricating or re-implementing someone else's unmerged `install.sh`.

wq#4345's own ticket text offered two acceptable fixes: silently skip the doc-only dir, or
emit a distinct "doc-only, N/A" message. The landed diff takes the first option, which is
in scope per the ticket as filed.

## Verification performed

1. **Unit tests.** `python3 -I -m pytest orchestrator/tests/test_judgment_ops_verify.py -v`
   in the wq4346 worktree (already on top of 8a0c7110): 5/5 pass, including the new
   `test_doc_only_bundle_dir_is_not_cited` and the pre-existing
   `test_legacy_bundle_is_called_out_not_probed` (a bundle **with** `install.sh` but no
   `--verify` handler is still cited and still flagged missing — the fix narrows the
   exclusion to "no `install.sh` at all," not "no working `--verify`").

2. **Real-directory probe (outside the synthetic `tmp_path` fixture population — the
   acceptance-probe requirement in play here).** Ran `judgment.ops_bundles_cited()`
   directly against the live `OPS_ROOT` (`/home/lumen/foreman/ops`), not the test's
   fabricated tree:
   - `ls ops/wq4329-direct-mcp-commander/` on the box: only `README.md`, no `install.sh` —
     confirms the production case is exactly what the ticket described.
   - Citing it (`"amended ops/wq4329-direct-mcp-commander/README.md, ..."`) →
     `ops_bundles_cited()` returns `[]`. This is the exact repro that misfired wq#4342's
     gate; it no longer does.
   - Citing a real bundle that does ship `install.sh`
     (`ops/wq1529-minerva-judge-rung-name/install.sh`) →  returns
     `['wq1529-minerva-judge-rung-name']`. Confirms the fix doesn't over-exclude legitimate
     bundles.
   - A census of `ops/wq*` (323 dirs, 162 with `install.sh`) shows ~161 dirs without one —
     i.e. this code path runs hot in production, not just on the one cited example.

3. **Blast radius.** `grep -rn "ops_bundles_cited"` — exactly one call site
   (`judgment.py:143`, inside `file_verify_pass`), plus the two test call sites. No other
   caller depends on the old `isdir`-only semantics.

4. **Diff scope.** `git show --stat 8a0c7110` — 2 files, 17 lines, matches the stated fix
   exactly. No stray changes.

## Things considered and not found to be problems

- Some older `ops/wq*` dirs (e.g. `wq1092-probe-target-liveness-guard`: `DESIGN.md` +
  `.diff` + a test file, no `install.sh`) also lack `install.sh` for reasons other than
  "doc-only README amendment" (they predate the `VERIFY_CONTRACT.md` convention, per
  `ops/VERIFY_CONTRACT.md`'s own description of every bundle shipping with an
  `install.sh`). The fix treats these identically to the doc-only case (silently dropped
  from citation) — broader than the single wq4329 symptom, but consistent with the
  ticket's own acceptance criteria ("a cited dir with no install.sh at all should ... be
  silently skipped"), and strictly safer than the prior behavior (telling someone to "add a
  `--verify` handler" to a dir with no `install.sh` at all was already the wrong message).
  Not a contradiction of the fix as specified.
- No TOCTOU/race concern: this runs against committed, shipped-ticket text, not
  user-controlled input at request time.

## Probe (re-runnable)

```
python3 -I -c "
import sys; sys.path.insert(0, '/home/lumen/foreman/orchestrator')
import judgment
print(judgment.ops_bundles_cited({'notes': 'amended ops/wq4329-direct-mcp-commander/README.md'}))
"
```
Expected and observed: `[]`.
