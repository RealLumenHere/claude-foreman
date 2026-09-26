# wq#2602 — verify of wq#2577 (claude-foreman PR#977, merged 0389dc6)

Verifier: claude-sonnet-5@high, fresh instance (same model as author). Verdict:
**CONFIRM** — no contradiction, no escalation.

wq#2577's title claims two gaps: (1) audit workers can't read the systemd journal,
(2) audit workers have no `--add-dir` access to `/home/lumen/agent`, `/home/lumen/logs`,
`/home/lumen/minerva`. The shipped diff (`harness/agents.yaml`, `harness/guard.sh`,
`harness/primary_tree_guard.py`) fixes only (2) and explicitly declines to re-fix (1),
on the documented grounds that (1) was already resolved out-of-band. I independently
reproduced both the "already resolved" claim and the (2) fix's mechanics rather than
trusting the author's own ISSUE-LOG #716 write-up at face value.

## Per claim

| wq#2577 claim | Result | Evidence |
|---|---|---|
| Journal read is broken: `journalctl -u <unit>` returns "-- No entries --", uid not in systemd-journal/adm | CONTRADICTED BY AUTHOR, CONFIRMED BY ME (correctly left unfixed) | Reproduced author's own liveness test verbatim: `journalctl -u lumen-monitor.service -n 1 --no-pager \| grep -q 'No entries'` → exit 1 (real journal lines returned, no "No entries" match). `id` shows `gid=4(adm)` present in the group list. Same result the author recorded in ISSUE-LOG #716. The PR correctly does not touch journal/group config — building a redundant journal-reader would have been unnecessary work against a premise that no longer holds. |
| No `--add-dir` access to `/home/lumen/agent`, `/home/lumen/logs`, `/home/lumen/minerva` for audit-lane workers | CONFIRMED | `harness/agents.yaml`: `rca-investigation` (the archetype `orchestrator/router.py:642,675` routes bare `audit` to) and `research-synthesis` each gained an explicit `add_dirs:` list = `defaults.add_dirs` (7 dirs) + `/home/lumen/agent`, `/home/lumen/logs`, `/home/lumen/minerva`. Traced the wiring end-to-end: `agents_spec.py:70-79` merges archetype entry over defaults (dict.update, so the archetype's full explicit list wins); `fworker.py:2206-2211` `add_dirs_for()` reads `_AGENTS.get(archetype).add_dirs`; `fworker.py:2276-2277` emits one `--add-dir <d>` per entry to the spawned Claude CLI. All three target dirs exist on disk (`/home/lumen/agent`, `/home/lumen/logs`, `/home/lumen/minerva` — verified `test -d`). |
| Fix should write-freeze the two new dirs the same way existing add_dirs are protected | CONFIRMED | `harness/guard.sh` `PRIMARY_TREES` (Bash wall) and `WRITE_PRIMARY_TREES` (Read/Edit/Write/Grep/Glob wall) both gained `:/home/lumen/agent:/home/lumen/logs` (minerva was already present in both from wq#1535 — correctly not duplicated). `harness/primary_tree_guard.py` `STATIC_PRIMARY_TREES` gained the same two entries (minerva already present). `check_tree()` (`primary_tree_guard.py:124-130`) handles a non-git dir by setting `error="not a git repo"` and returning early — same pattern already live for kb/minerva — so adding two more non-git dirs cannot crash the periodic drift-guard timer. |
| No regressions | CONFIRMED | `harness/agents.yaml`, `harness/primary_tree_guard.py` parse clean (`yaml.safe_load` / `ast.parse`). `pytest harness/tests/test_agents_spec.py`: 11 passed, 3 failed — the 3 failures are pre-existing `primary_tuple` drift (wq#2450 model-routing drift) plus a `decomposer` archetype-missing complaint, none touching `add_dirs`; the diff never touches `primary_tuple`, so these are unrelated and were already failing before this PR (author's own claim, reproduced exactly: same 3 test names, same problem list). `pytest harness/tests/test_fworker_grant.py harness/tests/test_fworker_integration.py`: 38 passed, 0 failed. |

## Conclusion

The diff is a minimal, correctly-scoped fix for the filesystem-access half of wq#2577's
claim, and correctly recognizes (rather than redundantly re-solving) that the journal-read
half was already fixed out-of-band. Wiring verified end-to-end from `agents.yaml` through
to the spawned CLI's `--add-dir` flags and through both write-freeze mechanisms
(`guard.sh`, `primary_tree_guard.py`). No test regressions. CONFIRM.
