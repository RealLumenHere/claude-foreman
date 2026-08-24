#!/usr/bin/env python3
"""wq#951 -- are the 688 uncapped Bash results a historical artefact or a live leak?

wq#888's smartcrusher_probe.py found 688 Bash tool results above the wq#729
2000-byte cap, totalling 3.4 MB = 70% of all Bash result bytes, in a fleet that
is supposed to cap Bash at 2000. It could not say whether those predate the cap
or leak through it today. Two measurements answer that:

1. *Date split.* Every session is stamped against the moment wq#729's cap landed
   on main (56c9d51, 2026-08-21 05:08:51 +0000 -- the guard is deployed from
   /home/lumen/foreman/harness/guard.sh, so landing on main is deployment).

2. *Replay.* Session dates only say what the corpus is; they cannot say what
   today's guard would do. So every over-cap command is fed back through the
   live guard.sh as a PreToolUse envelope and classified by the decision it gets
   now: blocked, exempted (SKIP_CAP), or capped. The exempted bytes are the
   residual leak -- the part wq#729 would still not catch if the corpus were
   re-run this minute.

The replay redirects guard.sh's event log to a scratch dir; it is otherwise a
pure decision function, so this is safe to run repeatedly.

SUPERSEDED by wq#954: guard.sh no longer caps Bash at all -- the cap moved to
BASH_MAX_OUTPUT_LENGTH, which truncates the result rather than the command. Run
against a current guard, the replay now reports 100% "exempt", which means "the
guard rewrote nothing", not "the bytes leak". Point WQ951_GUARD at a pre-wq#954
guard to reproduce the measurements in UNCAPPED_BASH.md §2-3; the numbers there
are a point-in-time record of the design this ticket replaced.

Reproduce:  python3 research/uncapped_bash_probe.py
Source:     /home/lumen/agents/work-queue/_runs/*.jsonl + /home/lumen/foreman/_runs/*.jsonl
            (the work-queue root alone is wq#888's corpus, kept comparable)
"""

import datetime as dt
import glob
import json
import os
import re
import subprocess
import sys
import tempfile
from collections import Counter

ROOTS = [
    "/home/lumen/agents/work-queue/_runs/*.jsonl",
    "/home/lumen/foreman/_runs/*.jsonl",
]

# The guard in this checkout, so the probe measures the tree it ships with. Point
# WQ951_GUARD at /home/lumen/foreman/harness/guard.sh to replay against the
# deployed one instead.
GUARD = os.environ.get("WQ951_GUARD") or os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "harness", "guard.sh")

# 56c9d51 "feat(harness): cap Bash tool output via PreToolUse updatedInput" on main.
CAP_LANDED = dt.datetime(2026, 8, 21, 5, 8, 51, tzinfo=dt.timezone.utc)
CAP_BYTES = 2000

# claude-code's own Bash truncation: BASH_MAX_OUTPUT_LENGTH, default 30000 chars,
# upper limit 150000 (2.1.233 binary, see UNCAPPED_BASH.md §2). Any result at or
# just under this is evidence the native cap -- not the guard -- was the binding
# limit.
NATIVE_DEFAULT = 30000


def blocks(frame):
    m = frame.get("message") or {}
    if not isinstance(m, dict):
        return []
    c = m.get("content")
    return c if isinstance(c, list) else []


def session_records(path):
    """(command, result_bytes) for every Bash tool result in one transcript, plus cwd."""
    cmds, out, cwd = {}, [], None
    with open(path, errors="replace") as fh:
        for line in fh:
            try:
                frame = json.loads(line)
            except ValueError:
                continue
            cwd = cwd or frame.get("cwd")
            for b in blocks(frame):
                if b.get("type") == "tool_use":
                    if b.get("name") == "Bash":
                        cmds[b.get("id")] = (b.get("input") or {}).get("command")
                elif b.get("type") == "tool_result":
                    tid = b.get("tool_use_id")
                    if tid not in cmds:
                        continue
                    content = b.get("content")
                    text = content if isinstance(content, str) else json.dumps(content)
                    out.append((cmds[tid], len(text)))
    return out, cwd


def guard_verdict(command, cwd, events_dir):
    """What the live guard.sh does with this command today: blocked | exempt | capped."""
    envelope = json.dumps({
        "tool_name": "Bash",
        "tool_input": {"command": command},
        "session_id": "wq951-replay",
        "cwd": cwd or "/home/lumen/worktrees/wq951-a1",
    })
    env = dict(os.environ,
               FOREMAN_EVENTS_DIR=events_dir,
               FOREMAN_WORKER="wq951-replay",
               FOREMAN_WORKTREE_ROOT=cwd or "/home/lumen/worktrees/wq951-a1")
    p = subprocess.run([GUARD], input=envelope, capture_output=True, text=True, env=env)
    if p.returncode != 0:
        return "blocked"
    # An allowed Bash call emits the updatedInput rewrite only when it is capped;
    # SKIP_CAP produces exit 0 with no stdout.
    return "capped" if "updatedInput" in p.stdout else "exempt"


STATE_RE = re.compile(
    r"(^|[;&|\s])(cd|export|source|alias|unalias|pushd|popd|declare|readonly"
    r"|exec|eval|set|unset)\b")
AMP_RE = re.compile(r"(^|[^&])&([^&]|$)")          # guard.sh's background clause
REAL_BG_RE = re.compile(r"&\s*($|[;\n|])")          # an & that actually backgrounds


def skip_reason(command):
    """Which SKIP_CAP clause claims this command, splitting the & clause by whether
    the & it matched is a real background operator or a redirect like 2>&1."""
    parts = []
    if AMP_RE.search(command):
        parts.append("& real bg" if REAL_BG_RE.search(command) else "& fd-dup only")
    if STATE_RE.search(command):
        parts.append("shell-state verb")
    return " + ".join(parts) or "unclassified"


def main():
    paths = sorted(p for pat in ROOTS for p in glob.glob(pat))
    if not paths:
        print("no transcripts found", file=sys.stderr)
        return 1

    pre, post = [], []          # (path, mtime, over_cap_records)
    n_bash = n_over = 0
    over_bytes = 0
    over = []                   # (command, size, cwd)
    at_native = 0

    for path in paths:
        mtime = dt.datetime.fromtimestamp(os.path.getmtime(path), dt.timezone.utc)
        recs, cwd = session_records(path)
        n_bash += len(recs)
        hits = [(c, n) for c, n in recs if n > CAP_BYTES]
        n_over += len(hits)
        over_bytes += sum(n for _, n in hits)
        at_native += sum(1 for _, n in recs if n >= NATIVE_DEFAULT - 1000)
        over.extend((c, n, cwd) for c, n in hits)
        (pre if mtime < CAP_LANDED else post).append((path, mtime, len(hits)))

    print(f"corpus: {len(paths)} sessions, {n_bash} Bash results, "
          f"{n_over} over the {CAP_BYTES} B cap, {over_bytes:,} B\n")

    print(f"wq#729's cap landed on main {CAP_LANDED:%Y-%m-%d %H:%M} UTC "
          f"(56c9d51, deployed from /home/lumen/foreman)")
    for label, group in (("BEFORE the cap", pre), ("AFTER the cap", post)):
        n = sum(h for _, _, h in group)
        print(f"  sessions {label:<15} {len(group):>3}  ({n} over-cap results)")
    if pre:
        print(f"  pre-cap  session span: {min(m for _, m, _ in pre):%Y-%m-%d} .. "
              f"{max(m for _, m, _ in pre):%Y-%m-%d}")
    if post:
        print(f"  post-cap session span: {min(m for _, m, _ in post):%Y-%m-%d} .. "
              f"{max(m for _, m, _ in post):%Y-%m-%d}")

    print(f"\nlargest single result: {max(n for _, n, _ in over):,} B  "
          f"(claude-code's native BASH_MAX_OUTPUT_LENGTH default is {NATIVE_DEFAULT:,} chars; "
          f"{at_native} results land within 1 KB of it)")

    # ---- replay every distinct over-cap command through the live guard ----
    with tempfile.TemporaryDirectory(prefix="wq951-guard-") as events:
        verdicts, cache = Counter(), {}
        bytes_by = Counter()
        reasons, reason_bytes = Counter(), Counter()
        for cmd, size, cwd in over:
            if not cmd:
                verdicts["no-command-recorded"] += 1
                bytes_by["no-command-recorded"] += size
                continue
            key = (cmd, cwd)
            if key not in cache:
                cache[key] = guard_verdict(cmd, cwd, events)
            v = cache[key]
            verdicts[v] += 1
            bytes_by[v] += size
            if v == "exempt":
                r = skip_reason(cmd)
                reasons[r] += 1
                reason_bytes[r] += size

    print(f"\nreplayed through {GUARD} ({len(cache)} distinct commands):")
    for v, n in verdicts.most_common():
        print(f"  {v:<20} {n:>4} results  {bytes_by[v]:>10,} B  "
              f"{100*bytes_by[v]/over_bytes:>5.1f}% of over-cap bytes")

    leak = bytes_by["exempt"]
    print(f"\nresidual leak: {verdicts['exempt']} results, {leak:,} B "
          f"= {100*leak/over_bytes:.1f}% of the {over_bytes/1e6:.1f} MB")
    for r, n in reasons.most_common():
        print(f"  SKIP_CAP via {r:<32} {n:>4} results  {reason_bytes[r]:>10,} B")

    heads = Counter((c or "").strip().split()[0] if (c or "").strip() else "?"
                    for c, _, _ in over)
    print("\ncommand heads (all over-cap):",
          "  ".join(f"{h} {n}" for h, n in heads.most_common(10)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
