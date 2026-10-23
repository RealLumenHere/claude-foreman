#!/usr/bin/env python3
"""wq#4483 -- lumen/code routes to OFFLINE isis with no fallback.

isis (100.100.129.24) is hard-off per wq#4420. `lumen/code`'s single
deployment points at it and `router_settings.fallbacks` has no entry for
`lumen/code`, so every call dies instead of failing over.

Same shape as wq#4213/wq#4135/wq#3212: comment-preserving text surgery over
the real `model_list`/`router_settings.fallbacks` blocks, never a YAML
round-trip (a round-trip would drop the file's comments and key order).
Retargets `lumen/code`'s deployment onto a live model_name already defined
in the config (default: deepseek-v4-flash) and adds a `lumen/code` fallback
entry pointing at the same model, so a config that already has the target
model wired up elsewhere never needs a new deployment block.

  retarget_lumen_code.py check CONFIG
  retarget_lumen_code.py apply CONFIG [--target deepseek-v4-flash] [--write]
"""
from __future__ import annotations

import argparse
import re
import shutil
import sys
import time
from pathlib import Path

try:
    import yaml
except ImportError:
    yaml = None

ROUTE = "lumen/code"


def _load(text: str) -> dict:
    if yaml is None:
        raise RuntimeError("PyYAML not available; cannot validate config")
    return yaml.safe_load(text)


def check(path: Path) -> list[str]:
    """Return a list of problems; empty list means lumen/code is healthy."""
    cfg = _load(path.read_text())
    problems = []
    deployments = [m for m in cfg.get("model_list", []) if m.get("model_name") == ROUTE]
    if not deployments:
        problems.append(f"no model_list entry for {ROUTE}")
    fallbacks = cfg.get("router_settings", {}).get("fallbacks", [])
    has_fallback = any(ROUTE in f for f in fallbacks if isinstance(f, dict))
    if not has_fallback:
        problems.append(f"router_settings.fallbacks has no entry for {ROUTE}")
    return problems


def _target_exists(cfg: dict, target: str) -> bool:
    return any(m.get("model_name") == target for m in cfg.get("model_list", []))


def plan(text: str, target: str) -> str:
    """Comment-preserving text surgery: retarget lumen/code's api_base/model
    onto `target`'s existing deployment params, and add the fallback entry.
    Returns the new file text.
    """
    cfg = _load(text)
    if not _target_exists(cfg, target):
        raise ValueError(f"target model_name {target!r} is not defined in model_list")
    target_block = next(m for m in cfg["model_list"] if m.get("model_name") == target)
    target_params = target_block["litellm_params"]

    lines = text.splitlines(keepends=True)
    out = []
    i = 0
    in_route_block = False
    route_indent = None
    replaced_params = False
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()
        if stripped in (f"model_name: {ROUTE}", f"- model_name: {ROUTE}"):
            in_route_block = True
            dash_pos = line.find("-")
            route_indent = dash_pos if dash_pos != -1 else (len(line) - len(line.lstrip(" ")))
            out.append(line)
            i += 1
            continue
        if in_route_block and stripped.startswith("litellm_params:"):
            out.append(line)
            i += 1
            # Replace every line in this litellm_params block with the
            # target's params, re-indented to match.
            param_indent = " " * (route_indent + 4)
            for key, val in target_params.items():
                out.append(f"{param_indent}{key}: {val}\n")
            replaced_params = True
            # Skip the original litellm_params body.
            while i < len(lines):
                nxt = lines[i]
                nxt_strip = nxt.strip()
                if nxt_strip.startswith("- model_name:") or nxt_strip.startswith("model_info:") and in_route_block:
                    break
                if nxt.strip() == "" or (len(nxt) - len(nxt.lstrip(" "))) <= route_indent and nxt_strip:
                    break
                i += 1
            in_route_block = False
            continue
        out.append(line)
        i += 1
    if not replaced_params:
        raise ValueError(f"did not find a litellm_params block under model_name: {ROUTE}")
    new_text = "".join(out)

    new_cfg = _load(new_text)
    fallbacks = new_cfg.setdefault("router_settings", {}).setdefault("fallbacks", [])
    if not any(ROUTE in f for f in fallbacks if isinstance(f, dict)):
        fb_marker = "  fallbacks:\n"
        idx = new_text.index(fb_marker) + len(fb_marker)
        new_text = new_text[:idx] + f"  - {ROUTE}:\n    - {target}\n" + new_text[idx:]

    final_cfg = _load(new_text)
    expected = {
        "model_name": ROUTE,
        "litellm_params": target_params,
    }
    actual = next(m for m in final_cfg["model_list"] if m.get("model_name") == ROUTE)
    if actual.get("litellm_params") != expected["litellm_params"]:
        raise ValueError(
            f"plan mismatch: expected litellm_params {expected['litellm_params']!r}, "
            f"got {actual.get('litellm_params')!r}"
        )
    if not any(ROUTE in f for f in final_cfg["router_settings"]["fallbacks"] if isinstance(f, dict)):
        raise ValueError("plan mismatch: fallback entry was not added")
    return new_text


def apply_(path: Path, target: str, write: bool) -> str:
    text = path.read_text()
    new_text = plan(text, target)
    if write:
        ts = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
        backup = path.with_name(f"{path.name}.bak-wq4483-{ts}")
        shutil.copy2(path, backup)
        path.write_text(new_text)
    return new_text


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p_check = sub.add_parser("check")
    p_check.add_argument("config", type=Path)
    p_apply = sub.add_parser("apply")
    p_apply.add_argument("config", type=Path)
    p_apply.add_argument("--target", default="deepseek-v4-flash")
    p_apply.add_argument("--write", action="store_true")
    a = ap.parse_args(argv)

    if a.cmd == "check":
        problems = check(a.config)
        for p in problems:
            print(p)
        return 1 if problems else 0

    if a.cmd == "apply":
        try:
            new_text = apply_(a.config, a.target, a.write)
        except ValueError as e:
            print(f"ABORT: {e}", file=sys.stderr)
            return 3
        if not a.write:
            sys.stdout.write(new_text)
        return 0
    return 2


if __name__ == "__main__":
    sys.exit(main())
