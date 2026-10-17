#!/usr/bin/env python3
"""wq#4135 — replace a dead rung in router_settings fallback chains.

wq#4102 made `lumen/fast` inherit `lumen-lfm2-1b`'s fallback chain, but that
chain's only rung is `cerebras-llama`, dead since the Cerebras billing lapse
(402 "Payment required", probed 2026-10-17 via probe_rung.py). A chain whose
every rung is dead still 500s, so the dead rung is swapped for a live one in
every chain that lists it, across every fallback kind.

  retarget_rung.py check CONFIG --dead NAME             exit 1 if any chain lists NAME
  retarget_rung.py apply CONFIG --dead NAME --to NAME2          dry run
  retarget_rung.py apply CONFIG --dead NAME --to NAME2 --write  back up, edit in place

If a chain already contains the replacement, the dead line is dropped instead
of duplicated. Same safety pattern as wq#4102's alias_fallbacks.py: the edit is
line-level so the rest of the file stays byte-identical, the result is
re-parsed and compared against original-with-replacements before writing, and
the write is in place (the gateway bind-mounts this single file, pinning the
inode). Nothing from the credential-bearing config is printed except model
group names.
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

FALLBACK_KINDS = ("fallbacks", "context_window_fallbacks", "content_policy_fallbacks")


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip(" "))


def _kind_spans(lines: list[str]) -> dict[str, tuple[int, int]]:
    """(first line after `<kind>:`, end) for each fallback kind under router_settings."""
    rs_at = next(i for i, l in enumerate(lines) if re.match(r"router_settings:\s*$", l))
    spans = {}
    for kind in FALLBACK_KINDS:
        key_re = re.compile(rf"(\s+){re.escape(kind)}:\s*$")
        for i in range(rs_at + 1, len(lines)):
            line = lines[i]
            if line.strip() and _indent(line) == 0:
                break
            m = key_re.match(line)
            if not m:
                continue
            key_indent = len(m.group(1))
            end = i + 1
            for j in range(i + 1, len(lines)):
                nxt = lines[j]
                if not nxt.strip():
                    continue
                ind = _indent(nxt)
                if ind < key_indent or (ind == key_indent and not nxt.lstrip().startswith("- ")):
                    break
                end = j + 1
            spans[kind] = (i + 1, end)
            break
    return spans


def plan(config: dict, dead: str, to: str) -> tuple[dict, list[str]]:
    """Expected post-edit config plus one note per chain touched."""
    want = copy.deepcopy(config)
    notes = []
    rs = want.get("router_settings") or {}
    for kind in FALLBACK_KINDS:
        for entry in rs.get(kind) or []:
            if not (isinstance(entry, dict) and len(entry) == 1):
                continue
            group, chain = next(iter(entry.items()))
            if not isinstance(chain, list) or dead not in chain:
                continue
            entry[group] = ([m for m in chain if m != dead] if to in chain
                            else [to if m == dead else m for m in chain])
            notes.append(f"router_settings.{kind}: {group}: {chain} -> {entry[group]}")
    return want, notes


def render(text: str, dead: str, to: str) -> str:
    lines = text.splitlines(keepends=True)
    out = list(lines)
    dead_re = re.compile(rf"^(\s*)- {re.escape(dead)}\s*$")
    for kind, (start, end) in _kind_spans(lines).items():
        chain_has_to = {}
        chain_id = None
        for i in range(start, end):
            if re.match(r"^\s*- \S+:\s*$", lines[i]) or re.match(r"^\s*- ['\"]", lines[i]):
                chain_id = i
                chain_has_to[chain_id] = False
            if re.match(rf"^\s*- {re.escape(to)}\s*$", lines[i]):
                chain_has_to[chain_id] = True
        chain_id = None
        for i in range(start, end):
            if re.match(r"^\s*- \S+:\s*$", lines[i]) or re.match(r"^\s*- ['\"]", lines[i]):
                chain_id = i
            m = dead_re.match(lines[i])
            if m:
                out[i] = None if chain_has_to.get(chain_id) else f"{m.group(1)}- {to}\n"
    return "".join(l for l in out if l is not None)


def build(text: str, dead: str, to: str) -> tuple[str, list[str]]:
    config = yaml.safe_load(text)
    want, notes = plan(config, dead, to)
    if not notes:
        return text, notes
    new_text = render(text, dead, to)
    if yaml.safe_load(new_text) != want:
        raise RuntimeError("rendered config does not parse to original + replacements; refusing")
    return new_text, notes


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("check"); p.add_argument("config", type=Path)
    p.add_argument("--dead", required=True)
    p = sub.add_parser("apply"); p.add_argument("config", type=Path)
    p.add_argument("--dead", required=True); p.add_argument("--to", required=True)
    p.add_argument("--write", action="store_true", help="back up and edit CONFIG in place")
    a = ap.parse_args(argv)

    text = a.config.read_text()
    if a.cmd == "check":
        _, notes = plan(yaml.safe_load(text), a.dead, a.dead + "-GONE")
        for n in notes:
            print("lists dead rung: " + n.split(" -> ")[0])
        print(f"ok: no chain lists {a.dead}" if not notes
              else f"{len(notes)} chain(s) still list {a.dead}")
        return 1 if notes else 0
    new_text, notes = build(text, a.dead, a.to)
    for n in notes:
        print("retarget: " + n)
    if not notes:
        print("nothing to change")
        return 0
    if not a.write:
        print("dry run; pass --write to apply")
        return 0
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup = a.config.with_name(f"{a.config.name}.bak-wq4135-{stamp}")
    shutil.copy2(a.config, backup)
    a.config.write_text(new_text)
    print(f"wrote {a.config} (backup {backup})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
