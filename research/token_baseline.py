#!/usr/bin/env python3
"""wq#629 T0 — token-burn baseline from existing worker transcripts.

Parses /home/lumen/foreman/_runs/*.jsonl (real claude -p stream-json session
logs from the wq#613/614/625 bootstrap sessions — run under the interim
ORCH-dispatched pattern per BUILD_PLAN.md §4, since fworker.py itself didn't
exist yet when these ran). Zero new Anthropic spend: reads existing files
only. Writes token_baseline.json alongside this script; the human-readable
walkthrough is TOKEN_BASELINE.md.

Usage: python3 token_baseline.py > token_baseline.json
"""
import json
import os
import re

RUNS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "_runs")

# fbuild-<N>[.<label>].jsonl -> (worker_id, ticket, label)
NAME_RE = re.compile(r"^fbuild-(\d+)(?:\.([a-z0-9]+))?\.jsonl$")


def load_frames(path):
    frames = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                frames.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return frames


def usage_total_tokens(u):
    """Tokens actually billed/consumed this call: input + cache-write + output.
    cache_read is reuse (near-free, but still counted separately below for the
    hit-rate metric — it is NOT part of "tokens consumed" the way the other
    three are)."""
    return (u.get("input_tokens", 0) + u.get("cache_creation_input_tokens", 0)
            + u.get("output_tokens", 0))


def analyze_file(path):
    frames = load_frames(path)
    init = next((f for f in frames if f.get("type") == "system" and f.get("subtype") == "init"), None)
    result = next((f for f in frames if f.get("type") == "result"), None)
    assistant_turns = [f for f in frames if f.get("type") == "assistant"]

    per_turn = []
    for f in assistant_turns:
        u = (f.get("message") or {}).get("usage") or {}
        if not u:
            continue
        per_turn.append({
            "input_tokens": u.get("input_tokens", 0),
            "cache_creation_input_tokens": u.get("cache_creation_input_tokens", 0),
            "cache_read_input_tokens": u.get("cache_read_input_tokens", 0),
            "output_tokens": u.get("output_tokens", 0),
            "model": (f.get("message") or {}).get("model"),
        })

    ru = (result or {}).get("usage", {}) or {}
    total_in = ru.get("input_tokens", 0)
    total_cache_w = ru.get("cache_creation_input_tokens", 0)
    total_cache_r = ru.get("cache_read_input_tokens", 0)
    total_out = ru.get("output_tokens", 0)
    total_consumed = total_in + total_cache_w + total_out
    denom = total_in + total_cache_w + total_cache_r
    cache_hit_rate = (total_cache_r / denom) if denom else None
    output_share = (total_out / (total_consumed + total_cache_r)) if (total_consumed + total_cache_r) else None

    # thrash proxy: a turn whose cache_read_input_tokens drops sharply from
    # the previous turn's. In a healthy incrementally-cached session
    # cache_read only grows (each turn resends the whole prior transcript,
    # now cached); a sharp drop means the running cache chain broke and had
    # to re-prime from a smaller base — no compact_boundary system frames
    # appear in any of these files (checked), so a drop here is a session
    # discontinuity (this file starting fresh / a resume boundary), not
    # autocompact.
    thrash_turns = 0
    for i in range(1, len(per_turn)):
        prev, cur = per_turn[i - 1], per_turn[i]
        if prev["cache_read_input_tokens"] - cur["cache_read_input_tokens"] > 5000:
            thrash_turns += 1

    fixed_prefix_overhead = per_turn[0]["cache_creation_input_tokens"] if per_turn else None

    return {
        "path": os.path.relpath(path, os.path.dirname(RUNS_DIR)),
        "model": (init or {}).get("model"),
        "mcp_servers_count": len((init or {}).get("mcp_servers") or []),
        "num_turns_result": (result or {}).get("num_turns"),
        "turns_with_usage": len(per_turn),
        "terminal_subtype": (result or {}).get("subtype"),
        "is_error": (result or {}).get("is_error"),
        "total_cost_usd": (result or {}).get("total_cost_usd"),
        "usage_totals": {
            "input_tokens": total_in,
            "cache_creation_input_tokens": total_cache_w,
            "cache_read_input_tokens": total_cache_r,
            "output_tokens": total_out,
            "total_consumed_tokens": total_consumed,
        },
        "derived": {
            "cache_hit_rate": round(cache_hit_rate, 4) if cache_hit_rate is not None else None,
            "output_token_share": round(output_share, 4) if output_share is not None else None,
            "fixed_prefix_overhead_tokens": fixed_prefix_overhead,
            "tokens_consumed_per_turn": round(total_consumed / len(per_turn), 1) if per_turn else None,
            "thrash_turns": thrash_turns,
        },
    }


def main():
    files = sorted(f for f in os.listdir(RUNS_DIR) if f.endswith(".jsonl"))
    per_file = {}
    for fname in files:
        m = NAME_RE.match(fname)
        if not m:
            continue
        per_file[fname] = analyze_file(os.path.join(RUNS_DIR, fname))

    # per-ticket rollup (a ticket may span multiple session files: original +
    # resume-after-LIMIT + a follow-up land session)
    ticket_map = {
        "613": {"ticket": "wq#613", "title": "T1 budget governor daemon", "files": ["fbuild-613.jsonl", "fbuild-613.resume.jsonl", "fbuild-613.land.jsonl"]},
        "613.wave1": {"ticket": "wq#613 (early/contrast)", "title": "T1 scouting session, MCP-inheriting", "files": ["fbuild-613.wave1.jsonl"]},
        "614": {"ticket": "wq#614", "title": "T2a design doc (fable-5)", "files": ["fbuild-614.jsonl", "fbuild-614.wave1.jsonl", "fbuild-614.wave2.jsonl"]},
        "625": {"ticket": "wq#625", "title": "T2a build (dep-preflight, PR#4)", "files": ["fbuild-625.jsonl"]},
    }
    rollups = {}
    for key, spec in ticket_map.items():
        members = [per_file[f] for f in spec["files"] if f in per_file]
        if not members:
            continue
        tot = {"input_tokens": 0, "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0,
               "output_tokens": 0, "total_consumed_tokens": 0}
        turns = 0
        cost = 0.0
        for m in members:
            for k in tot:
                tot[k] += m["usage_totals"][k]
            turns += m["turns_with_usage"]
            cost += m["total_cost_usd"] or 0
        denom = tot["input_tokens"] + tot["cache_creation_input_tokens"] + tot["cache_read_input_tokens"]
        rollups[key] = {
            "ticket": spec["ticket"], "title": spec["title"], "files": spec["files"],
            "sessions": len(members), "total_turns": turns, "total_cost_usd": round(cost, 4),
            "usage_totals": tot,
            "cache_hit_rate": round(tot["cache_read_input_tokens"] / denom, 4) if denom else None,
            "tokens_per_turn": round(tot["total_consumed_tokens"] / turns, 1) if turns else None,
        }

    # wave1-MCP-inheriting vs strict-mcp-config contrast (deliverable 1's
    # explicit compare: only fbuild-613.wave1.jsonl actually shows non-empty
    # mcp_servers among the two ".wave1" files — see TOKEN_BASELINE.md for
    # the correction to the ticket's premise).
    mcp_inheriting = [v for v in per_file.values() if v["mcp_servers_count"] > 0]
    strict = [v for v in per_file.values() if v["mcp_servers_count"] == 0]

    out = {
        "source": "existing /home/lumen/foreman/_runs/*.jsonl, zero new Anthropic spend",
        "files_analyzed": len(per_file),
        "per_file": per_file,
        "per_ticket_rollup": rollups,
        "mcp_inheriting_vs_strict": {
            "mcp_inheriting_files": [v["path"] for v in mcp_inheriting],
            "mcp_inheriting_first_turn_cache_write_tokens": [v["derived"]["fixed_prefix_overhead_tokens"] for v in mcp_inheriting],
            "strict_files_sample_first_turn_cache_write_tokens": [v["derived"]["fixed_prefix_overhead_tokens"] for v in strict][:5],
        },
    }
    print(json.dumps(out, indent=2))


if __name__ == "__main__":
    main()
