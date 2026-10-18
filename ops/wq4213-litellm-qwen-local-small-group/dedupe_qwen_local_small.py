#!/usr/bin/env python3
"""wq#4213 — qwen-local-small has two model_list deployments and no
router_settings.fallbacks entry, so a ClientConnectorError on either
deployment surfaces raw ("No fallback model group found") instead of
degrading to a live rung.

Host identity (lumen_state.py current, 2026-10-18 — the config's own inline
`description` fields are stale, see README.md):
  192.168.8.125 = ptah     (NOT hathor — hathor.lan_ip is 192.168.8.172)
  192.168.8.135 = sekhmet  (NOT ptah)

ptah flaps (OFFLINE since 2026-10-16, but had a live window 2026-10-15,
conf 0.88) while sekhmet has been unreachable continuously since 2026-10-11
with no recovery and is flagged for a physical check (kb/ISSUE-LOG.md
#1194, #1206) — the one with no live precedent in three weeks. This drops
sekhmet's deployment (192.168.8.135), keeps ptah's (192.168.8.125), and adds
a fallback to `lumen-local-judge` — qwen2.5-coder:7b on the Lumen
docker-gateway ollama (172.17.0.1:11434), already live and already
`lumen/judge`'s own local fallback, so this makes qwen-local-small degrade
the same way the rest of the config's cheap/local groups already do.

Same safety pattern as wq#3212/wq#4135: line-level text surgery (comments
preserved), the result is re-parsed and compared against an
independently-built expected plan before writing, and the write is a
timestamped-backup in-place edit (the gateway bind-mounts this one file).

  dedupe_qwen_local_small.py check CONFIG
  dedupe_qwen_local_small.py apply CONFIG [--write]
     [--group qwen-local-small] [--drop-host 192.168.8.135]
     [--fallback-to lumen-local-judge]
"""
from __future__ import annotations

import argparse
import copy
import datetime as dt
import re
import shutil
import sys
from pathlib import Path

import yaml

GROUP = "qwen-local-small"
DROP_HOST = "192.168.8.135"
FALLBACK_TO = "lumen-local-judge"

ENTRY_RE = re.compile(r"^(\s*)-\s+model_name:\s*['\"]?([^'\"\s]+)")
FALLBACKS_KEY_RE = re.compile(r"^(\s*)fallbacks:\s*$")


def remove_entry(text: str, group: str, drop_host: str) -> tuple[str, int]:
    """Drop the model_list entry named `group` whose api_base names `drop_host`."""
    lines = text.splitlines(keepends=True)
    drop = [False] * len(lines)
    removed = 0
    i = 0
    while i < len(lines):
        m = ENTRY_RE.match(lines[i])
        if not m or m.group(2) != group:
            i += 1
            continue
        indent = len(m.group(1))
        start, end = i, i + 1
        while end < len(lines):
            ln = lines[end]
            if not ln.strip():
                end += 1
                continue
            cur = len(ln) - len(ln.lstrip(" "))
            if cur < indent or (cur == indent and not re.match(r"^\s*#", ln)):
                break
            end += 1
        if drop_host in "".join(lines[start:end]):
            for j in range(start, end):
                drop[j] = True
            removed += 1
        i = end
    return "".join(ln for j, ln in enumerate(lines) if not drop[j]), removed


def _fallbacks_span(lines: list[str]) -> tuple[int, int, int]:
    """(list-item indent, first item line, end) of router_settings.fallbacks.
    The `- group:` items sit at the SAME indent as the `fallbacks:` key
    itself (a block sequence indented level with its mapping key), not one
    level deeper."""
    rs_at = next(i for i, l in enumerate(lines) if re.match(r"^router_settings:\s*$", l))
    for i in range(rs_at + 1, len(lines)):
        line = lines[i]
        if line.strip() and len(line) - len(line.lstrip(" ")) == 0:
            break
        m = FALLBACKS_KEY_RE.match(line)
        if not m:
            continue
        key_indent = len(m.group(1))
        start = end = i + 1
        for j in range(start, len(lines)):
            nxt = lines[j]
            if not nxt.strip():
                continue
            ind = len(nxt) - len(nxt.lstrip(" "))
            if ind < key_indent or (ind == key_indent and not nxt.lstrip().startswith("-")):
                break
            end = j + 1
        return key_indent, start, end
    raise RuntimeError("router_settings.fallbacks: not found")


def add_fallback(text: str, group: str, to: str) -> tuple[str, bool]:
    lines = text.splitlines(keepends=True)
    item_indent, start, end = _fallbacks_span(lines)
    entry_re = re.compile(rf"^{' ' * item_indent}- {re.escape(group)}:\s*$")
    chain_item_re = re.compile(rf"^{' ' * (item_indent + 2)}- (\S+)\s*$")
    for i in range(start, end):
        if not entry_re.match(lines[i]):
            continue
        j = i + 1
        chain = []
        while j < end and chain_item_re.match(lines[j]):
            chain.append(chain_item_re.match(lines[j]).group(1))
            j += 1
        if to in chain:
            return "".join(lines), False
        lines.insert(j, " " * (item_indent + 2) + f"- {to}\n")
        return "".join(lines), True
    block = " " * item_indent + f"- {group}:\n" + " " * (item_indent + 2) + f"- {to}\n"
    lines.insert(end, block)
    return "".join(lines), True


def plan(config: dict, group: str, drop_host: str, to: str) -> dict:
    """Expected post-edit config, built independently of the text surgery."""
    want = copy.deepcopy(config)
    want["model_list"] = [
        e for e in want.get("model_list", [])
        if not (e.get("model_name") == group
                and drop_host in (e.get("litellm_params") or {}).get("api_base", ""))
    ]
    rs = want.setdefault("router_settings", {})
    fb = rs.setdefault("fallbacks", [])
    for entry in fb:
        if isinstance(entry, dict) and len(entry) == 1 and group in entry:
            if to not in entry[group]:
                entry[group].append(to)
            return want
    fb.append({group: [to]})
    return want


def build(text: str, group: str, drop_host: str, to: str) -> tuple[str, int, bool]:
    config = yaml.safe_load(text)
    want = plan(config, group, drop_host, to)
    new_text, removed = remove_entry(text, group, drop_host)
    new_text, added = add_fallback(new_text, group, to)
    if yaml.safe_load(new_text) != want:
        raise RuntimeError("rendered config does not match the expected plan; refusing to write")
    return new_text, removed, added


def do_check(text: str, group: str) -> int:
    config = yaml.safe_load(text)
    entries = [e for e in config.get("model_list", []) if e.get("model_name") == group]
    fb = (config.get("router_settings") or {}).get("fallbacks") or []
    has_fallback = any(isinstance(e, dict) and group in e and e[group] for e in fb)
    print(f"{group}: {len(entries)} deployment(s), fallback {'present' if has_fallback else 'MISSING'}")
    if len(entries) > 1 and not has_fallback:
        print(f"BUG: {len(entries)} deployments and no fallback — a dead one surfaces raw")
        return 1
    print("ok")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("check")
    p.add_argument("config", type=Path)
    p.add_argument("--group", default=GROUP)

    p = sub.add_parser("apply")
    p.add_argument("config", type=Path)
    p.add_argument("--group", default=GROUP)
    p.add_argument("--drop-host", default=DROP_HOST)
    p.add_argument("--fallback-to", default=FALLBACK_TO)
    p.add_argument("--write", action="store_true", help="back up and edit CONFIG in place")

    a = ap.parse_args(argv)
    text = a.config.read_text()

    if a.cmd == "check":
        return do_check(text, a.group)

    new_text, removed, added = build(text, a.group, a.drop_host, a.fallback_to)
    print(f"dropped {removed} deployment(s) with api_base host {a.drop_host!r} "
          f"from model_name {a.group!r}")
    print(f"fallback {a.group} -> {a.fallback_to}: {'added' if added else 'already present'}")
    if not removed and not added:
        print("nothing to change")
        return 0
    if not a.write:
        print("dry run; pass --write to apply")
        return 0

    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup = a.config.with_name(f"{a.config.name}.bak-wq4213-{stamp}")
    shutil.copy2(a.config, backup)
    a.config.write_text(new_text)
    print(f"wrote {a.config} (backup {backup})")
    print("NOTE: the litellm container reads the config only at startup — "
          "queue a recreate/restart (see README.md window plan) to load this edit.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
