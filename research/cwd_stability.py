#!/usr/bin/env python3
"""wq#705 T5 — cross-session prefix-cache reuse vs per-ticket worktree cwd.

Companion instrument to token_baseline.py (wq#629). Where that script rolled up
whole-session token burn from the pre-fworker `_runs/*.jsonl` bootstrap
transcripts, this one answers a narrower question that TOKEN_BASELINE.md and
PREFIX_STABILITY.md both left open:

    Does the per-ticket worktree cwd (WORKTREES_ROOT/wq{ticket}-a{attempt})
    actually prevent cross-session prompt-cache reuse, as PREFIX_STABILITY.md
    §1b predicted?

Source: the fworker-era session transcripts the CLI writes to
`~/.claude/projects/<cwd-slug>/<session_id>.jsonl`. That directory name IS the
cwd, url-slugged, so the corpus is self-labelling: one project dir per worker
worktree. This is exactly the follow-up corpus TOKEN_BASELINE.md's "What this
baseline does NOT cover" section asked the next foreman-tok ticket to pull.

Zero new Anthropic spend: reads existing transcripts only, makes no API calls.

The measurement rests on the first assistant turn of each session. On turn 1 a
session has no cache chain of its own, so `cache_read_input_tokens` there can
only come from a prefix that was already resident — i.e. it is the shared,
cwd-independent part of the request. `cache_creation_input_tokens` on the same
turn is its complement: everything from the first divergence onward, paid fresh
by every worker spawn.

Usage: python3 cwd_stability.py            # human-readable report
       python3 cwd_stability.py --json     # machine-readable, for diffing
"""
import collections
import glob
import json
import os
import re
import sys

PROJECTS = os.path.expanduser("~/.claude/projects")

# The CLI slugs cwd into the project dir name; worker worktrees are the ones
# matching WORKTREES_ROOT/wq{ticket}-a{attempt}.
WORKER_DIR_RE = re.compile(r"^-home-lumen-worktrees-wq(\d+)-a(\d+)$")

try:
    import tiktoken

    _ENC = tiktoken.get_encoding("cl100k_base")

    def toklen(s):
        return len(_ENC.encode(s))

    TOKENIZER = ("cl100k_base (proxy — Claude's own tokenizer is not exposed to this "
                 "box's OAuth CLI surface; valid for relative comparison, not exact "
                 "counts)")
except Exception:  # pragma: no cover - tiktoken is present on this box
    def toklen(s):
        return len(s) // 4

    TOKENIZER = "chars/4 fallback (tiktoken unavailable)"


def scan_session(path):
    """Pull first-turn usage, whole-session usage, and identity from one transcript.

    Assistant frames repeat a message id across streamed deltas, so usage is
    deduped by message id — double-counting it would inflate every total.
    """
    totals = collections.Counter()
    first = None
    model = started = cwd = branch = prompt = None
    deferred = None
    seen_ids = set()

    with open(path, errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                frame = json.loads(line)
            except json.JSONDecodeError:
                continue

            if cwd is None and frame.get("cwd"):
                cwd = frame["cwd"]
                branch = frame.get("gitBranch")

            # The dispatched worker prompt, verbatim, before the CLI wraps it.
            if prompt is None and frame.get("type") == "queue-operation" \
                    and frame.get("operation") == "enqueue":
                prompt = frame.get("content")

            att = frame.get("attachment") or {}
            if att.get("type") == "deferred_tools_delta":
                deferred = len(att.get("addedNames") or [])

            if frame.get("type") != "assistant":
                continue
            msg = frame.get("message") or {}
            usage = msg.get("usage") or {}
            if not usage or msg.get("id") in seen_ids:
                continue
            seen_ids.add(msg.get("id"))
            for key in ("input_tokens", "cache_creation_input_tokens",
                        "cache_read_input_tokens", "output_tokens"):
                totals[key] += usage.get(key, 0)
            if first is None:
                first = dict(usage)
                model = msg.get("model")
                started = frame.get("timestamp")

    if first is None:
        return None
    return {
        "session_id": os.path.basename(path)[:-len(".jsonl")],
        "cwd": cwd,
        "branch": branch,
        "model": model,
        "started_at": started,
        "deferred_tools": deferred,
        "prompt_tokens": toklen(prompt) if prompt else None,
        "turn1": {
            "input_tokens": first.get("input_tokens", 0),
            "cache_creation_input_tokens": first.get("cache_creation_input_tokens", 0),
            "cache_read_input_tokens": first.get("cache_read_input_tokens", 0),
        },
        "session_totals": dict(totals),
        "consumed": (totals["input_tokens"] + totals["cache_creation_input_tokens"]
                     + totals["output_tokens"]),
    }


def collect():
    out = []
    if not os.path.isdir(PROJECTS):
        return out
    for name in sorted(os.listdir(PROJECTS)):
        m = WORKER_DIR_RE.match(name)
        if not m:
            continue
        for path in sorted(glob.glob(os.path.join(PROJECTS, name, "*.jsonl"))):
            rec = scan_session(path)
            if rec is None:
                continue
            rec["ticket"] = int(m.group(1))
            rec["attempt"] = int(m.group(2))
            out.append(rec)
    out.sort(key=lambda r: r["started_at"] or "")
    return out


def summarize(rows):
    by_model = collections.defaultdict(set)
    for r in rows:
        by_model[r["model"]].add(r["turn1"]["cache_read_input_tokens"])

    cw1 = sum(r["turn1"]["cache_creation_input_tokens"] for r in rows)
    cr1 = sum(r["turn1"]["cache_read_input_tokens"] for r in rows)
    consumed = sum(r["consumed"] for r in rows)

    first_try = [r for r in rows if r["attempt"] == 1]
    retries = [r for r in rows if r["attempt"] > 1]
    tickets = {r["ticket"] for r in rows}

    return {
        "sessions": len(rows),
        "tickets": len(tickets),
        "attempts_per_ticket": round(len(rows) / len(tickets), 2) if tickets else None,
        # The headline: is turn-1 cache_read a per-model constant regardless of cwd?
        "turn1_cache_read_by_model": {
            k: sorted(v) for k, v in sorted(by_model.items(), key=lambda kv: str(kv[0]))
        },
        "turn1_cache_read_is_cwd_invariant": all(len(v) == 1 for v in by_model.values()),
        "turn1_shared_prefix_tokens": cr1,
        "turn1_fresh_write_tokens": cw1,
        "turn1_reused_share": round(cr1 / (cr1 + cw1), 4) if (cr1 + cw1) else None,
        "turn1_fresh_write_mean_per_session": round(cw1 / len(rows)) if rows else None,
        "all_session_consumed_tokens": consumed,
        "turn1_fresh_write_share_of_consumed": round(cw1 / consumed, 4) if consumed else None,
        "burn_first_attempts": sum(r["consumed"] for r in first_try),
        "burn_retries": sum(r["consumed"] for r in retries),
        "retry_share_of_burn": (
            round(sum(r["consumed"] for r in retries) / consumed, 4) if consumed else None
        ),
    }


def main():
    rows = collect()
    if not rows:
        print("no fworker-era worker transcripts found under", PROJECTS, file=sys.stderr)
        return 1
    summary = summarize(rows)

    if "--json" in sys.argv[1:]:
        print(json.dumps({"tokenizer": TOKENIZER, "per_session": rows,
                          "summary": summary}, indent=2))
        return 0

    print(f"tokenizer: {TOKENIZER}\n")
    hdr = (f"{'started':20s} {'wq':>5s} {'a':>2s} {'model':16s} "
           f"{'prompt':>7s} {'cw_t1':>7s} {'cr_t1':>7s} {'cw-prompt':>9s}")
    print(hdr)
    print("-" * len(hdr))
    for r in rows:
        t1 = r["turn1"]
        p = r["prompt_tokens"] or 0
        print(f"{(r['started_at'] or '')[:19]:20s} {r['ticket']:5d} {r['attempt']:2d} "
              f"{str(r['model'])[:16]:16s} {p:7d} "
              f"{t1['cache_creation_input_tokens']:7d} "
              f"{t1['cache_read_input_tokens']:7d} "
              f"{t1['cache_creation_input_tokens'] - p:9d}")

    print()
    for k, v in summary.items():
        print(f"{k:38s} {v}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
