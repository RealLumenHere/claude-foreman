#!/usr/bin/env python3
"""wake_judge.py — wq#3650: standalone wake-on-demand for a `wake.method: wol` registry node.

node_power.py's own wake() (dispatcher/node_power.py:380) already does this inline for the
20-minute dispatch pass: send the magic packet, poll health, flip the registry to live. This is
the SAME three steps (wakeonlan the MAC, poll ssh, dispatch) exposed as a standalone entry point
for everything that is not that pass — a human at a terminal, a cron job, or `bin/power-remediate`
coordinating a wake outside the pass cadence (NETWORK_PREMORTEM.md L4; ISSUE-LOG #compatibility
with its own WoL rung is a follow-up, not this ticket — see README.md).

Usage:  dispatcher/wake_judge.sh <node> [--send-only] [--timeout SECONDS]
  1. wakeonlan   ports.wol(mac) — mac comes from research/inference_nodes.yaml's
                 nodes.<node>.wake.mac (INFERENCE_NODE_REGISTRY.md §2).
  2. poll ssh    ports.ssh(node, "true") every SSH_POLL_S until it answers or --timeout elapses.
                 Default timeout is 2 * wake.measured_ready_s (the same multiplier node_power.py
                 uses), or DEFAULT_TIMEOUT_S when that is unset.
  3. dispatch    unless --send-only, start one immediate lumen-foreman-dispatch.service pass
                 (systemctl --user is not in play; this is the system unit) so a node that just
                 came up does not sit idle for the rest of the 20-minute cadence.

--send-only is what node_power.py's own in-pipeline wake would use if it shelled out here instead
of calling ports.wol() directly — it does not (duplicating the poll loop it already owns would be
two sources of truth for the same deadline math), so today this flag is for a caller that wants
step 1 only. Exit codes: 0 ok, 1 node unknown / mac missing / not a wol node, 2 wol send failed,
3 ssh never answered inside the timeout.
"""
import argparse
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "harness"))

SSH_POLL_S = 5
DEFAULT_TIMEOUT_S = 180


def _row(node, registry_path=None):
    import inference_registry as ir
    reg = ir.load(registry_path or ir.REGISTRY_YAML)
    row = (reg.get("nodes") or {}).get(node)
    if not isinstance(row, dict):
        raise SystemExit(f"wake_judge: unknown node {node!r} (not in {registry_path or ir.REGISTRY_YAML})")
    return row


def wake(node, ports, send_only=False, timeout=None, registry_path=None, dispatch_unit=None, sleep=time.sleep):
    """Returns (rc, message). rc matches the module docstring's exit codes."""
    row = _row(node, registry_path)
    wake_cfg = row.get("wake") or {}
    if wake_cfg.get("method") != "wol":
        return 1, f"{node}: wake.method is {wake_cfg.get('method')!r}, not 'wol'"
    mac = wake_cfg.get("mac")
    if not mac:
        return 1, f"{node}: wake.mac is unset"
    rc, out = ports.wol(mac)
    if rc != 0:
        return 2, f"{node}: wakeonlan {mac} failed (rc {rc}): {out.strip()}"
    if send_only:
        return 0, f"{node}: magic packet sent to {mac}"
    deadline = timeout
    if deadline is None:
        ready_s = wake_cfg.get("measured_ready_s")
        deadline = 2 * ready_s if isinstance(ready_s, (int, float)) and ready_s > 0 else DEFAULT_TIMEOUT_S
    t0 = time.monotonic()
    while True:
        up, _ = ports.ssh(node, "true", timeout=10)
        if up == 0:
            break
        if time.monotonic() - t0 >= deadline:
            return 3, f"{node}: ssh did not answer within {deadline}s of the magic packet"
        sleep(SSH_POLL_S)
    if dispatch_unit:
        ports.start_unit(dispatch_unit)
    return 0, f"{node}: up after {round(time.monotonic() - t0)}s, dispatch kicked"


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("node")
    p.add_argument("--send-only", action="store_true", help="step 1 only: send the magic packet, do not poll")
    p.add_argument("--timeout", type=float, default=None, help="seconds to poll ssh before giving up")
    p.add_argument("--no-dispatch", action="store_true", help="skip step 3 (kicking lumen-foreman-dispatch)")
    args = p.parse_args(argv)

    import node_ports
    ports = node_ports.SystemPorts()
    dispatch_unit = None if args.no_dispatch else "lumen-foreman-dispatch.service"
    rc, msg = wake(args.node, ports, send_only=args.send_only, timeout=args.timeout, dispatch_unit=dispatch_unit)
    print(msg)
    return 0 if rc == 0 else rc


if __name__ == "__main__":
    sys.exit(main())
