# wq#2748 mcp-gateway fail-closed auth

Defect was bigger than the ticket: auth() in /home/lumen/agent/mcp_gateway.py was dead code
(`if False`) and attached to no route, so POST /mcp incl. run_shell (shell=True as user lumen)
was open on 0.0.0.0:3333 to the whole tailnet and every local container. The hardcoded default
token was never even checked.

Fix: no default; token read from /home/lumen/.secrets/mcp-gateway.env (0600) via unit drop-in
/etc/systemd/system/mcp-gateway.service.d/10-wq2748-token.conf. Startup exits 78 if the token is
unset, <32 chars, or the burned literal; RestartPreventExitStatus=78 stops a crash loop.
Constant-time bearer check on GET+POST /mcp. /health stays open.

Test: test_failclosed.sh [gw.py] [port] -> 11/11 on patched, 8 FAIL on the original.
Rollback: cp /home/lumen/agent/mcp_gateway.py.bak.20260926_003550 back (original also in /home/lumen/backups/wq2748_wq2713_orig/), rm the drop-in,
daemon-reload, restart mcp-gateway.
