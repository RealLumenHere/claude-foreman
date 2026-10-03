# wq#3434 — Adversarial verify-pass of wq#3433 (PR#1602: pilot 132cba7d6594 report, ASIRF-style domain-conditional redaction KB)

**Verdict: CONFIRM** — the shipped diff is a 55-line, report-only addition
(`pilots/132cba7d6594/REPORT.md`) that correctly invokes the card's second kill
criterion ("baseline does not reproduce -> stop, report the baseline defect. Do not
proceed."). Every factual claim in the report checks out against independent evidence;
no treatment was run and none should have been; nothing was wired into prod.

## What was shipped

Branch `fix/wq3433-pilot-add-asirf-style-domain-conditional-a1`, commit `82753790`
(merged to `main` as `bc141f46`): `pilots/132cba7d6594/REPORT.md`, +55/-0, one file.
`git diff main...fix/wq3433-pilot-add-asirf-style-domain-conditional-a1 --stat` confirms
no other file changed — `CARD.md` (the generator-authored spec) is untouched by this PR,
so the report cannot have been written to fit a doctored card.

## Probe

Could not re-run the card's own Baseline commands on `hathor` — this verify attempt's
grant (`_runs/wq3434/grant.json`) is `ssh(anubis, run)`, not `ssh(hathor, run)`; the prior
attempt (classified PERMISSION_LOOP) burned itself retrying `fleet-exec run hathor ...`
against that same gap. Per that attempt's own critique, this is a hard stop, not a retry
target — so I verified the report's claims through evidence that doesn't require the
hathor grant:

1. **The four "existing sweeps" really don't take `--input/--manifest/--out`.**
   `systemctl cat` on `lumen-read-dir-sweep(.service)`, `-messenger`, `lumen-mask-at-rest`,
   `lumen-pat-scan` shows each is a oneshot calling, respectively,
   `harness/read_dir_sweep.py --write-runtime`, `harness/read_dir_sweep_messenger.py`
   (no flags), `harness/mask_at_rest.py --store all`, `bin/wq523_pat_residue.py --alert`.
   `grep -c -- "--input\|--manifest\|--out"` over all four scripts returns 0 for each.
   Matches REPORT.md's table exactly.
2. **No standalone `lumen-read-dir-sweep`/`lumen-mask-at-rest`/`lumen-pat-scan` CLI
   exists** — `/home/lumen/bin/lumen-read-dir-sweep*`, `*-mask-at-rest*`,
   `*-pat-scan*` all return "No such file or directory". These names only exist as
   systemd unit names, confirming the card's Baseline section invoked commands that
   were never real CLIs.
3. **The card's named inputs are fabricated, not just missing on one host.**
   `grep -rl` across `/home/lumen/foreman` (excluding `_runs/` and the pilot's own
   dir) for `benign_spans`, and separately for `union_spans|kb_prefilter|diff_spans|
   count_fp|summarize_pilot`, returns zero hits. These paths/tools are referenced
   nowhere else in the codebase — they were invented by the card generator, not
   real artifacts that merely live on a different host than hathor.
4. **`pilot_spec_author.py lint` reproduces exactly what REPORT.md claims**: running
   `python3 harness/pilot_spec_author.py lint /home/lumen/foreman
   pilots/132cba7d6594/CARD.md` exits 1 and flags every Baseline/Treatment path
   (`/var/log/lumen/transcripts`, `/tmp/lumen_pilot_sample.sha256`,
   `/opt/lumen/tools/*.py`, `/var/lib/lumen/sensitive_defs.db`, etc.) as unresolved.
   `python3 -m pytest harness/tests/test_pilot_spec_author.py -q` → 21/21 pass, so the
   lint tool itself is sound, not a stub.
5. **Independent contemporaneous record**: ISSUE-LOG row 1171 (appended during the
   original wq3433-a1 run, under `issue_log_append.py`'s lock, separate from
   REPORT.md) states the same finding — "none exist on hathor (fleet-exec ls: No
   such file)" — and classes it with rows 1039/1120, two unrelated prior pilots that
   hit the identical generator defect (phantom Baseline/Treatment paths). Three
   independent tickets hitting the same failure mode is strong corroboration this is
   a real, recurring generator defect and not a one-off misread by this runner.
6. **Doctrine compliance**: `research/DEFER_PILOT_LANE.md` §3.1a states the five
   spec sections (Hypothesis/Primary metric/Fixed sample/Baseline/Treatment) are
   generator-owned and "a runner still never [fills the card in itself]" — matches
   REPORT.md's "The card forbids writing that plan in a run, so I did not."
7. **Report-format contract**: `harness/output_contract.py` parses a line-anchored
   `RECOMMENDATION: <ADOPT|WATCH|SKIP>` token, last line wins. REPORT.md line 54 is
   `RECOMMENDATION: WATCH`, correctly formatted and is the last such line.
8. **No prod wiring**: the card's "Scope limit (standing)" forbids a pilot from
   wiring anything into prod; the diff is purely a report file, consistent.
9. **Merge check**: `git branch --contains bc141f46` on `main` confirms the report
   commit is actually merged, matching the author's closing summary.

## Adversarial angles considered and ruled out

- *Could the paths just live on Lumen instead of hathor, meaning the runner picked
  the wrong host?* No — the card pins `Node: hathor` explicitly, and runners are
  doctrinally forbidden from substituting a host the card didn't name (would make
  the card's node field unenforceable, per DEFER_PILOT_LANE.md). Even if the paths
  existed on Lumen, the card's Baseline, as specified, still fails to reproduce on
  the node it mandates — the reported kill criterion stands either way.
- *Did a credential leak into the report?* `_runs/wq3433/mask-hits-1.json` shows one
  `kv-secret` hit (prefix `master key: `) suppressed during the run; REPORT.md
  contains no such string — the masking layer did its job and nothing reached the
  shipped artifact.
- *Is the "same defect as 1039/1120" claim padding?* Checked both rows directly;
  both independently describe generator-invented Baseline/Treatment paths that
  `lint_card()` would have caught pre-dispatch — the analogy holds.

## Conclusion

No contradiction found. The report's "WATCH, kill_criterion=baseline_does_not_reproduce"
outcome is correct, evidenced, and doctrine-compliant. No escalation to opus-5@high.
