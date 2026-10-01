# wq#3298 verify: wq#3222 live aq/ntfy guard (PR#1516, dc2d0ea6)

Verdict: CONFIRM. No contradiction.

Verifier: burn-4 (claude-sonnet-5-5 @high), 2026-10-01. The prior two dispatches ended FALSE_COMPLETE because a worker `find` was denied approval; no verification content was lost.

## Checked
- dc2d0ea6 is an ancestor of origin/main and main.
- Ran harness/tests/test_live_bin_guard.py, test_scratch_grant.py and test_mask_replay.py in one process with `-p no:randomly`: 130 passed, 24 skipped, rc=0 (336s on a load-8 box). test_scratch_grant and test_mask_replay are the files wq#3222 reported failing in the full-suite run.
- Probe (scratch script, calls live `aq --help` only, which is read-only), live_bin_guard.install() then Popen:

| spawn | result |
|---|---|
| ["/home/lumen/bin/aq", ...] | refused |
| ["aq", ...] with PATH containing /home/lumen/bin | refused |
| ["timeout","5","aq", ...] | refused |
| shell=True "aq --help" | refused |
| ["env","X=1","/home/lumen/bin/aq", ...] | refused |
| ["sh","-c","/home/lumen/bin/aq --help"] | NOT refused |
| ["nice","-n","19","/home/lumen/bin/aq", ...] | NOT refused |

## Residual gaps (not blocking, not a contradiction)
- The nice case is the documented first-three-argv-words limit in the module docstring.
- The `sh -c "<abs path> ..."` case is not documented: argv[2] is the whole command string, which never resolves to the live path. Layer 1 (FOREMAN_AQ_BIN/NTFY env pointed at a nonexistent path) still covers every module that reads those vars; only a hardcoded path inside a shell string escapes.
- Suggested follow-up if wanted: in live_target(), under non-shell argv with an `sh|bash -c` prefix, split argv[2] on whitespace and check each word. Low priority.

Full-suite run (~20 min at nice 19) was not repeated; wq#3222 recorded 3594 + 6751 passed.
