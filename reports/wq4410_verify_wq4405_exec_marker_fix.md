# wq#4410 verify-pass of wq#4405 (PR#2301) — CONFIRM

Ticket: wq#4410, archetype 8 verify-pass, sonnet-5@high, fresh instance.
Target: wq#4405's corrective PR http://100.121.17.5:3000/lumen/claude-foreman/pulls/2301
(`65c78eb4`), which is itself the judgment resolving wq#4403's CONTRADICT of
wq#4398's `_exec_output` fix. Scope: `plugins/limpet-plugin-remote-cli/mcp/server.py`,
its tests, and the README.

## What I verified and confirms

1. **Root cause matches the code.** wq#4403's findings (A: multi-line command
   lost all but its last line; B: `stty -echo` returned a stale prompt string
   as `output` with `ok:true`; C: a trailing `#`/`&` broke the marker clause)
   all trace to the same thing: the old `_exec_output` inferred the output's
   *start* boundary from the shell's echo of the typed command, which a shared
   PTY does not guarantee exists or occurs once. Read `mcp/server.py:191-306`
   (`_exec_quote`, `_exec_eval_clause`, `_exec_payload`, `_exec_start_marker`,
   `_exec_last_marker`, `_exec_output`) line by line and traced each marker
   through a worked example by hand before running anything — the START/DONE
   printf format strings (`'%s%s-%s\n' RC START <token>` → `RCSTART-<token>`;
   `'%s%s-%s-%s\n' RC DONE <token> "$?"` → `RCDONE-<token>-<code>`) only ever
   match in printf's own output, never in the echoed argument list (`RC START
   <token>`, with spaces), which is the thing `test_exec_payload_echo_cannot_match_its_own_marker`
   asserts.
2. **In-tree suite passes as claimed.** `python3 -m pytest tests/test_server.py -q`
   from `plugins/limpet-plugin-remote-cli` → `37 passed in 17.64s`, live layer
   included (real `remote-cli serve` over a real PTY, not skipped — confirmed
   `REMOTE_CLI_BIN` resolved to `/home/lumen/bin/remote-cli` and `needs_remote_cli`
   tests actually ran rather than skipping).
3. **Falsifiability claim reproduced, with one explained gap.** Rebuilt the PR's
   probe independently (not reusing its `/tmp/fworker-wq4405/prefix`, which this
   worktree doesn't have permission to read): checked out the pre-fix
   `server.py` at `65c78eb4~1` (`7dda7a04`) into a scratch tree mirroring the
   real repo layout (`plugins/limpet-plugin-remote-cli/{mcp,tests}` +
   `harness/`) under `/tmp/fworker-wq4410/prefix2`, pointed the current test
   file at it, and reran: **13 failed, 25 passed**, one more failure than the
   PR's claimed 12. The extra failure is
   `test_exec_nonce_names_do_not_trip_the_kv_secret_mask`, and it is a sandbox
   artifact, not a real finding: that test `import`s `harness/masking.py`,
   which itself imports `harness/live_code.py`; I'd only copied `masking.py`
   into the scratch tree, so the test fails on `ModuleNotFoundError` before it
   reaches any assertion about the fix under test. That test is about wq#4409
   (the `token` parameter's name vs. the kv-secret mask), unrelated to the
   START-marker logic — I confirmed by grepping the pre-fix file directly
   (`/tmp/fworker-wq4410/prefix2/.../mcp/server.py`) that it already used
   `token` as the parameter name throughout, identical to post-fix, so this
   test's substantive check would pass either way with full dependencies
   present. The other 12 failures line up exactly with the PR's list:
   `test_exec_payload_echo_cannot_match_its_own_marker`,
   `test_exec_output_carves_command_output_from_scrollback`,
   `test_exec_output_keeps_every_line_of_a_multiline_command`,
   `test_exec_payload_is_a_single_typed_line_whatever_the_command`,
   `test_exec_output_boundary_is_the_last_marker_not_a_printed_one`,
   `test_exec_marker_survives_a_trailing_comment_or_ampersand`,
   `test_exec_output_warns_rather_than_guessing_when_the_start_marker_aged_out`,
   `test_exec_reports_exit_code_once_the_marker_lands`, and the four live
   probes: `test_live_exec_multiline_keeps_every_line_of_output` (finding A),
   `test_live_exec_with_local_echo_disabled` (finding B),
   `test_live_exec_survives_a_trailing_comment` and
   `test_live_exec_survives_a_backgrounded_command` (finding C) — all four live
   probes failed against the pre-fix binary exactly as the PR claims, and all
   pass post-fix.
4. **One extra adversarial probe outside the shipped suite's population**: a
   single-line command with an unbalanced quote (`echo 'unbalanced`, no closing
   `'`), which exercises the documented-but-untested "a syntax error is
   reported against `eval`'s line" limit. Ran `_exec_payload` through a real
   `/bin/bash -c` directly:
   ```
   stdout: 'RCSTART-deadbeefcafe1\nRCDONE-deadbeefcafe1-2\n'
   stderr: "/bin/bash: eval: line 1: unexpected EOF while looking for matching `''"
   ```
   Both markers still print (status 2, the shell's syntax-error code), `output`
   is correctly empty, and the DONE marker still lands because `;` (not `&&`)
   separates the three clauses — `tool_exec` would return `ok: True,
   exit_code: 2, output: ""` rather than hanging to a timeout. Matches the
   module's own documented contract and is not a silent-wrong-data case.
5. **Design choices hold up under adversarial reading**: `_exec_eval_clause`'s
   multi-line reassembly (`eval "$(printf '%s\n' 'l1' 'l2')"`) keeps the typed
   payload to exactly one physical line regardless of command content
   (`test_exec_payload_is_a_single_typed_line_whatever_the_command` asserts
   `payload.count("\n") == 1`); `_exec_quote`'s POSIX single-quote escaping is
   exercised against a real shell with embedded `'` and `"` characters, not
   just asserted on the Python string; `_exec_last_marker` is used for both
   `exit_code` and the output's end boundary, so they can't disagree; the
   `warning` field is the honest-failure path when the START marker aged out of
   `SCROLLBACK_CAP` (`remote-cli:35`, confirmed `2_000_000` bytes, oldest
   trimmed first), rather than silently returning truncated output.
6. **No other caller depends on the touched internals.** Grepped the repo for
   `_exec_output`/`_exec_payload`/`_exec_marker` and for `import server` outside
   this plugin — the hits in `prompt-forge`, `ops/wq4031-kiosk-chat-ui`, and
   `limpet-plugin-flashrom` are unrelated `server.py` modules in other
   directories, not this one.

## Verdict

**CONFIRM.** wq#4405's diagnosis and fix are correct: the redesign eliminates
the echo-dependent boundary inference that caused wq#4403's three findings,
the new unit and live tests are genuine (falsifiable against the pre-fix file,
independently reproduced here down to the individual failing test names), and
an out-of-population probe I ran myself (unbalanced-quote syntax error) behaves
per the documented contract rather than hanging or returning silently-wrong
data. No re-implementation performed, per ticket instructions.

## Evidence

- `python3 -m pytest plugins/limpet-plugin-remote-cli/tests/test_server.py -q`
  → `37 passed in 17.64s` (post-fix, this worktree).
- Pre-fix reproduction at `/tmp/fworker-wq4410/prefix2` (scratch, built from
  `git show 7dda7a04:plugins/limpet-plugin-remote-cli/mcp/server.py`):
  `13 failed, 25 passed`; 12 of the 13 match the PR's claimed failures
  verbatim, the 13th is a missing-transitive-import artifact of the scratch
  tree (explained above), not a defect in wq#4405.
- `grep -n "SCROLLBACK_CAP" /home/lumen/bin/remote-cli` → `2_000_000` bytes,
  confirming the `warning` field's premise.
- Unbalanced-quote probe (item 4 above), run against `_exec_payload` through a
  real `/bin/bash -c`.

## Files

- `plugins/limpet-plugin-remote-cli/mcp/server.py`,
  `plugins/limpet-plugin-remote-cli/tests/test_server.py`,
  `plugins/limpet-plugin-remote-cli/README.md` (all read in full).
- `/home/lumen/kb/ISSUE-LOG.md` row 1476 (matches the PR's description).
