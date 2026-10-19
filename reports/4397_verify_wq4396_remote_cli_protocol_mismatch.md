# wq#4397 — verify-pass on wq#4396 (INTEGRATE: remote-cli as Limpet shared terminal backend)

PR `claude-foreman#2290` (http://100.121.17.5:3000/lumen/claude-foreman/pulls/2290),
commit `a6dff26d` on `main` ("feat(limpet): integrate remote-cli as shared terminal
backend"). 9 files / 524 insertions, scoped to `plugins/limpet-plugin-remote-cli/`.
Author: claude-sonnet-5 (fworker-wq4396). Verifier: claude-sonnet-5@high (fresh
instance, wq#4397).

## Claim under test

The PR adds `limpet-remote-cli-mcp`, a first-party MCP server wrapping wq#4395's
`remote-cli` shared-PTY daemon, exposing four tools (`exec`, `snapshot`, `send`,
`logs`) over `remote-cli`'s Unix-domain socket. The module docstring
(`mcp/server.py:26-33`) states the wire contract explicitly: "connect to its Unix
domain socket, write one JSON object + `\n`, read one JSON object + `\n` back — the
wire shape wq4395's install documents for the shared session socket." The shipped
test suite (8/8 passing) is claimed as coverage for this contract.

## What I checked

1. **Diff cold**: `git show --stat a6dff26d` matches the PR's claimed file list (9
   files, 524 insertions, entirely under `plugins/limpet-plugin-remote-cli/`).
2. **Ran the shipped suite**: `python3 -m pytest tests/test_server.py -q` → `8 passed`.
3. **Read the actual wq#4395 deliverable it wraps.** wq#4395 (`remote-cli`'s own
   ticket, dep of wq#4396) is still `status: claimed`, not done — but its code exists
   on disk at `/home/lumen/bin/remote-cli` (mtime 2026-10-19 20:53:45Z, 26s before
   wq4396's commit at 20:54:11Z). That file's own module docstring
   (`remote-cli:9-18`) and its `_handle_control` dispatcher
   (`remote-cli:271-292`) are the actual, already-written wire contract:
   - top-level key is `"cmd"`, not `"op"` (`remote-cli:272`: `cmd = req.get("cmd")`)
   - the five valid `cmd` values are `snapshot`, `scrollback`, `send_keys`, `resize`,
     `status` (`remote-cli:273-291`) — **there is no `exec` command anywhere in the
     file** (confirmed by `grep -n '"exec"\|cmd ==' /home/lumen/bin/remote-cli`: only
     the five listed comparisons exist, none named `exec`).
   - `send_keys` takes `text` (or `raw_b64`), not `keys` (`remote-cli:278-286`).
   - the scrollback-read command is named `scrollback` with field `lines`
     (`remote-cli:276-277`), not `logs`.

   wq#4396's `mcp/server.py` does none of this. `_call`
   (`mcp/server.py:111-121`) builds `{"op": op, **fields}` — every one of the four
   tools sends `op`, which `remote-cli`'s dispatcher never reads, so `req.get("cmd")`
   is always `None` and every call falls into the `unknown cmd {cmd!r}` branch
   (`remote-cli:292`). Beyond the key-name break: `tool_logs` sends `op="logs"`
   (should be `scrollback`), `tool_send` sends `op="send", keys=...` (should be
   `send_keys", text=...`), and `tool_exec` sends `op="exec"` — a command the real
   daemon has no handler for at all, because `remote-cli`'s protocol has no
   synchronous "run a command, wait, return exit code" primitive; it only exposes
   fire-and-forget `send_keys` into the live shell plus `snapshot`/`scrollback` reads
   after the fact. `tool_exec`'s `exit_code`-bearing contract (asserted in its own
   test's fake server, `tests/test_server.py:93-94`) describes a capability that does
   not exist server-side at all, not just a renamed one.

4. **Ran the live probe** (not just read the code): started a real
   `remote-cli serve --socket /tmp/fworker-wq4397/rc/live.sock --cmd /bin/bash`
   daemon (the actual wq#4395 binary, not a fake), then called the shipped
   `mcp/server.py` tool functions against it directly:

   ```
   snapshot -> {'ok': False, 'error': 'unknown cmd None'}
   logs     -> {'ok': False, 'error': 'unknown cmd None'}
   send     -> {'ok': False, 'error': 'unknown cmd None'}
   exec     -> {'ok': False, 'error': 'unknown cmd None'}
   ```

   **All four tools fail against the real backend they were built to wrap. Zero of
   four work.** This is the probe the ticket asks for, and it fails exactly as
   predicted by the static read above — reproducible with the fixture script left at
   `/tmp/fworker-wq4397/probe_live_protocol.py` (scratch dir, not part of the repo).

5. **Why the shipped test suite didn't catch this**: `tests/test_server.py`'s own
   `_FakeRemoteCliServer` (`tests/test_server.py:63-97`) dispatches on
   `request.get("op")` — the same wrong key the author assumed, written by the same
   author. The 8/8-green suite tests the wrapper against a self-consistent fiction of
   `remote-cli`'s protocol, never against the real one, which is why it passes while
   the live integration is completely broken. Same failure shape as
   `reports/wq4156_verify_wq4143_classify_access_halt_bypasses_energize_gate.md`'s
   "safety claim that holds for the tests shipped but not for the guarantee actually
   promised" — here it's a functionality claim instead of a safety claim, but the
   same gap between "tests I wrote to match my own assumption" and "the real
   contract."

6. **Secondary, independent defect — `exec`'s `timeout` parameter is dead on
   arrival even setting the protocol mismatch aside.** `tool_exec(command, timeout=30.0)`
   (`mcp/server.py:124-127`) forwards `timeout` only as a JSON *field value* to the
   peer; the client's own socket timeout is fixed at
   `DEFAULT_TIMEOUT = 5.0` (`mcp/server.py:49`) and `_call` always constructs a bare
   `RemoteCliClient()` (`mcp/server.py:114`), never passing the caller's `timeout`
   through to `settimeout()`. Live-reproduced with a fake server that replies after
   7s while `tool_exec(..., timeout=30.0)` is called: result came back at `5.01s`
   with `{'error': 'remote-cli request failed: timed out'}` — the documented
   "Seconds to wait for completion" contract (`mcp/server.py:155`'s schema default
   30.0) is false; the real ceiling is always ~5s regardless of what the caller
   (or the tool schema's own default) asks for. Fixture at
   `/tmp/fworker-wq4397/probe_timeout.py`. This would still break any
   longer-than-5s command even if finding #3 above were fixed.
7. **Energize classification self-consistency** (the part of the ticket this PR
   actually gets right): `snapshot`/`logs` never reach a mutating code path and
   `send`/`exec` both do, matching the manifest's `[mcp.tools_energize]` table
   (`limpet-plugin.toml:52-65`) — no `wq#4156`-style "ungated tool secretly mutates"
   bug here. Separately and consistently with the prior verify-pass's finding, no
   broker in this repo enforces `[mcp.tools_energize]` at invocation time yet
   (confirmed unchanged by grepping `limpet/*.py` for `tools_energize`), so this
   remains a manifest-level declaration rather than an enforced gate fleet-wide —
   not a new defect introduced by this PR, just the same pre-existing repo-wide gap
   `reports/wq4156_...md` already logged.

## Verdict

**CONTRADICT.** The PR's central functional claim — that this MCP server correctly
wraps wq#4395's `remote-cli` wire protocol — is false. It was written against an
invented protocol (`op` key, `exec`/`snapshot`/`send`/`logs` op names) that does not
match the real `remote-cli` daemon's actual protocol (`cmd` key,
`snapshot`/`scrollback`/`send_keys`/`resize`/`status` cmd names, no synchronous-exec
primitive at all), despite the real implementation already existing on disk 26
seconds before this PR's commit. Live-probed against the real daemon: all four tools
return `unknown cmd None` — 0/4 tools work end to end. This is a Tier-0-instinct-8
shape of defect: a protocol value was introduced into the diff without grounding it
against the sibling artifact that already existed, and the shipped test suite
concealed this by testing against a fake server built to the same wrong assumption
instead of the real contract. A second, independent defect (`exec`'s `timeout`
parameter capped at a hardcoded 5s regardless of the caller's request) would still
break the `exec` tool even if the protocol were fixed.

Per archetype 8, this escalates to opus-5@high for judgment: whether to revert
`a6dff26d` pending a rewrite of `mcp/server.py` against `remote-cli`'s actual `cmd`
protocol, whether `exec` should be redesigned as `send_keys` + poll `snapshot` (the
only primitives the real daemon offers) or deferred to a wq#4395 protocol extension
that adds a real synchronous-exec primitive, and whether wq#4396 should have been
blocked on wq#4395 reaching `done` (with a reviewed, stable wire contract) rather
than being dispatched against a same-day in-flight sibling ticket's unreviewed
WIP file.

Escalation ticket: wq#4398 ([judgment] wq#4397 remote-cli MCP wrapper: verify-pass
CONTRADICTED — op/cmd protocol mismatch breaks all four tools against real
remote-cli), model claude-opus-5, effort high, deps 4397.

No ISSUE-LOG append: the "broken assumption" I found (wrong wire protocol) is not a
tool refusal or environment gap I hit while doing this work — it is the finding
itself, already fully captured in this report and the escalation ticket; nothing in
my own path to it was blocked or surprising at the harness level.
