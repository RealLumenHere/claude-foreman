#!/usr/bin/env python3
"""wq#4148 -- serve lumen-local-judge through Ollama's chat endpoint.

The rung is `model: ollama/qwen2.5-coder:7b`. LiteLLM's `ollama/` provider posts
to /api/generate with the messages flattened into one '### System:/### User:'
prompt (ollama journal: every request from the gateway container, 172.20.0.2, is
POST /api/generate), so qwen's chat template wraps the whole blob as a single
user turn and the judge rubric never sits in the system slot. Replaying the
calibration's 8 false-allows through that flattened path reproduces 8/8; through
/api/chat (what `ollama_chat/` sends) only 3/8 recur (bench_direct.py).

This rewrites that one line to `ollama_chat/`, nothing else, and refuses unless a
re-parse shows exactly that delta. The live config is credential-bearing, so it
prints only the rung name and provider.

  judge_chat_transport.py check CONFIG       exit 0 chat, 1 generate, 2 other
  judge_chat_transport.py apply CONFIG [--write]
"""
import argparse
import copy
import datetime as dt
import re
import shutil
import sys
from pathlib import Path

import yaml

RUNG = "lumen-local-judge"
OLD, NEW = "ollama/", "ollama_chat/"
RUNG_RE = re.compile(r"^\s*-\s*model_name:\s*" + re.escape(RUNG) + r"\s*$")
NEXT_ENTRY_RE = re.compile(r"^\s*-\s*model_name:")
MODEL_RE = re.compile(r"^(\s*model:\s*)" + re.escape(OLD) + r"(\S+)\s*$")


def rung(cfg: dict) -> dict | None:
    hits = [m for m in cfg.get("model_list") or [] if m.get("model_name") == RUNG]
    return hits[0] if len(hits) == 1 else None


def classify(cfg: dict) -> str:
    r = rung(cfg)
    model = ((r or {}).get("litellm_params") or {}).get("model", "")
    if model.startswith(NEW):
        return "chat"
    if model.startswith(OLD):
        return "generate"
    return "other"


def transform(text: str, cfg: dict) -> str:
    lines = text.split("\n")
    starts = [i for i, ln in enumerate(lines) if RUNG_RE.match(ln)]
    if len(starts) != 1:
        raise SystemExit(f"expected one `- model_name: {RUNG}` line, found {len(starts)}")
    start = starts[0]
    end = next((i for i in range(start + 1, len(lines)) if NEXT_ENTRY_RE.match(lines[i])),
               len(lines))
    hits = [i for i in range(start + 1, end) if MODEL_RE.match(lines[i])]
    if len(hits) != 1:
        raise SystemExit(f"expected one `model: {OLD}...` line in the {RUNG} entry, found {len(hits)}")
    lines[hits[0]] = MODEL_RE.sub(lambda m: m.group(1) + NEW + m.group(2), lines[hits[0]])
    new_text = "\n".join(lines)

    want = copy.deepcopy(cfg)
    params = rung(want)["litellm_params"]
    params["model"] = NEW + params["model"][len(OLD):]
    if yaml.safe_load(new_text) != want:
        raise SystemExit("post-edit config does not match the intended one-field delta -- refusing to write")
    return new_text


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("check").add_argument("config")
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
        return {"chat": 0, "generate": 1}.get(state, 2)
    if state == "chat":
        print(f"{RUNG} already on {NEW}; nothing to do")
        return 0
    if state != "generate":
        print(f"{RUNG} is not an {OLD} rung; aborting")
        return 2
    new_text = transform(text, cfg)
    print(f"  {RUNG}: {OLD} -> {NEW}")
    if not args.write:
        print("(dry run; pass --write to apply)")
        return 0
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup = path.with_name(path.name + f".bak-wq4148-{stamp}")
    shutil.copy2(path, backup)
    path.write_text(new_text)
    print(f"wrote {path} (backup {backup})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
