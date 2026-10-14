# wq#3821 — judgment: wq#3815 (verify) vs wq#3808 (LIMPET MCP meta-server)

- Ticket: wq#3821, judgment pass, claude-opus-5
- Contradiction under review: wq#3815's CONTRADICT of wq#3808
  (`reports/wq3815_verify_wq3808_hot_reload_and_hang_bugs.md`, ISSUE-LOG row 1286)
- Target: `ops/wq3808-limpet-mcp-meta-server-a1/`

## Ruling in one line

wq#3815 is **upheld on all three bugs** — each reproduces on the pre-fix tree
and each contradicts a documented claim — and all three are **fixed in this
ticket**, with regression tests that fail against the pre-fix code.

## (a) Revision to wq#3808's PR, or follow-up tickets?

Neither, as posed: **wq#3808's PR #1803 is already merged** (`063e0249da`, and
`0d020ff5` carries it on `main`; `wq show 3808` → status `done`). There is no
open PR left to revise, so "land it as a revision" was not available.

Between "one ticket" and "three follow-ups", one: the three bugs sit in the
same ~450 lines (`meta_server.py` / `stdio_mcp_client.py` / `registry.py`),
bugs 1 and 3 touch the *same* block of `ToolRegistry.rebuild()`, and all three
need the same doc edits in `README.md` / `manifest.example.json`. Three
tickets would each pay the full read cost of the same five files to land a
~20-line change, and two of them would collide in `rebuild()`. Fixed here,
in this judgment ticket's PR.

## (b) Fix direction, per bug

**Bug 1 — stale reuse of a changed child.** Confirmed. Fix as wq#3815
proposed: diff what is actually baked into the running process. `rebuild()`
now compares `entry.command` and `entry.env` against the live client
(`_entry_matches_client()`, `meta_server.py`) and stops + replaces it on a
mismatch. Timeouts are deliberately *not* part of that comparison — they are
plain client attributes, so a timeout change is applied to the live client in
place rather than forcing a pointless restart. Also fixed in the same block: a
same-named client that had *died* was dropped without being reaped, leaving a
zombie.

**Bug 2 — SIGHUP is dead code.** Confirmed. Of wq#3815's two options (wire the
Event, or drop it and fix the docs), **wire it**: `_reload_loop()` now does
`self._reload_requested.wait(timeout=self.reload_poll_sec)` instead of
`time.sleep()`, so SIGHUP cuts the wait short. Dropping it would have meant
editing two shipped docs to promise less, to save one line; the feature is
cheap, already documented, and the thing an operator reaches for. The docs are
tightened on one point of precision: SIGHUP forces the *check* to happen now;
if the manifest is unchanged there is nothing to rebuild.

**Bug 3 — one unresponsive child wedges everything.** Confirmed, and agreed
that it is the severe one. Fix direction follows wq#3815's suggestion
(mirror `cli_wrapper.py`'s `timeout_sec`) and the MCP lifecycle spec, where a
request timeout is a SHOULD and a sender that gives up SHOULD send
`notifications/cancelled`; the shape also matches how other MCP clients name
it (e.g. `strands.tools.mcp.mcp_client`'s `startup_timeout`, "timeout after
which MCP server initialization should be cancelled"). Landed as:

- `startup_timeout_sec` (default 10s) and `request_timeout_sec` (default 30s)
  on each `mcp_servers` entry, parsed and validated in `registry.py` — a
  non-positive or non-numeric budget is a `ManifestError`, because a 0s budget
  fails every read and looks exactly like the bug the knob prevents.
- `StdioMcpClient` drains the child's stdout in a reader thread into a queue,
  and `_recv()` takes a deadline. Threaded, not `select()`-based, because the
  child's stdout is a buffered text stream: `select()` can say "not ready"
  while a complete line already sits in the wrapper's buffer.
- A timed-out request sends `notifications/cancelled` and remembers the
  abandoned id, so a late answer is dropped instead of being handed to the
  next request (which would have failed its id check).
- Failure is **isolated, not just bounded**: a timeout raises
  `ChildServerTimeout(ChildServerError)`, which the existing
  `except (ChildServerError, OSError)` in `rebuild()` already turns into "log
  it and skip this entry". Every other child and every `cli_tools` entry is
  still built and served.

Two adjacent hangs/crashes on the same axis were fixed with it, since leaving
them would have left bug 3 only half-closed:
- the old `_recv()` error path called a blocking `stderr.read()`, which never
  returns for a child that closes stdout but holds stderr open (stderr is now
  drained by its own thread);
- a child writing a non-JSON banner line on stdout raised `JSONDecodeError`
  out of `rebuild()` — uncaught, so it killed `MetaServer.__init__` outright.
  It is now a `ChildServerError`, i.e. a skipped entry;
- a `BrokenPipeError` from `_send()` to a dead child escaped
  `handle_request()`, which would have taken `serve_forever()` down on a
  `tools/call`. Now `ChildServerError` → a JSON-RPC error response.

The initial `rebuild()` stays synchronous in `MetaServer.__init__`, so the tool
set is complete and deterministic when `initialize` is answered; worst-case
startup is now bounded by the sum of enabled children's
`startup_timeout_sec`. Parallel/lazy child startup plus
`notifications/tools/list_changed` is the right next step *if* a real
deployment ever carries enough children for serial startup to matter — noted
in `README.md`, not ticketed, because today every shipped entry is
`enabled: false`.

## (c) Should bug 3 have blocked wiring this bundle into a HAL role?

**Yes — and the question is now moot, because it is fixed.** For the record,
the ruling on the principle: an aggregator whose whole pitch is "one endpoint
instead of a connection per tool" must not convert one child's failure into a
total outage, and unbounded reads made that conversion certain the first time
any real child was slow to boot. That is a correctness bug, not a polish item,
so yes, it would have been a hard block on any Limpet/HAL wiring.

Standing gate going forward, independently verified here: nothing on the fleet
invokes this bundle yet (wq#3815 grepped `limpet/`, `ops/`, `fleet-config/`
for `meta_server.py` / `limpet-meta-server`; still zero hits outside the
bundle's own tree), so there was no live deployment at risk. The gate for
wiring it in is `install.sh --verify` passing — which since this ticket runs
the regression tests below, so a regression to the wedging behaviour fails the
verify handler rather than being discovered in production.

## Evidence

Baseline: 16/16 shipped tests pass before any change (and after). Suite now
30 passed (`python3 -m pytest ops/wq3808-limpet-mcp-meta-server-a1/tests -q`).

Each new test was run against a pre-fix tree — the three sources at `HEAD`
(`37ba0b11`) copied to `/tmp/fworker-wq3821/prefix/` with the *new* tests
alongside — to prove the probes can fail:

```
$ python3 -m pytest /tmp/fworker-wq3821/prefix/tests/test_meta_server_integration.py \
    -q -k "changed_command or request_reload"
FAILED ...::test_changed_command_restarts_a_still_alive_child
FAILED ...::test_request_reload_forces_an_out_of_cycle_check
  AssertionError: request_reload() did not force a check well inside the 60s poll interval
2 failed, 7 deselected

$ python3 -m pytest .../prefix/tests/test_meta_server_integration.py -q -k changed_env
FAILED ...::test_changed_env_restarts_a_still_alive_child
```

Bug 3 can't be shown by a pre-fix pytest run (the new tests import
`ChildServerTimeout`, which doesn't exist pre-fix), so it has standalone
probes runnable against either tree:

```
$ python3 -I /tmp/fworker-wq3821/probe_hang_initialize.py /tmp/fworker-wq3821/prefix
HANG: no response within 5.0s to a plain 'initialize'          # exit 1
$ python3 -I /tmp/fworker-wq3821/probe_hang_initialize.py ops/wq3808-limpet-mcp-meta-server-a1
initialize answered: {'name': 'limpet-meta-server', 'version': '0.1'}
tools served: ['cli.fast']
OK: unrelated cli tool survived the hung child                 # exit 0

$ python3 -I /tmp/fworker-wq3821/probe_non_json_child.py /tmp/fworker-wq3821/prefix
NOT ISOLATED: MetaServer() raised JSONDecodeError: Expecting value: line 1 column 1
$ python3 -I /tmp/fworker-wq3821/probe_non_json_child.py ops/wq3808-limpet-mcp-meta-server-a1
[limpet-meta] failed to start child server 'banner': child server 'banner' wrote
  non-JSON on stdout while answering initialize: 'starting up...'
tools served: ['cli.fast']                                     # exit 0
```

Both probes are reproduced as permanent tests
(`tests/test_child_timeouts.py::test_end_to_end_subprocess_answers_initialize_despite_hung_child`,
`::test_child_writing_non_json_is_skipped_not_fatal`) against committed
fixtures (`tests/fixture_hang_child.py`), not `/tmp` scratch, so the evidence
is re-runnable.

Probing outside the fix's own default scope (wq#2630): the hang tests pin
`startup_timeout_sec`/`request_timeout_sec` to 0.5s, *not* the 10s/30s
defaults the code was written around, so a pass can't come from the default
budget; `::test_child_that_goes_silent_mid_call_returns_an_error` hangs at
`tools/call` rather than at `initialize`, a phase no startup timeout covers;
`::test_child_writing_non_json_is_skipped_not_fatal` is a different failure
mode (bad output, not silence) that no timeout catches at all; and
`test_registry.py::test_shipped_example_manifest_parses` validates
`manifest.example.json` — a real shipped input, not a test fixture — against
the new validation rules.

## Allowlist gap (unchanged, re-confirmed)

`bash ops/wq3808-limpet-mcp-meta-server-a1/install.sh --verify` → `This
command requires approval`, for a BUILD worker as well as the VERIFY worker
that hit it in wq#3815 (ISSUE-LOG row 624 / wq#2410). Validated the underlying
check by running its body directly: `python3 -m pytest
ops/wq3808-limpet-mcp-meta-server-a1/tests -q` → 30 passed.
