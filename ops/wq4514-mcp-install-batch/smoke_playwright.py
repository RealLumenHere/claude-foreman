#!/usr/bin/env python3
"""wq#4517 acceptance + SSRF test for the playwright MCP. Canary HTTP servers listen on loopback, Lumen's
tailnet IP and its LAN IP and record every request. The browser is then pointed at them in every way an
injected page could (plain, hex/decimal/short IP forms, IPv6, a DNS name that resolves to 127.0.0.1,
forbidden schemes). PASS = zero canary hits and public sites still load. Exit 0 only if all pass."""
import asyncio, os, re, socket, subprocess, sys, threading, time, warnings
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
warnings.filterwarnings("ignore")
LAUNCHER = os.environ.get("PW_LAUNCHER", "/home/lumen/.local/share/mcp/playwright-mcp/0.0.83/launch.sh")  # PW_LAUNCHER: mutation tests only
HITS = []
HOSTS = {"loopback": "127.0.0.1", "tailnet": "100.117.66.102", "lan": "192.168.8.160"}


class Canary(BaseHTTPRequestHandler):
    def do_GET(self):
        HITS.append((self.server.label, self.path, self.headers.get("Host")))
        body = b"<html><body>CANARY-REACHED</body></html>"
        self.send_response(200); self.send_header("Content-Type", "text/html"); self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)
    do_POST = do_GET
    def log_message(self, *a): pass


def serve(label, host, v6=False):
    cls = type("S6", (ThreadingHTTPServer,), {"address_family": socket.AF_INET6}) if v6 else ThreadingHTTPServer
    srv = cls((host, 0), Canary); srv.label = label
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv.server_address[1]


def text(res):
    return " ".join(getattr(c, "text", "") or "" for c in res.content)


async def main():
    from fastmcp import Client
    from fastmcp.client.transports import StdioTransport
    ports = {k: serve(k, h) for k, h in HOSTS.items()}
    try: p6 = serve("ipv6-loopback", "::1", v6=True)
    except OSError: p6 = None
    fails = []

    def check(label, ok, detail=""):
        print(f"{'PASS' if ok else 'FAIL'}  {label}  {detail}")
        if not ok: fails.append(label)
    async with Client(StdioTransport(command=LAUNCHER, args=[], env=dict(os.environ)), timeout=180) as c:
        tools = sorted(t.name for t in await c.list_tools())
        check("tools/list through guard", len(tools) == 19 and "browser_run_code_unsafe" not in tools and "browser_evaluate" not in tools, f"{len(tools)} tools")
        async def nav(url):
            return await c.call_tool("browser_navigate", {"url": url}, raise_on_error=False)
        # ---- positive: public web works through the egress proxy
        r = await nav("https://example.com")
        check("public https via egress proxy: example.com", (not r.is_error) and "Example Domain" in text(r), re.sub(r"\s+", " ", text(r))[:90])
        r = await c.call_tool("browser_snapshot", {}, raise_on_error=False)
        check("browser_snapshot returns page content", "Example Domain" in text(r), f"{len(text(r))} chars")
        r = await nav("https://kernelnewbies.org")
        check("ticket test: kernelnewbies.org content extracted", (not r.is_error) and re.search(r"kernel\s*newbies", text(r), re.I) is not None, re.sub(r"\s+", " ", text(r))[:90])
        # ---- hostile: every route to internal services
        targets = []
        for k, p in ports.items():
            h = HOSTS[k]
            targets += [(f"{k} direct", f"http://{h}:{p}/direct")]
        lp = ports["loopback"]
        targets += [("loopback hex IP", f"http://0x7f.0.0.1:{lp}/hex"), ("loopback decimal IP", f"http://2130706433:{lp}/dec"),
                    ("loopback short form", f"http://127.1:{lp}/short"), ("localhost name", f"http://localhost:{lp}/name"),
                    ("DNS name -> 127.0.0.1 (localtest.me)", f"http://localtest.me:{lp}/dns"),
                    ("IPv4-mapped IPv6", f"http://[::ffff:127.0.0.1]:{lp}/mapped"),
                    ("cloud metadata IP", "http://169.254.169.254/latest/meta-data/")]
        if p6: targets.append(("IPv6 loopback", f"http://[::1]:{p6}/v6"))
        for label, url in targets:
            before = len(HITS)
            r = await nav(url)
            check(f"BLOCKED {label}", len(HITS) == before and "CANARY-REACHED" not in text(r), f"hits +{len(HITS)-before}; {re.sub(chr(10), ' ', text(r))[:70]}")
        # ---- forbidden schemes (guard argument policy)
        for url in ("file:///etc/passwd", "chrome://version", "javascript:alert(1)", "data:text/html,CANARY-REACHED", "about:blank", "ftp://example.com/"):
            r = await nav(url)
            check(f"REFUSED scheme {url.split(':')[0]}:", r.is_error and "http(s)" in text(r), text(r)[:80])
        r = await c.call_tool("browser_tabs", {"action": "new", "url": "file:///etc/passwd"}, raise_on_error=False)
        check("REFUSED browser_tabs new file://", r.is_error and "http(s)" in text(r), text(r)[:80])
        # ---- withheld tools
        for tool, a in (("browser_run_code_unsafe", {"code": "async (page) => 1"}), ("browser_evaluate", {"function": "() => 1"}),
                        ("browser_file_upload", {"paths": ["/etc/passwd"]}), ("browser_drop", {"paths": ["/etc/passwd"]})):
            try:
                r = await c.call_tool(tool, a, raise_on_error=False); ok = r.is_error and "not permitted" in text(r)
            except Exception as exc:
                ok = True
            check(f"REFUSED {tool}", ok)
        await c.call_tool("browser_close", {}, raise_on_error=False)
    time.sleep(4)
    leaked = subprocess.run(["pgrep", "-fc", "chromium-1243/chrome-linux64/chrome.*--remote-debugging"], capture_output=True, text=True).stdout.strip()
    check("no leaked Chrome after shutdown", leaked in ("0", ""), f"pgrep count={leaked}")
    check("canary servers saw ZERO requests", not HITS, f"hits={HITS}")
    print("RESULT:", "FAIL " + ", ".join(fails) if fails else "ALL PASS")
    return 1 if fails else 0


sys.exit(asyncio.run(main()))
