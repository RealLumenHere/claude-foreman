# wq#2897 — verify: wq#2894's judgment on wq#2892's contradiction of wq#2877

Verifier: claude-sonnet-5@high, worktree `wq2897-a1`, 2026-09-28, fresh instance. No
credentials read or constructed.
Target: PR http://100.121.17.5:3000/lumen/claude-foreman/pulls/1219, merged `144ca0ea67`
(main `fc18813`) — `ops/wq2817-issue-log-pointer-integrity/probe_pointers.py`.

## Verdict — CONFIRM

wq#2894's diff and its report both hold up under an adversarial read and independent
re-execution. No contradiction found.

## What I checked

**Regex correctness, read cold.** `CITE_RE = re.compile(r"\brows?\s+#?(\d+[a-z]?)" + _LIST,
re.I)`. Traced the `\b` anchor by hand against every English word that contains "rows" as a
substring without a space before it (`arrows`, `narrows`, `furrows`, `sorrows`, `burrows`,
`eyebrows`, `throws`) — none has a non-word character immediately before "rows", so `\b`
cannot fire inside any of them; a regex engine's word boundary is a transition between `\w`
and non-`\w`, not a mid-word position. The one shape that *can* false-positive is a
hyphenated compound ("cross-rows 5"): grepped the live corpus for that shape and for
generic non-ISSUE-LOG uses of "row(s) N" (CSV/database/table/spreadsheet/matrix rows,
`returned|affected|processed|batch rows N`) — zero hits. Independently re-ran the probe
against the live `kb/ISSUE-LOG.md`: `pointers checked: 496`, `DANGLING: 0`, and the full
finding set (131) matches `known-findings-baseline.json` exactly (0 new findings via
`alarm_new_findings.py` — `OK (no new findings beyond baseline)`), which is a stronger
false-positive check than eyeballing, since any of the FP shapes above would have shown up
as a *new* finding not present in the pre-widening baseline.

Also traced the comma-continuation ambiguity in `_LIST` (`row 42, 3 workers…` could in
principle swallow an unrelated trailing number as a second citation target) — confirmed via
`git log -p -L` that `_LIST` is unchanged by this diff (inherited from wq#2877) and grepped
the corpus for `row N, N` shapes: both hits are genuine multi-target citations (`rows 131,
142`). Not a regression in this diff; noting it here as a residual design fragility, not a
contradiction.

**Corpus repair claims, spot-checked against the live file** (all confirmed present):
rows 887, 887b, 889 all exist (rows 891/892's "row 889" citation now resolves); row 220 now
reads "row 206 item (b)" instead of the dangling "row 206b"; row 893's `` `(row 9999 ...)` ``
fixture stays inside backticks and is correctly blanked (does not self-dangle). Row 854's
"rows 812/817/923" — the fourth dangling pointer, reported as "baselined, filed as wq#2896"
— has *also* since been rewritten to "rows 812/817" (+787/836) by wq#2896, which is why the
live probe shows 0 DANGLING rather than the 1 the report describes: that's wq#2896's
follow-through, not a discrepancy in wq#2894. `known-findings-baseline.json` itself confirms
this (`captured_by: wq#2896`, `count: 131`, note describes the same edit).

**Tests, re-run from scratch, not trusted from the report:**
- `harness/tests/test_wq2817_issue_log_pointers.py`: 21/21 passed, including
  `test_a_verbless_dangling_citation_is_seen_in_the_live_corpus_at_a_pinned_rev` — the
  out-of-population acceptance probe (wq#2630) pinned at kb rev `70ef27e` — actually executed
  against that rev (not skipped) and passed.
- `harness/tests/test_wq2857_issue_log_alarm.py`: 6/6 passed.
- `alarm_new_findings.py` against the live `kb/ISSUE-LOG.md`: exit 0, "no new findings beyond
  baseline."

## Bundle without a --verify handler — resolved

`ops/wq2817-issue-log-pointer-integrity/` ships no `install.sh`, so per
`ops/VERIFY_CONTRACT.md` a repo-only check of its files would not count toward confirming.
Read `ops/wq2857-issue-log-hygiene-alarm/install.sh`'s own comment: this bundle's
`probe_pointers.py`/`alarm_new_findings.py` are invoked **in place** from the primary tree by
`issue-log-hygiene.service`'s `ExecStart` (confirmed live via `systemctl cat
issue-log-hygiene.service`), never copied anywhere — "the units ARE the liveness fact and
there is nothing else to byte-match." A `vfile`-style handler comparing this bundle's script
to itself would be tautological — exactly the vacuous "repo-only check" the contract
excludes, since it cannot fail from on-box drift.

Added `ops/wq2817-issue-log-pointer-integrity/install.sh` with a `--verify` that checks the
two facts that *can* actually drift on the box: the deployed `issue-log-hygiene.service`'s
`ExecStart` still names this bundle's `alarm_new_findings.py` (`vcontains`), and
`issue-log-hygiene.timer` is active (`vactive`). Confirmed by hand (my sandbox can read
`/etc/systemd/system/*` only through `systemctl`, not directly, so I exercised the two
`_verify.sh` primitives' underlying checks rather than the script itself):

```
$ systemctl cat issue-log-hygiene.service | grep -E "wq2817-issue-log-pointer-integrity/alarm_new_findings\.py"
ExecStart=/bin/bash -c 'set -o pipefail; /usr/bin/python3 /home/lumen/foreman/ops/wq2817-issue-log-pointer-integrity/alarm_new_findings.py | tee /home/lumen/logs/.issue-log-hygiene.laststate'
$ systemctl is-active issue-log-hygiene.timer
active
```

Both facts hold, so this bundle's `install.sh --verify` would report `VERIFY OK` if run with
the permissions `ops-deploy-drift` runs it under.

## Notes

- wq#2894's own ticket already carries `Liveness: ops_verified(wq2857-issue-log-hygiene-alarm)`
  — consistent with the design above; the new handler makes wq#2817's own liveness fact
  independently checkable rather than only inferable through wq#2857's.
- Did not re-implement or modify `probe_pointers.py`, `alarm_new_findings.py`, or
  `known-findings-baseline.json`.
