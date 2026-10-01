#!/usr/bin/env python3
"""commander-pool-failover — health-check/failover loop for the Commander backend pool.

wq#3294 item B. Runs on Bes (must survive Lumen's death). Probes every pool backend
with the real liveness check — POST /mcp JSON-RPC tools/list, the wq#2606 watchdog's
proven probe — applies hysteresis, and on a demote/promote transition runs the
configured hook commands (page now; Worker pool-membership edit once wq#3294-A lands).

Config (JSON):
{
  "interval_s": 30,
  "probe_timeout_s": 10,
  "fail_threshold": 3,
  "rise_threshold": 2,
  "backends": [
    {"name": "lumen-dc", "url": "http://localhost:3100/mcp"},
    {"name": "bes-dc",   "url": "https://mcp-bes.lumennode.uk/mcp"}
  ],
  "on_demote": ["/home/bes/bin/page-commander-pool.sh", "{name}", "demote"],
  "on_promote": ["/home/bes/bin/page-commander-pool.sh", "{name}", "promote"]
}

State is persisted after every tick so a restart never re-fires old transitions.
Stdlib only; `--once` supports timer-driven operation.
"""

import argparse
import json
import subprocess
import sys
import time
import urllib.error
import urllib.request

PROBE_BODY = json.dumps(
    {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}
).encode()
PROBE_HEADERS = {
    "Content-Type": "application/json",
    "Accept": "application/json, text/event-stream",
}


def probe_backend(url: str, timeout_s: float) -> bool:
    """True iff the backend answers tools/list with a JSON-RPC result.

    Accepts both plain-JSON and SSE-framed responses (supergateway stateless
    streamable-HTTP replies with text/event-stream). A 2xx with an error body
    is NOT healthy — a crash-looping DC behind a live gateway answers 200s
    with errors, which is exactly the outage mode this loop must catch.
    """
    req = urllib.request.Request(url, data=PROBE_BODY, headers=PROBE_HEADERS)
    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            if not 200 <= resp.status < 300:
                return False
            body = resp.read(65536).decode("utf-8", "replace")
    except (urllib.error.URLError, OSError, ValueError):
        return False
    return '"result"' in body and '"error"' not in body.split('"result"')[0]


def run_hook(template, name, url, event):
    """Run a hook command list with {name}/{url}/{event} substituted. Never raises."""
    if not template:
        return
    cmd = [a.format(name=name, url=url, event=event) for a in template]
    try:
        subprocess.run(cmd, timeout=60, check=False)
    except (OSError, subprocess.TimeoutExpired) as e:
        print(f"hook failed for {name}/{event}: {e}", file=sys.stderr)


def tick(config, state, probe=probe_backend, hook=run_hook):
    """One probe round. Mutates state, returns [(name, 'demote'|'promote'), ...]."""
    events = []
    for b in config["backends"]:
        name, url = b["name"], b["url"]
        s = state.setdefault(name, {"healthy": True, "fails": 0, "rises": 0})
        if probe(url, config.get("probe_timeout_s", 10)):
            s["fails"] = 0
            s["rises"] += 1
            if not s["healthy"] and s["rises"] >= config.get("rise_threshold", 2):
                s["healthy"] = True
                events.append((name, "promote"))
                hook(config.get("on_promote"), name, url, "promote")
        else:
            s["rises"] = 0
            s["fails"] += 1
            if s["healthy"] and s["fails"] >= config.get("fail_threshold", 3):
                s["healthy"] = False
                events.append((name, "demote"))
                hook(config.get("on_demote"), name, url, "demote")
    return events


def load_json(path, default):
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def save_state(path, state):
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(state, f, indent=1)
    import os

    os.replace(tmp, path)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--config", required=True)
    ap.add_argument("--state", required=True)
    ap.add_argument("--once", action="store_true", help="single tick (timer mode)")
    args = ap.parse_args(argv)

    config = load_json(args.config, None)
    if config is None or "backends" not in config:
        print(f"bad config: {args.config}", file=sys.stderr)
        return 2
    state = load_json(args.state, {})

    while True:
        for name, event in tick(config, state):
            print(f"{time.strftime('%FT%TZ', time.gmtime())} {event} {name}")
        save_state(args.state, state)
        if args.once:
            return 0
        time.sleep(config.get("interval_s", 30))


if __name__ == "__main__":
    sys.exit(main())
