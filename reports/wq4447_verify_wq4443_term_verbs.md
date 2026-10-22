# wq#4447 verify-pass of wq#4443 (PR claude-foreman#2327, commit 3d03a11a)
Verifier: patch-s-18583b (claude-sonnet-5-5 @high), 2026-10-21 PDT. Cold read of the diff, no re-implementation.

## Verdict: CONFIRM the shipped diff. PARTIAL SCOPE on the ticket as titled (see Gap).

## What was checked and how
1. Tests: `pytest plugins/limpet-plugin-remote-cli/tests/test_server.py` -> 47 passed.
2. Energize gate consistency: server.py marks send, start, serial_console_open, serial_console_catch, term_do, term_at, term_catch as ENERGIZING.
   limpet-plugin.toml [mcp.tools_energize] sets term_do/term_at/term_catch = true. The two sets agree; no energizing verb is ungated.
3. Signature agreement: term_do -> tool_send + tool_snapshot(ansi), term_at -> tool_wait + tool_send, term_catch -> tool_serial_console_catch.
   Every keyword each wrapper passes exists on the callee (inspect.signature).
4. Attack on the race: term_do snapshots immediately after the send. Empirical test in a throwaway shared-terminal session (wq4447verify, killed after):
   6/6 trials showed the command OUTPUT (not just the typed command) in the returned snapshot. No race observed for fast commands.
   Caveat (design, not defect): a slow command will not have finished when term_do snapshots; use term_at/wait for those.
5. Guarded-send safety: term_at with a regex that never matches (timeout 2s) returned ok=False and typed NOTHING (screen checked afterwards).
6. Cleanup: scratch session removed; shared-terminal list empty before and after.

## Gap (author disclosed it; verified it is real and UNTRACKED)
The ticket title is "track in lumen-bin + add MCP verbs". Only the MCP-verbs half shipped. horus:~/bin/re-term (sha 5440810c) was NOT committed to lumen-bin
(ISSUE-LOG row 1490: fleet-exec grant_missing_env, no needs: tag). wq#4443 is done and no open ticket carries the remaining half -> follow-up to be filed (see wq tickets referencing re-term lumen-bin).

## Not verified
Live MCP round trip through the Sentinel energize gate (verified the gate declaration, not a gated end-to-end call).
