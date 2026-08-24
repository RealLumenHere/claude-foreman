#!/usr/bin/env python3
"""wq#888 -- how much of our real tool-result surface a stdout filter can reach.

research/HEADROOM_EVAL.md §3 reports "93.7% JSON dicts" for our tool results, and
that number is what makes SmartCrusher look like the highest-value engine on the
stack. It is measured on the *tool_result wrapper* -- the `{stdout, stderr,
exitCode}` envelope claude-code builds around a Bash call. The only hook point
that exists (PreToolUse `updatedInput`, see SMARTCRUSHER_HOOK.md) rewrites the
command, so a filter runs inside the pipeline and can only touch the *stdout
bytes*. The envelope is assembled afterwards, out of reach.

So the addressable surface is not "93.7% of results". It is "the subset of Bash
stdout that is itself JSON or JSONL". This probe measures that subset, and runs
harness/crush.py over it to get the achieved saving rather than a claimed one.

Reproduce:  python3 research/smartcrusher_probe.py
Source:     /home/lumen/agents/work-queue/_runs/*.jsonl (same corpus as
            research/call_audit.py, per wq#704's data-source correction)
"""

import glob
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "harness"))
import crush  # noqa: E402

RUNS = "/home/lumen/agents/work-queue/_runs/*.jsonl"

# claude-code prefixes a nonzero-exit Bash result with this line. It is part of
# the result but not part of stdout, so it is stripped before asking "is this
# JSON" -- otherwise every failed command looks unaddressable for the wrong
# reason.
EXIT_PREFIX = "Exit code "


def blocks(frame):
    m = frame.get("message") or {}
    if not isinstance(m, dict):
        return []
    c = m.get("content")
    return c if isinstance(c, list) else []


def stdout_of(content):
    """The bytes a PreToolUse-injected filter would actually see."""
    if not isinstance(content, str):
        return None
    if content.startswith(EXIT_PREFIX):
        nl = content.find("\n")
        return content[nl + 1:] if nl >= 0 else ""
    return content


def main():
    paths = sorted(glob.glob(RUNS))
    if not paths:
        print(f"no transcripts at {RUNS}", file=sys.stderr)
        return 1

    per_tool = {}          # tool -> [count, bytes]
    bash = {"n": 0, "bytes": 0}
    addressable = {"n": 0, "in": 0, "out": 0, "json": 0, "jsonl": 0}
    over_cap = {"n": 0, "bytes": 0}   # results above the wq#729 2000-byte cap
    wins = []

    for path in paths:
        names = {}
        with open(path, errors="replace") as fh:
            for line in fh:
                try:
                    frame = json.loads(line)
                except ValueError:
                    continue
                for b in blocks(frame):
                    if b.get("type") == "tool_use":
                        names[b.get("id")] = b.get("name")
                    elif b.get("type") == "tool_result":
                        name = names.get(b.get("tool_use_id"), "?")
                        content = b.get("content")
                        text = content if isinstance(content, str) else json.dumps(content)
                        slot = per_tool.setdefault(name, [0, 0])
                        slot[0] += 1
                        slot[1] += len(text)
                        if name != "Bash":
                            continue
                        bash["n"] += 1
                        bash["bytes"] += len(text)
                        if len(text) > 2000:
                            over_cap["n"] += 1
                            over_cap["bytes"] += len(text)
                        out_bytes = stdout_of(content)
                        if out_bytes is None:
                            continue
                        crushed, st = crush.crush_text(out_bytes)
                        if st["kind"] == "passthrough":
                            continue
                        # Losslessness is a property of the encoding, but assert
                        # it here too: a probe that overstates savings by silently
                        # dropping data is worse than no probe.
                        orig, _ = crush._parse(out_bytes)
                        assert crush.decode(json.loads(crushed)) == orig, path
                        addressable["n"] += 1
                        addressable["in"] += st["in"]
                        addressable["out"] += st["out"]
                        addressable[st["kind"]] += 1
                        wins.append((st["in"] - st["out"], st["in"], st["out"], path))

    tot_n = sum(v[0] for v in per_tool.values())
    tot_b = sum(v[1] for v in per_tool.values())
    print(f"corpus: {len(paths)} sessions, {tot_n} tool results, {tot_b:,} bytes\n")

    print("bytes by tool:")
    for name, (n, b) in sorted(per_tool.items(), key=lambda kv: -kv[1][1]):
        print(f"  {name:<14} {n:>5} results  {b:>10,} B  {100*b/tot_b:>5.1f}%")

    print(f"\nBash: {bash['n']} results, {bash['bytes']:,} B "
          f"({100*bash['bytes']/tot_b:.1f}% of all result bytes)")
    print(f"  above the wq#729 2000 B cap: {over_cap['n']} results, {over_cap['bytes']:,} B")

    a = addressable
    print(f"\naddressable (stdout parses as JSON or JSONL): {a['n']} of {bash['n']} Bash "
          f"results = {100*a['n']/bash['n']:.1f}% by count")
    print(f"  json={a['json']}  jsonl={a['jsonl']}")
    if a["in"]:
        print(f"  {a['in']:,} B in -> {a['out']:,} B out = {100*(1-a['out']/a['in']):.1f}% saved "
              f"on the addressable subset")
    saved = a["in"] - a["out"]
    print(f"\nnet: {saved:,} B saved = {100*saved/bash['bytes']:.2f}% of Bash result bytes, "
          f"{100*saved/tot_b:.2f}% of all tool-result bytes")

    if wins:
        print("\ntop savings:")
        for d, i, o, p in sorted(wins, reverse=True)[:5]:
            print(f"  -{d:>7,} B  {i:>7,} -> {o:>6,}  {os.path.basename(p)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
