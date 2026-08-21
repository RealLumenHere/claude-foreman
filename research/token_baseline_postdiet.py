#!/usr/bin/env python3
"""wq#704 T4 — post-diet token baseline re-measurement.

TOKEN_BASELINE.md (wq#629) measured 8 pre-fworker interim-ORCH-dispatch
sessions. INSTRUCTION_DIET.md (wq#631) + PREFIX_STABILITY.md (wq#630) then
shipped a prompt diet + reorder to both live worker prompts (harness/
prompts/worker.md and the interim FOREMAN_DISPATCH.prompt), but the effect
was never re-measured against real token consumption. This script does that:
same method as token_baseline.py (parse real claude -p stream-json
transcripts, zero new Anthropic spend), but pulls from
/home/lumen/agents/work-queue/_runs (the accessible real-transcript
directory — fworker.py's own worker.md-driven sessions write their full
transcript to ~/.claude/projects/*/<session_id>.jsonl, outside this sandbox;
the interim FOREMAN_DISPATCH.prompt dispatcher, still live fleet-wide per
INSTRUCTION_DIET.md, is what actually lands full jsonl transcripts here) and
classifies each fbuild<N>-<hex>.jsonl by the wording of its .prompt sidecar:
pre-diet still says "that is your spec" / "Do not re-derive"; post-diet
(after wq#631 landed, 2026-08-16T02:39:56Z) says "Don't re-derive". Files
without a .prompt sidecar or on claude-sonnet-4-6 (T4 local-rung routing,
wq#687-adjacent, a different tokenizer/model) are reported separately, not
folded into the headline pre/post delta, since that would confound the
diet's effect with an unrelated model change.

Usage: python3 token_baseline_postdiet.py > token_baseline_postdiet.json
"""
import json
import os
import re

RUNS_DIR = os.environ.get(
    "FOREMAN_RUNS_ROOT",
    "/home/lumen/agents/work-queue/_runs" if os.path.isdir("/home/lumen/agents/work-queue/_runs")
    else "/home/lumen/foreman/_runs",
)

NAME_RE = re.compile(r"^fbuild(\d+[a-z]?)-([0-9a-f]+)\.jsonl$")

PRE_MARKERS = ("that is your spec", "Do not re-derive")
POST_MARKERS = ("Don't re-derive",)

# ticket id (base, without retry-letter suffix) -> title, from queue.db `tasks` table
TICKET_TITLES = {
    "613": "foreman T1: budget governor daemon (foreman-usaged)",
    "614": "foreman T2a: worker harness (single worker)",
    "616": "foreman T2b: concurrency hardening",
    "618": "foreman T4: local rung (LiteLLM fallback)",
    "625": "foreman T2a-BUILD: worker harness per approved design",
    "626": "MC Agents view: live worker cards + transcript feed",
    "639": "fworker shakedown bugs: false-stale governor fail-close + claim held after DEFER exit",
    "645": "fworker bug 3: instant child death misclassified as TURNS (dropped --max-turns)",
    "646": "reliability-S1: verdict engine per RELIABILITY_SPEC + shim tests 1-8",
    "647": "reliability-S2: watchdogs + lease reaper + false-complete verification + shim 9-13",
    "648": "reliability-S3: governor cache-on-429 + NDJSON hardening + OOM forensics + shim 14-16",
    "649": "reliability: fworker bug 4, worktree/branch lifecycle stale-branch crash",
}

# wq#629's own committed baseline (TOKEN_BASELINE.md Per-ticket rollup) -- cited, not
# re-derived, since those 3 tickets already closed before the diet shipped and can't be
# re-run; this is the fixed comparison point the wq#704 acceptance criteria names.
WQ629_BASELINE = {
    "613": {"title": "T1 governor daemon (sonnet-5)", "sessions": 3, "turns": 293,
            "total_consumed_tokens": 526564, "cache_hit_rate": 0.976, "tokens_per_turn": 1797, "cost_usd": 9.34},
    "614": {"title": "T2a design doc (fable-5)", "sessions": 3, "turns": 154,
            "total_consumed_tokens": 221289, "cache_hit_rate": 0.964, "tokens_per_turn": 1437, "cost_usd": 10.53},
    "625": {"title": "T2a build / PR#4 (sonnet-5)", "sessions": 1, "turns": 276,
            "total_consumed_tokens": 277105, "cache_hit_rate": 0.991, "tokens_per_turn": 1004, "cost_usd": 8.70},
}


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


def classify(ticket, prompt_path):
    """pre_diet | post_diet | unclassified (no sidecar to check wording)."""
    if not os.path.exists(prompt_path):
        return "unclassified"
    text = open(prompt_path, errors="replace").read()
    if any(m in text for m in POST_MARKERS):
        return "post_diet"
    if any(m in text for m in PRE_MARKERS):
        return "pre_diet"
    return "unclassified"


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
        })

    ru = (result or {}).get("usage", {}) or {}
    total_in = ru.get("input_tokens", 0)
    total_cache_w = ru.get("cache_creation_input_tokens", 0)
    total_cache_r = ru.get("cache_read_input_tokens", 0)
    total_out = ru.get("output_tokens", 0)
    total_consumed = total_in + total_cache_w + total_out
    denom = total_in + total_cache_w + total_cache_r
    cache_hit_rate = (total_cache_r / denom) if denom else None

    thrash_turns = 0
    for i in range(1, len(per_turn)):
        if per_turn[i - 1]["cache_read_input_tokens"] - per_turn[i]["cache_read_input_tokens"] > 5000:
            thrash_turns += 1

    fixed_prefix_overhead = per_turn[0]["cache_creation_input_tokens"] if per_turn else None
    first_turn_cache_read = per_turn[0]["cache_read_input_tokens"] if per_turn else None

    return {
        "path": os.path.relpath(path, os.path.dirname(RUNS_DIR)),
        "model": (init or {}).get("model"),
        "cwd": (init or {}).get("cwd"),
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
            "fixed_prefix_overhead_tokens": fixed_prefix_overhead,
            "first_turn_cache_read_tokens": first_turn_cache_read,
            "tokens_consumed_per_turn": round(total_consumed / len(per_turn), 1) if per_turn else None,
            "thrash_turns": thrash_turns,
        },
    }


def bucket_rollup(name, members):
    if not members:
        return None
    tot = {"input_tokens": 0, "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0,
           "output_tokens": 0, "total_consumed_tokens": 0}
    turns = 0
    cost = 0.0
    fixed_overheads = []
    for m in members:
        for k in tot:
            tot[k] += m["usage_totals"][k]
        turns += m["turns_with_usage"]
        cost += m["total_cost_usd"] or 0
        if m["derived"]["fixed_prefix_overhead_tokens"] is not None:
            fixed_overheads.append(m["derived"]["fixed_prefix_overhead_tokens"])
    denom = tot["input_tokens"] + tot["cache_creation_input_tokens"] + tot["cache_read_input_tokens"]
    return {
        "bucket": name,
        "sessions": len(members),
        "files": [m["path"] for m in members],
        "total_turns": turns,
        "total_cost_usd": round(cost, 4),
        "usage_totals": tot,
        "cache_hit_rate": round(tot["cache_read_input_tokens"] / denom, 4) if denom else None,
        "tokens_per_turn": round(tot["total_consumed_tokens"] / turns, 1) if turns else None,
        "avg_fixed_prefix_overhead_tokens": round(sum(fixed_overheads) / len(fixed_overheads), 1) if fixed_overheads else None,
        "cross_session_cache_hits": sum(1 for m in members if (m["derived"]["first_turn_cache_read_tokens"] or 0) > 0),
    }


def main():
    files = sorted(f for f in os.listdir(RUNS_DIR) if f.endswith(".jsonl"))
    per_file = {}
    for fname in files:
        m = NAME_RE.match(fname)
        if not m:
            continue
        ticket = m.group(1)
        path = os.path.join(RUNS_DIR, fname)
        info = analyze_file(path)
        prompt_path = os.path.join(RUNS_DIR, f"fbuild{ticket}.prompt")
        if not os.path.exists(prompt_path):
            # some files carry the hex suffix on the .prompt sidecar too
            prompt_path = os.path.join(RUNS_DIR, fname.replace(".jsonl", ".prompt"))
        info["diet_classification"] = classify(ticket, prompt_path)
        info["confound_model"] = info["model"] != "claude-sonnet-5"
        per_file[fname] = info

    clean_pre = [v for v in per_file.values() if v["diet_classification"] == "pre_diet" and not v["confound_model"]]
    clean_post = [v for v in per_file.values() if v["diet_classification"] == "post_diet" and not v["confound_model"]]
    unclassified = [v for v in per_file.values() if v["diet_classification"] == "unclassified"]
    confounded_model = [v for v in per_file.values() if v["confound_model"]]

    pre_rollup = bucket_rollup("pre_diet (FOREMAN_DISPATCH.prompt, sonnet-5, before wq#631 wording landed)", clean_pre)
    post_rollup = bucket_rollup("post_diet (FOREMAN_DISPATCH.prompt, sonnet-5, after wq#631 wording landed)", clean_post)

    # per-ticket rollup (base ticket id, stripping retry-letter suffixes like "625r"/"649b"
    # so multi-attempt tickets roll up into one row, same shape as TOKEN_BASELINE.md)
    def base_ticket(fname):
        m = NAME_RE.match(fname)
        return re.match(r"\d+", m.group(1)).group(0)

    def ticket_rollup(members_by_bucket):
        by_ticket = {}
        for fname, v in members_by_bucket:
            t = base_ticket(fname)
            by_ticket.setdefault(t, []).append(v)
        rows = []
        for t, members in sorted(by_ticket.items(), key=lambda kv: int(kv[0])):
            r = bucket_rollup(t, members)
            rows.append({
                "ticket": f"wq#{t}", "title": TICKET_TITLES.get(t, "(title not in TICKET_TITLES map)"),
                "sessions": r["sessions"], "turns": r["total_turns"],
                "total_tokens_consumed": r["usage_totals"]["total_consumed_tokens"],
                "cache_hit_rate": r["cache_hit_rate"], "tokens_per_turn": r["tokens_per_turn"],
                "cost_usd": r["total_cost_usd"],
            })
        return rows

    pre_ticket_rollup = ticket_rollup([(fn, v) for fn, v in per_file.items() if v in clean_pre])
    post_ticket_rollup = ticket_rollup([(fn, v) for fn, v in per_file.items() if v in clean_post])

    delta = None
    if pre_rollup and post_rollup and pre_rollup["tokens_per_turn"] and post_rollup["tokens_per_turn"]:
        delta = {
            "tokens_per_turn_delta_pct": round(
                100 * (post_rollup["tokens_per_turn"] - pre_rollup["tokens_per_turn"]) / pre_rollup["tokens_per_turn"], 2),
            "cache_hit_rate_delta_pp": round(
                100 * ((post_rollup["cache_hit_rate"] or 0) - (pre_rollup["cache_hit_rate"] or 0)), 2),
            "avg_fixed_prefix_overhead_delta_pct": round(
                100 * (post_rollup["avg_fixed_prefix_overhead_tokens"] - pre_rollup["avg_fixed_prefix_overhead_tokens"])
                / pre_rollup["avg_fixed_prefix_overhead_tokens"], 2)
                if pre_rollup["avg_fixed_prefix_overhead_tokens"] else None,
        }

    wq629_totals = {
        "tickets": WQ629_BASELINE,
        "combined_total_consumed_tokens": sum(v["total_consumed_tokens"] for v in WQ629_BASELINE.values()),
        "combined_turns": sum(v["turns"] for v in WQ629_BASELINE.values()),
        "note": "cited from committed TOKEN_BASELINE.md, not re-derived -- these 3 tickets closed "
                "before the diet shipped and can't be re-run for a like-for-like post-diet pair; "
                "the pre_diet_rollup bucket above (also FOREMAN_DISPATCH.prompt/sonnet-5, same _runs "
                "source, same instrument, just captured a day later) is the actual apples-to-apples "
                "pre/post control for this ticket's delta.",
    }

    out = {
        "source": f"{RUNS_DIR}/fbuild<N>-<hex>.jsonl (real interim-ORCH-dispatch sessions, zero new Anthropic spend)",
        "diet_landed": "2026-08-16T02:39:56Z (harness/prompts/worker.md, commit e17a2da; FOREMAN_DISPATCH.prompt edited live same session per INSTRUCTION_DIET.md)",
        "files_analyzed": len(per_file),
        "per_file": per_file,
        "pre_diet_rollup": pre_rollup,
        "post_diet_rollup": post_rollup,
        "pre_diet_per_ticket": pre_ticket_rollup,
        "post_diet_per_ticket": post_ticket_rollup,
        "delta": delta,
        "wq629_baseline_cited": wq629_totals,
        "excluded_unclassified": [v["path"] for v in unclassified],
        "excluded_model_confound": {
            "note": "claude-sonnet-4-6, not claude-sonnet-5 -- different tokenizer/routing (T4 local-rung / wq#618 LiteLLM-fallback era, downstream of wq#687's downgrade-detection work), would confound a wording-diet delta",
            "files": [v["path"] for v in confounded_model],
        },
        "unexplained_observation": {
            "note": "every file in both buckets (pre AND post diet) shows an identical "
                    "first-turn cache_read_input_tokens of 24649 tokens, despite "
                    "PREFIX_STABILITY.md's T0 finding of zero cross-session cache reuse in the "
                    "original 8-file baseline. Consistent across both buckets so it is not a "
                    "diet effect either way -- flagged as-is, not investigated further here "
                    "(would need infra-side info on how the interim ORCH dispatcher spawns "
                    "sessions, out of this ticket's scope).",
        },
    }
    print(json.dumps(out, indent=2))


if __name__ == "__main__":
    main()
