#!/usr/bin/env python3
"""wq#3108 genrules — build a node's staged INPUT default-drop from its recon bundle.

    python3 genrules.py --recon <bundle-dir> --vars nodes/<node>.vars --out <staged-dir>

Refuses to run without a recon bundle: the staged files are derived from the node's
LIVE iptables-save/ip6tables-save (so Docker/ts-* chains survive a full restore) and
the listener-coverage report is derived from its live ss output. Outputs:

    staged.v4 / staged.v6   full-table saves, INPUT policy DROP + standard accepts
    diff.v4 / diff.v6       unified diff vs live (review: only intended lines)
    coverage.txt            every listening socket -> which rule admits it, or CUT
"""
import argparse
import difflib
import ipaddress
import re
import sys
from pathlib import Path

REQUIRED = ["live.v4", "live.v6", "ss-ltnp.txt", "ss-lunp.txt", "ip-addr.txt"]


def die(msg):
    sys.stderr.write(f"genrules: REFUSED: {msg}\n")
    sys.exit(2)


def load_vars(path):
    v = {"EXTRA4": [], "EXTRA6": []}
    for line in Path(path).read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        key, _, val = line.partition("=")
        key, val = key.strip(), val.strip()
        if key in ("EXTRA4", "EXTRA6"):
            v[key].append(val)
        else:
            v[key] = val
    for key in ("LAN_IFACE", "LAN_SUBNET"):
        if not v.get(key) or "TBD" in v[key]:
            die(f"{key} unset or TBD in {path} — fill it from the recon bundle first")
    ipaddress.ip_network(v["LAN_SUBNET"])  # validate early, not at restore time
    return v


def standard_accepts(family, v, has_ts_jump):
    rules = [
        "-A INPUT -i lo -j ACCEPT",
        "-A INPUT -m conntrack --ctstate RELATED,ESTABLISHED -j ACCEPT",
    ]
    if not has_ts_jump:
        rules.append("-A INPUT -i tailscale0 -j ACCEPT")
    rules.append(
        f"-A INPUT -i {v['LAN_IFACE']} -s {v['LAN_SUBNET']} -p tcp --dport 22 -j ACCEPT"
        if family == 4
        else f"-A INPUT -i {v['LAN_IFACE']} -p tcp --dport 22 -j ACCEPT"
    )
    rules.append("-A INPUT -p icmp -j ACCEPT" if family == 4
                 else "-A INPUT -p ipv6-icmp -j ACCEPT")
    if family == 6 and v.get("DHCPV6", "no") == "yes":
        rules.append(f"-A INPUT -i {v['LAN_IFACE']} -s fe80::/10 -p udp --dport 546 -j ACCEPT")
    rules += [f"-A INPUT {x}" for x in (v["EXTRA4"] if family == 4 else v["EXTRA6"])]
    return rules


def stage(live_text, family, v):
    """Rewrite only the *filter table: INPUT policy -> DROP, standard accepts first."""
    out, in_filter, inserted = [], False, False
    has_ts_jump = bool(re.search(r"^-A INPUT -j ts-input\s*$", live_text, re.M))
    accepts = standard_accepts(family, v, has_ts_jump)
    for line in live_text.splitlines():
        if line.startswith("*"):
            in_filter = line == "*filter"
        if in_filter and line.startswith(":INPUT "):
            counters = line.split()[-1]
            out.append(f":INPUT DROP {counters}")
            continue
        if in_filter and not inserted and (line.startswith("-A ") or line == "COMMIT"):
            # first rule (or empty table) — standard accepts go ahead of everything,
            # except an existing ts-input jump which tailscaled owns and re-inserts
            # at the top anyway; keep it first to match live behavior.
            if line == "-A INPUT -j ts-input":
                out.append(line)
                out.extend(accepts)
                inserted = True
                continue
            out.extend(accepts)
            inserted = True
        out.append(line)
    text = "\n".join(out) + "\n"
    for acc in accepts:
        if text.count(acc) > 1:
            die(f"live rules already contain '{acc}' — refusing to duplicate; "
                "reconcile the node by hand first")
    return text


LISTEN_RE = re.compile(r"^\S+\s+\S+\s+\S+\s+(\S+):(\d+|\*)\s")


def coverage(recon, v, staged4, staged6):
    """One row per listening socket: which standard/extra rule admits it, or CUT."""
    extra_ports = set()
    for spec in v["EXTRA4"] + v["EXTRA6"]:
        m = re.search(r"--dport (\d+)", spec)
        if m:
            extra_ports.add(int(m.group(1)))
    rows = []
    for fname, proto in (("ss-ltnp.txt", "tcp"), ("ss-lunp.txt", "udp")):
        for line in (recon / fname).read_text().splitlines()[1:]:
            m = LISTEN_RE.match(line)
            if not m:
                continue
            addr, port = m.group(1), m.group(2)
            proc = (re.search(r'users:\(\("([^"]+)"', line) or [None, "?"])[1]
            bare = addr.strip("[]").split("%")[0]
            if bare in ("127.0.0.1", "::1") or bare.startswith("127."):
                verdict = "lo only (rule 1)"
            elif port == "22":
                verdict = "tailnet (rule 3) + LAN ssh (rule 4)"
            elif port != "*" and int(port) in extra_ports:
                verdict = "tailnet (rule 3) + EXTRA accept"
            else:
                verdict = "tailnet only (rule 3) — CUT from LAN/WAN"
            rows.append(f"{proto:4} {addr}:{port:<6} {proc:<20} -> {verdict}")
    return "\n".join(rows) + "\n"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--recon", required=True)
    ap.add_argument("--vars", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    recon = Path(args.recon)
    missing = [f for f in REQUIRED if not (recon / f).exists()]
    if missing:
        die(f"recon bundle {recon} is missing {missing} — run recon.sh on the node "
            "and review it BEFORE generating rules (ticket wq#3108 gate)")
    v = load_vars(args.vars)

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    for fam, live_name in ((4, "live.v4"), (6, "live.v6")):
        live = (recon / live_name).read_text()
        staged = stage(live, fam, v)
        (out / f"staged.v{fam}").write_text(staged)
        diff = difflib.unified_diff(
            live.splitlines(True), staged.splitlines(True),
            fromfile=live_name, tofile=f"staged.v{fam}")
        (out / f"diff.v{fam}").write_text("".join(diff))
    cov = coverage(recon, v, out / "staged.v4", out / "staged.v6")
    (out / "coverage.txt").write_text(cov)
    cut = cov.count("CUT")
    print(f"staged -> {out} ({cut} listener(s) CUT from LAN/WAN; review coverage.txt)")


if __name__ == "__main__":
    main()
