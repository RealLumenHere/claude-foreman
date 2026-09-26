#!/usr/bin/env python3
"""wq#2751 test-to-failure for the listener lockdown. Side ports only, unless --live.

  python3 test_wq2751.py            staged artefacts: preload, supergateway E2E, projects_app
  python3 test_wq2751.py --baseline same checks with the fix removed; every lockdown check must FAIL
  python3 test_wq2751.py --live     probe the live listeners on this host's tailnet address

"Refused on the tailnet address" is checked from this host: a loopback bind refuses a connect to
the tailnet IP from anywhere, so it holds from a peer too. --live only opens TCP connections to
3103/3104 and sends no MCP traffic (a stray request crashed memory-mcp during the audit).
"""
import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
PRELOAD = os.path.join(HERE, "loopback-listen.cjs")
PROJECTS_APP = os.path.join(HERE, "projects_app.py")
SUPERGATEWAY = "/home/lumen/.npm-global/bin/supergateway"
FIXTURE_SERVER = f"{sys.executable} {os.path.join(HERE, 'fixture_mcp_stdio.py')}"
RESULTS = []


def tailnet_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("100.100.100.100", 53))
        ip = s.getsockname()[0]
    finally:
        s.close()
    if not ip.startswith("100."):
        sys.exit(f"no tailnet address on this host (got {ip})")
    return ip


def check(name, got, want):
    ok = got == want
    RESULTS.append(ok)
    print(f"{'PASS' if ok else 'FAIL'} {name} (got={got!r} want={want!r})")


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def reachable(host, port):
    try:
        socket.create_connection((host, port), timeout=2).close()
        return True
    except OSError:
        return False


def wait_up(port, deadline=15.0):
    end = time.monotonic() + deadline
    while time.monotonic() < end:
        if reachable("127.0.0.1", port):
            return True
        time.sleep(0.2)
    return False


def http(url, body=None, headers=None):
    req = urllib.request.Request(url, body, headers or {})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, r.headers, r.read().decode(errors="replace")
    except urllib.error.HTTPError as e:
        return e.code, e.headers, ""


def node_env(port, preload):
    env = {k: v for k, v in os.environ.items() if k not in ("NODE_OPTIONS", "LOOPBACK_LISTEN_PORT")}
    if preload:
        env["NODE_OPTIONS"] = f"--require={PRELOAD}"
        env["LOOPBACK_LISTEN_PORT"] = str(port)
    return env


def preload_cases(ts, preload):
    for label, val in (("unset", None), ("garbage", "abc"), ("zero", "0"), ("huge", "70000")):
        env = node_env(1, True)
        env.pop("LOOPBACK_LISTEN_PORT")
        if val is not None:
            env["LOOPBACK_LISTEN_PORT"] = val
        if not preload:
            env.pop("NODE_OPTIONS")
        rc = subprocess.run(["node", "-e", ""], env=env, capture_output=True, timeout=20).returncode
        check(f"preload fails closed, port {label}", rc, 78)

    port, other = free_port(), free_port()
    shapes = {
        "listen(port)": f"s.listen({port})",
        "listen(port, cb)": f"s.listen({port}, () => {{}})",
        "listen(port, '0.0.0.0')": f"s.listen({port}, '0.0.0.0')",
        "listen({port})": f"s.listen({{port: {port}}})",
    }
    for label, call in shapes.items():
        js = f"const s=require('http').createServer((q,r)=>r.end('ok'));{call};setTimeout(()=>process.exit(0),20000)"
        p = subprocess.Popen(["node", "-e", js], env=node_env(port, preload),
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            wait_up(port)
            check(f"{label} answers on 127.0.0.1", reachable("127.0.0.1", port), True)
            check(f"{label} refused on tailnet {ts}", reachable(ts, port), False)
        finally:
            p.kill()
            p.wait()
            time.sleep(0.2)

    js = f"require('http').createServer((q,r)=>r.end('ok')).listen({other});setTimeout(()=>process.exit(0),20000)"
    p = subprocess.Popen(["node", "-e", js], env=node_env(port, preload),
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        wait_up(other)
        check("a different port keeps its all-interfaces bind", reachable(ts, other), True)
    finally:
        p.kill()
        p.wait()


def supergateway_e2e(ts, preload):
    port = free_port()
    p = subprocess.Popen([SUPERGATEWAY, "--stdio", FIXTURE_SERVER, "--port", str(port),
                          "--outputTransport", "streamableHttp", "--streamableHttpPath", "/mcp",
                          "--healthEndpoint", "/health"],
                         env=node_env(port, preload), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        check("supergateway came up", wait_up(port), True)
        check(f"supergateway refused on tailnet {ts}", reachable(ts, port), False)
        hdr = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
        init = {"jsonrpc": "2.0", "id": 1, "method": "initialize",
                "params": {"protocolVersion": "2024-11-05", "capabilities": {},
                           "clientInfo": {"name": "wq2751-test", "version": "0"}}}
        status, _, body = http(f"http://127.0.0.1:{port}/mcp", json.dumps(init).encode(), hdr)
        check("MCP initialize over 127.0.0.1", status, 200)
        check("initialize reaches the stdio child", "wq2751-fixture" in body, True)
        lst = {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}
        status, _, body = http(f"http://127.0.0.1:{port}/mcp", json.dumps(lst).encode(), hdr)
        check("tools/list over 127.0.0.1", "wq2751_echo" in body, True)
    finally:
        p.kill()
        p.wait()


def projects_app(ts, fixed):
    port = free_port()
    env = dict(os.environ, PROJECTS_APP_PORT=str(port))
    if not fixed:
        env["PROJECTS_APP_BIND"] = "0.0.0.0"
    p = subprocess.Popen([sys.executable, PROJECTS_APP], env=env,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        check("projects_app came up", wait_up(port), True)
        check(f"projects_app refused on tailnet {ts}", reachable(ts, port), False)
        check("/api/projects still served", http(f"http://127.0.0.1:{port}/api/projects")[0], 200)
        handoffs = sorted(f[:-3] for f in os.listdir("/home/lumen/projects/_handoffs") if f.endswith(".md"))
        if handoffs:
            check(f"real handoff {handoffs[0]} still served",
                  http(f"http://127.0.0.1:{port}/api/handoff/{handoffs[0]}")[0], 200)
        if fixed:
            trav = curl_path_as_is(f"http://127.0.0.1:{port}/api/handoff/../../kb/ISSUE-LOG")
            check("handoff traversal to /home/lumen/kb/ISSUE-LOG.md", trav, "404")
    finally:
        p.kill()
        p.wait()


def curl_path_as_is(url):
    # urllib and requests both collapse ../ before sending, which would hide the traversal
    return subprocess.run(["curl", "-s", "-m", "5", "--path-as-is", "-o", "/dev/null", "-w", "%{http_code}", url],
                          capture_output=True, text=True).stdout


def live(ts):
    check("live chrome-mcp :3103 refused on tailnet", reachable(ts, 3103), False)
    check("live memory-mcp :3104 refused on tailnet", reachable(ts, 3104), False)
    check("live chrome-mcp :3103 still on 127.0.0.1", reachable("127.0.0.1", 3103), True)
    check("live memory-mcp :3104 still on 127.0.0.1", reachable("127.0.0.1", 3104), True)
    check("live projects-app :8124 refused on tailnet", reachable(ts, 8124), False)
    check("live projects-app handoff traversal on 127.0.0.1",
          curl_path_as_is("http://127.0.0.1:8124/api/handoff/../../kb/ISSUE-LOG"), "404")


def main():
    ts = tailnet_ip()
    mode = sys.argv[1] if len(sys.argv) > 1 else ""
    if mode == "--live":
        live(ts)
    else:
        fixed = mode != "--baseline"
        preload_cases(ts, fixed)
        supergateway_e2e(ts, fixed)
        projects_app(ts, fixed)
    failed = RESULTS.count(False)
    print(f"{len(RESULTS) - failed}/{len(RESULTS)} passed")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
