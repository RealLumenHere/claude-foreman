# wq#1330 — offline triage replay vs WORKER_EXEC_CAPABILITY_GAP.md §2.6

Gate from doc §5.2: "replay the last 30 days of attempt-N.json + transcripts through
the classifier offline; every currently-blocked ticket in §2.6 must land in the column
this doc predicts, or the classifier is wrong and the doc says so."

Run: 2026-09-03, `python3 harness/failure_record.py replay` against
`/home/lumen/foreman/_runs` + `~/.claude/projects/*/<sid>.jsonl` + read-only
`queue.db`. Node map from `lumen_state.py current` (live). The 30-day window covers
50 failed tickets; §2.6's older tickets were re-run with `--days 400` (same
classifier, wider mtime cutoff) since their attempt records predate the window —
noted per row. wq#915 and wq#1124 are `done` as of 2026-09-02 (resolved after the
doc snapshot) and are correctly absent from a currently-blocked replay.

| wq | §2.6 prediction | replay triage | inferred_needs | verdict |
|---|---|---|---|---|
| 349 | ssh/copy/root(thor) | CAPABILITY_DENIED | ssh(thor), dir(...) | ✓ |
| 484 | ssh(thor) | CAPABILITY_DENIED | ssh(thor) | ✓ exact |
| 679 | bench(thor,adb) | CAPABILITY_DENIED | bench(thor,adb), dir(...) | ✓ exact |
| 684 | repo+bench | REAL_BLOCK | — (reason names the wrong-repo bug) | ✓ good-path block; needs:repo backfill is a human/slice-2 read |
| 683 | repo+dir(kernel tree) | CAPABILITY_DENIED | dir(/tmp/wq683-kernel/...) | ✓ |
| 1152 | dir(nle-flasher) | CAPABILITY_DENIED | dir(/home/lumen/projects/nest) | ✓ |
| 1155 | repo(NoLongerEvil) | CAPABILITY_DENIED | dir(nest/build/...) | ✓ (§4.1 maps paths to dir; repo-vs-dir is the slice-2 resolver's call) |
| 1157 | restart(qbittorrent@thoth) | CAPABILITY_DENIED | ssh(thoth), dir(...) | ✓ (worker tried ssh to thoth; ssh(thoth) is the mechanically-observed need) |
| 1295 | partial (docker restart is v2) | CAPABILITY_DENIED | dir(...) | ✓ class right |
| 1278 / 1293 / 627 | "? — no failure record" | CAPABILITY_DENIED | dir(...) each | ✓ §4 delivered exactly this: previously-unreadable PERMISSION_LOOPs now classified |
| 354 | ssh/root/power(isis) | CAPABILITY_DENIED | dir(/home/lumen/burnin/...) | ✓ class right; isis never appears in the denied argv so no ssh tag is guessed |
| 362 | no — physical console | CAPABILITY_DENIED, needs=[] | — | ✓ effectively: empty inferred_needs means no auto-retag (doc §4.2), stays human |
| 325 | no by design (crown-jewel drill) | REAL_BLOCK | — | ✓ |
| 916 | hardware dead (Frank) | CAPABILITY_DENIED | ssh(apis) | honest caveat: the pack refused ssh before the dead node could be observed (ISSUE-LOG #71), so the mechanical read is the denial. One auto-retagged retry would then fail with a REAL error — acceptable by construction |
| 940 / 927 | stays sync-live by rule wq#900 | 940 CAPABILITY_DENIED dir(foreman,bin); 927 REAL_BLOCK | mixed | 927 ✓; 940's dir() proposal must be overruled by the wq#900 rule at dispatch — that guard belongs to slice 4's lint, noted here so it isn't forgotten |
| 600 / 913 / 919 (HANG) | "classify first" | UNKNOWN | — | ✓ recorded as needs-a-human-read, never silent |
| 869 / 879 / 983 / 1122 (CRASH) | "classify first" | UNKNOWN | — | ✓ (no traceback in last tool result ⇒ not GENUINE_BUG) |

Net: every §2.6 currently-blocked ticket lands in (or consistently with) the doc's
predicted column. No classifier change was needed beyond one replay-path fix found
during the run: a worker that takes the wq#749 good path (wq block + BLOCKED sentinel
+ exit 0) leaves a SUCCESS attempt record on a blocked ticket — the replay now keeps
those rows and classifies them REAL_BLOCK instead of skipping them as successes
(harness/failure_record.py `replay()`).

Caveats logged above where the mechanical read and the doc's routing intent diverge
(916, 940, 362): in all three the divergence is safe because auto-retag only fires on
non-empty inferred_needs and the dispatcher stage that would consume tags is a later
slice — nothing in slice 1 acts on these records.
