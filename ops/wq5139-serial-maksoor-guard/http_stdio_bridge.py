#!/usr/bin/env python3
"""wq#5139: serial-maksoor (qarnet serial-mcp 0.9.3 on Maksoor, streamable HTTP) as a stdio MCP server,
so the shared lumen_mcp_guard (stdio backends only) can sit in front of it, plus the energize-confirm gate.

  http_stdio_bridge.py URL

lumen_mcp_guard gives the allowlist, pins, masking and audit. What it cannot give is the hal-spec.md
§9.2a rule that a tool which energizes a target (writes bytes, toggles DTR/RTS, asserts BREAK, changes
line settings, discards captured data) needs an operator-issued confirm token. action_gate.py enforces
that only inside foreman workers; interactive sessions in /data/lumen/archive/re run no PreToolUse hook
for mcp__serial-*, so before this bundle a write reached the UART with no gate at all.

This bridge:
  * adds an optional string property `confirm_token` to the input schema of each ENERGIZING tool, so the
    guard's strict schema check accepts it and the model sees it documented;
  * on a call to an ENERGIZING tool, verifies the token with energize_confirm.verify_token(token,
    "serial.<tool>") (single-use, capability-scoped, mintable only with Frank's passphrase, wq#5216: `python3
    /home/lumen/foreman/harness/energize_confirm.py issue serial.<tool>`), and refuses without one;
  * strips `confirm_token` before forwarding, so serial-mcp never sees an undeclared argument;
  * refuses `confirm_token` on any other tool (schema check upstream would too; belt and braces).

The capability names are the ones action_gate.classify_mcp_tool uses ("serial.<fn>").
Tools not in ENERGIZING pass straight through; which of them are reachable at all is the guard's
allowlist (serial-allow.txt), not this file.
"""
import os
import sys
import warnings

warnings.filterwarnings("ignore")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))  # energize_confirm.py sits next to us (-I drops it)

ENERGIZING = frozenset({
    "write", "transact", "capture_boot", "send_break", "set_dtr_rts",
    "set_flow_control", "reconfigure", "flush", "clear_log",
})
TOKEN_ARG = "confirm_token"
TOKEN_SCHEMA = {
    "type": "string",
    "description": ("Operator-issued energize-confirm token (single-use, 5 min). Frank mints it at a "
                    "terminal: python3 /home/lumen/foreman/harness/energize_confirm.py issue serial.<tool>. "
                    "Required: this tool drives the target (hal-spec.md 9.2a)."),
}


def with_token_param(schema):
    s = dict(schema or {})
    props = dict(s.get("properties") or {})
    props[TOKEN_ARG] = TOKEN_SCHEMA
    s["properties"] = props
    s.setdefault("type", "object")
    return s


def make_gate(verify):
    from fastmcp.exceptions import ToolError
    from fastmcp.server.middleware import Middleware
    import mcp.types as mt

    class EnergizeGate(Middleware):
        async def on_list_tools(self, context, call_next):
            tools = await call_next(context)
            return [t.model_copy(update={"parameters": with_token_param(t.parameters)})
                    if t.name in ENERGIZING else t for t in tools]

        async def on_call_tool(self, context, call_next):
            name = context.message.name
            args = dict(context.message.arguments or {})
            token = args.pop(TOKEN_ARG, None)
            if name in ENERGIZING:
                cap = f"serial.{name}"
                if not isinstance(token, str) or not verify(token, cap):
                    raise ToolError(
                        f"{name} energizes the target and needs a valid operator-issued {TOKEN_ARG} "
                        f"(capability={cap}); none given, expired, used, or minted for another capability. "
                        f"Ask Frank to run at a terminal: python3 /home/lumen/foreman/harness/energize_confirm.py "
                        f"issue {cap}")
            elif token is not None:
                raise ToolError(f"{name} takes no {TOKEN_ARG}")
            msg = mt.CallToolRequestParams(name=name, arguments=args)
            return await call_next(context.copy(message=msg))

    return EnergizeGate()


def build(url, verify):
    from fastmcp import FastMCP
    from fastmcp.client.transports import StreamableHttpTransport
    from fastmcp.server.proxy import ProxyClient
    server = FastMCP.as_proxy(ProxyClient(StreamableHttpTransport(url)), name="serial-maksoor-bridge")
    server.add_middleware(make_gate(verify))
    return server


def main(argv):
    if len(argv) != 1 or not argv[0].startswith(("http://", "https://")):
        raise SystemExit("usage: http_stdio_bridge.py URL")
    from energize_confirm import verify_token
    build(argv[0], verify_token).run(transport="stdio", show_banner=False)


if __name__ == "__main__":
    main(sys.argv[1:])
