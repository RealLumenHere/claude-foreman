#!/usr/bin/env python3
"""mcp_pin_check: cross-server checks over security-gate tool pin files (wq#4945, audit F5).

The pins baseline (/home/lumen/docs/security/mcp-tool-pins-2026-11.json, snap.py) and the
per-guard pin files (ops/wq4946-ghidra-mcp-guard/ghidra-pins.json) map SERVER@SURFACE to
{tool name: sha256}. Each guard only ever sees its own server, so a name another server also
exposes goes unnoticed there. Claude Code namespaces tools as mcp__<server>__<tool>, but a
flat-namespace client resolves a shared name to whichever server registered last, and a new
server can take over an existing name the same way.

`collisions` builds the agent-visible surface of every server (SERVER@guarded if the files
have one, else SERVER@unguarded; @backend is never shown to an agent) and groups tool names
by a folded key (case-insensitive, '-' and '.' read as '_'), so `List-Instances` collides with
`list_instances`. A name on two or more servers fails the check unless --accept lists that
name with exactly that server set: a third server joining a reviewed collision fails again.

Later files override earlier ones per SERVER@SURFACE, so a guard's own pin file can be layered
on the baseline after the guard lands (ghidra-lumen@guarded replaces @unguarded).

Usage:
  mcp_pin_check.py collisions PINS [PINS ...] [--accept FILE] [--json]
      exit 0: no collision outside --accept; exit 1: unreviewed collision; exit 2: bad input.
      Accepted entries that no longer collide are reported as stale (exit stays 0).
"""
import argparse
import json
import re
import sys

VISIBLE = ("guarded", "unguarded")  # preference order for what the agent sees


def fold(name):
    return re.sub(r"[-.]", "_", name).casefold()


def load_surfaces(paths):
    """{SERVER@SURFACE: set(tool names)} merged over paths, later files winning."""
    surfaces = {}
    for path in paths:
        try:
            with open(path) as fh:
                servers = json.load(fh)["servers"]
            for key, entry in servers.items():
                tools = entry["tools"]
                if not isinstance(tools, dict) or "@" not in key:
                    raise TypeError(key)
                surfaces[key] = set(tools)
        except (OSError, ValueError) as exc:
            raise SystemExit(f"{path}: cannot read: {type(exc).__name__}: {exc}")
        except (KeyError, TypeError, AttributeError) as exc:
            raise SystemExit(f"{path}: expected servers[SERVER@SURFACE].tools mapping, bad entry {exc}")
    return surfaces


def visible_surfaces(surfaces):
    """{server: (surface, names)} for the surface each server shows an agent."""
    out = {}
    for key in surfaces:
        server, surface = key.rsplit("@", 1)
        if surface not in VISIBLE:
            continue
        have = out.get(server)
        if have is None or VISIBLE.index(surface) < VISIBLE.index(have[0]):
            out[server] = (surface, surfaces[key])
    return out


def find_collisions(visible):
    """{folded name: {server: [raw names]}} for every folded name on two or more servers."""
    by_key = {}
    for server, (_surface, names) in visible.items():
        for name in names:
            by_key.setdefault(fold(name), {}).setdefault(server, []).append(name)
    return {k: {s: sorted(v) for s, v in sorted(srv.items())}
            for k, srv in sorted(by_key.items()) if len(srv) > 1}


def load_accept(path):
    """{folded name: frozenset(servers)} from {"accepted": [{"name", "servers", "reason"}]}."""
    if not path:
        return {}
    try:
        with open(path) as fh:
            rows = json.load(fh)["accepted"]
        accept = {}
        for row in rows:
            if not str(row.get("reason", "")).strip():
                raise ValueError(f"accepted {row.get('name')!r} has no reason")
            accept[fold(row["name"])] = frozenset(row["servers"])
        return accept
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
        raise SystemExit(f"--accept {path}: {type(exc).__name__}: {exc}")


def check(paths, accept_path=None):
    visible = visible_surfaces(load_surfaces(paths))
    collisions = find_collisions(visible)
    accept = load_accept(accept_path)
    report = {"servers": {s: f"{s}@{surf} ({len(n)})" for s, (surf, n) in sorted(visible.items())},
              "unreviewed": {}, "accepted": {}, "stale_accept": []}
    for key, servers in collisions.items():
        bucket = "accepted" if accept.get(key) == frozenset(servers) else "unreviewed"
        report[bucket][key] = servers
    report["stale_accept"] = sorted(k for k in accept if k not in collisions)
    return report


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    col = sub.add_parser("collisions", help="fail on tool names shared across servers")
    col.add_argument("pins", nargs="+", help="pin files, later overriding earlier per SERVER@SURFACE")
    col.add_argument("--accept", help="reviewed collisions file (name + exact server set + reason)")
    col.add_argument("--json", action="store_true", help="print the report as JSON")
    args = ap.parse_args(argv)
    try:
        report = check(args.pins, args.accept)
    except SystemExit as exc:
        print(f"mcp_pin_check: {exc}", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(report, indent=1, sort_keys=True))
    else:
        print("surfaces: " + ", ".join(report["servers"].values()))
        for bucket in ("unreviewed", "accepted"):
            for key, servers in report[bucket].items():
                where = "; ".join(f"{s}: {','.join(n)}" for s, n in servers.items())
                print(f"{bucket.upper()} {key}: {where}")
        for key in report["stale_accept"]:
            print(f"STALE_ACCEPT {key}: no longer collides, drop it from --accept")
        print(f"{len(report['unreviewed'])} unreviewed, {len(report['accepted'])} accepted collision(s)")
    return 1 if report["unreviewed"] else 0


if __name__ == "__main__":
    sys.exit(main())
