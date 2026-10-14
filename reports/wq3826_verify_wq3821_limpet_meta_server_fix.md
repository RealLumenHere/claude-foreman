# wq#3826 — verify: wq#3821's fix for wq#3815's three LIMPET meta-server bugs

- Ticket: wq#3826, verify-pass (archetype 8), claude-sonnet-5@high, fresh instance
- Diff under review: wq#3821's `fix(limpet): bound child reads, restart changed
  children, wire SIGHUP` (`5c4db043`, already on `main`)
- Target: `ops/wq3808-limpet-mcp-meta-server-a1/`
- Writeup: `reports/wq3826_verify_wq3821_limpet_meta_server_fix.md`

## Ruling: CONFIRM

wq#3821's fix holds against adversarial re-derivation. All three wq#3815 bugs
are genuinely fixed, the regression tests are meaningful (not tautological),
and independently reconstructing the pre-fix tree reproduces every failure
wq#3821 claimed. No contradiction, no judgment escalation.

## What I checked, independently of wq#3821's own transcripts

**Read the live code cold** (`meta_server.py`, `stdio_mcp_client.py`,
`registry.py` in full, not just the diff hunks):

- Bug 1 fix: `_entry_matches_client()` (`meta_server.py:37`) compares
  `entry.command`/`entry.env` against the live client; `rebuild()`
  (`meta_server.py:94-121`) stops and replaces on a mismatch, reaps a dead
  same-named client, and updates timeouts in place on a live client
  (`meta_server.py:102-105`) rather than forcing a pointless restart on a
  timeout-only edit. Traced the "stop a client twice" edge (mismatch-restart
  whose replacement then fails to start): the old client is swept again by
  the `stale` cleanup at the end of `rebuild()` (`meta_server.py:136-138`),
  but `StdioMcpClient.stop()` (`stdio_mcp_client.py:106-120`) is idempotent
  (`proc, self._proc = self._proc, None; if proc is None: return`) — no
  double-kill, no exception.
- Bug 2 fix: `_reload_loop()` (`meta_server.py:181-191`) now
  `self._reload_requested.wait(timeout=self.reload_poll_sec)` instead of
  `time.sleep()`, so a SIGHUP's `request_reload()` (`.set()`) actually cuts
  the wait short. Correct `Event` usage (`.wait()` returns immediately if
  already set, `.clear()` after).
- Bug 3 fix: `StdioMcpClient` drains stdout/stderr in daemon reader threads
  into a queue (`stdio_mcp_client.py:147-178`); `_recv()`
  (`stdio_mcp_client.py:208-245`) takes a wall-clock deadline instead of a
  blocking `readline()`; a timeout raises `ChildServerTimeout` (subclass of
  `ChildServerError`), which `rebuild()`'s existing
  `except (ChildServerError, OSError): continue` (`meta_server.py:114-121`,
  `122-126`) already turns into "skip this entry, keep serving the rest." A
  timed-out request sends `notifications/cancelled` and remembers the id so a
  late answer doesn't get misdelivered to the next request
  (`stdio_mcp_client.py:247-260`, `239-244`). `registry.py`'s
  `_positive_timeout()` (`registry.py:63-78`) correctly rejects a <=0 budget
  as a `ManifestError` rather than accepting a setting that would fail every
  read instantly.

**Ran the shipped suite as-is:**

```
$ python3 -m pytest ops/wq3808-limpet-mcp-meta-server-a1/tests -q
30 passed in 3.31s
```

**Reconstructed the pre-fix tree myself** (not reusing wq#3821's `/tmp`
transcript) by extracting `meta_server.py`/`stdio_mcp_client.py`/
`registry.py`/`cli_wrapper.py` from parent commit `2ccdae3d` into
`/tmp/fworker-wq3826/prefix/`, copying today's tests/fixtures on top, and
running the new regression tests against it:

```
$ python3 -m pytest tests/test_meta_server_integration.py -q \
    -k "changed_command or changed_env or request_reload"
FAILED ...::test_changed_command_restarts_a_still_alive_child
FAILED ...::test_changed_env_restarts_a_still_alive_child
FAILED ...::test_request_reload_forces_an_out_of_cycle_check
3 failed, 7 deselected in 5.29s
```

Confirms bugs 1 and 2 independently: all three new tests fail on the
reconstructed pre-fix tree, pass on the live bundle.

Bug 3 can't be run as a pytest import against the pre-fix tree (as wq#3821
noted): `test_child_timeouts.py` imports `ChildServerTimeout`, which doesn't
exist pre-fix —

```
ImportError: cannot import name 'ChildServerTimeout' from 'stdio_mcp_client'
```

— confirmed structurally instead: pre-fix `stdio_mcp_client.py`'s `_recv()`
is a bare `self._proc.stdout.readline()` with no deadline at all. Wrote a
standalone probe (`/tmp/fworker-wq3826/probe_hang.py`, own script, separate
from the extracted tree) that spawns `meta_server.py` as a real subprocess
against a manifest with one child that never answers `initialize`, and
checked both trees:

```
$ timeout 20 python3 -I probe_hang.py /tmp/fworker-wq3826/prefix      # pre-fix
HANG: no response within 15.0s to a plain 'initialize'                 # exit 1
$ timeout 20 python3 -I probe_hang.py ops/wq3808-limpet-mcp-meta-server-a1  # live
OK: answered within 15s: {...}                                         # exit 0
```

One self-correction worth recording: my first pass at this probe used a 5s
wait, which "failed" against the *live* (fixed) bundle too — that's not a
regression, it's the probe being shorter than the live bundle's own
`startup_timeout_sec` default (10s). The shipped tests sidestep this
correctly by pinning `startup_timeout_sec=0.5` in the manifest fixture
(`tests/test_child_timeouts.py:27-28`) rather than relying on the default —
exactly the wq#2630 "probe outside the fix's own default scope" requirement,
and the reason I widened my own probe to 15s rather than calling it a finding.

**Allowlist gap, re-confirmed a third time:** `bash
ops/wq3808-limpet-mcp-meta-server-a1/install.sh --verify` → `This command
requires approval` (ISSUE-LOG row 624 / wq#2410), same as wq#3815 and wq#3821
hit. Validated the handler's body directly instead: `python3 -m pytest
ops/wq3808-limpet-mcp-meta-server-a1/tests -q` → 30 passed (above). Not
re-logging — same row, third occurrence, nothing new to record.

## Things I tried to break and couldn't

- Double-stop/double-reap on a client that is both mismatched and fails to
  restart: idempotent, see above.
- Concurrent reload-thread vs. in-flight `tools/call` on the same client
  (`StdioMcpClient.stop()` never takes `self._lock`, so a reload racing a
  live request is theoretically possible): present in both the pre-fix and
  post-fix code identically (`_lock` was never held by `stop()` in either
  version) — not a regression wq#3821 introduced, and not one of wq#3815's
  three bugs, so out of scope for this contradiction check.
- Manifest validation edge cases (`0`, `-1`, non-numeric timeout): covered by
  `test_registry.py::test_unusable_child_timeout_is_a_manifest_error`
  (parametrized), and independently re-read `_positive_timeout()`
  (`registry.py:63-78`) to confirm the rejection logic matches.
- Doc/code drift: `manifest.example.json`'s `_comment` and the `ghidra`
  placeholder's new `startup_timeout_sec: 60` / `request_timeout_sec: 120`
  match the claims in `README.md` and are themselves parsed and asserted on
  by `test_shipped_example_manifest_parses` (a real shipped file, not a
  fixture).

## Evidence summary

- `python3 -m pytest ops/wq3808-limpet-mcp-meta-server-a1/tests -q` → 30
  passed (live tree).
- `python3 -m pytest tests/ -q -k "changed_command or changed_env or
  request_reload"` on independently-reconstructed pre-fix tree → 3 failed, 7
  deselected.
- `probe_hang.py` → HANG (exit 1) on reconstructed pre-fix tree, OK (exit 0)
  on live tree, both at a 15s wait that exceeds the live bundle's own default
  `startup_timeout_sec` (10s).
