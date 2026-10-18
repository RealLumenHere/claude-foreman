#!/usr/bin/env python3
"""wq#4198 -- OpenHands sandbox egress allowlist + resource limits.

OpenHands 1.8.0 cannot do either itself (README.md "Research"): the
DockerSandboxServiceInjector has no network or limit field, and its
containers.run() passes network_mode=None (default bridge, docker0) and no
mem/cpu/pids kwargs. So this runs beside it, as root:

* Egress, fail-closed. Every packet from docker0 enters OH-SBX-SRC. Running
  docker0 containers whose name is NOT an OpenHands sandbox get a per-IP
  RETURN there (unchanged behaviour). Everything else, including a sandbox
  that has just started, falls to OH-SBX-EGRESS, which RETURNs only replies
  and new TCP to 172.17.0.1:4000 (LiteLLM) and 172.17.0.1:3000 (app
  webhook/MCP callback), then REJECTs. RETURN never accepts, so the rest of
  INPUT/FORWARD still decides; this only removes reach.
* Limits. A running sandbox with Memory/NanoCpus/PidsLimit unset gets
  `docker update` with LIMITS.

Modes: rules (print static fragment), start (unit ExecStartPre, see
start()), once [--dry-run] (reconcile), watch (once, then on every container
start event, and every RESYNC_S seconds).
"""
import argparse
import json
import select
import subprocess
import sys
import time

# docker_sandbox_service.py:598 at OpenHands tag 1.8.0 (README.md, Research).
SANDBOX_PREFIX = "oh-agent-server-"
BRIDGE_IF = "docker0"
BRIDGE_NET = "bridge"
SRC_CHAIN = "OH-SBX-SRC"
EGRESS_CHAIN = "OH-SBX-EGRESS"
COMMENT = "wq4198"
ALLOW = (("172.17.0.1", 4000, "litellm"), ("172.17.0.1", 3000, "openhands-app-callback"))
# 4 CPUs / 60 GB host; max_num_sandboxes defaults to 5 (1.8.0
# docker_sandbox_service.py:599-602), so the worst case is 30 GB / 10 CPUs of shares.
LIMITS = {"memory": "6g", "memory-swap": "6g", "cpus": "2", "pids-limit": "2048"}
RESYNC_S = 60
JUMPS = (("INPUT", ["-i", BRIDGE_IF, "-j", SRC_CHAIN]), ("DOCKER-USER", ["-i", BRIDGE_IF, "-j", SRC_CHAIN]))


def egress_rules() -> list[str]:
    rules = [f"-A {EGRESS_CHAIN} -m conntrack --ctstate RELATED,ESTABLISHED -m comment --comment {COMMENT} -j RETURN"]
    for ip, port, label in ALLOW:
        rules.append(
            f"-A {EGRESS_CHAIN} -p tcp -m conntrack --ctstate NEW --ctproto tcp --ctorigdst {ip}/32 "
            f"--ctorigdstport {port} -m comment --comment {COMMENT}:{label} -j RETURN"
        )
    rules += [
        f'-A {EGRESS_CHAIN} -m limit --limit 6/min -m comment --comment {COMMENT} -j LOG --log-prefix "oh-sbx-deny: "',
        f"-A {EGRESS_CHAIN} -p tcp -m comment --comment {COMMENT} -j REJECT --reject-with tcp-reset",
        f"-A {EGRESS_CHAIN} -m comment --comment {COMMENT} -j REJECT --reject-with icmp-port-unreachable",
    ]
    return rules


def src_rules(exempt: list[tuple[str, str]]) -> list[str]:
    rules = [
        f"-A {SRC_CHAIN} -s {ip}/32 -m comment --comment {COMMENT}:exempt:{name} -j RETURN"
        for ip, name in sorted(exempt)
    ]
    rules.append(f"-A {SRC_CHAIN} -m comment --comment {COMMENT} -j {EGRESS_CHAIN}")
    return rules


def restore_fragment(exempt: list[tuple[str, str]] | None) -> str:
    """iptables-restore --noflush input. Declaring a chain flushes just that
    chain, so the swap is atomic. exempt=None leaves OH-SBX-SRC out."""
    lines = ["*filter", f":{EGRESS_CHAIN} - [0:0]"]
    if exempt is not None:
        lines.append(f":{SRC_CHAIN} - [0:0]")
    lines += egress_rules()
    if exempt is not None:
        lines += src_rules(exempt)
    lines.append("COMMIT")
    return "\n".join(lines) + "\n"


def classify(containers: list[dict]) -> tuple[list[dict], list[tuple[str, str]]]:
    """Split `docker inspect` output into (sandboxes, exempt (ip, name) on docker0)."""
    sandboxes, exempt = [], []
    for c in containers:
        if not c.get("State", {}).get("Running"):
            continue
        name = c.get("Name", "").lstrip("/")
        if name.startswith(SANDBOX_PREFIX):
            sandboxes.append(c)
            continue
        net = (c.get("NetworkSettings", {}).get("Networks") or {}).get(BRIDGE_NET) or {}
        if net.get("IPAddress"):
            exempt.append((net["IPAddress"], name))
    return sandboxes, exempt


def needs_limits(c: dict) -> bool:
    hc = c.get("HostConfig", {})
    return not hc.get("Memory") or not hc.get("NanoCpus") or (hc.get("PidsLimit") or 0) <= 0


def update_argv(name: str) -> list[str]:
    argv = ["docker", "update"]
    for k, v in LIMITS.items():
        argv += [f"--{k}", v]
    return argv + [name]


def _run(argv, **kw):
    return subprocess.run(argv, capture_output=True, text=True, **kw)


def inspect_all() -> list[dict]:
    ids = _run(["docker", "ps", "-q", "--no-trunc"], check=True).stdout.split()
    if not ids:
        return []
    return json.loads(_run(["docker", "inspect", *ids], check=True).stdout)


def restore(fragment: str) -> None:
    subprocess.run(["iptables-restore", "--noflush"], input=fragment, text=True, check=True)


def ensure_jumps() -> None:
    if _run(["iptables", "-n", "-L", "DOCKER-USER"]).returncode != 0:
        subprocess.run(["iptables", "-N", "DOCKER-USER"], check=True)
    for chain, spec in JUMPS:
        if _run(["iptables", "-C", chain, *spec]).returncode != 0:
            subprocess.run(["iptables", "-I", chain, "1", *spec], check=True)


def docker_up() -> bool:
    return _run(["docker", "info", "--format", "{{.ID}}"]).returncode == 0


def start() -> int:
    """Unit ExecStartPre. At boot (docker not up yet) the exemptions restored
    from rules.v4 name IPs docker may hand to a sandbox, so drop them all;
    on a restart with docker up, reconcile instead of blipping egress."""
    if docker_up():
        return once()
    restore(restore_fragment([]))
    ensure_jumps()
    print(f"start: {SRC_CHAIN} has 0 exemptions, every {BRIDGE_IF} source is allowlist-only")
    return 0


def once(dry_run: bool = False) -> int:
    sandboxes, exempt = classify(inspect_all())
    rc = 0
    if dry_run:
        print(restore_fragment(exempt), end="")
    else:
        restore(restore_fragment(exempt))
        ensure_jumps()
    for c in sandboxes:
        name = c["Name"].lstrip("/")
        if not needs_limits(c):
            continue
        argv = update_argv(name)
        if dry_run:
            print("would run:", " ".join(argv))
            continue
        r = _run(argv)
        if r.returncode != 0:
            print(f"limits FAILED for {name}: {r.stderr.strip()}", file=sys.stderr)
            rc = 1
        else:
            print(f"limits applied to {name}: {LIMITS}")
    print(f"once: {len(exempt)} exempt docker0 container(s), {len(sandboxes)} sandbox(es)")
    return rc


def watch() -> int:
    while not docker_up():
        time.sleep(2)
    once()
    ev = subprocess.Popen(
        ["docker", "events", "--filter", "type=container", "--filter", "event=start", "--format", "{{.Actor.Attributes.name}}"],
        stdout=subprocess.PIPE, text=True,
    )
    while True:
        ready, _, _ = select.select([ev.stdout], [], [], RESYNC_S)
        if ready and not ev.stdout.readline():
            return 1  # docker events exited; systemd Restart= brings us back via start
        once()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("mode", choices=["rules", "start", "once", "watch"])
    ap.add_argument("--dry-run", action="store_true", help="once: print the fragment and docker update argv, change nothing")
    a = ap.parse_args(argv)
    if a.mode == "rules":
        print(restore_fragment(None), end="")
        return 0
    if a.mode == "start":
        return start()
    if a.mode == "once":
        return once(dry_run=a.dry_run)
    return watch()


if __name__ == "__main__":
    sys.exit(main())
