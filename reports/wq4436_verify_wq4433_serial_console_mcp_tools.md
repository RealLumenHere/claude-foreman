# wq#4436 — verify-pass on wq#4433's serial-console MCP tools (PR #2320, commit `665b1a41`) — CONFIRM

Author: claude-sonnet-5. Verifier: claude-sonnet-5@high (fresh instance, no prior context on
wq#4433 beyond the ticket text and the shipped diff). Prior attempt (wq4436-a1) stalled in a
permission loop trying to read `/home/lumen/projects/limpet/src` instead of this worktree;
that path never existed inside the diff under review and is not touched here.

## What wq#4433 shipped

`plugins/limpet-plugin-remote-cli/mcp/server.py` (+150/-9), `limpet-plugin.toml` (+21/-4),
`README.md` (+30/-3), `tests/test_server.py` (+122/-5), one new fixture
`tests/fixtures/serial_console_cli.json` (+8). Three new MCP tools wrapping the
`serial-console` CLI (lumen-bin, not part of this diff): `serial_console_open` (energizing),
`serial_console_catch` (energizing), `serial_console_expect` (read-only) — same stdlib-only,
shell-out pattern as the existing `send`/`start`/`snapshot`/etc. tools.

## Independent re-derivation (cold, not trusting the diff)

**Code reading.** Read `server.py` in full (not just the diff) and the unmodified
`/home/lumen/bin/serial-console` bash script it wraps, line by line:
- `tool_serial_console_open` (server.py:146-168): rejects `session`/`baud` given without
  `dev` (CLI is positional — `open [dev] [name] [baud]`), matching the CLI's own comment.
- `tool_serial_console_catch` (server.py:171-186) and `tool_serial_console_expect`
  (server.py:189-198): both map CLI exit code 2 to `timed_out`, matching `tool_wait`'s
  existing, already-proven convention.
- `limpet-plugin.toml`'s `[mcp.tools_energize]` additions (`serial_console_open = true`,
  `serial_console_catch = true`, `serial_console_expect = false`) match `TOOLS` dict's
  descriptions, the docstring's classification comment, and the README table exactly — no
  three-way drift between manifest/code/docs.
- `test_manifest_energize_classification_matches_the_tools` (pre-existing, not touched by
  this diff) parses the live toml and asserts it against `READ_ONLY_TOOLS`/`ENERGIZING_TOOLS`,
  so this isn't just me eyeballing agreement — a real test enforces it.

**Ran the shipped suite fresh**, not trusted from the PR notes:
```
$ python3 -m pytest tests/test_server.py -q
...................................                                      [100%]
35 passed in 2.45s
```
Verified with `-v` that nothing was skipped — `TMUX`, `ST_BIN`, and `SC_BIN` were all
resolvable in this sandbox, so `test_fixture_matches_real_serial_console_cli_help` and the
live-integration class actually ran against the real binaries, not short-circuited.

**Went past the shipped suite's own mocks.** The argv-mapping tests for
`serial_console_catch`/`serial_console_expect` only ever call a monkeypatched
`_run_serial_console` — they prove the wrapper *builds the right argv*, not that the real
CLI+tmux round-trip behaves as claimed. Built an isolated-socket live rig (same pattern as
the suite's own `LiveSharedTerminal`, never the fleet's `shared` tmux socket) and called the
actual `tool_serial_console_catch`/`tool_serial_console_expect` functions — not mocks —
against a real `shared-terminal` session:
- `tool_serial_console_catch('probe2', 'CAUGHT_IT', secs=6)` against a session that echoes
  `CAUGHT_IT` after a 2s sleep → `{'ok': True, 'exit_code': 0, ...,
  'info': '...matched /CAUGHT_IT/'}`. Real key-spam-until-match works end to end.
- `tool_serial_console_expect('probe2', 'NEVER_XYZ', timeout=2)` → `{'ok': False,
  'exit_code': 2, 'error': 'timeout waiting for /NEVER_XYZ/', 'timed_out': True}`. Real
  timeout path, correctly surfaced.
- Raw CLI probe (bypassing the Python wrapper) additionally confirmed `expect` exit 0 on
  match and exit 2 on timeout directly, corroborating the wrapper's exit-code mapping isn't
  coincidental.

## Found, not load-bearing

`tool_serial_console_open`'s guard ("reject that combination instead of guessing", its own
docstring) checks `dev is None`, but the arg-building step below it gates on `if dev:`
(truthiness). `dev=""` passes the `is None` check (it isn't `None`) but then fails the
truthiness check, so `session`/`baud` are silently dropped:
```
>>> server.tool_serial_console_open(dev='', session='my-session', baud=9600)
{'ok': True, 'exit_code': 0, 'output': ''}   # CLI called with just ['open'] — autodetect
```
Confirmed live (monkeypatched `_run_serial_console`, calls recorded as `[['open']]`). Not
load-bearing: it requires actively passing `dev=""` rather than omitting the key, and the
failure mode is a confusingly autodetect-named session, not a wrong or destructive device
open (the CLI's own one-port-owner refusal still applies). Filed as a low-priority follow-up,
wq#4438, rather than fixed here per this ticket's "do not re-implement, verify" scope.

**Noted, not a contradiction.** wq#4433's own ticket text ends "Then register in
limpet-plugin-remote-cli successor + fleet_tools.yaml" — left undone, openly logged at
`ISSUE-LOG.md` row 1486 (status OPEN) as out of reach for a sandboxed build worker
(`fleet_tools.yaml` lives in `/home/lumen/minerva`, the deployed successor
`shared-terminal-mcp.py` lives in `/home/lumen/projects/limpet/src`, neither is part of the
`claude-foreman` repo or this worker class's `--add-dir` roots). This is the same structural
class of problem as ISSUE-LOG rows 1021 and 304, not something novel to this PR, and the
ticket's own `done` notes disclose it plainly rather than claiming the tools are live for
agents — so nothing in the PR/notes misrepresents what shipped.

## Probe

`python3 -m pytest tests/test_server.py -q` (35/35, no skips) plus the live rig above, run
fresh in this worktree.

## Verdict

**CONFIRM.** The three new tools' CLI argv mapping, exit-code semantics, and
energize-classification wiring (code/manifest/docs/tests, four ways) all hold up under an
adversarial live re-run that goes beyond the shipped suite's own mocks. One minor,
non-load-bearing validation gap found and filed (wq#4438); the cross-repo registration gap
is pre-existing, disclosed, and not a contradiction of any claim in the diff. No escalation,
no judgment trigger.

## Evidence trail (this pass)

- `git show 665b1a41 -- plugins/limpet-plugin-remote-cli/{mcp/server.py,limpet-plugin.toml,README.md,tests/test_server.py}` — full diff read, this session.
- `Read plugins/limpet-plugin-remote-cli/mcp/server.py` (full file, 462 lines) and `/home/lumen/bin/serial-console` (full file, 116 lines) — this session.
- `python3 -m pytest plugins/limpet-plugin-remote-cli/tests/test_server.py -q` and `-v` — 35 passed, 0 skipped, this session.
- Live isolated-socket rig: real `shared-terminal start`/`send`, real `tool_serial_console_catch`/`tool_serial_console_expect` calls, real `serial-console expect` raw-CLI exit codes 0/2 — this session.
- `python3 -c` monkeypatch repro of the `dev=""` validation gap — this session; filed `wq#4438`.
- `grep -n "4433\|serial-console" /home/lumen/kb/ISSUE-LOG.md` → row 1486; cross-checked rows 1021 and 304 for the same structural (cross-repo) pattern — this session.
