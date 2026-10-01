#!/usr/bin/env python3
"""wq#3321 — remove the dead chrome-mcp.lumennode.uk cloudflared ingress entry.

chrome-mcp.service has been stopped AND disabled since 2026-09-29T20:12:55Z
(systemd: "Stopped chrome-mcp.service", confirmed via `systemctl status`
showing `disabled; preset: enabled` + `inactive (dead)`, and `ss -tlnp`
showing no listener on 127.0.0.1:3103). No commit or memory entry records
the decommission explicitly, but the timing lines up with the wq#2751/wq#2768
chrome-mcp RCE-surface remediation (anonymous `use_browser` with `file://`
nav + `eval` reachable over the tailnet) landing hours earlier the same day
— the simplest read is that the service was shut off as belt-and-suspenders
on top of the Access guard, same pattern as the wq#1099 "research mill"
retirement (ISSUE-LOG row reconstructed from unit-file placement + log
timestamps, no explicit ticket).

cloudflared keeps serving the now-dead ingress, so ui-drift's ORIGIN_DOWN
check (ui-drift:204-207 in lumen-bin) fires every cycle. This script removes
that one ingress entry by hostname match (not a line-numbered patch — this
worker's sandbox has no read access to the live /etc/cloudflared/config.yml
to verify exact line numbers against, per the house "never guess a config
value" doctrine) and reloads cloudflared via the sanctioned detached
systemd-run path (never foreground, per house policy on vault/cloudflared/
Commander changes).

Needs root. This worker cannot run it directly (no sudo, path outside this
worktree's grants) — staged here for Commander/whoever holds cloudflared
config access, same as ops/wq1099-ui-drift/cloudflared-remove-vip.patch.

Usage:
  remove_chrome_mcp_ingress.py --dry-run     # show the entry that would be removed
  remove_chrome_mcp_ingress.py --apply       # back up config.yml, remove entry, reload cloudflared
"""
import argparse
import datetime
import re
import subprocess
import sys
from pathlib import Path

CONFIG = Path("/etc/cloudflared/config.yml")
HOSTNAME = "chrome-mcp.lumennode.uk"


def load():
    import yaml
    text = CONFIG.read_text()
    doc = yaml.safe_load(text)
    return text, doc


def find_entry(doc):
    for e in doc.get("ingress", []) or []:
        if e.get("hostname") == HOSTNAME:
            return e
    return None


def find_block(lines, hostname):
    """Locate the [start, end) line range of the ingress list item whose
    hostname matches, WITHOUT touching anything else in the file.

    yaml.safe_load + yaml.safe_dump is not used here on purpose: that
    round-trip drops every comment and reformats every untouched entry
    (wq#3326). This instead walks the raw lines of the `ingress:` block,
    isolates each `- ...` list item by indentation, and only confirms a
    match by parsing that one item's own text as YAML — so the rest of
    the file is never re-serialized and stays byte-identical.
    """
    import yaml

    ingress_idx = None
    for i, line in enumerate(lines):
        if re.match(r"^ingress:\s*$", line):
            ingress_idx = i
            break
    if ingress_idx is None:
        return None

    item_indent = None
    i = ingress_idx + 1
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()
        if stripped == "" or stripped.startswith("#"):
            i += 1
            continue
        m = re.match(r"^(\s*)-\s", line)
        if not m:
            return None
        item_indent = m.group(1)
        break
    if item_indent is None:
        return None

    i = ingress_idx + 1
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()
        if stripped == "" or stripped.startswith("#"):
            i += 1
            continue
        indent = len(line) - len(line.lstrip())
        if indent < len(item_indent):
            break
        if not re.match(r"^" + re.escape(item_indent) + r"-\s", line):
            i += 1
            continue

        start = i
        i += 1
        while i < len(lines):
            l2 = lines[i]
            if l2.strip() == "":
                break
            indent2 = len(l2) - len(l2.lstrip())
            if indent2 <= len(item_indent):
                break
            i += 1
        end = i

        block_text = "".join(lines[start:end])
        try:
            parsed = yaml.safe_load(block_text)
        except yaml.YAMLError:
            parsed = None
        if (
            isinstance(parsed, list)
            and len(parsed) == 1
            and isinstance(parsed[0], dict)
            and parsed[0].get("hostname") == hostname
        ):
            return start, end

    return None


def main():
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--dry-run", action="store_true")
    g.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    text, doc = load()
    entry = find_entry(doc)
    if entry is None:
        print(f"no ingress entry for {HOSTNAME} — nothing to do")
        return 0

    print(f"found: {entry}")
    if args.dry_run:
        return 0

    lines = text.splitlines(keepends=True)
    block = find_block(lines, HOSTNAME)
    if block is None:
        print(f"could not locate a surgical text block for {HOSTNAME} — refusing to touch the file")
        return 1
    start, end = block
    new_text = "".join(lines[:start] + lines[end:])

    ts = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%d_%H%M%S")
    backup = CONFIG.with_suffix(CONFIG.suffix + f".bak.{ts}")
    backup.write_text(text)
    print(f"backed up to {backup}")

    CONFIG.write_text(new_text)
    print(f"removed {HOSTNAME} ingress entry")

    # Detached reload — never foreground, per house policy on cloudflared changes.
    subprocess.run([
        "systemd-run", "--collect", "--unit=wq3321-cloudflared-reload",
        "systemctl", "restart", "cloudflared",
    ], check=True)
    print("reload dispatched via systemd-run --collect (detached)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
