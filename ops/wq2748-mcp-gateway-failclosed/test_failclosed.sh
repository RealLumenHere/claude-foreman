#!/bin/bash
# wq#2748 test-to-failure for mcp_gateway.py fail-closed auth. Runs the given copy on a side port.
GW=${1:-/home/lumen/foreman/ops/wq2748-mcp-gateway-failclosed/mcp_gateway.py}; P=${2:-3399}; FAIL=0
chk(){ if [ "$2" = "$3" ]; then echo "PASS $1 ($2)"; else echo "FAIL $1 got=$2 want=$3"; FAIL=1; fi; }
for case in unset burned short; do
  case $case in unset) E=();; burned) E=(MCP_GATEWAY_TOKEN=lumen-gateway-2026);; short) E=(MCP_GATEWAY_TOKEN=abc);; esac
  env -u MCP_GATEWAY_TOKEN "${E[@]}" MCP_PORT=$P timeout 10 python3 $GW >/dev/null 2>&1; chk "startup-$case" $? 78
done
TOK=$(python3 -c 'import secrets;print(secrets.token_urlsafe(40))')
MCP_GATEWAY_TOKEN=$TOK MCP_PORT=$P python3 $GW >/tmp/wq2748_gw.log 2>&1 & PID=$!
for i in $(seq 1 30); do curl -s -o /dev/null http://127.0.0.1:$P/health && break; sleep 0.3; done
B='{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"run_shell","arguments":{"command":"echo PWNED"}}}'
chk health-open $(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:$P/health) 200
chk post-noauth $(curl -s -o /dev/null -w '%{http_code}' -X POST -H 'Content-Type: application/json' -d "$B" http://127.0.0.1:$P/mcp) 401
chk post-wrong $(curl -s -o /dev/null -w '%{http_code}' -X POST -H "Authorization: Bearer ${TOK}x" -H 'Content-Type: application/json' -d "$B" http://127.0.0.1:$P/mcp) 401
chk post-burned $(curl -s -o /dev/null -w '%{http_code}' -X POST -H 'Authorization: Bearer lumen-gateway-2026' -H 'Content-Type: application/json' -d "$B" http://127.0.0.1:$P/mcp) 401
chk post-nobearer $(curl -s -o /dev/null -w '%{http_code}' -X POST -H "Authorization: $TOK" -H 'Content-Type: application/json' -d "$B" http://127.0.0.1:$P/mcp) 401
chk get-noauth $(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:$P/mcp) 401
OUT=$(curl -s -X POST -H "Authorization: Bearer $TOK" -H 'Content-Type: application/json' -d "$B" http://127.0.0.1:$P/mcp)
echo "$OUT" | grep -q PWNED && chk post-goodtoken ok ok || chk post-goodtoken bad ok
chk noauth-no-exec $(grep -c PWNED /tmp/wq2748_gw.log) 0
kill $PID; wait $PID 2>/dev/null; rm -f /tmp/wq2748_gw.log
echo "RESULT: $([ $FAIL = 0 ] && echo ALL_PASS || echo FAILED)"; exit $FAIL
