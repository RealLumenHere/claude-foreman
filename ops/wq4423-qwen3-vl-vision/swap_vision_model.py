#!/usr/bin/env python3
"""wq#4423 — retarget the `lumen-vision` LiteLLM deployment from moondream
(ollama/vision, ~1.7GB, weak OCR/dense-text) to qwen3-vl:8b (~5GB,
massively more capable: PCB component ID, document OCR, screenshot
analysis, Freedom Bot camera feed, Limpet vision pipeline — also
video-capable, moondream is not).

`router_settings.model_group_alias.lumen/vision -> lumen-vision` already
exists on the live config (wq#4102, verified in
ops/wq4102-litellm-alias-fallbacks/tests/fixtures/live_router_settings_20261017.yaml),
so retargeting the one `lumen-vision` deployment's underlying model is
enough to make `lumen/vision` serve qwen3-vl — no new model_list entry or
alias needed.

Same safety pattern as wq#3212/wq#4135/wq#4213: line-level text surgery
(comments preserved, never a YAML round-trip), the result is re-parsed and
checked against an independently-built expected plan before writing, and
the write is a timestamped-backup in-place edit (the gateway bind-mounts
this one file).

  swap_vision_model.py check CONFIG
  swap_vision_model.py apply CONFIG [--write]
     [--group lumen-vision] [--from-model ollama/vision]
     [--to-model ollama/qwen3-vl:8b]

Exit (check): 0 if --to-model is already live, 1 if --from-model is still
live (needs apply), 2 if neither value is present (config drifted, don't guess).
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

GROUP = "lumen-vision"
FROM_MODEL = "ollama/vision"
TO_MODEL = "ollama/qwen3-vl:8b"

ENTRY_RE = re.compile(r"^(\s*)-\s+model_name:\s*['\"]?([^'\"\s]+)")
MODEL_LINE_RE = re.compile(r"^(\s*)model:\s*(.+?)\s*$")


def _entry_span(lines: list[str], group: str) -> tuple[int, int, int]:
    """(indent, start, end) of the model_list entry named `group`. Raises if
    not found or if more than one deployment shares the name (ambiguous —
    refuse rather than guess which one to touch)."""
    spans = []
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
        spans.append((indent, start, end))
        i = end
    if not spans:
        raise RuntimeError(f"model_list entry model_name: {group!r} not found")
    if len(spans) > 1:
        raise RuntimeError(f"{len(spans)} deployments named {group!r} — ambiguous, refusing")
    return spans[0]


def swap_model(text: str, group: str, from_model: str, to_model: str) -> tuple[str, bool]:
    """Replace the litellm_params.model value inside the `group` entry.
    Returns (new_text, changed). Refuses (raises) if the entry's current
    value is neither from_model nor to_model — a drifted config is a stop,
    not a guess."""
    lines = text.splitlines(keepends=True)
    indent, start, end = _entry_span(lines, group)
    for i in range(start, end):
        m = MODEL_LINE_RE.match(lines[i])
        if not m or len(m.group(1)) <= indent:
            continue
        current = m.group(2)
        if current == to_model:
            return text, False
        if current != from_model:
            raise RuntimeError(
                f"{group}: litellm_params.model is {current!r}, expected "
                f"{from_model!r} or {to_model!r} — refusing to guess")
        lines[i] = m.group(1) + f"model: {to_model}\n"
        return "".join(lines), True
    raise RuntimeError(f"{group}: no litellm_params.model line found in entry")


def plan(config: dict, group: str, to_model: str) -> dict:
    """Expected post-edit config, built independently of the text surgery."""
    want = copy.deepcopy(config)
    found = False
    for entry in want.get("model_list", []):
        if entry.get("model_name") == group:
            entry.setdefault("litellm_params", {})["model"] = to_model
            found = True
    if not found:
        raise RuntimeError(f"model_list entry model_name: {group!r} not found")
    return want


def build(text: str, group: str, from_model: str, to_model: str) -> tuple[str, bool]:
    config = yaml.safe_load(text)
    want = plan(config, group, to_model)
    new_text, changed = swap_model(text, group, from_model, to_model)
    if yaml.safe_load(new_text) != want:
        raise RuntimeError("rendered config does not match the expected plan; refusing to write")
    return new_text, changed


def do_check(text: str, group: str, from_model: str, to_model: str) -> int:
    config = yaml.safe_load(text)
    entries = [e for e in config.get("model_list", []) if e.get("model_name") == group]
    if len(entries) != 1:
        print(f"{group}: {len(entries)} deployment(s) (expected exactly 1)")
        return 2
    current = (entries[0].get("litellm_params") or {}).get("model")
    print(f"{group}: litellm_params.model = {current!r}")
    if current == to_model:
        print("ok: already on qwen3-vl")
        return 0
    if current == from_model:
        print("needs apply: still on moondream")
        return 1
    print(f"UNKNOWN model value (neither {from_model!r} nor {to_model!r}) — config drifted, not applying blind")
    return 2


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("check")
    p.add_argument("config", type=Path)
    p.add_argument("--group", default=GROUP)
    p.add_argument("--from-model", default=FROM_MODEL)
    p.add_argument("--to-model", default=TO_MODEL)

    p = sub.add_parser("apply")
    p.add_argument("config", type=Path)
    p.add_argument("--group", default=GROUP)
    p.add_argument("--from-model", default=FROM_MODEL)
    p.add_argument("--to-model", default=TO_MODEL)
    p.add_argument("--write", action="store_true", help="back up and edit CONFIG in place")

    a = ap.parse_args(argv)
    text = a.config.read_text()

    if a.cmd == "check":
        return do_check(text, a.group, a.from_model, a.to_model)

    new_text, changed = build(text, a.group, a.from_model, a.to_model)
    print(f"{a.group}: litellm_params.model -> {a.to_model!r}: {'changed' if changed else 'already set'}")
    if not changed:
        return 0
    if not a.write:
        print("dry run; pass --write to apply")
        return 0

    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup = a.config.with_name(f"{a.config.name}.bak-wq4423-{stamp}")
    shutil.copy2(a.config, backup)
    a.config.write_text(new_text)
    print(f"wrote {a.config} (backup {backup})")
    print("NOTE: the litellm container reads the config only at startup — "
          "queue a recreate/restart (see README.md window plan) to load this edit.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
