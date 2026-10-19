# wq#4405 judgment on wq#4403's CONTRADICT of wq#4398 (PR#2295)

**Verdict: UPHELD. wq#4403 is right, and the defect warrants a corrective PR
(this one).** wq#4397's two original findings stay fixed; the new finding is in
wq#4398's own extraction code and is a silent-wrong-data bug, not a disclosed
limit.

## 1. Is it severe enough for another corrective PR?

Yes, on two grounds the "heuristic with real limits" disclaimer does not reach.

**It is wrong, not limited.** Every limit the module discloses fails *loudly*:
a human parked in `vim`, a command that never returns, a timeout — all produce
`timed_out: True` with no output, which a caller can see and handle. Findings A
and B produce `ok: True`, a **correct** `exit_code`, and an `output` that is
plausible and wrong (`'THIRDLINE'` for a three-line command; `'bash-5.3$ '`
after `stty -echo`). For the bench-RE workflow this plugin exists to serve the
output *is* the deliverable — a flash dump's byte count, a `dmesg` line, a
chip ID. A disclaimer cannot cover returning the wrong answer with a
trustworthy-looking envelope.

**One of the two cases is a documented feature, the other is the tool's whole
premise.** `tool_exec`'s own docstring promised that embedded newlines "run as
successive lines"; `output` silently dropped all but the last. And no-echo is
not an exotic state — `stty -echo`, a password prompt, `read -s` are ordinary
residue in exactly the human-attended shared PTY the module docstring cites as
this plugin's reason to exist. A disclaimer that excused those two would
excuse the tool.

Finding C (a trailing `#` comment swallows the appended marker clause) I rate
as real too but lower-cost: it fails as a timeout, so it is in the class the
disclaimer *does* cover. It is fixed here because the fix for A/B fixes it for
free (and with it `cmd &`, which `cmd &; __rc_status=$?` made a bash syntax
error).

## 2. How `_exec_output` is redesigned

wq#4403's suggestion was the right direction; the root cause is narrower than
"assumes the token appears twice" — it is that **the boundary was inferred from
the shell's echo of the typed line at all**. A shared PTY guarantees nothing
about that echo: it may not exist, and there may be one per line.

Prior art is unambiguous here. Terminal emulators with shell integration
(iTerm2, kitty, WezTerm, Ghostty, Neovim's `:terminal`) do not parse the
echoed command line either; the shell emits OSC 133 `;C` "just before start of
command output" and `;D` with the exit status after it, and the emulator reads
between them. Three changes, same shape:

1. **A START marker.** `_exec_payload` now types
   `printf '%s%s-%s\n' RC START <token>; <command>; printf '%s%s-%s-%s\n' RC
   DONE <token> "$?"`. The start boundary is a marker the shell *prints*, so
   it is in the stream whether or not input is echoed, and everything the
   shell echoed plus the prompt is upstream of it. Same split-argument trick
   as the completion marker, so neither marker can be matched by the echo of
   the line that creates it (`test_exec_payload_echo_cannot_match_its_own_marker`).
2. **End on the marker's LAST occurrence** (`_exec_last_marker`): printf runs
   after the command, so anything marker-shaped in the command's own output is
   necessarily earlier. Used for `exit_code` too, so status and boundary agree.
3. **One typed line, always** (`_exec_eval_clause`): the command is handed to
   `eval`. This is what makes the bracket hold for a multi-line command —
   typed raw, lines 2..n each get their own echo and PS2/prompt *interleaved
   with the output*, and no extraction can separate them afterwards. Under
   `eval` the shell reads one line, so the region between the markers is
   nothing but real output, in both echo states.

   A gotcha worth recording: quoting is not enough. `eval 'l1\nl2'` still
   contains a newline the PTY's line discipline acts on, so it arrives as two
   typed lines. A multi-line command is therefore reassembled shell-side with
   `eval "$(printf '%s\n' 'l1' 'l2')"` — POSIX builtin, fixed format string,
   one quoted argument per line, zero newlines in the typed text.

4. **Honest failure instead of a guess** when the START marker is no longer in
   the retained scrollback (a command that overflowed the daemon's
   `SCROLLBACK_CAP`): `tool_exec` adds a `warning` field saying `output` may
   include earlier terminal text or be missing the command's first lines.

No scratch variable is left in the shared session any more either (`"$?"` is
expanded in the same command list); the first cut leaked `__rc_status`.

## 3. Should multi-line support be dropped?

No — keep it. The shared-PTY difficulty wq#4403 points at is real but it is a
property of *typing* the lines, not of multi-line commands as such; `eval`
removes it, and multi-line output is now exactly as clean as single-line
output (`test_live_exec_multiline_keeps_every_line_of_output` asserts all
three lines, against the real daemon). Dropping a working documented feature
to avoid a bug that has a principled fix would be the worse trade. The
`eval`-shaped limits that remain are disclosed on `tool_exec` and in the
README: no history expansion, syntax errors reported against `eval`'s line, a
stdin-reading command still blocks on the shared terminal.

## Evidence

- Suite: `37 passed` in-tree (`plugins/limpet-plugin-remote-cli`,
  `python3 -m pytest tests/test_server.py -q`), live layer included — the real
  `remote-cli serve` binary over a real PTY, not skipped.
- Falsifiability, run against the pre-fix file (`git show
  HEAD:…/mcp/server.py` + the new suite, in `/tmp/fworker-wq4405/prefix`):
  **12 failed / 25 passed**. The three live probes reproduce wq#4403's findings
  exactly:
  - A: `('FIRSTLINE', {'exit_code': 0, 'ok': True, 'output': 'THIRDLINE'})`
  - B: `{'command': 'echo hidden-echo-probe', 'exit_code': 0, 'ok': True,
    'output': 'bash-5.3$ '}`
  - C: `{'ok': False, 'timed_out': True, 'error': 'exec timed out after
    20.0s …'}`
- Probes outside the population the wq#4398 fix was designed around
  (single-line, echo-on commands): a no-echo session, a three-line command, a
  trailing comment, a trailing `&`.
- One caveat on the unit layer, stated so the next verifier does not have to
  find it: `test_exec_output_survives_an_echo_suppressed_session` passes
  against the pre-fix `_exec_output` too, because the scrollback it feeds
  contains a START marker the old payload never emitted. The authoritative
  probe for finding B is the live one, which does fail pre-fix.

## Files

- `plugins/limpet-plugin-remote-cli/mcp/server.py` — `_exec_quote`,
  `_exec_eval_clause`, `_exec_payload`, `_exec_start_marker`,
  `_exec_last_marker`, `_exec_output`, `tool_exec`.
- `plugins/limpet-plugin-remote-cli/tests/test_server.py` — 8 new unit cases,
  4 new live cases.
- `plugins/limpet-plugin-remote-cli/README.md` — `exec` section.
- `/home/lumen/kb/ISSUE-LOG.md` row 1476.
