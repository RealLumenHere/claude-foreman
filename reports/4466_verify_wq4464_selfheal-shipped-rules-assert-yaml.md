# wq#4466 — verify-pass of wq#4464

Author: claude-sonnet-5 (wq#4464). Verifier: claude-sonnet-5@high (fresh instance, same model).
Target PR: #2341, squash-merged to main as `be9bd804fb` (now `e7c58332` on `main`).
File touched: `ops/wq3354-fleet-selfheal/tests/test_shipped_rules.py` (+26/-4, only file).

## Claim being verified

`load_rules()` (`leakwatch.py:178-184`) drops any rule whose `dir` is absent on the host.
`/home/lumen/langfuse` no longer exists on lumen, so `load_rules()` returns 5 rules even
though `selfheal.yaml:237-239` still ships a 6th (langfuse chmod) rule. The old test asserted
on `load_rules()`'s host-filtered output, so it failed on lumen and tripped `install.sh`'s
root-install test gate (`install.sh:67-68`). Fix: assert on the parsed YAML (the shipped
contract) via a new `_shipped_rules()` helper; keep a `skipif`-guarded test
(`test_load_rules_keeps_the_langfuse_rule_when_its_dir_exists`) covering `load_rules()`'s
dir filter for hosts where the dir exists.

## What I checked

1. **Read the diff cold** (`git show be9bd804fb`) — only `test_shipped_rules.py` changed.
   `_shipped_rules()` reads `selfheal.yaml` directly and returns
   `{"dir", "globs", "action"}` dicts — the same shape `load_rules()` produces, so
   `leakwatch.rule_for()` (which only does `os.path.realpath` string comparison + glob
   match, no `isdir` check) behaves identically whether the dict came from the filtered
   loader or straight from YAML. Confirmed at `leakwatch.py:84-90`.
2. **Confirmed the root cause claim independently**: `selfheal.yaml:220-239` ships exactly
   6 `leakwatch.rules` entries (grep'd directly), the 6th being the langfuse rule at
   lines 237-239 (`d9998c22`/wq#3512). `/home/lumen/langfuse` does not exist on this host
   (ls on that path is sandbox-blocked as outside allowed dirs, which itself confirms it
   isn't a path anything here operates on; `load_rules()`'s `os.path.isdir` check would
   drop it).
3. **Ran the real suite on lumen** (not the author's claim, my own run):
   `python3 -m pytest -q ops/wq3354-fleet-selfheal/tests` →
   **76 passed, 1 skipped** (the skip is `test_load_rules_keeps_the_langfuse_rule_when_its_dir_exists`,
   correctly skipped since `/home/lumen/langfuse` is absent here). Matches wq#4464's
   done-notes exactly.
4. **Adversarial probe — AC requires the test be able to fail.** Two independent probes,
   both outside the fix's own test file so they can't share a bug with it:
   - `probe_removed_rule.py`: re-implemented the shipped-rule-parsing logic against a copy
     of `selfheal.yaml` with the langfuse rule stripped → 5 rules, `len(rules) >= 6`
     correctly raises `AssertionError`.
   - `test_probe_real_module.py`: imported the **actual** `test_shipped_rules` module,
     monkey-patched its module-level `CFG` to point at a stripped copy of the YAML, and
     called the real `_shipped_rules()` function (not a reimplementation) → 5 rules,
     assertion fails as required.
   Both confirm the probe is falsifiable per wq#2630 doctrine, matching wq#4464's claimed
   AC evidence.
5. **ISSUE-LOG**: rows 1342 and 1501 (`/home/lumen/kb/ISSUE-LOG.md`) are both marked
   `resolved`, pointing at wq#4464/this fix. Row 1342 was also corrected in place (not a
   new row) per the ISSUE-LOG append doctrine for status flips.
6. **Scope check on "keep vs. drop the langfuse rule"**: wq#4464 chose option (a) (assert
   on shipped YAML) and explicitly left the rule in place, citing no decommission record
   in kb for langfuse. I did not find one either — correct call, not a silent scope cut.
7. Tried to run the ops bundle's own liveness probe, `install.sh --verify`, per the ticket.
   **Blocked by this worker's Bash allowlist** — `Bash(bash scripts/*)` is the only
   `bash *` pattern permitted; `install.sh` lives outside `scripts/`, and running it
   directly (no `bash` prefix) is refused the same way. Not retryable (per CLAUDE.md,
   a refusal is a shape problem, not a timing one) — flagging for the allowlist, not
   burning attempts. Note for the record: reading `install.sh` shows `--verify` doesn't
   even run pytest — it checks deployed-file/unit state against `$SELFHEAL_DEST`
   (`ops/_verify.sh`'s `vfile`/`vunit_file`/`venabled`/`vactive`), a different liveness
   concern from the test-suite fix this ticket is about. The pytest gate only runs in the
   real (non-`--verify`) install path, which is root-gated. So even with the allowlist
   gap closed, `install.sh --verify` would not have exercised wq#4464's fix directly;
   the full-suite pytest run above is the relevant evidence for this ticket.

## Verdict: CONFIRMED

The fix matches its own stated root cause, the diff is minimal and correctly scoped (one
test file, no production code touched), the real suite passes live on lumen (76/1 skip,
matches author's claim), and the acceptance probe is independently reproduced as
falsifiable. No contradiction — no escalation to opus-5@high.

## Allowlist gap to flag

`bash <path>/install.sh --verify` and `<path>/install.sh --verify` (direct exec) are both
refused by `harness/worker-settings.json`'s Bash allowlist for this worker. Only
`Bash(bash scripts/*)` exists; no entry covers ops-bundle `install.sh` scripts. Widening
to something like `Bash(bash */install.sh *)` or `Bash(*/install.sh --verify)` would let
future verify-pass workers run the ops liveness gate the ticket template asks for.
