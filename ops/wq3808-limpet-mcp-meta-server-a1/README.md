# wq#3808 — LIMPET: unified MCP meta-server

One MCP endpoint an agent can point at and get every tool the fleet has,
instead of juggling a separate MCP connection per tool.

## Pieces

- `registry.py` — loads the registry manifest (`manifest.example.json` is a
  template): a flat JSON file listing `mcp_servers` (tools with their own MCP
  server — REA, Ghidra MCP, radare2 MCP, ...) and `cli_tools` (tools with no
  MCP server, CLI-wrapped instead). `ManifestWatcher` polls the file's mtime
  so a manifest edit can be picked up without restarting the server.
- `cli_wrapper.py` — turns a `cli_tools` manifest entry into something that
  looks like an ordinary MCP tool: a name, description, JSON-schema, and an
  `invoke(args)` that runs the command (`shell=False`, argv list — manifest
  args are substituted as whole argv entries, never interpolated into a
  shell string, so they can't break out of their argument position).
- `stdio_mcp_client.py` — a minimal MCP client (stdlib only) that spawns a
  child MCP server over stdio, does the `initialize` handshake, and forwards
  `tools/list` / `tools/call`. This is the "proxy" half.
- `meta_server.py` — ties it together. `ToolRegistry.rebuild()` merges the
  CLI-wrapped tools and every child server's discovered tools into one
  namespaced set (`<server-or-"cli">.<tool-name>`, so two child servers that
  both export e.g. `list_files` don't collide). `MetaServer` then speaks MCP
  upstream (stdio, newline-delimited JSON-RPC — `initialize`, `tools/list`,
  `tools/call`) to whatever agent connects to it, dispatching each call to
  either a CLI wrapper or a child-server proxy.

## Hot reload

A background thread waits up to `reload_poll_sec` (default 2s) on the manifest
and on change calls `ToolRegistry.rebuild()`, which:
- starts any newly-enabled child server or CLI tool,
- stops any child server that's no longer in the manifest,
- restarts a child whose `command` or `env` changed — those are baked into the
  running process, so the live child is the wrong process once they differ,
- reuses (does not restart) a child whose `command`/`env` are unchanged and
  whose process is still alive; a changed `startup_timeout_sec` /
  `request_timeout_sec` is applied to the live client without a restart,
- leaves a currently in-flight `tools/call` alone — only the next
  `tools/list`/`tools/call` sees the new set.

`SIGHUP` (`MetaServer.request_reload`) cuts the wait short, so the manifest
check happens immediately instead of at the next tick. It forces the *check*:
if the file is unchanged there is nothing to rebuild.

## One bad child can't take the server down

Every read from a child MCP server is bounded (`stdio_mcp_client.py`):
`startup_timeout_sec` (default 10s) covers the `initialize` handshake and
`request_timeout_sec` (default 30s) covers each `tools/list`/`tools/call` —
the `mcp_servers` counterpart of a `cli_tools` entry's `timeout_sec`, settable
per entry in the manifest. A child that times out, dies, or writes non-JSON on
stdout raises `ChildServerError`, which `rebuild()` logs and skips: that one
entry loses its tools and every other child and CLI tool is still served.
Worst-case startup is therefore bounded by the sum of the enabled children's
`startup_timeout_sec`, not unbounded.

The initial `rebuild()` still runs synchronously in `MetaServer.__init__`, so
the tool set is complete and deterministic by the time `initialize` is
answered. If a deployment ever carries enough children that bounded-but-serial
startup is too slow, the next step is building them in parallel (or lazily)
and emitting `notifications/tools/list_changed` as each lands — the server
already advertises that capability.

## Per-call evidence (wq#3964)

Every `tools/call` answer carries `_meta.evidence` (`evidence.py`): the
namespaced tool (`operation`/`tool`), `source` {kind, server, child tool,
`git_sha` from the manifest entry}, `args_sha256`, `started_at`/`finished_at`,
`ok`, `exit_code` (CLI tools), `error`, the child's own `coverage` note if it
supplied one (`_meta.coverage`), `untrusted_content`, and an
`ev_<sha256>` `evidence_id` shaped like REA's Evidence records so the log
exports into an REA-style bundle. A call that errors or times out is still
a JSON-RPC error, with the block (`ok: false`) at `error.data._meta.evidence`.

Manifest entries (both kinds) take two optional fields: `git_sha` (the
plugin's pinned revision) and `untrusted_output` (default `true`: output is
target-derived; a child marking `_meta.provenance.untrusted_content` forces
it true regardless). When a workspace is bound (`MetaServer(workspace=...)`
or `LIMPET_WORKSPACE`), each block is appended to
`<workspace>/evidence/tool-calls.jsonl`.

## Why no dependency on the `mcp` SDK

`ops/wq1151-lumen-kb/server.py` builds its MCP server on `mcp.server.fastmcp`
(the official Python SDK) — the right call for a single, static tool list.
This meta-server's job is different: dynamically discovering and proxying an
*arbitrary, changing* set of child servers, which means owning the tool
registry directly rather than going through FastMCP's decorator-based
registration. Implementing the stdio JSON-RPC slice directly (it's a few
methods: `initialize`, `notifications/initialized`, `tools/list`,
`tools/call`) keeps the meta-server deployable anywhere on the fleet with
zero `pip install` step, which matters for a thing meant to run on Limpet
(`ops/wq3658-hardware-role-assignment-limpet-secondar/ROLE_ASSIGNMENT.md`)
rather than wherever a venv happens to already exist.

## Registering a real child server

Per `TOOL_REGISTRY.md`, REA is on a SECURITY HOLD (wq#3786) — not installed,
not registered — so `manifest.example.json` lists it `"enabled": false` with
that note attached. Ghidra MCP and radare2 MCP are listed as disabled
placeholders: no fleet install of either exists yet to point `command` at.
Flipping any of these to `enabled: true` is a manifest edit plus a real
install, not a code change here.

## Tests

`tests/` covers manifest parsing/validation, the CLI wrapper (including that
a shell-metacharacter-bearing argument does nothing special, since `argv` is
never shelled out as a string), hot reload add/remove without restart, and
an end-to-end test that spawns `meta_server.py` as a real subprocess and
drives it over stdin/stdout exactly as an agent would — not just the
in-process shortcut.

`test_child_timeouts.py` plus the restart/SIGHUP tests in
`test_meta_server_integration.py` are the regression tests for the three bugs
wq#3815 found and wq#3821 ruled on; each one hangs or fails outright against
the pre-fix code (transcripts in
`reports/wq3821_judgment_wq3815_vs_wq3808_meta_server.md`). Run with:

    python3 -m pytest ops/wq3808-limpet-mcp-meta-server-a1/tests -q
