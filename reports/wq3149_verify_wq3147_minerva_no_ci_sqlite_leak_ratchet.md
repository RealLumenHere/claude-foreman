# wq#3149 — verify wq#3147 [minerva] no CI: sqlite leak ratchet (and all minerva tests)

Archetype 8 adversarial verify pass. Author: claude-opus-5-5. Verifier: claude-sonnet-5@high (fresh instance).

PR: http://100.121.17.5:3000/lumen/claude-foreman/pulls/1421 (branch
`fix/wq3147-minerva-no-ci-sqlite-leak-ratchet-and-al-a1`, tip `6841eb34`; already
squash-merged to foreman `main` as `8a94d713` — confirmed `git diff 8a94d713 6841eb34 --
ops/wq3147-minerva-guard` is empty, so the merged tree is byte-identical to the PR tip).

## Claim under test

minerva has no CI/Gitea runner and `lumen-pr land` merges on review alone, so the wq#2900
sqlite leak ratchet (and every other minerva test) only guarded when someone ran it by
hand — it sat red on main for ~18h (wq#3146) with nothing alerting. wq#3147 adds
`ops/wq3147-minerva-guard/`: a `minerva_guard.py` that exports a clean tree of a pinned
git rev via `git archive` (never the live checkout, so uncommitted edits can't mask or
fake a result), runs minerva's full tracked pytest suite against it, and pages (alert
queue + ntfy) on red or on its own breakage, installed as a `lumen-minerva-guard.timer`
running every 6h via a documented `install.sh`.

## What I checked

- Read `minerva_guard.py` (272 lines), `tests/test_minerva_guard.py` (157 lines),
  `README.md`, `install.sh`, and the `.service`/`.timer` units in full, cold.
- Confirmed the PR touches only `ops/wq3147-minerva-guard/**` — no changes to minerva
  itself, no ratchet/baseline file, no scope creep.
- Ran the shipped test suite as-is:
  `python3 -m pytest -v ops/wq3147-minerva-guard/tests` → **6 passed**, including
  `test_real_ratchet_goes_red_on_unguarded_connect`, which was **not skipped** (needs
  `/home/lumen/minerva/.git`, which exists) — this is the ticket's mandatory acceptance
  probe: minerva pinned at `864fe16` (the wq#3146 fix commit, ratchet green) plus one
  freshly committed unguarded `sqlite3.connect` on a throwaway branch comes back
  `check` exit 1, naming both
  `test_sqlite_leak_ratchet.py::test_no_new_unguarded_sqlite_connections` and the
  injected `wq3147_leak_probe.py::leaky()` in stdout; `864fe16` alone comes back exit 0.
  The probe is drawn from outside the fixture population (a real clone of the real
  target repo, not the synthetic throwaway repos the other 5 tests build) and can
  provably fail either way, satisfying the wq#2630 falsifiability requirement.
- **Adversarial probe of an untested transition**: the suite covers red→red (no
  re-page), red→broken→red (broken resolves, red not re-paged), and green after a
  first-ever red, but not broken→green while a red row is still open. Drove
  `minerva_guard.report()` directly through red → broken → green with `aq_ingest` /
  `aq_resolve` / `ntfy` monkeypatched: broken row resolves ("guard ran again"), red row
  *also* resolves ("green at ..."), exactly one RECOVERED ntfy fires, and final state
  has both `red_aq_id` and `broken_aq_id` null. No double-page, no stuck alert. Correct.
- Checked the tar-extraction path (`export_tree`): uses `tf.extractall(dest,
  filter="data")` (Python 3.12+ safe extraction), so a malicious/odd rev can't write
  outside the temp dir via symlinks or absolute paths.
- Checked cross-referenced paths actually exist and are runnable: `AQ_DIR`
  (`/home/lumen/agents/alert-queue/aq.py`) and `NTFY_BIN`
  (`/home/lumen/bin/ntfy-send`) both present; `README.md`'s installed-path reference
  (`/home/lumen/services/lumen-minerva-guard/minerva_guard.py`) matches what
  `install.sh` actually writes.
- **Production verification, not just code review**: `lumen-minerva-guard.timer` is
  `active`/`enabled` on this host, `systemctl list-timers` shows a clean next-fire (no
  duplicate/competing minerva timer), the deployed
  `/home/lumen/services/lumen-minerva-guard/minerva_guard.py` is byte-identical to the
  repo source (line count and tail matched via `Read`), and the guard has already run
  for real: `journalctl -u lumen-minerva-guard.service` shows a clean 17:16:51Z run
  (`minerva-guard: GREEN origin/main 864fe161bb59`, 11.4s wall clock, no errors), and
  `/home/lumen/logs/.lumen-minerva-guard.state.json` shows `"verdict": "green"`,
  `"sha": "864fe161bb594792351280220b41e8ca1e8b5f28"` — the guard evaluated the real
  minerva `origin/main` and correctly found it green. This is the exact failure this
  ticket exists to prevent, observed working end-to-end rather than inferred from tests.
- Confirmed `ops/_verify.sh` (the repo's deploy-verify contract used by `install.sh
  --verify`) exists and `install.sh` follows the same `vfile`/`vunit_file`/`vactive`/
  `venabled`/`vdone` convention as other `ops/*` units in this repo — consistent with
  house pattern, not a one-off.

## Verdict

**CONFIRMED.** The guard is correctly scoped (ops-only diff, no minerva/ratchet
changes), its acceptance probe is genuinely falsifiable and passes both directions, an
untested state transition (broken→green with an open red row) resolves correctly under
direct probing, tar extraction is safe, and — beyond the code review — the guard is
already installed, enabled, and has produced one real clean run against minerva's
actual `origin/main`, correctly reporting green at the known-good commit. No
contradiction found; not escalating to judgment.
