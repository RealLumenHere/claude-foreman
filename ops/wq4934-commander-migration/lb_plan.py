#!/usr/bin/env python3
"""lb_plan — Cloudflare Load Balancer for the Commander dual-tunnel failover (wq#4934).

One load balancer on the hostname mcpw already calls, two pools, one tunnel per pool,
steering "off": all traffic goes to the first pool while it is healthy and to the second
when it is not. Subcommands:

  plan     Offline. Print the monitor, pool and load balancer request bodies.
  check    Read-only GETs. Exit 0 iff the live objects match the plan and both pools
           report healthy.
  apply    Create or update the monitor, both pools and the load balancer. A new load
           balancer is created DISABLED, so the existing DNS record keeps serving.
  enable   Cutover: enable the load balancer. Refuses unless `check` would pass.
  disable  Rollback: disable it. The existing DNS record serves again.

apply, enable and disable change the live control plane and refuse to run without
--attended (lumen_state commander.rule_no_unattended). The API token is read from
--token-file, which must be mode 0600. It is never taken from argv or the environment
and never printed.

    python3 lb_plan.py plan --config lb_config.json
    python3 lb_plan.py check --config lb_config.json --token-file <path> --expect disabled
"""

import argparse
import json
import os
import re
import stat
import sys
import urllib.error
import urllib.request

API = "https://api.cloudflare.com/client/v4"
HEX32_RE = re.compile(r"^[0-9a-f]{32}$")
UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
HOST_RE = re.compile(r"^(?=.{1,253}$)([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,}$")
MONITOR_DESCRIPTION = "wq4934 commander gateway health"

# What `check` compares. The failover design rests on these fields; the rest of each
# body (timings, descriptions) is tuning that `apply` sets but a drift there is not a fault.
MONITOR_KEYS = ("type", "method", "path", "port", "header", "expected_codes")
POOL_KEYS = ("enabled", "minimum_origins", "monitor", "origins")
LB_KEYS = ("name", "enabled", "proxied", "steering_policy", "default_pools", "fallback_pool")


class ConfigError(ValueError):
    pass


class CfError(RuntimeError):
    pass


def validate_config(cfg):
    """Return the config with defaults filled in, or raise ConfigError listing every problem."""
    problems = []
    for key in ("account_id", "zone_id"):
        if not HEX32_RE.match(str(cfg.get(key, ""))):
            problems.append(f"{key}: expected 32 hex characters")
    lb_hostname = str(cfg.get("lb_hostname", ""))
    if not HOST_RE.match(lb_hostname):
        problems.append("lb_hostname: not a hostname")
    host_header = str(cfg.get("origin_host_header") or lb_hostname)
    if not HOST_RE.match(host_header):
        problems.append("origin_host_header: not a hostname")
    health_path = str(cfg.get("health_path") or "/health")
    if not health_path.startswith("/"):
        problems.append("health_path: must start with /")
    expected_codes = str(cfg.get("expected_codes") or "200")
    if not re.match(r"^[1-5]([0-9]{2}|xx)$", expected_codes):
        problems.append("expected_codes: one status such as 200, or a class such as 2xx")
    interval_s = cfg.get("interval_s", 60)
    if not isinstance(interval_s, int) or isinstance(interval_s, bool) or interval_s <= 0:
        problems.append("interval_s: positive integer of seconds")

    pools = cfg.get("pools")
    if not isinstance(pools, list) or len(pools) != 2:
        problems.append("pools: exactly two (primary first, standby second)")
        pools = []
    for i, pool in enumerate(pools):
        for key in ("name", "endpoint_name"):
            if not re.match(r"^[A-Za-z0-9_-]{1,64}$", str(pool.get(key, ""))):
                problems.append(f"pools[{i}].{key}: 1-64 of letters, digits, - or _")
        if not UUID_RE.match(str(pool.get("tunnel_id", ""))):
            problems.append(f"pools[{i}].tunnel_id: not a tunnel UUID")
    if len(pools) == 2:
        if pools[0].get("name") == pools[1].get("name"):
            problems.append("pools: names must differ")
        # The load balancer treats every replica of one tunnel as a single endpoint, so
        # two pools on one UUID would give no failover between the two hosts.
        if pools[0].get("tunnel_id") == pools[1].get("tunnel_id"):
            problems.append("pools: both pools use the same tunnel_id; each host needs its own tunnel")
    if problems:
        raise ConfigError("; ".join(problems))
    return {**cfg, "origin_host_header": host_header, "health_path": health_path,
            "expected_codes": expected_codes, "interval_s": interval_s}


def load_config(path):
    with open(path, encoding="utf-8") as f:
        return validate_config(json.load(f))


def monitor_body(cfg):
    return {
        "type": "https",
        "description": MONITOR_DESCRIPTION,
        "method": "GET",
        "path": cfg["health_path"],
        "port": 443,
        "header": {"Host": [cfg["origin_host_header"]]},
        "expected_codes": cfg["expected_codes"],
        "interval": cfg["interval_s"],
        "timeout": 5,
        "retries": 2,
        "consecutive_down": 2,
        "consecutive_up": 3,
        "follow_redirects": False,
        "allow_insecure": False,
    }


def pool_body(cfg, index, monitor_id):
    pool = cfg["pools"][index]
    return {
        "name": pool["name"],
        "description": f"wq4934 commander via {pool['endpoint_name']}",
        "enabled": True,
        "minimum_origins": 1,
        "monitor": monitor_id,
        "origins": [{
            "name": pool["endpoint_name"],
            "address": f"{pool['tunnel_id']}.cfargotunnel.com",
            "enabled": True,
            "weight": 1,
            "header": {"Host": [cfg["origin_host_header"]]},
        }],
    }


def lb_body(cfg, pool_ids, enabled):
    return {
        "name": cfg["lb_hostname"],
        "description": "wq4934 commander: primary pool, then standby",
        "enabled": enabled,
        "proxied": True,
        "steering_policy": "off",
        "session_affinity": "none",
        "default_pools": list(pool_ids),
        "fallback_pool": pool_ids[-1],
    }


def build_plan(cfg):
    """The request bodies with symbolic ids, for review before anything is created."""
    refs = [f"<id of pool {p['name']}>" for p in cfg["pools"]]
    return {
        "monitor": monitor_body(cfg),
        "pools": [pool_body(cfg, i, "<id of monitor>") for i in range(2)],
        "load_balancer": lb_body(cfg, refs, False),
    }


def read_token(path):
    try:
        st = os.stat(path)
        if not stat.S_ISREG(st.st_mode) or st.st_mode & 0o077:
            raise SystemExit(f"{path}: token file must be a regular file with mode 0600")
        with open(path, encoding="utf-8") as f:
            token = f.read().strip()
    except OSError as e:
        raise SystemExit(f"{path}: {e.strerror}") from e
    if not token:
        raise SystemExit(f"{path}: token file is empty")
    return token


def http_caller(token, timeout_s=20.0):
    def call(method, path, body=None):
        req = urllib.request.Request(
            API + path, method=method,
            data=None if body is None else json.dumps(body).encode(),
            headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=timeout_s) as resp:
                payload = json.load(resp)
        except urllib.error.HTTPError as e:
            try:
                payload = json.load(e)
            except ValueError:
                payload = {"errors": [{"code": e.code, "message": e.reason}]}
        except (urllib.error.URLError, OSError, ValueError) as e:
            raise CfError(f"{method} {path}: {e}") from e
        if not payload.get("success"):
            raise CfError(f"{method} {path}: {payload.get('errors')}")
        return payload.get("result")
    return call


def _paths(cfg):
    acct = f"/accounts/{cfg['account_id']}/load_balancers"
    return f"{acct}/monitors", f"{acct}/pools", f"/zones/{cfg['zone_id']}/load_balancers"


def _find(items, key, value):
    return next((it for it in items or [] if it.get(key) == value), None)


def _pick(body, keys):
    return {k: body[k] for k in keys}


def mismatches(expected, actual, path=""):
    """Paths where `actual` differs from `expected`. Keys only in `actual` are ignored,
    because the API adds ids, timestamps and defaults we do not set."""
    if isinstance(expected, dict):
        if not isinstance(actual, dict):
            return [f"{path or '.'}: expected an object"]
        out = []
        for key, want in expected.items():
            out += mismatches(want, actual.get(key), f"{path}.{key}" if path else key)
        return out
    if isinstance(expected, list):
        if not isinstance(actual, list) or len(actual) != len(expected):
            return [f"{path}: expected {len(expected)} item(s)"]
        out = []
        for i, want in enumerate(expected):
            out += mismatches(want, actual[i], f"{path}[{i}]")
        return out
    return [] if expected == actual else [f"{path}: want {expected!r}, live {actual!r}"]


def _collect(node, key, kind):
    """Every value of type `kind` stored under `key` in a response, wherever it is nested."""
    if isinstance(node, dict):
        out = [v for k, v in node.items() if k == key and isinstance(v, kind)]
        for v in node.values():
            out += _collect(v, key, kind)
        return out
    if isinstance(node, list):
        return [found for item in node for found in _collect(item, key, kind)]
    return []


def pool_health(call, cfg, pool_id):
    """(healthy, detail). No health data at all counts as not healthy."""
    report = call("GET", f"{_paths(cfg)[1]}/{pool_id}/health")
    flags = _collect(report, "healthy", bool)
    if not flags:
        return False, "no health data yet"
    detail = f"{sum(flags)}/{len(flags)} health reports healthy"
    reasons = sorted({r for r in _collect(report, "failure_reason", str) if r})
    if reasons and not all(flags):
        detail += f" ({'; '.join(reasons)})"
    return all(flags), detail


def live_state(call, cfg):
    monitors, pools, lbs = _paths(cfg)
    pool_list = call("GET", pools)
    return {
        "monitor": _find(call("GET", monitors), "description", MONITOR_DESCRIPTION),
        "pools": [_find(pool_list, "name", p["name"]) for p in cfg["pools"]],
        "lb": _find(call("GET", lbs), "name", cfg["lb_hostname"]),
    }


def check(call, cfg, expect_enabled=None, health_gate=True):
    """Problems that make the live load balancer differ from the plan. Empty means OK."""
    live = live_state(call, cfg)
    problems = []
    if live["monitor"] is None:
        return [f"monitor {MONITOR_DESCRIPTION!r} not found"]
    want = _pick(monitor_body(cfg), MONITOR_KEYS)
    problems += [f"monitor {m}" for m in mismatches(want, live["monitor"])]
    for i, pool in enumerate(live["pools"]):
        name = cfg["pools"][i]["name"]
        if pool is None:
            problems.append(f"pool {name} not found")
            continue
        want = _pick(pool_body(cfg, i, live["monitor"]["id"]), POOL_KEYS)
        problems += [f"pool {name} {m}" for m in mismatches(want, pool)]
        if health_gate:
            healthy, detail = pool_health(call, cfg, pool["id"])
            if not healthy:
                problems.append(f"pool {name} not healthy: {detail}")
    if None in live["pools"]:
        return problems
    if live["lb"] is None:
        return problems + [f"load balancer {cfg['lb_hostname']} not found"]
    enabled = live["lb"].get("enabled") if expect_enabled is None else expect_enabled
    want = _pick(lb_body(cfg, [p["id"] for p in live["pools"]], enabled), LB_KEYS)
    return problems + [f"load balancer {m}" for m in mismatches(want, live["lb"])]


def _upsert(call, base, existing, body):
    if existing is None:
        return call("POST", base, body), "created"
    if mismatches(body, existing):
        return call("PUT", f"{base}/{existing['id']}", body), "updated"
    return existing, "unchanged"


def apply(call, cfg):
    """Create or update monitor, pools and load balancer. Returns [(object, action)].
    Never changes `enabled` on an existing load balancer; a new one starts disabled."""
    monitors, pools, lbs = _paths(cfg)
    live = live_state(call, cfg)
    actions = []
    monitor, action = _upsert(call, monitors, live["monitor"], monitor_body(cfg))
    actions.append(("monitor", action))
    pool_ids = []
    for i, existing in enumerate(live["pools"]):
        pool, action = _upsert(call, pools, existing, pool_body(cfg, i, monitor["id"]))
        pool_ids.append(pool["id"])
        actions.append((f"pool {cfg['pools'][i]['name']}", action))
    enabled = bool(live["lb"] and live["lb"].get("enabled"))
    _, action = _upsert(call, lbs, live["lb"], lb_body(cfg, pool_ids, enabled))
    actions.append((f"load balancer {cfg['lb_hostname']}", action))
    return actions


def set_enabled(call, cfg, enabled, health_gate=True):
    """Enable (cutover) or disable (rollback). Enabling requires the plan to match first."""
    if enabled:
        problems = check(call, cfg, expect_enabled=None, health_gate=health_gate)
        if problems:
            raise CfError("refusing to enable: " + "; ".join(problems))
    lb = live_state(call, cfg)["lb"]
    if lb is None:
        raise CfError(f"load balancer {cfg['lb_hostname']} not found")
    call("PATCH", f"{_paths(cfg)[2]}/{lb['id']}", {"enabled": enabled})


def main(argv=None, caller=http_caller):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("plan", "check", "apply", "enable", "disable"):
        sp = sub.add_parser(name)
        sp.add_argument("--config", required=True)
        if name == "plan":
            continue
        sp.add_argument("--token-file", required=True, help="0600 file holding the API token")
        if name == "check":
            sp.add_argument("--expect", choices=("enabled", "disabled"))
        else:
            sp.add_argument("--attended", action="store_true",
                            help="Frank is driving this session (required)")
        if name in ("check", "enable"):
            sp.add_argument("--no-health-gate", action="store_true",
                            help="skip the pool health check (confirm it in the dashboard instead)")
    args = ap.parse_args(argv)

    try:
        cfg = load_config(args.config)
    except (OSError, ValueError) as e:
        print(f"config: {e}", file=sys.stderr)
        return 2
    if args.cmd == "plan":
        print(json.dumps(build_plan(cfg), indent=2))
        return 0
    if args.cmd != "check" and not args.attended:
        print(f"{args.cmd} changes the live control plane: re-run with --attended "
              "while Frank is driving", file=sys.stderr)
        return 2

    call = caller(read_token(args.token_file))
    try:
        if args.cmd == "check":
            expect = None if args.expect is None else args.expect == "enabled"
            problems = check(call, cfg, expect, not args.no_health_gate)
            for p in problems:
                print(f"FAIL {p}")
            if not problems:
                print("OK   live load balancer matches the plan")
            return 1 if problems else 0
        if args.cmd == "apply":
            for obj, action in apply(call, cfg):
                print(f"{action:9} {obj}")
        elif args.cmd == "enable":
            set_enabled(call, cfg, True, not args.no_health_gate)
            print(f"enabled   load balancer {cfg['lb_hostname']}")
        else:
            set_enabled(call, cfg, False)
            print(f"disabled  load balancer {cfg['lb_hostname']}")
    except CfError as e:
        print(f"FAIL {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
