# mcpw control-plane outage 2026-09-25 (~2.5h)
Impact: Claude lost Commander (mcpw) for ~2.5h; Frank recovered by hand over SSH from Thor.
Chain: (1) wq#2610 HA worker deployed; its pool/router called deps.fetchFn() with wrong `this` ("Illegal
invocation") so no backend call could ever succeed; the drill wq#2612, dispatched UNATTENDED, put it on the live
path. (2) Rollback kept new vars -> 1101. (3) Claude misread Access state with a deploy token lacking Access
scope (empty list, not error), had Frank delete the working mcpw Access app. (4) First auth bypass left
/authorize open to anyone; closed with AUTHORIZE_PASSPHRASE form. (5) Wrong-this fetch fixed -> (0,deps.fetchFn)().
Verified with full register->authorize->token->MCP initialize (/tmp/mcpw_diag.py).
Rules added (claude_memory.md): fallback Commander always live; backup 2 weeks behind on own window; nothing
touches the control plane unattended; health = full MCP initialize.
Follow-ups: P1 two-node Commander; 2606 watchdog failed to act; rebuild CF Access for mcpw; wq#2610 HA code
had no test catching the fetch-this bug; mcp_gateway.py hardcoded default token.
