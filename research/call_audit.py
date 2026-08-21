#!/usr/bin/env python3
"""wq#706 T6 — full per-call payload audit.

Decomposes every API request a worker session sends, at the request-body level,
into the categories wq#706 names: injected system floor, our prompt slab,
CLAUDE.md, settings pack, ticket trailer, rolling transcript, tool results,
file reads.

Method (zero new Anthropic spend, all from existing transcripts):

1. An "API call" is a unique `message.id`, NOT a stream frame. Claude Code emits
   one `assistant` frame per content block and repeats the message_start `usage`
   on each -- so frame count over-counts calls ~1.8x. Summing usage per unique
   message id reproduces the result frame's totals exactly (asserted below);
   summing per frame does not. `result.num_turns` == unique message ids.

2. The request-body size of call k, in real Claude tokens, is
       context_k = input_tokens + cache_read_input_tokens + cache_creation_input_tokens
   from that call's message_start usage. This is measured, not estimated.

3. growth_k = context_k - context_(k-1) is exactly the tokens added by the
   messages between the two calls: assistant message k-1 (thinking + text +
   tool_use) plus the user/tool_result messages that followed it. Attribution
   *within* a growth step uses:
     - real thinking tokens from the `system/thinking_tokens` frames (their
       deltas sum to within ~1.2% of result.usage.output_tokens_details.
       thinking_tokens, so they are a measured quantity, not a guess);
     - a cl100k_base tiktoken proxy for the visible text/tool_use/tool_result
       blocks, rescaled so each step's parts sum to that step's measured growth.
   The unscaled proxy/measured ratio is reported per session so the size of that
   correction is visible rather than hidden.

4. The boot payload is call 1's whole context, and it is what carries in every
   later call. It is decomposed against the *total*, not against
   `cache_creation` alone: the read/write split of call 1 varies with how much a
   prior session left in the 1h cache (two clusters across the clean set, 24.6k
   and 30.4k) while the total does not (35,646-35,889, a 0.7% spread). What we
   wrote -- prompt slab, CLAUDE.md, ticket trailer -- is sized from the on-disk
   files; everything else is Anthropic's system prompt, the tool schemas, and the
   CLI's own session block, which a transcript cannot separate further.

5. Carry cost. With prompt caching, a chunk added before call k is re-read on
   every call from k to N. Its lifetime input-side cost is
       size * cache_write_rate  +  size * (N - k) * cache_read_rate
   Rates are solved against the session's own reported total_cost_usd rather
   than taken from models.yaml (they disagree; see PRICES).

Usage:
    python3 call_audit.py > call_audit.json
    python3 call_audit.py --session fbuild648-cb4682.jsonl > other.json
"""
import argparse
import json
import os
import re
from collections import OrderedDict, defaultdict

import tiktoken

RUNS_DIR = os.environ.get("FOREMAN_RUNS_ROOT", "/home/lumen/agents/work-queue/_runs")
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CLAUDE_MD = "/home/lumen/CLAUDE.md"

# The wq#704 clean set: FOREMAN_DISPATCH.prompt, claude-sonnet-5, post-diet.
CLEAN_POST_DIET = [
    "fbuild616-25ac3f.jsonl",
    "fbuild618-789121.jsonl",
    "fbuild647-6d3c8b.jsonl",
    "fbuild648-cb4682.jsonl",
    "fbuild649b-26b1dc.jsonl",
]

# $/Mtok. Solved from the session's own result.total_cost_usd (validated to
# <1e-6 relative error in every clean-set session) -- NOT models.yaml, which
# lists the 2/10 intro rate for sonnet-5. The CLI billed 3/15. cache_read is
# 0.1x base; cache_write here is the 1-hour tier (usage.cache_creation shows
# ephemeral_1h_input_tokens carrying 100% of cache creation), i.e. 2x base.
PRICES = {"input": 3.00, "output": 15.00, "cache_read": 0.30, "cache_write": 6.00}

ENC = tiktoken.get_encoding("cl100k_base")


def tok(s):
    if not s:
        return 0
    if not isinstance(s, str):
        s = json.dumps(s, ensure_ascii=False)
    return len(ENC.encode(s, disallowed_special=()))


def load_frames(path):
    out = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return out


# ---------------------------------------------------------------------------
# call reconstruction
# ---------------------------------------------------------------------------

def build_calls(frames):
    """Ordered list of API calls. Each carries its measured request-body size,
    the assistant content it produced, the thinking tokens spent producing it,
    and the user messages that followed it before the next call."""
    calls = OrderedDict()
    order = []
    pending_thinking = 0          # thinking_tokens frames seen since last assistant
    cur = None

    for f in frames:
        t, st = f.get("type"), f.get("subtype")

        if st == "thinking_tokens":
            # cumulative within a group; the last one before the assistant frames
            # is that call's total
            pending_thinking = max(pending_thinking, f.get("estimated_tokens", 0))
            continue

        if t == "assistant":
            m = f["message"]
            mid = m["id"]
            if mid not in calls:
                u = m.get("usage") or {}
                calls[mid] = {
                    "id": mid,
                    "index": len(order) + 1,
                    "input_tokens": u.get("input_tokens", 0),
                    "cache_read": u.get("cache_read_input_tokens", 0),
                    "cache_write": u.get("cache_creation_input_tokens", 0),
                    "thinking_tokens": pending_thinking,
                    "blocks": [],
                    "user_after": [],
                }
                calls[mid]["context"] = (calls[mid]["input_tokens"]
                                         + calls[mid]["cache_read"]
                                         + calls[mid]["cache_write"])
                order.append(mid)
                pending_thinking = 0
            calls[mid]["blocks"].extend(m.get("content") or [])
            cur = mid
            continue

        if t == "user" and cur is not None:
            content = (f.get("message") or {}).get("content")
            if isinstance(content, list):
                calls[cur]["user_after"].extend(content)
            elif content:
                calls[cur]["user_after"].append({"type": "text", "text": content})

    return [calls[m] for m in order]


def classify_tool(name):
    if name in ("Read", "NotebookEdit"):
        return "file_read"
    if name == "Bash":
        return "bash"
    if name in ("Edit", "Write"):
        return "file_write"
    if name in ("Grep", "Glob"):
        return "search"
    if name in ("Task", "Agent"):
        return "subagent"
    return "other"


def analyze(path, prompt_path):
    frames = load_frames(path)
    init = next((f for f in frames if f.get("subtype") == "init"), {})
    result = next((f for f in frames if f.get("type") == "result"), {})
    ru = result.get("usage") or {}

    calls = build_calls(frames)
    n = len(calls)

    # --- integrity: per-unique-id usage must reproduce the result totals -----
    sums = {
        "input_tokens": sum(c["input_tokens"] for c in calls),
        "cache_creation_input_tokens": sum(c["cache_write"] for c in calls),
        "cache_read_input_tokens": sum(c["cache_read"] for c in calls),
    }
    integrity = {
        "unique_message_ids": n,
        "assistant_frames": sum(1 for f in frames if f.get("type") == "assistant"),
        "result_num_turns": result.get("num_turns"),
        "usage_sum_matches_result": all(sums[k] == ru.get(k) for k in sums),
        "sums": sums,
        "result_usage": {k: ru.get(k) for k in sums},
    }

    # --- cost model validation ---------------------------------------------
    predicted = (ru.get("input_tokens", 0) * PRICES["input"]
                 + ru.get("cache_creation_input_tokens", 0) * PRICES["cache_write"]
                 + ru.get("cache_read_input_tokens", 0) * PRICES["cache_read"]
                 + ru.get("output_tokens", 0) * PRICES["output"]) / 1e6
    reported = result.get("total_cost_usd") or 0.0
    cost_model = {
        "prices_usd_per_mtok": PRICES,
        "predicted_usd": round(predicted, 6),
        "reported_total_cost_usd": round(reported, 6),
        "relative_error": round(abs(predicted - reported) / reported, 9) if reported else None,
        "note": "models.yaml lists sonnet-5 at 2/10 (intro). The CLI billed 3/15; "
                "these rates reproduce total_cost_usd exactly, so they are what "
                "the reduction estimates below use.",
    }

    # --- per-call growth + attribution -------------------------------------
    tool_names = {}          # tool_use_id -> name
    tool_inputs = {}
    chunks = []              # every attributed piece of context, with its birth call

    prev_ctx = None
    per_call = []
    proxy_ratios = []

    for i, c in enumerate(calls):
        ctx = c["context"]
        growth = 0 if prev_ctx is None else ctx - prev_ctx
        prev_ctx = ctx

        # proxy-size every visible piece produced *by the previous call* and the
        # user messages after it -- that is exactly what growth_k paid for.
        parts = []
        if i > 0:
            p = calls[i - 1]
            if p["thinking_tokens"]:
                parts.append({"cat": "assistant_thinking", "proxy": p["thinking_tokens"],
                              "fixed": True, "label": "thinking"})
            for b in p["blocks"]:
                bt = b.get("type")
                if bt == "text":
                    parts.append({"cat": "assistant_text", "proxy": tok(b.get("text")),
                                  "label": (b.get("text") or "")[:80]})
                elif bt == "tool_use":
                    tool_names[b.get("id")] = b.get("name")
                    tool_inputs[b.get("id")] = b.get("input")
                    parts.append({"cat": "assistant_tool_use",
                                  "proxy": tok(b.get("input")) + tok(b.get("name")),
                                  "label": f"{b.get('name')} {json.dumps(b.get('input'))[:80]}",
                                  "tool": b.get("name"), "tool_use_id": b.get("id")})
            for b in p["user_after"]:
                if b.get("type") == "tool_result":
                    tid = b.get("tool_use_id")
                    name = tool_names.get(tid, "?")
                    parts.append({"cat": "tool_result",
                                  "proxy": tok(b.get("content")),
                                  "tool": name,
                                  "tool_class": classify_tool(name),
                                  "tool_use_id": tid,
                                  "is_error": bool(b.get("is_error")),
                                  "label": json.dumps(tool_inputs.get(tid))[:120]})
                else:
                    parts.append({"cat": "user_message",
                                  "proxy": tok(b.get("text") or b),
                                  "label": str(b.get("text"))[:80]})

        # rescale the proxy so the parts of this step sum to the measured growth.
        # thinking is already real tokens, so it is held fixed and only the
        # tiktoken-proxied remainder is scaled.
        fixed = sum(p["proxy"] for p in parts if p.get("fixed"))
        flex = sum(p["proxy"] for p in parts if not p.get("fixed"))
        target = max(growth - fixed, 0)
        scale = (target / flex) if flex else 0.0
        if flex:
            proxy_ratios.append(flex / target if target else None)
        for p in parts:
            p["tokens"] = p["proxy"] if p.get("fixed") else p["proxy"] * scale
            p["born_call"] = i + 1        # first request that carried it
            p["present_in_calls"] = n - i          # requests it appears in
            p["cache_reads"] = n - (i + 1)         # written once at birth, read after
            chunks.append(p)

        per_call.append({
            "call": i + 1,
            "context_tokens": ctx,
            "cache_read": c["cache_read"],
            "cache_write": c["cache_write"],
            "growth_vs_prev": growth,
            "thinking_tokens": c["thinking_tokens"],
        })

    # the fixed prefix is the whole of call 1 and is carried by every call
    fixed_prefix_total = calls[0]["context"] if calls else 0
    injected_floor = calls[0]["cache_read"] if calls else 0
    session_specific = calls[0]["cache_write"] + calls[0]["input_tokens"] if calls else 0

    # --- category rollup ----------------------------------------------------
    cats = defaultdict(lambda: {"tokens": 0.0, "count": 0, "carry_tokens": 0.0})
    for p in chunks:
        e = cats[p["cat"]]
        e["tokens"] += p["tokens"]
        e["count"] += 1
        e["carry_tokens"] += p["tokens"] * p["cache_reads"]

    # fixed prefix as its own set of categories, decomposed against on-disk files
    slab = open(prompt_path, errors="replace").read() if os.path.exists(prompt_path) else ""
    static_slab, _, volatile_slab = slab.partition("\n---\n")
    claude_md = open(CLAUDE_MD, errors="replace").read() if os.path.exists(CLAUDE_MD) else ""
    settings_path = os.path.join(REPO, "harness", "worker-settings.json")
    settings = open(settings_path, errors="replace").read() if os.path.exists(settings_path) else ""
    settings_perms = ""
    if settings:
        try:
            settings_perms = json.dumps(json.loads(settings).get("permissions", {}))
        except json.JSONDecodeError:
            settings_perms = settings

    # --- tokenizer calibration ---------------------------------------------
    # cl100k is a proxy; Claude's own tokenizer is not exposed to this box. Two
    # independent measurements of how far off it is on this content mix:
    #   (1) assistant side: real non-thinking output_tokens (billed by the model
    #       itself, no invisible wrapper possible) vs cl100k of the same blocks;
    #   (2) tool-result side: total new input tokens minus the prefix, minus
    #       thinking, minus non-thinking output, vs cl100k of the tool_result
    #       contents.
    # These are derived from disjoint quantities and land on ~1.6-1.8x, which is
    # why raw cl100k figures elsewhere in research/ understate real spend.
    proxy_text = sum(tok(b.get("text")) for c in calls for b in c["blocks"]
                     if b.get("type") == "text")
    proxy_tool_use = sum(tok(b.get("input")) + tok(b.get("name"))
                         for c in calls for b in c["blocks"] if b.get("type") == "tool_use")
    proxy_tool_result = sum(tok(b.get("content")) for c in calls for b in c["user_after"]
                            if b.get("type") == "tool_result")
    out_total = ru.get("output_tokens", 0)
    think_real = (ru.get("output_tokens_details") or {}).get("thinking_tokens", 0)
    nonthink_out = out_total - think_real
    implied_tool_result_real = (ru.get("cache_creation_input_tokens", 0)
                                - session_specific - think_real - nonthink_out)
    calibration = {
        "assistant_blocks_real_over_cl100k": round(nonthink_out / (proxy_text + proxy_tool_use), 3)
            if (proxy_text + proxy_tool_use) else None,
        "tool_results_real_over_cl100k": round(implied_tool_result_real / proxy_tool_result, 3)
            if proxy_tool_result else None,
        "cl100k_totals": {"assistant_text": proxy_text, "assistant_tool_use": proxy_tool_use,
                          "tool_result": proxy_tool_result},
        "note": "Content mix here is code, shell output, JSON and paths. Prose "
                "(CLAUDE.md, worker.md) very likely sits closer to 1.0-1.3x, so do "
                "not apply this factor to prose without re-measuring it.",
    }

    prefix_parts = {
        "b_our_prompt_slab_static": tok(static_slab),
        "e_ticket_trailer_volatile": tok(volatile_slab),
        "c_claude_md": tok(claude_md),
    }
    ours = sum(prefix_parts.values())
    # Everything in the boot payload that is not text we wrote. Reported against
    # the *total* first-call context rather than against cache_creation alone:
    # the read/write split of call 1 varies with how much of the payload a prior
    # session happened to leave in the 1h cache (two clusters across the clean
    # set, 24.6k and 30.4k), while the total does not (35,646-35,889, 0.7% spread).
    prefix_parts["a_i_injected_and_cli_boot_not_ours"] = fixed_prefix_total - ours
    # (d) is measured but deliberately NOT added: if the permission table is
    # serialized into the request at all it is already inside the residual, so
    # adding it would double-count. Reported as a ceiling on its own size.
    settings_ceiling = tok(settings_perms)

    def carry_cost(tokens, born_call):
        """input-side lifetime $ of `tokens` first present at call `born_call`:
        written into cache once, re-read on every later call."""
        reads = n - born_call
        return (tokens * PRICES["cache_write"] + tokens * reads * PRICES["cache_read"]) / 1e6

    prefix_cost = {}
    for k, v in prefix_parts.items():
        if k == "a_i_injected_and_cli_boot_not_ours":
            # the part that arrived already cached was never paid for as a write
            # inside this session; the rest was written once like anything else
            written = max(v - injected_floor, 0)
            prefix_cost[k] = round(
                (injected_floor * n * PRICES["cache_read"]) / 1e6
                + carry_cost(written, 1), 4)
        else:
            prefix_cost[k] = round(carry_cost(v, 1), 4)
    prefix_cost["d_settings_permissions_table_ceiling_if_present"] = round(
        carry_cost(settings_ceiling, 1), 4)
    prefix_cost["_ours_total_b_c_e"] = round(carry_cost(ours, 1), 4)

    cat_rollup = {}
    for k, v in cats.items():
        cat_rollup[k] = {
            "tokens_added": round(v["tokens"]),
            "messages": v["count"],
            "carry_token_reads": round(v["carry_tokens"]),
            "carry_cost_usd": round(v["carry_tokens"] * PRICES["cache_read"] / 1e6
                                    + v["tokens"] * PRICES["cache_write"] / 1e6, 4),
        }

    # --- top lists ----------------------------------------------------------
    ranked = sorted(chunks, key=lambda p: -p["tokens"])
    top_messages = [{
        "rank": i + 1,
        "category": p["cat"],
        "tool": p.get("tool"),
        "tokens": round(p["tokens"]),
        "born_call": p["born_call"],
        "carried_in_calls": p["present_in_calls"],
        "carry_cost_usd": round(carry_cost(p["tokens"], p["born_call"]), 4),
        "label": p.get("label", "")[:140],
    } for i, p in enumerate(ranked[:20])]

    tool_results = [p for p in chunks if p["cat"] == "tool_result"]
    top_tool_results = [{
        "rank": i + 1,
        "tool": p.get("tool"),
        "tool_class": p.get("tool_class"),
        "tokens": round(p["tokens"]),
        "born_call": p["born_call"],
        "carried_in_calls": p["present_in_calls"],
        "carry_cost_usd": round(carry_cost(p["tokens"], p["born_call"]), 4),
        "invocation": p.get("label", "")[:160],
    } for i, p in enumerate(sorted(tool_results, key=lambda p: -p["tokens"])[:10])]

    by_tool = defaultdict(lambda: {"calls": 0, "tokens": 0.0, "carry": 0.0})
    for p in tool_results:
        e = by_tool[p.get("tool") or "?"]
        e["calls"] += 1
        e["tokens"] += p["tokens"]
        e["carry"] += carry_cost(p["tokens"], p["born_call"])
    tool_rollup = {k: {"calls": v["calls"], "tokens": round(v["tokens"]),
                       "mean_tokens": round(v["tokens"] / v["calls"]),
                       "carry_cost_usd": round(v["carry"], 4)}
                   for k, v in sorted(by_tool.items(), key=lambda kv: -kv[1]["carry"])}

    # --- duplication: same invocation issued more than once -----------------
    dup = defaultdict(lambda: {"n": 0, "tokens": 0.0, "carry": 0.0, "tool": None})
    for p in tool_results:
        key = f"{p.get('tool')}::{p.get('label')}"
        e = dup[key]
        e["n"] += 1
        e["tokens"] += p["tokens"]
        e["carry"] += carry_cost(p["tokens"], p["born_call"])
        e["tool"] = p.get("tool")
    repeats = sorted(((k, v) for k, v in dup.items() if v["n"] > 1),
                     key=lambda kv: -kv[1]["carry"])
    duplication = {
        "repeated_invocations": len(repeats),
        "redundant_tokens": round(sum(v["tokens"] * (v["n"] - 1) / v["n"] for _, v in repeats)),
        "redundant_carry_cost_usd": round(
            sum(v["carry"] * (v["n"] - 1) / v["n"] for _, v in repeats), 4),
        "top": [{"invocation": k[:160], "times": v["n"], "tokens": round(v["tokens"]),
                 "carry_cost_usd": round(v["carry"], 4)} for k, v in repeats[:10]],
    }

    # --- errored tool results (pure waste: carried, taught nothing) ---------
    errs = [p for p in tool_results if p.get("is_error")]
    error_waste = {
        "errored_tool_results": len(errs),
        "tokens": round(sum(p["tokens"] for p in errs)),
        "carry_cost_usd": round(sum(carry_cost(p["tokens"], p["born_call"]) for p in errs), 4),
    }

    # --- reduction levers, each priced against a measured baseline ----------
    def cap_saving(cap, pred=lambda p: True):
        """$ saved if every matching tool_result had been truncated to `cap`
        tokens at the moment it entered context."""
        over = [(p["tokens"] - cap, p["born_call"]) for p in tool_results
                if pred(p) and p["tokens"] > cap]
        return {
            "cap_tokens": cap,
            "results_over_cap": len(over),
            "tokens_removed": round(sum(t for t, _ in over)),
            "saving_usd": round(sum(carry_cost(t, b) for t, b in over), 4),
        }

    unbounded_reads = [p for p in tool_results
                       if p.get("tool") == "Read"
                       and "offset" not in (p.get("label") or "")
                       and "limit" not in (p.get("label") or "")]
    input_side_cost = (ru.get("cache_creation_input_tokens", 0) * PRICES["cache_write"]
                       + ru.get("cache_read_input_tokens", 0) * PRICES["cache_read"]
                       + ru.get("input_tokens", 0) * PRICES["input"]) / 1e6

    levers = {
        "session_cost_usd": round(reported, 4),
        "input_side_cost_usd": round(input_side_cost, 4),
        "output_side_cost_usd": round(ru.get("output_tokens", 0) * PRICES["output"] / 1e6, 4),
        "usd_per_1k_tokens_added_at_call_1": round(1000 * (PRICES["cache_write"]
                                                   + (n - 1) * PRICES["cache_read"]) / 1e6, 4),
        "usd_per_1k_tokens_added_at_midpoint": round(1000 * (PRICES["cache_write"]
                                                     + (n / 2) * PRICES["cache_read"]) / 1e6, 4),
        "tool_result_cap": [cap_saving(c) for c in (500, 1000, 2000, 4000)],
        "bash_output_cap": [cap_saving(c, lambda p: p.get("tool") == "Bash")
                            for c in (500, 1000, 2000)],
        "unbounded_whole_file_reads": {
            "count": len(unbounded_reads),
            "of_total_reads": sum(1 for p in tool_results if p.get("tool") == "Read"),
            "tokens": round(sum(p["tokens"] for p in unbounded_reads)),
            "carry_cost_usd": round(sum(carry_cost(p["tokens"], p["born_call"])
                                        for p in unbounded_reads), 4),
        },
        "dedupe_repeat_reads_usd": duplication["redundant_carry_cost_usd"],
        "errored_results_usd": error_waste["carry_cost_usd"],
        "whole_fixed_prefix_if_deleted_usd": round(
            sum(v for k, v in prefix_cost.items()
                if not k.startswith(("a_", "d_"))), 4),
    }

    return {
        "file": os.path.basename(path),
        "prompt_sidecar": os.path.basename(prompt_path),
        "model": init.get("model"),
        "cwd": init.get("cwd"),
        "claude_code_version": init.get("claude_code_version"),
        "tools_offered": len(init.get("tools") or []),
        "skills_offered": len(init.get("skills") or []),
        "api_calls": n,
        "integrity": integrity,
        "cost_model": cost_model,
        "usage_totals": {k: ru.get(k) for k in
                         ("input_tokens", "cache_creation_input_tokens",
                          "cache_read_input_tokens", "output_tokens")},
        "thinking_tokens_reported": (ru.get("output_tokens_details") or {}).get("thinking_tokens"),
        "thinking_tokens_from_frames": sum(c["thinking_tokens"] for c in calls),
        "context_growth": {
            "first_call_context": fixed_prefix_total,
            "last_call_context": calls[-1]["context"] if calls else 0,
            "mean_call_context": round(sum(c["context"] for c in calls) / n) if n else 0,
            "total_input_side_tokens": sum(c["context"] for c in calls),
        },
        "fixed_prefix": {
            "total_tokens": fixed_prefix_total,
            "injected_floor_tokens": injected_floor,
            "session_specific_tokens": session_specific,
            "ours_cl100k_tokens": ours,
            "ours_pct_of_boot_payload": round(100 * ours / fixed_prefix_total, 2)
                if fixed_prefix_total else None,
            "settings_permissions_table_cl100k_ceiling": settings_ceiling,
            "parts": prefix_parts,
            "lifetime_cost_usd": prefix_cost,
            "pct_of_mean_call": round(100 * fixed_prefix_total / (
                sum(c["context"] for c in calls) / n), 2) if n else None,
        },
        "proxy_quality": {
            "median_unscaled_proxy_over_measured": round(sorted(
                r for r in proxy_ratios if r)[len(
                [r for r in proxy_ratios if r]) // 2], 3) if any(proxy_ratios) else None,
            "note": "1.0 would mean the cl100k proxy exactly predicted the measured "
                    "growth. Deviation is absorbed by the per-step rescale; it is "
                    "reported so the size of that correction is visible.",
        },
        "tokenizer_calibration": calibration,
        "categories": cat_rollup,
        "levers": levers,
        "top_20_messages": top_messages,
        "top_10_tool_results": top_tool_results,
        "tool_rollup": tool_rollup,
        "duplication": duplication,
        "error_waste": error_waste,
    }


def survey():
    """Session-selection table over the wq#704 clean post-diet set."""
    rows = []
    for fn in CLEAN_POST_DIET:
        path = os.path.join(RUNS_DIR, fn)
        frames = load_frames(path)
        init = next((f for f in frames if f.get("subtype") == "init"), {})
        result = next((f for f in frames if f.get("type") == "result"), {})
        ru = result.get("usage") or {}
        calls = build_calls(frames)
        n = len(calls)
        consumed = (ru.get("input_tokens", 0) + ru.get("cache_creation_input_tokens", 0)
                    + ru.get("output_tokens", 0))
        rows.append({
            "file": fn,
            "cwd": init.get("cwd"),
            "claude_code_version": init.get("claude_code_version"),
            "tools_offered": len(init.get("tools") or []),
            "api_calls": n,
            "assistant_frames": sum(1 for f in frames if f.get("type") == "assistant"),
            "first_call_context_total": calls[0]["context"] if calls else None,
            "first_call_cache_read": calls[0]["cache_read"] if calls else None,
            "first_call_cache_write": calls[0]["cache_write"] if calls else None,
            "last_call_context": calls[-1]["context"] if calls else None,
            "mean_call_context": round(sum(c["context"] for c in calls) / n) if n else None,
            "tokens_consumed_per_api_call": round(consumed / n, 1) if n else None,
            "total_cost_usd": round(result.get("total_cost_usd") or 0, 2),
        })
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--session", default="fbuild647-6d3c8b.jsonl")
    args = ap.parse_args()

    path = os.path.join(RUNS_DIR, args.session)
    stem = args.session[:-len(".jsonl")]
    prompt_path = os.path.join(RUNS_DIR, stem + ".prompt")
    if not os.path.exists(prompt_path):
        base = re.match(r"(fbuild\d+[a-z]?)", stem)
        prompt_path = os.path.join(RUNS_DIR, (base.group(1) if base else stem) + ".prompt")

    out = {
        "source_dir": RUNS_DIR,
        "session_selection": survey(),
        "audit": analyze(path, prompt_path),
    }
    print(json.dumps(out, indent=2))


if __name__ == "__main__":
    main()
