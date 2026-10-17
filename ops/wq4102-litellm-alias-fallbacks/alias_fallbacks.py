#!/usr/bin/env python3
"""wq#4102 — give every router_settings.model_group_alias the fallback chains of
the group it points at.

LiteLLM looks fallbacks up by the model name the caller sent, before any alias
resolution (router_utils/fallback_event_handlers.get_fallback_model_group: exact
key match, then provider-stripped match, then "*"; v1.83.10). A caller on
`lumen/best` (alias -> deepseek-v4-pro) therefore never sees the
`deepseek-v4-pro -> openrouter-deepseek-pro` entry and gets the primary's raw
error ("No fallback model group found for original model_group=lumen/best").

This tool mirrors each target's chain under the alias name, for every
fallback kind present in router_settings. An alias that already has its own
entry is left alone (explicit override wins).

  alias_fallbacks.py check CONFIG          exit 1 if any alias is missing a chain its target has
  alias_fallbacks.py apply CONFIG          print the lines that would be added (dry run)
  alias_fallbacks.py apply CONFIG --write  back up CONFIG, then add them in place

The live config is credential-bearing: nothing here prints a value from it
other than model-group names. Edits are line insertions into the existing
fallback lists, so the rest of the file is untouched byte-for-byte, and the
result is re-parsed and compared before anything is written.
"""
from __future__ import annotations

import argparse
import copy
import datetime as dt
import json
import re
import shutil
import sys
from pathlib import Path

import yaml

FALLBACK_KINDS = ("fallbacks", "context_window_fallbacks", "content_policy_fallbacks")
PLAIN_SCALAR = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_./:@-]*")


def alias_targets(router_settings: dict) -> dict[str, str]:
    """model_group_alias accepts `alias: target` or `alias: {model: target, hidden: ...}`."""
    out = {}
    for alias, value in (router_settings.get("model_group_alias") or {}).items():
        target = value.get("model") if isinstance(value, dict) else value
        if isinstance(target, str):
            out[str(alias)] = target
    return out


def chain_index(entries: list) -> dict[str, list]:
    index = {}
    for item in entries or []:
        if isinstance(item, dict) and len(item) == 1:
            key, chain = next(iter(item.items()))
            index.setdefault(str(key), chain)
    return index


def plan(config: dict) -> tuple[dict[str, list[tuple[str, list]]], list[str]]:
    """Return ({kind: [(alias, chain), ...]} to add, [notes])."""
    rs = config.get("router_settings") or {}
    targets = alias_targets(rs)
    additions: dict[str, list[tuple[str, list]]] = {}
    notes = []
    for kind in FALLBACK_KINDS:
        index = chain_index(rs.get(kind))
        for alias, target in targets.items():
            if alias in index or target not in index:
                continue
            additions.setdefault(kind, []).append((alias, list(index[target])))
    for alias, target in targets.items():
        if not any(target in chain_index(rs.get(k)) or alias in chain_index(rs.get(k))
                   for k in FALLBACK_KINDS):
            notes.append(f"{alias} -> {target}: target has no fallback chain to inherit")
    return additions, notes


def _scalar(name: str) -> str:
    return name if PLAIN_SCALAR.fullmatch(name) else json.dumps(name)


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip(" "))


def _block_span(lines: list[str], kind: str) -> tuple[int, int, int]:
    """Locate router_settings.<kind>. Returns (end, item_indent, child_indent)."""
    rs_at = next(i for i, l in enumerate(lines) if re.match(r"router_settings:\s*$", l))
    key_re = re.compile(rf"(\s+){re.escape(kind)}:\s*$")
    key_at = key_indent = None
    for i in range(rs_at + 1, len(lines)):
        line = lines[i]
        if line.strip() and _indent(line) == 0:
            break
        m = key_re.match(line)
        if m:
            key_at, key_indent = i, len(m.group(1))
            break
    if key_at is None:
        raise ValueError(f"router_settings.{kind} is not a block list in this file")
    item_indent = child_indent = None
    end = key_at + 1
    for i in range(key_at + 1, len(lines)):
        line = lines[i]
        if not line.strip():
            continue
        ind = _indent(line)
        is_item = line.lstrip().startswith("- ")
        if ind < key_indent or (ind == key_indent and not is_item):
            break
        if item_indent is None:
            item_indent = ind
        elif child_indent is None and ind > item_indent:
            child_indent = ind
        end = i + 1
    if item_indent is None:
        raise ValueError(f"router_settings.{kind} has no entries to match the style of")
    return end, item_indent, child_indent if child_indent is not None else item_indent + 2


def render(text: str, additions: dict[str, list[tuple[str, list]]]) -> str:
    lines = text.splitlines(keepends=True)
    if lines and not lines[-1].endswith("\n"):
        lines[-1] += "\n"
    # Insert bottom-up so earlier spans stay valid.
    spans = sorted(((kind, _block_span(lines, kind)) for kind in additions),
                   key=lambda kv: kv[1][0], reverse=True)
    for kind, (insert_at, item_indent, child_indent) in spans:
        new = []
        for alias, chain in additions[kind]:
            new.append(" " * item_indent + f"- {_scalar(alias)}:\n")
            new.extend(" " * child_indent + f"- {_scalar(str(m))}\n" for m in chain)
        lines[insert_at:insert_at] = new
    return "".join(lines)


def expected(config: dict, additions: dict[str, list[tuple[str, list]]]) -> dict:
    want = copy.deepcopy(config)
    for kind, items in additions.items():
        want["router_settings"][kind].extend({alias: chain} for alias, chain in items)
    return want


def build(text: str) -> tuple[str, dict, list[str]]:
    config = yaml.safe_load(text)
    additions, notes = plan(config)
    if not additions:
        return text, additions, notes
    new_text = render(text, additions)
    if yaml.safe_load(new_text) != expected(config, additions):
        raise RuntimeError("rendered config does not parse to original + additions; refusing")
    return new_text, additions, notes


def describe(additions) -> list[str]:
    return [f"router_settings.{kind}: + {alias} -> {chain}"
            for kind, items in additions.items() for alias, chain in items]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("check"); p.add_argument("config", type=Path)
    p = sub.add_parser("apply"); p.add_argument("config", type=Path)
    p.add_argument("--write", action="store_true", help="back up and edit CONFIG in place")
    a = ap.parse_args(argv)

    text = a.config.read_text()
    new_text, additions, notes = build(text)
    for n in notes:
        print(f"note: {n}")
    for d in describe(additions):
        print(("missing: " if a.cmd == "check" else "add: ") + d)
    if a.cmd == "check":
        print("ok: every alias carries its target's fallback chains" if not additions
              else f"{sum(map(len, additions.values()))} alias chain(s) missing")
        return 1 if additions else 0
    if not additions:
        print("nothing to add")
        return 0
    if not a.write:
        print("dry run; pass --write to apply")
        return 0
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup = a.config.with_name(f"{a.config.name}.bak-wq4102-{stamp}")
    shutil.copy2(a.config, backup)
    # In place, not os.replace: the gateway bind-mounts this single file, and a
    # file mount pins the inode.
    a.config.write_text(new_text)
    print(f"wrote {a.config} (backup {backup})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
