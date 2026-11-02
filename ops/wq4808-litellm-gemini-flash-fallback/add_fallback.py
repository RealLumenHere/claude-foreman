#!/usr/bin/env python3
"""wq#4808 — add an explicit `router_settings.fallbacks` entry.

`gemini-flash` has no fallback entry, so a free-tier 429 on the Gemini rung
(20 RPD on `gemini-2.5-flash`, research/models.yaml:271, wq#2580) hard-fails
with "No fallback model group found for original model_group=gemini-flash"
instead of landing on a working rung (ops/wq2471-litellm-429-fallback/README.md,
wq#2065's 24h log window). wq#2471 and wq#2627 both proposed
`gemini-flash -> cerebras-oss-120b`, but every Cerebras rung has returned 402
Payment Required since the 2026-08-19 billing lapse (wq#4135's probe table) —
neither fragment was ever safe to merge, which is why gemini-flash still has
no entry today.

This ticket targets `deepseek-v4-flash` instead: a live chat-tier rung with
its own existing fallback to `openrouter-free` (the committed live-shape
fixture below has `deepseek-v4-flash: [openrouter-free]` already), giving
gemini-flash -> deepseek-v4-flash -> openrouter-free: depth 2, terminal at a
rung with headroom (already the shared fallback for four other groups per
wq#4135). Both rungs are fast/cheap chat tier, and nothing in this repo
routes gemini-flash to a vision/multimodal path (grepped council.yaml,
council.py, minerva_verify.py callers — all text judge/research seats; the
only vision-capable group is lumen-vision, a separate LiteLLM model_name),
so no route needs excluding.

  add_fallback.py check CONFIG --group NAME                    exit 1 if NAME has no entry
  add_fallback.py apply CONFIG --group NAME --to NAME2          dry run
  add_fallback.py apply CONFIG --group NAME --to NAME2 --write  back up, edit in place

Same safety pattern as wq#4102/wq#4135: a line-level insertion so the rest of
the file stays byte-identical, the result is re-parsed and compared against
original + the new entry before writing, and the write is in place (the
gateway bind-mounts this single file, pinning the inode). An existing entry
for NAME is left alone (explicit wins) rather than duplicated.
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


def chain_index(entries: list) -> dict[str, list]:
    index = {}
    for item in entries or []:
        if isinstance(item, dict) and len(item) == 1:
            key, chain = next(iter(item.items()))
            index.setdefault(str(key), chain)
    return index


def _block_span(lines: list[str], kind: str) -> tuple[int, int, int]:
    """Locate router_settings.<kind>. Returns (insert-at, item_indent, child_indent)."""
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


def plan(config: dict, group: str, to: str, kind: str) -> tuple[list | None, list[str]]:
    rs = config.get("router_settings") or {}
    index = chain_index(rs.get(kind))
    if group in index:
        return None, [f"{group} already has a router_settings.{kind} entry: {index[group]} (left alone)"]
    return [to], []


def render(text: str, group: str, chain: list, kind: str) -> str:
    lines = text.splitlines(keepends=True)
    if lines and not lines[-1].endswith("\n"):
        lines[-1] += "\n"
    insert_at, item_indent, child_indent = _block_span(lines, kind)
    new = [" " * item_indent + f"- {group}:\n"]
    new.extend(" " * child_indent + f"- {m}\n" for m in chain)
    lines[insert_at:insert_at] = new
    return "".join(lines)


def build(text: str, group: str, to: str, kind: str = "fallbacks") -> tuple[str, list[str]]:
    config = yaml.safe_load(text)
    chain, notes = plan(config, group, to, kind)
    if chain is None:
        return text, notes
    new_text = render(text, group, chain, kind)
    want = copy.deepcopy(config)
    want.setdefault("router_settings", {}).setdefault(kind, []).append({group: chain})
    if yaml.safe_load(new_text) != want:
        raise RuntimeError("rendered config does not parse to original + addition; refusing")
    return new_text, [f"router_settings.{kind}: + {group} -> {chain}"]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("check")
    p.add_argument("config", type=Path)
    p.add_argument("--group", required=True)
    p.add_argument("--kind", default="fallbacks", choices=FALLBACK_KINDS)
    p = sub.add_parser("apply")
    p.add_argument("config", type=Path)
    p.add_argument("--group", required=True)
    p.add_argument("--to", required=True)
    p.add_argument("--kind", default="fallbacks", choices=FALLBACK_KINDS)
    p.add_argument("--write", action="store_true", help="back up and edit CONFIG in place")
    a = ap.parse_args(argv)

    text = a.config.read_text()
    if a.cmd == "check":
        config = yaml.safe_load(text)
        index = chain_index((config.get("router_settings") or {}).get(a.kind))
        has = a.group in index
        print(f"has entry: {a.group} -> {index.get(a.group)}" if has
              else f"missing: {a.group} has no router_settings.{a.kind} entry")
        return 0 if has else 1

    new_text, notes = build(text, a.group, a.to, a.kind)
    for n in notes:
        print(n)
    if new_text == text:
        print("nothing to add")
        return 0
    if not a.write:
        print("dry run; pass --write to apply")
        return 0
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup = a.config.with_name(f"{a.config.name}.bak-wq4808-{stamp}")
    shutil.copy2(a.config, backup)
    # In place, not os.replace: the gateway bind-mounts this single file, and a
    # file mount pins the inode.
    a.config.write_text(new_text)
    print(f"wrote {a.config} (backup {backup})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
