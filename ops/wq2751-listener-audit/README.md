# wq#2751 Lumen listeners reachable from the tailnet

`ts-input` accepts everything on tailscale0, so every `0.0.0.0`/`*` listener is open to every tailnet
peer and every local container, whatever ufw says. Audited 2026-09-26 01:30-02:00Z: source read,
plus probes of each port on the tailnet address 100.117.66.102.

## Findings

| Port | Service (unit) | Write/exec surface | Auth | Consumer | Verdict |
|---|---|---|---|---|---|
| 3103 | chrome-mcp supergateway | **`use_browser`: navigate (incl. `file://`) + `eval` JS as lumen.** Reads any lumen-readable file (`~/.secrets`), SSRF to every loopback service, including 3100 desktop-commander (shell), which gets around 3100's lo-only lockdown | none. Anonymous `initialize`+`tools/list` = 200 from the TS IP | CF `chrome-mcp.lumennode.uk` → localhost:3103 | **CRITICAL. Staged: loopback** |
| 3104 | memory-mcp supergateway | create/delete_entities on the knowledge graph | none (unit Description claims 127.0.0.1, but it binds `*`) | CF `memory-mcp.lumennode.uk` → localhost | **HIGH. Staged: loopback.** Also broken, see below |
| 8124 | projects-app | `/api/handoff/<pid>` path traversal: `../../kb/ISSUE-LOG` returned 200, 1,305,797 bytes. Reads any lumen-readable `*.md` | none on the tailnet. CF hostname is behind Access (302) | CF `projects.lumennode.uk` → localhost | **HIGH. Staged: traversal fix + loopback** |
| 8765 | lumen-messenger | deny-by-default cookie middleware. WS gated separately. `/ghost-webhook` is a public POST that spawns `lumen_indexer.py` on every call and posts an attacker-chosen title to ntfy | cookie, except `/ghost-webhook` | CF chat/copilot → localhost, Thor bars (tailnet telemetry allowlist) | control plane. Webhook filed as a follow-up |
| 3100 | desktop-commander supergateway | full shell | none in supergateway | CF `mcp-backend` | ticket records an lo-only firewall lockdown. Not verifiable by a worker (rules.v4 is root 0600). Reachable through 3103 until 3103 is locked |
| 3333 | mcp-gateway | run_shell | bearer (wq#2748) | | fixed in wq#2748 |
| 7070 | fleet-status | none: GET `/status`, fixed shell probes | none | projects-app at 127.0.0.1:7070 | read-only host metrics. Left as is |
| 8123 | lumen-dashboard | none: GET + WS. `/api/v1/node/{name}` runs a fixed ssh probe to a named fleet node | none | tailnet browser (no CF hostname) | read-only info disclosure. Left as is (loopback would kill its only consumer) |
| 8145 | lumen-copilot-status | none: GET `/`, `/api/state`, `/api/thor` | none | tailnet | read-only. Left as is |
| 8871 | lumen-kiosk-idle (lpp-cover-serve) | none: SimpleHTTPRequestHandler over `pokemon/assets` (traversal-safe) | none | CF `pokemon.lumennode.uk` → localhost | read-only, LPP decommissioned. Low |
| 5678 | n8n | workflows | n8n login: `/rest/workflows` and `/api/v1/workflows` = 401 | CF | OK |
| 8090 | beszel hub | | PocketBase rules: record lists come back empty to anon | CF | OK |
| 45876 | beszel agent | | SSH key | hub | OK |
| 9090 | cockpit.socket | | PAM login | | OK |

Found outside the ticket's list: ollama `*:11434` answers anonymously (pull/delete/create model API),
and docker-published 3080, 5006, 5959, 8080, 8082, 8418 were not audited in depth. Filed as a follow-up.

**memory-mcp is dead.** `/home/lumen/.npm-global/bin/mcp-server-memory` does not exist and
`@modelcontextprotocol/server-memory` is not installed. Every request makes stateless supergateway
spawn a missing child and die on an unhandled `write EPIPE`. One anonymous `initialize` from the TS IP
crashed it during this audit: crash at 01:36:08, systemd restart at 01:36:13 (NRestarts=2). That is
the only `write EPIPE` in 3 days of journal, so no client has used it recently.

## Fix

supergateway 3.4.3 has no host option (`app.listen(port)` in `dist/gateways/*StreamableHttp.js`).
`loopback-listen.cjs` is a `NODE_OPTIONS=--require` preload that rewrites a `listen()` on
`LOOPBACK_LISTEN_PORT` to 127.0.0.1. It covers `listen(port)`, `(port, cb)`, `(port, host)` and
`({port})`. Listens on any other port are left alone, so children that inherit NODE_OPTIONS keep their
own binds. A missing or malformed port env exits 78, and the drop-in sets RestartPreventExitStatus=78.
cloudflared reaches the loopback bind unchanged: `localhost` resolves to 127.0.0.1 only on this host.

`projects_app.py` is the live file (sha256 `1ead5ab3…06dd` at copy time) with the handoff id held to
`[A-Za-z0-9_-]{1,128}` and the bind moved to `PROJECTS_APP_BIND` (default 127.0.0.1) /
`PROJECTS_APP_PORT`.

## Test

`python3 test_wq2751.py` gives 23/23. `--baseline` (fix removed) gives 10 FAIL. `--live` before deploy
gives 4 FAIL: 3103, 3104 and 8124 accept on the TS IP, and the traversal returns 200. After both parts
of `apply.sh`, `--live` must show 6/6. The supergateway check runs a real supergateway through a full
MCP initialize + tools/list, with a committed stub child (`fixture_mcp_stdio.py`).

## Deploy

Needs root, and the build worker has none (guard blocks sudo). `apply.sh projects` can run unattended.
`apply.sh control-plane` is CONTROL-PLANE RULE 3: Frank drives it, then runs a full MCP initialize
through chrome-mcp.lumennode.uk and memory-mcp.lumennode.uk. memory-mcp's E2E cannot pass until
server-memory is reinstalled.
