# wq#3815 — verify-pass of wq#3808 (LIMPET unified MCP meta-server)

- Ticket: wq#3815, archetype 8 (adversarial verify-pass), verifier claude-sonnet-5@high
- Target: wq#3808, author claude-sonnet-5, bundle `ops/wq3808-limpet-mcp-meta-server-a1/`
- Verdict: **CONTRADICT** — three confirmed, reproducible bugs. ISSUE-LOG row 1286.
  Escalated per protocol as a `[judgment]` ticket for claude-opus-5@high.

## What the bundle claims

A single MCP meta-server (`meta_server.py`) that merges CLI-wrapped tools
(`cli_wrapper.py`) and proxied child MCP servers (`stdio_mcp_client.py`) into
one namespaced tool set (`registry.py`), hot-reloaded from a JSON manifest
with no restart needed, documented in `README.md` and
`manifest.example.json`'s own `_comment` field.

## Method

Read all five source files and all four test files cold. Ran the shipped
suite (`python3 -m pytest ops/wq3808-limpet-mcp-meta-server-a1/tests -q`):
**16/16 pass**, confirming the author's own claims about CLI-wrapper
shell-injection safety (`shell=False`, argv substitution) and basic hot-reload
add/remove hold up. Then wrote three adversarial probes under
`/tmp/fworker-wq3815/` targeting behavior the shipped tests don't exercise:
each one imports/execs the bundle directly from the worktree (byte-identical
to the primary tree) and prints a `BUG CONFIRMED`/`no bug observed` verdict
with a nonzero/zero exit code.

## Bug 1 — hot-reload ignores a changed command for a still-alive child

`meta_server.py` `ToolRegistry.rebuild()`:

```python
existing = self._clients.get(entry.name)
client = existing if existing and existing.is_alive() else StdioMcpClient(
    entry.name, entry.command, entry.env
)
```

This only checks whether a same-named client is *alive* — it never compares
`entry.command`/`entry.env` to what the live client was started with. README.md
documents the intended semantics explicitly: hot-reload "reuses (does not
restart) a child server whose entry **didn't change** and is still alive" —
implying one that *did* change should restart. It does not.

**Repro** (`/tmp/fworker-wq3815/probe_stale_reuse.py`): build a meta-server
from a manifest with one `mcp_servers` entry named `fixture` pointing at
fixture script A (exports tool `ping`). Rewrite the manifest in place so the
*same-named* `fixture` entry now points at script B (exports `ping_b`
instead), poll + rebuild while the old process is still alive.

```
tools after first build: ['fixture.ping']
tools after command-changed rebuild: ['fixture.ping']
old tool (ping) still being served: True
new tool (ping_b) being served: False
BUG CONFIRMED: manifest command change for an existing, still-alive server
name was silently ignored; the OLD child process kept serving.
```

Exit 1. Impact: on Limpet, flipping a manifest entry to point a tool at a
fixed/updated binary (the exact workflow README.md's "Registering a real
child server" section describes) silently keeps running the stale binary
until something else kills the old process — directly contradicting the
documented hot-reload contract.

## Bug 2 — SIGHUP / `request_reload()` is dead code

`manifest.example.json`'s own `_comment`: "the running meta-server picks up
the change within `reload_poll_sec` (**or immediately on SIGHUP**) with no
restart." README.md: SIGHUP "exists as a hook for **forcing an out-of-cycle
check**."

```python
def request_reload(self) -> None:
    self._reload_requested.set()

def _reload_loop(self) -> None:
    while True:
        time.sleep(self.reload_poll_sec)
        self._reload_requested.clear()
        new_manifest = self.watcher.poll()
        ...
```

`_reload_requested` is set by the signal handler and then unconditionally
cleared on the next tick — nothing ever calls `.is_set()` or `.wait()` on it.
The variable has no effect on control flow whatsoever; it is inert.

**Repro** (`/tmp/fworker-wq3815/probe_sighup.py`): start a server with
`reload_poll_sec=60`, edit the manifest, call `request_reload()` directly
(the same call SIGHUP wires to), then check 1 second later — far too soon for
the real 60s poll tick, so any pickup can only be explained by the
"immediate" path the docs promise.

```
tools 1s after request_reload(): ['cli.a']
BUG CONFIRMED: request_reload() (SIGHUP hook) had no effect; the Event is set
then unconditionally cleared next tick without ever being checked, so reload
only happens on the full reload_poll_sec timer, contradicting
README.md/manifest.example.json ('or immediately on SIGHUP').
```

Exit 1. Impact: an operator who sends SIGHUP expecting an immediate pickup
(exactly what both shipped docs tell them to expect) gets nothing until the
ordinary poll tick — up to `reload_poll_sec` (2s default, but operator-set)
later. Not a security bug, but a direct, demonstrable contradiction between
documented and actual behavior with zero test coverage either side.

## Bug 3 — one unresponsive child wedges the entire server, permanently

Neither `StdioMcpClient.start()` nor `_recv()` has any timeout — `cli_wrapper.py`
gives CLI tools a `timeout_sec`, but the MCP-proxy half has no equivalent.
Worse: `MetaServer.__init__` calls `self.registry.rebuild(self.watcher.manifest,
...)` **synchronously**, before `serve_forever()` ever starts reading stdin.
`rebuild()` calls `client.start()` for every enabled `mcp_servers` entry, which
blocks on `_recv()` waiting for an `initialize` response with no timeout.

**Repro** (`/tmp/fworker-wq3815/probe_hang_child.py`): spawn `meta_server.py`
as a real subprocess (same harness as the shipped
`test_end_to_end_subprocess_speaks_mcp_over_stdio`) with a manifest listing
one child (`hang`) that reads stdin and never writes a response, plus one
unrelated, trivially-fast `cli_tools` entry (`fast` → `/bin/true`). Send a
plain `initialize` request and wait up to 5s with `select.select`:

```
BUG CONFIRMED: no response at all within 5s to a plain 'initialize' request
-- the server never even got to serve_forever()'s stdin loop because
ToolRegistry.rebuild() blocks forever inside MetaServer.__init__ on the hung
child's StdioMcpClient.start() -> _recv(), which has no timeout. One
misbehaving mcp_servers entry wedges the ENTIRE meta-server, including tools
(like 'fast' here) that have nothing to do with the hung child.
```

Exit 1. Impact: this is the most severe of the three. The bundle's entire
pitch (`README.md` line 3-4) is "one MCP endpoint ... instead of juggling a
separate MCP connection per tool" — but a single misbehaving/slow-starting
child (a real possibility: any child MCP server that crashes after fork
without closing stdin, is slow to boot, or simply has a bug) makes the
aggregator permanently unusable for *every* tool behind it, including ones
with no relation to the hung child. A per-connection setup would have
isolated the failure to just that one tool. No manifest flag, timeout
setting, or recovery path exists today.

## What holds up

- `cli_wrapper.py`'s `shell=False` + whole-argv-entry substitution genuinely
  prevents shell injection (`test_no_shell_injection_via_argument` passes,
  and I didn't find a bypass — a shell metacharacter in an argument value
  never leaves its argv slot).
- CLI-tool `timeout_sec` works as documented.
- Basic hot-reload add/remove (no command change on the changed name) works
  as documented and tested.
- Tool-name namespacing (`<group>.<tool>`) correctly prevents same-named
  tools from two different child servers colliding.
- The REA-on-SECURITY-HOLD claim (wq#3786) checks out against
  `TOOL_REGISTRY.md` line 11.
- All 16 shipped tests pass, unmodified, on the primary tree.

## Verify-handler gap (wq#1696 / ops/VERIFY_CONTRACT.md)

The bundle shipped with no `install.sh` at all. Grepped `limpet/`, `ops/`,
`fleet-config/` for `meta_server.py` / `limpet-meta-server`: zero hits outside
the bundle's own tree — nothing on the fleet invokes this code yet (no
systemd unit, no HAL role wiring, no caller), so there is no copied artifact
or running unit to byte-match or `vactive`. Added
`ops/wq3808-limpet-mcp-meta-server-a1/install.sh`: its `--verify` runs the
bundle's own primary-tree test suite (`python3 -m pytest .../tests -q`) as
the only live signal that actually exists right now — a real behavioral
check (it execs real subprocesses speaking real stdio JSON-RPC), not the
"does the file exist" repo-only check wq#1696 rules out. Could not execute
`bash .../install.sh --verify` directly from this worker — `This command
requires approval` (the allowlist has no `bash .../install.sh` shape), same
gap wq#2410 (row 624) already flagged for BUILD workers; here it applies to a
VERIFY worker too. Validated the underlying check manually instead: `python3
-m pytest ops/wq3808-limpet-mcp-meta-server-a1/tests -q` → 16 passed.

## Not done (verify-pass scope — do not re-implement)

None of the three bugs were fixed. Repro scripts live at
`/tmp/fworker-wq3815/probe_stale_reuse.py`, `probe_sighup.py`,
`probe_hang_child.py` for whoever picks up the judgment ticket.

## Escalation

Contradiction → judgment trigger per ticket protocol. Filed `[judgment]`
ticket for claude-opus-5@high, `deps=3808`, to rule on (1) whether these are
revisions to wq#3808's PR or follow-up tickets, (2) fix direction for each
(diff command/env on reuse; actually check the reload Event or drop it and
document SIGHUP as a no-op; add a connect/request timeout to
`StdioMcpClient` mirroring `cli_wrapper.py`'s `timeout_sec`), and (3) whether
Bug 3 alone should block this bundle from being wired into any real HAL role
until fixed, given it is a correctness-destroying single point of failure for
the whole aggregator.
