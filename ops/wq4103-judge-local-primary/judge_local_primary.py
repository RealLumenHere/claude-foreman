#!/usr/bin/env python3
"""wq#4103 -- make lumen-local-judge the primary for the lumen/judge gateway group.

Problem (fleet-health-c8220e, 2026-10-16): the LiteLLM `lumen/judge` model group
points its primary deployment at `ollama/hathor-judge` with `timeout: 10`, and
falls back to `lumen-local-judge`. Hathor is shut down after work BY POLICY
(Frank 2026-09-18, auto-sleep.timer; ISSUE-LOG #1235), so every judge call blocks
the full 10s on the unreachable primary before the fallback serves it. Measured
20.2s / 11.2s per verdict, 100% of traffic on the fallback.

Decision (option a of the ticket): make `lumen-local-judge` the primary until
wake-on-demand is viable. The ticket forbids ad-hoc hathor wake (power policy +
the tailscaled cold-boot race; wake-dispatch.sh unfixed), so demoting hathor out
of the hot path is the only change that stops the per-call 10s now. The judge
gate is fail-closed by design (judge_common.parse_verdict returns None on any
non-verdict -> caller denies), so a terminal local primary with no asleep-node
fallback is correct: a local outage fails closed immediately instead of paying
10s first.

Transformation (three minimal, line-level edits, indentation preserved):
  1. model_list: rename the `model_name: lumen/judge` entry to `hathor-judge`
     (its backend def is kept intact as an unused rung a future wake-on-demand
     ticket can re-wire; renaming frees the group name for the alias).
  2. router_settings.model_group_alias: add `lumen/judge: lumen-local-judge`.
  3. router_settings.fallbacks: drop the `lumen/judge -> [lumen-local-judge]`
     entry (local is now the primary, not the fallback; no asleep hathor in the
     chain means no wasted 10s on a local failure either).

A caller still sends `model: lumen/judge`; the alias rewrites it to
`lumen-local-judge` and routes straight to the local qwen2.5-coder:7b.

The live config is credential-bearing: this tool prints only model-group names
from it, never a value. Edits are line operations, so the rest of the file stays
byte-for-byte; the result is re-parsed and compared field-by-field against the
intended delta before anything is written.

  judge_local_primary.py check CONFIG          exit 0 if already local-primary,
                                               1 if still hathor-primary, 2 if the
                                               config is in neither known shape
  judge_local_primary.py apply CONFIG          print the diff that would be made
  judge_local_primary.py apply CONFIG --write  back up CONFIG, then apply in place
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

GROUP = "lumen/judge"
LOCAL = "lumen-local-judge"
RUNG = "hathor-judge"

MODEL_NAME_RE = re.compile(r"^(\s*-\s*model_name:\s*)" + re.escape(GROUP) + r"\s*$")
ALIAS_HDR_RE = re.compile(r"^(\s*)model_group_alias:\s*$")
ALIAS_CHILD_RE = re.compile(r"^(\s+)\S")
FB_KEY_RE = re.compile(r"^(\s*)-\s*" + re.escape(GROUP) + r":\s*$")
FB_ITEM_RE = re.compile(r"^\s*-\s*" + re.escape(LOCAL) + r"\s*$")


def model_names(cfg: dict) -> list[str]:
    return [m.get("model_name") for m in (cfg.get("model_list") or [])]


def aliases(cfg: dict) -> dict:
    return ((cfg.get("router_settings") or {}).get("model_group_alias") or {})


def fallbacks(cfg: dict) -> list:
    return ((cfg.get("router_settings") or {}).get("fallbacks") or [])


def classify(cfg: dict) -> str:
    """hathor-primary | local-primary | unknown."""
    names = model_names(cfg)
    al = aliases(cfg)
    if GROUP in names and GROUP not in al:
        return "hathor-primary"
    if GROUP not in names and al.get(GROUP) == LOCAL:
        return "local-primary"
    return "unknown"


def expected_after(cfg: dict) -> dict:
    """The structure the text transform must produce, computed from the parsed
    original. Used to prove the line edits changed nothing but the intended
    delta."""
    out = copy.deepcopy(cfg)
    for m in out.get("model_list") or []:
        if m.get("model_name") == GROUP:
            m["model_name"] = RUNG
    rs = out.setdefault("router_settings", {})
    rs.setdefault("model_group_alias", {})[GROUP] = LOCAL
    rs["fallbacks"] = [f for f in (rs.get("fallbacks") or []) if GROUP not in f]
    return out


def _rename_model_name(lines: list[str]) -> list[str]:
    hits = [i for i, ln in enumerate(lines) if MODEL_NAME_RE.match(ln)]
    if len(hits) != 1:
        raise SystemExit(f"expected exactly one `model_name: {GROUP}` line, found {len(hits)}")
    i = hits[0]
    lines[i] = MODEL_NAME_RE.sub(r"\1" + RUNG, lines[i])
    return lines


def _add_alias(lines: list[str]) -> list[str]:
    hdr = next((i for i, ln in enumerate(lines) if ALIAS_HDR_RE.match(ln)), None)
    if hdr is None:
        raise SystemExit("no `model_group_alias:` block found in router_settings")
    child_indent = None
    insert_at = hdr + 1
    for j in range(hdr + 1, len(lines)):
        m = ALIAS_CHILD_RE.match(lines[j])
        if m and len(m.group(1)) > len(ALIAS_HDR_RE.match(lines[hdr]).group(1)):
            child_indent = m.group(1)
            insert_at = j + 1
        else:
            break
    if child_indent is None:
        child_indent = ALIAS_HDR_RE.match(lines[hdr]).group(1) + "  "
    lines.insert(insert_at, f"{child_indent}{GROUP}: {LOCAL}")
    return lines


def _drop_fallback(lines: list[str]) -> list[str]:
    key = next((i for i, ln in enumerate(lines) if FB_KEY_RE.match(ln)), None)
    if key is None:
        raise SystemExit(f"no `- {GROUP}:` fallback entry found")
    end = key + 1
    while end < len(lines) and FB_ITEM_RE.match(lines[end]):
        end += 1
    # entry must hold exactly lumen-local-judge; anything else is unexpected shape
    item_lines = lines[key + 1:end]
    if len(item_lines) != 1:
        raise SystemExit(
            f"`- {GROUP}:` fallback has {len(item_lines)} items, expected only {LOCAL}; aborting")
    del lines[key:end]
    return lines


def transform(text: str, original: dict) -> tuple[str, dict]:
    lines = text.split("\n")
    trailing_nl = text.endswith("\n")
    if trailing_nl:
        lines = lines[:-1]
    lines = _rename_model_name(lines)
    lines = _add_alias(lines)
    lines = _drop_fallback(lines)
    new_text = "\n".join(lines) + ("\n" if trailing_nl else "")

    got = yaml.safe_load(new_text)
    want = expected_after(original)
    if got != want:
        raise SystemExit("post-edit config does not match the intended delta -- refusing to write")
    if classify(got) != "local-primary":
        raise SystemExit(f"post-edit config is {classify(got)!r}, expected local-primary")
    summary = {
        "renamed": f"model_list: {GROUP} -> {RUNG}",
        "alias_added": f"model_group_alias: {GROUP} -> {LOCAL}",
        "fallback_removed": f"{GROUP} -> [{LOCAL}]",
    }
    return new_text, summary


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    pc = sub.add_parser("check")
    pc.add_argument("config")
    pa = sub.add_parser("apply")
    pa.add_argument("config")
    pa.add_argument("--write", action="store_true")
    args = p.parse_args(argv)

    path = Path(args.config)
    text = path.read_text()
    cfg = yaml.safe_load(text)
    state = classify(cfg)

    if args.cmd == "check":
        print(state)
        return {"local-primary": 0, "hathor-primary": 1}.get(state, 2)

    if state == "local-primary":
        print("already local-primary; nothing to do")
        return 0
    if state != "hathor-primary":
        print(f"config is {state!r}, not the expected hathor-primary shape; aborting")
        return 2

    new_text, summary = transform(text, cfg)
    for k, v in summary.items():
        print(f"  {k}: {v}")
    if not args.write:
        print("(dry run; pass --write to apply)")
        return 0
    stamp = dt.datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
    backup = path.with_name(path.name + f".bak-wq4103-{stamp}")
    shutil.copy2(path, backup)
    path.write_text(new_text)
    print(f"wrote {path} (backup {backup})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
