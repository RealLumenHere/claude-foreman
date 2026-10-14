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

A background thread polls the manifest's mtime every `reload_poll_sec`
(default 2s) and on change calls `ToolRegistry.rebuild()`, which:
- starts any newly-enabled child server or CLI tool,
- stops any child server that's no longer in the manifest,
- reuses (does not restart) a child server whose entry didn't change and is
  still alive,
- leaves a currently in-flight `tools/call` alone — only the next
  `tools/list`/`tools/call` sees the new set.

`SIGHUP` (`MetaServer.request_reload`) exists as a hook for forcing an
out-of-cycle check; the poll loop still carries the actual reload.

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
in-process shortcut. Run with:

    python3 -m pytest ops/wq3808-limpet-mcp-meta-server-a1/tests -q
