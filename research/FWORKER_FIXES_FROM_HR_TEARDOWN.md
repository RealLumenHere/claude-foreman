FLEET WORKER-LAYER FIXES from de-spaghettifier Phase-0 Section E (fworker.py bugs found while studying HR).
These are CURRENT-FLEET fixes, separate from de-spaghettifier. Ranked by severity.

--- HIGH (correctness/data): worker cancel doesn't kill the process group ---
E.1: FW:1299-1304 SIGTERMs the CLI pid only, though start_new_session=True (FW:1201) makes a group
kill available. A Bash-tool child (build/sleep/server) survives the CLI's death, holds the stdout
pipe, keeps mutating the worktree AFTER fworker recorded KILLED. Fix = HR's _kill_proc_tree
(HR:4116-4127) at every terminate site (run, fire-mode FW:1414-1425, resume). This is the worst one:
a "killed" worker can still be running and corrupting a worktree.

--- HIGH (misclassification, retroactive): API-error text scored as success ---
E.2: classifier.py lacks HR:903-925's scrub. A CLI "API Error:" retry line rendered as assistant
text satisfies "produced output" -> FALSE_COMPLETE (112 of 1306 token attempts). ACTION: re-score
those 112 against ^\s*\[?API Error: on their last assistant block before treating them as model
failures. Some "false completes" may be API errors, not bad work.

--- MEDIUM (metric blind spot): LIMIT classifier under-reads the wall ---
E.3: api_retries=0 across all 1306 attempts is implausible given 114 LIMIT runs. Either the 2.1.268
CLI stopped emitting the api_retry frame shape classifier.py:199-204 matches, or it's mismatched.
30-sec check: grep recent _runs/wq*/ stream captures for "type": "api_retry". If present+uncounted,
the LIMIT classifier catches only the terminal case.

--- MEDIUM (evidence gate): HEAD-cursor makes committed changes invisible ---
E.4: FW:668-684 uses git diff against HEAD to decide "worktree changed." Anything that commits
(worker, lumen-pr commit) before the gate reads it makes the change invisible. HR fixed this
2026-08-25 with refs/hr/collected (HR:4862-4950). Adopt refs/foreman/collected advanced by the gate,
not by whoever commits. This is a likely contributor to false-complete tickets.

--- MEDIUM (idempotency): double-spawn not impossible-by-construction ---
E.5: dispatcher spawn path has concurrency.py ceiling + spawner.py stagger (wq#616) but no per-ticket
reservation. HR dedups retried /turn on idempotency_key (HR:5046-5064) + per-session lease fence.
Class fix for any "two fworkers on one ticket" episode. (Relates to the wq#1543 re-spawn issue.)

--- LOW (hardening, matters more for de-spaghettifier's long-lived gateway) ---
E.6: reaper.py worktree TTL should be mtime-driven (HR:127-160) so a reaped worktree can never be a
live one - no bookkeeping, mtime IS the record, a running turn keeps it fresh.
E.7: fd hygiene - HR:4071-4090 closes pipes explicitly (2-3 fd leak/turn). Moot for one-process-per-
attempt fworker; REAL the moment de-spaghettifier's gateway is long-lived. Build in from day one there.
E.8: resume-silently-starts-fresh - FW:2237-2260,2482-2497 checks agent liveness but not transcript
presence in ~/.claude/projects/<cwd>. HR:244-254 _resume_lost reports it. Cheap add; prevents a
resumed ticket that forgot everything being scored as a continuation.
