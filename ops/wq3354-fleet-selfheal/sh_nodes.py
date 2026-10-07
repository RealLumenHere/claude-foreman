"""CLASS 6 — fleet node offline (wq#3354).

Reads `tailscale status --json`. A peer is a fleet node when one of its TailscaleIPs is
a `<node>.tailscale_ip` fact in lumen_state (matching by IP, not HostName: lumen's
tailscale hostname is `lumen-1`); phones and laptops that come and go are not fleet.
A fleet node offline for `offline_after_s` (1800) gets one ticket per outage carrying
tailscale's LastSeen and its known power state: the lumen_state power_* / status facts
and, when its power_plug fact names a smart plug, that plug's live `plug-ctl <p> state`.
A node named in `nodes.notes` (wq#3619) gets that text folded into the ticket too, so a
recurring known-cause outage isn't re-researched from scratch on every drop.
Coming back online closes the episode with a low-priority notice.
"""
import datetime
import json
import re

CLS = "nodes"
ZERO_TIME = "0001-01-01T00:00:00Z"
LUMEN_STATE = ["python3", "/home/lumen/minerva/lumen_state.py", "current"]
PLUG_CTL = "/home/lumen/bin/plug-ctl"
FACT = re.compile(r"^\s*([a-z0-9_-]+)\.([a-z0-9_]+)\s*=\s*(.*)$")


def _ts(s):
    if not s or s == ZERO_TIME:
        return None
    try:
        return datetime.datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def peers(ctx):
    """{peer_key: {"host", "ips", "online", "last_seen"}} from tailscale, or None."""
    p = ctx.runner(["tailscale", "status", "--json"], timeout=30)
    if p is None or p.returncode != 0:
        return None
    try:
        data = json.loads(p.stdout or "{}")
    except ValueError:
        return None
    out = {}
    for key, peer in (data.get("Peer") or {}).items():
        ls = peer.get("LastSeen")
        out[key] = {"host": (peer.get("HostName") or key).lower(),
                    "ips": list(peer.get("TailscaleIPs") or []),
                    "online": bool(peer.get("Online")),
                    "last_seen": None if ls in (None, ZERO_TIME) else ls}
    return out


def fleet_facts(ctx):
    """{node: {predicate: value}} from `lumen_state current`, or {} if it cannot answer."""
    p = ctx.runner(LUMEN_STATE, timeout=60)
    facts = {}
    for line in (p.stdout or "").splitlines() if p and p.returncode == 0 else []:
        m = FACT.match(line)
        if m:
            facts.setdefault(m.group(1), {})[m.group(2)] = m.group(3).strip()
    return facts


def node_for(ips, facts):
    for node, preds in facts.items():
        ip = preds.get("tailscale_ip", "").split()[0] if preds.get("tailscale_ip") else ""
        if ip and ip in ips:
            return node
    return None


def _trunc(v, n=200):
    """Cut at the last space before n, not mid-word — a hard v[:n] cut split a fact's
    trailing file path mid-name (wq#3508: ".../thor-power" -> ".../thor-powe")."""
    if len(v) <= n:
        return v
    cut = v.rfind(" ", 0, n)
    return (v[:cut] if cut > 0 else v[:n]) + "…"


def power_state(ctx, preds):
    lines = [f"{k} = {_trunc(v)}" for k, v in sorted(preds.items())
             if k.startswith("power_") or k == "status"]
    plug = re.match(r"\s*(p\d+)\b", preds.get("power_plug", ""))
    if plug:
        p = ctx.runner([PLUG_CTL, plug.group(1), "state"], timeout=20)
        live = _trunc((p.stdout or p.stderr or "").strip()) if p else "plug-ctl did not run"
        lines.append(f"live plug {plug.group(1)} state: {live}")
    return lines or ["no power facts in lumen_state"]


def sweep(ctx, peers_fn=peers, facts_fn=fleet_facts):
    ncfg = ctx.cfg.get("nodes", {})
    after = int(ncfg.get("offline_after_s", 1800))
    ignore = set(ncfg.get("ignore", []))
    seen = peers_fn(ctx)
    if seen is None:
        ctx.note(CLS, "tailscale status unavailable; skipped")
        return
    st = ctx.section("nodes")
    meta = ctx.section("nodes_meta")
    facts, due = None, []
    for key, rec in seen.items():
        ns = st.get(key)
        if rec["online"]:
            if st.pop(key, None) is not None and ns.get("node"):
                ctx.resolve(CLS, f"node-offline:{ns['node']}",
                            f"{ns['node']} is back on tailscale.")
            continue
        if ns is None:
            ns = st[key] = {"offline_since": int(min(_ts(rec["last_seen"]) or ctx.now,
                                                     ctx.now))}
        ns["last_seen"] = rec["last_seen"]
        if ctx.now - ns["offline_since"] < after or ns.get("not_fleet"):
            continue
        if facts is None:
            facts = facts_fn(ctx)
        node = node_for(rec["ips"], facts)
        if node is None or node in ignore or rec["host"] in ignore:
            ns["not_fleet"] = True   # stop re-querying lumen_state for this outage
            continue
        ns["node"] = node
        if f"node-offline:{node}" not in ctx.section("tickets"):
            mins = int((ctx.now - ns["offline_since"]) // 60)
            lines = power_state(ctx, facts.get(node, {}))
            note = ncfg.get("notes", {}).get(node)
            if note:
                lines.append(f"known issue: {note}")
            due.append((node, mins,
                        f"{node} (tailscale host {rec['host']}, {', '.join(rec['ips'])}) "
                        f"offline {mins} min, LastSeen {rec['last_seen'] or 'unknown'}\n  "
                        + "\n  ".join(lines)))
    for key in [k for k in st if k not in seen]:
        st.pop(key)
    if not due:
        meta["baselined"] = True
        return
    hint = "\nIf a node is meant to be off, add it to selfheal.yaml nodes.ignore."
    if not meta.get("baselined"):
        # First sweep after install: outages audits already know about go into ONE
        # ticket instead of N, and each node is marked so it never re-files.
        tid = ctx.file_once(CLS, "node-offline-baseline",
                            f"[selfheal] {len(due)} fleet nodes already offline at "
                            f"fleet-selfheal install",
                            "\n\n".join(d for _, _, d in due) + hint)
        if not tid:
            return          # wq add failed (or dry-run): try the baseline again next sweep
        for node, _, _ in due:
            ctx.section("tickets")[f"node-offline:{node}"] = {"id": tid, "at": int(ctx.now)}
        meta["baselined"] = True
        return
    for node, mins, detail in due:
        ctx.file_once(CLS, f"node-offline:{node}",
                      f"[selfheal] fleet node {node} offline on tailscale for {mins} min",
                      detail + hint, page="default")
