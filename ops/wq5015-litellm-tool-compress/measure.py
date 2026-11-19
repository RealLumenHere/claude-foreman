#!/usr/bin/env python3
"""wq#5015: measure tool_result compression on real transcripts. Prints aggregates only (no
content), so it is safe to run over transcripts that may hold secrets.

  python3 measure.py [--days 7] [--max-files 80] [--seed 5015] [ROOT ...]

ROOT defaults to /home/lumen/.claude/projects. Tokens: tiktoken cl100k_base when importable
(a stand-in for both Claude and GPT tokenizers), else chars/4.
"""
import argparse
import collections
import glob
import json
import os
import random
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import tool_result_compress as trc  # noqa: E402

try:
    import tiktoken
    _enc = tiktoken.get_encoding("cl100k_base")

    def ntok(s):
        return len(_enc.encode(s, disallowed_special=()))
    TOK = "cl100k_base"
except Exception:
    def ntok(s):
        return (len(s) + 3) // 4
    TOK = "chars/4"


def texts_of(content):
    if isinstance(content, str):
        yield content
    elif isinstance(content, list):
        for p in content:
            if isinstance(p, dict) and p.get("type") == "text" and isinstance(p.get("text"), str):
                yield p["text"]


def walk(path):
    """yield ('tool', text) for tool_result text and ('other', text) for all other text."""
    with open(path, errors="replace") as f:
        for line in f:
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            msg = rec.get("message") if isinstance(rec, dict) else None
            if not isinstance(msg, dict):
                continue
            c = msg.get("content")
            if isinstance(c, str):
                yield "other", c
                continue
            if not isinstance(c, list):
                continue
            for b in c:
                if not isinstance(b, dict):
                    continue
                if b.get("type") == "tool_result":
                    for t in texts_of(b.get("content")):
                        yield "tool", t
                elif b.get("type") == "text" and isinstance(b.get("text"), str):
                    yield "other", b["text"]
                elif b.get("type") == "tool_use":
                    yield "other", json.dumps(b.get("input"))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=float, default=7)
    ap.add_argument("--max-files", type=int, default=80)
    ap.add_argument("--seed", type=int, default=5015)
    ap.add_argument("roots", nargs="*", default=["/home/lumen/.claude/projects"])
    a = ap.parse_args()
    cutoff = time.time() - a.days * 86400
    files = sorted(p for r in a.roots for p in glob.glob(os.path.join(r, "**", "*.jsonl"),
                                                        recursive=True)
                   if os.path.getmtime(p) >= cutoff)
    random.Random(a.seed).shuffle(files)
    files = files[: a.max_files]
    tool_in = tool_out = other = n_tool = n_hit = 0
    by = collections.defaultdict(lambda: [0, 0, 0])  # filter -> [hits, tok_in, tok_out]
    for p in files:
        for kind, text in walk(p):
            if kind == "other":
                other += ntok(text)
                continue
            n_tool += 1
            before = ntok(text)
            out, name = trc.compress_text(text)
            after = ntok(out) if name else before
            if name and after >= before:  # fewer chars but not fewer tokens: count as kept
                after = before
            tool_in += before
            tool_out += after
            if name:
                n_hit += 1
                by[name][0] += 1
                by[name][1] += before
                by[name][2] += after
    saved = tool_in - tool_out
    print(f"files={len(files)} (last {a.days:g}d, seed {a.seed}) tokenizer={TOK}")
    print(f"tool_results={n_tool} compressed={n_hit} ({100 * n_hit / max(n_tool, 1):.1f}%)")
    print(f"tool_result tokens: {tool_in} -> {tool_out}  saved {saved} "
          f"({100 * saved / max(tool_in, 1):.1f}% of tool-result tokens)")
    total = tool_in + other
    print(f"all message tokens in sample: {total}; tool results are "
          f"{100 * tool_in / max(total, 1):.1f}%; saving = {100 * saved / max(total, 1):.1f}% "
          f"of all message tokens")
    for name, (h, i, o) in sorted(by.items(), key=lambda kv: -(kv[1][1] - kv[1][2])):
        print(f"  {name:14s} hits={h:5d} tokens {i:9d} -> {o:9d}  "
              f"saved {i - o:8d} ({100 * (i - o) / max(i, 1):.1f}% on hits)")


if __name__ == "__main__":
    main()
