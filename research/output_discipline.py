#!/usr/bin/env python3
"""wq#632 — where a worker's output tokens actually go, and how much of that
volume the worker.md brevity rules can reach.

Method notes (the non-obvious parts):

1. The authoritative output-token total for a session is the `result` frame's
   `usage.output_tokens`. The per-`assistant`-frame usage block is the
   message_start snapshot (output_tokens is 1-ish there), so summing it gives
   nonsense -- 1,193 vs a real 102,812 on fbuild616. CALL_AUDIT.md (wq#706)
   established the same thing for the input side.

2. An API call is a unique `message.id`, not a stream frame. Claude Code emits
   one assistant frame per content block.

3. Thinking text is NOT in the stream. `thinking` blocks arrive with empty
   text; the token count comes from the `thinking_tokens` system frames, which
   are cumulative within a call group -- take the max seen since the last new
   message id.

4. So the block split is measured in two different units and they can't be
   summed naively: thinking in tokens (from #3), text/tool_use in characters.
   Converting chars->tokens at the fixed rates below leaves a real residual
   (`unattributed_tokens`, ~18% of output on the clean set) that the transcript
   does not explain. Scaling the rates to close that gap instead would imply
   ~1.9 chars/token, which no tokenizer produces for prose or JSON -- so the
   residual is reported rather than absorbed. Most likely it is the
   `thinking_tokens` frames undercounting (they are labelled `estimated_`) plus
   streamed-then-superseded output that never reached the transcript.
   `*_tokens_est_hi` is the alternative reading where the residual IS
   text/tool_use volume, i.e. a strict upper bound on each share.

Zero new Anthropic spend: parses existing transcripts only.
Re-run: python3 output_discipline.py > output_discipline.json
"""

import json
import os
import re
import sys
import glob
from collections import OrderedDict, Counter

RUNS_DIR = "/home/lumen/agents/work-queue/_runs"

# Chars per token. Stated assumptions, not measurements -- the transcript
# carries no per-block token counts to calibrate against. English prose with
# markdown runs ~3.6; densely-punctuated JSON tool payloads run ~3.0. The
# headline conclusion is robust to these: see `*_est_hi`, the upper bound that
# assumes every unattributed token is text/tool_use volume.
CPT_TEXT = 3.6
CPT_TOOL = 3.0

# The wq#704 clean set: real claude-foreman build sessions on claude-sonnet-5,
# post-INSTRUCTION_DIET. Same five sessions CALL_AUDIT.md audited, so their
# api_call counts are independently traceable.
CLEAN_SET = {
    "fbuild616-25ac3f.jsonl": "616",
    "fbuild618-789121.jsonl": "618",
    "fbuild647-6d3c8b.jsonl": "647",
    "fbuild648-cb4682.jsonl": "648",
    "fbuild649b-26b1dc.jsonl": "649",
}

CEREMONY = re.compile(
    r"^\s*(let me\b|i'?ll\b|i will\b|now (?:i'?ll|let me)\b|perfect[!.]|great[!.]|"
    r"excellent[!.]|got it[!.,]|sure[!.,]|alright[!.,]|okay[!.,]|ok[!.,]|"
    r"first,|next,|finally,|now,)",
    re.I,
)
FENCE = re.compile(r"```[^\n]*\n(.*?)```", re.S)
# A prose line that is just narrating the tool call that follows/precedes it.
NARRATION = re.compile(
    r"^\s*(i'?m going to|i'?ve|i have|let'?s|going to|about to)\b", re.I
)


def norm(s):
    return re.sub(r"\s+", " ", s).strip()


def load(path):
    for line in open(path, errors="replace"):
        line = line.strip()
        if not line:
            continue
        try:
            yield json.loads(line)
        except json.JSONDecodeError:
            continue


def analyze(path):
    calls = OrderedDict()
    result = None
    pending_thinking = 0
    thinking_tokens = 0

    text_chars = 0
    text_blocks = 0
    tool_chars = Counter()
    tool_calls = Counter()

    fenced_chars = 0
    fenced_restated_chars = 0
    ceremony_chars = 0
    narration_chars = 0
    last_text = ""
    # Everything the session has already put on screen via a tool payload.
    # A fenced block in prose whose content is already in here is a restatement.
    shown = []

    for f in load(path):
        if f.get("subtype") == "thinking_tokens":
            pending_thinking = max(pending_thinking, f.get("estimated_tokens", 0))
            continue
        if f.get("type") == "result":
            result = f
            continue
        if f.get("type") != "assistant":
            continue

        m = f.get("message") or {}
        mid = m.get("id")
        if mid and mid not in calls:
            calls[mid] = True
            thinking_tokens += pending_thinking
            pending_thinking = 0

        for b in m.get("content") or []:
            t = b.get("type")
            if t == "tool_use":
                name = b.get("name") or "?"
                payload = json.dumps(b.get("input") or {})
                tool_chars[name] += len(payload)
                tool_calls[name] += 1
                shown.append(norm(payload))
            elif t == "text":
                txt = b.get("text") or ""
                if not txt.strip():
                    continue
                text_blocks += 1
                text_chars += len(txt)
                last_text = txt

                haystack = " ".join(shown)
                for body in FENCE.findall(txt):
                    fenced_chars += len(body)
                    n = norm(body)
                    # 40 chars is long enough that an incidental match is not
                    # plausible; shorter fences are usually a one-word command.
                    if len(n) >= 40 and n in haystack:
                        fenced_restated_chars += len(body)

                stripped = FENCE.sub("", txt)
                for line in stripped.splitlines():
                    if CEREMONY.match(line):
                        ceremony_chars += len(line)
                    elif NARRATION.match(line):
                        narration_chars += len(line)

    ru = (result or {}).get("usage") or {}
    out = ru.get("output_tokens", 0)
    consumed = (
        ru.get("input_tokens", 0) + ru.get("cache_creation_input_tokens", 0) + out
    )
    api_calls = len(calls)
    tool_chars_total = sum(tool_chars.values())

    # See docstring note 4.
    text_tok = round(text_chars / CPT_TEXT)
    tool_tok = round(tool_chars_total / CPT_TOOL)
    unattributed = out - thinking_tokens - text_tok - tool_tok
    # Upper bound: split the residual between text and tool_use in proportion
    # to their char volume, i.e. assume none of it is thinking undercount.
    chars = text_chars + tool_chars_total
    scale = ((text_tok + tool_tok + max(unattributed, 0)) / (text_tok + tool_tok)
             if (text_tok + tool_tok) else 1.0)

    def est(c):
        return round(c / CPT_TEXT)

    return {
        "file": os.path.basename(path),
        "api_calls": api_calls,
        "output_tokens": out,
        "total_consumed_tokens": consumed,
        "output_tokens_per_api_call": round(out / api_calls, 1) if api_calls else None,
        "output_share_of_consumed": round(out / consumed, 4) if consumed else None,
        "split": {
            "thinking_tokens": thinking_tokens,
            "thinking_share": round(thinking_tokens / out, 4) if out else None,
            "text_chars": text_chars,
            "text_blocks": text_blocks,
            "text_tokens_est": text_tok,
            "text_tokens_est_hi": round(text_tok * scale),
            "text_share_est": round(text_tok / out, 4) if out else None,
            "tool_use_chars": tool_chars_total,
            "tool_use_tokens_est": tool_tok,
            "tool_use_tokens_est_hi": round(tool_tok * scale),
            "tool_use_share_est": round(tool_tok / out, 4) if out else None,
            "unattributed_tokens": unattributed,
            "unattributed_share": round(unattributed / out, 4) if out else None,
        },
        "tool_use_chars_by_tool": dict(tool_chars.most_common()),
        "tool_use_calls_by_tool": dict(tool_calls.most_common()),
        "addressable_prose": {
            "fenced_code_chars_in_prose": fenced_chars,
            "fenced_code_restated_chars": fenced_restated_chars,
            "ceremony_line_chars": ceremony_chars,
            "narration_line_chars": narration_chars,
            "final_summary_chars": len(last_text),
            "final_summary_tokens_est": est(len(last_text)),
        },
    }


def rollup(name, rows):
    if not rows:
        return None
    s = lambda k: sum(r[k] for r in rows)
    sp = lambda k: sum(r["split"][k] or 0 for r in rows)
    ap = lambda k: sum(r["addressable_prose"][k] or 0 for r in rows)
    out = s("output_tokens")
    calls = s("api_calls")
    text_tok = sp("text_tokens_est")
    tool_tok = sp("tool_use_tokens_est")
    think_tok = sp("thinking_tokens")

    # What the worker.md rules can actually reach: ceremony + narration +
    # restated code + whatever the final summary runs over its 200-token cap.
    over_cap = sum(
        max((r["addressable_prose"]["final_summary_tokens_est"] or 0) - 200, 0)
        for r in rows
    )
    reachable = (
        round((ap("ceremony_line_chars") + ap("narration_line_chars")
               + ap("fenced_code_restated_chars")) / CPT_TEXT)
        + over_cap
    )

    return {
        "bucket": name,
        "sessions": len(rows),
        "api_calls": calls,
        "output_tokens": out,
        "output_tokens_per_api_call": round(out / calls, 1) if calls else None,
        "output_share_of_consumed": round(out / s("total_consumed_tokens"), 4),
        "split_tokens": {
            "thinking": think_tok,
            "text_prose_est": text_tok,
            "text_prose_est_hi": sp("text_tokens_est_hi"),
            "tool_use_payload_est": tool_tok,
            "tool_use_payload_est_hi": sp("tool_use_tokens_est_hi"),
            "unattributed": sp("unattributed_tokens"),
        },
        "split_share": {
            "thinking": round(think_tok / out, 4) if out else None,
            "text_prose_est": round(text_tok / out, 4) if out else None,
            "text_prose_est_hi": round(sp("text_tokens_est_hi") / out, 4) if out else None,
            "tool_use_payload_est": round(tool_tok / out, 4) if out else None,
            "unattributed": round(sp("unattributed_tokens") / out, 4) if out else None,
        },
        "prose_totals": {
            "text_chars": sp("text_chars"),
            "fenced_code_chars_in_prose": ap("fenced_code_chars_in_prose"),
            "fenced_code_restated_chars": ap("fenced_code_restated_chars"),
            "ceremony_line_chars": ap("ceremony_line_chars"),
            "narration_line_chars": ap("narration_line_chars"),
            "final_summary_tokens_est_total": ap("final_summary_tokens_est"),
            "final_summary_tokens_over_200_cap": over_cap,
        },
        "rules_reachable_tokens_est": reachable,
        "rules_reachable_share_of_output": round(reachable / out, 5) if out else None,
    }


FWORKER_DIR = "/home/lumen/foreman/_runs"


def fworker_sessions():
    """Source A: fworker's own per-attempt usage sidecars.

    These are the real worker.md-driven sessions -- the population this ticket's
    change actually applies to. Their full transcripts live under the
    guard-denied ~/.claude/projects/ path, but `usage-N.json` carries the
    session's authoritative usage totals, including a real (not estimated)
    `output_tokens_details.thinking_tokens`. No content, so the prose
    decomposition still has to come from Source B.
    """
    out = []
    for p in sorted(glob.glob(os.path.join(FWORKER_DIR, "wq*", "usage-*.json"))):
        try:
            u = json.load(open(p))
        except (json.JSONDecodeError, OSError):
            continue
        usage = u.get("usage") or {}
        o = usage.get("output_tokens", 0)
        calls = u.get("result_num_turns") or u.get("turns") or 0
        if not o or not calls:
            continue

        meta = {}
        att = os.path.join(os.path.dirname(p),
                           "attempt-%s.json" % u.get("attempt"))
        if os.path.exists(att):
            try:
                meta = json.load(open(att))
            except (json.JSONDecodeError, OSError):
                pass

        think = (usage.get("output_tokens_details") or {}).get("thinking_tokens", 0)
        consumed = (usage.get("input_tokens", 0)
                    + usage.get("cache_creation_input_tokens", 0) + o)
        out.append({
            "ticket": u.get("ticket"),
            "attempt": u.get("attempt"),
            "started_at": u.get("started_at"),
            "classification": u.get("classification"),
            "model": meta.get("observed_model") or meta.get("model"),
            "effort": meta.get("effort"),
            "archetype": meta.get("archetype"),
            "api_calls": calls,
            "output_tokens": o,
            "thinking_tokens": think,
            "thinking_share_of_output": round(think / o, 4),
            "non_thinking_output_tokens": o - think,
            "total_consumed_tokens": consumed,
            "cache_read_input_tokens": usage.get("cache_read_input_tokens", 0),
            "output_share_of_consumed": round(o / consumed, 4) if consumed else None,
            "output_tokens_per_api_call": round(o / calls, 1),
            "cost_usd": u.get("total_cost_usd"),
        })
    return out


def fworker_rollup(name, rows):
    if not rows:
        return None
    s = lambda k: sum(r[k] or 0 for r in rows)
    o, calls = s("output_tokens"), s("api_calls")
    think = s("thinking_tokens")
    return {
        "bucket": name,
        "sessions": len(rows),
        "api_calls": calls,
        "output_tokens": o,
        "thinking_tokens": think,
        "thinking_share_of_output": round(think / o, 4) if o else None,
        "non_thinking_output_tokens": o - think,
        "output_tokens_per_api_call": round(o / calls, 1) if calls else None,
        "non_thinking_output_tokens_per_api_call": round((o - think) / calls, 1) if calls else None,
        "output_share_of_consumed": round(o / s("total_consumed_tokens"), 4),
        "cost_usd": round(s("cost_usd"), 2),
    }


def per_closed_ticket(rows):
    """Tokens per CLOSED ticket -- every attempt on that ticket, not just the
    one that landed. A ticket that took 4 attempts cost all 4."""
    by_ticket = {}
    for r in rows:
        by_ticket.setdefault(r["ticket"], []).append(r)
    closed = {t: v for t, v in by_ticket.items()
              if any(x["classification"] == "SUCCESS" for x in v)}
    if not closed:
        return None
    tot_out = sum(x["output_tokens"] for v in closed.values() for x in v)
    tot_con = sum(x["total_consumed_tokens"] for v in closed.values() for x in v)
    att = sum(len(v) for v in closed.values())
    return {
        "closed_tickets": len(closed),
        "attempts_on_closed_tickets": att,
        "attempts_per_closed_ticket": round(att / len(closed), 2),
        "output_tokens_per_closed_ticket": round(tot_out / len(closed)),
        "consumed_tokens_per_closed_ticket": round(tot_con / len(closed)),
        "wasted_output_tokens_on_non_success_attempts": sum(
            x["output_tokens"] for v in closed.values() for x in v
            if x["classification"] != "SUCCESS"),
    }


def since_split(rows, ts):
    """Pre/post rollups around a landing timestamp.

    Source A is a live directory, so a post-change re-measurement would
    otherwise average the new sessions in with every old one. Pass the merge
    commit's epoch:
        python3 output_discipline.py --since $(git log -1 --format=%ct <sha>)
    """
    pre = [r for r in rows if (r["started_at"] or 0) < ts]
    post = [r for r in rows if (r["started_at"] or 0) >= ts]
    a, b = fworker_rollup("pre-change", pre), fworker_rollup("post-change", post)
    delta = None
    if a and b:
        delta = {
            k: round((b[k] - a[k]) / a[k], 4) if a.get(k) else None
            for k in ("output_tokens_per_api_call",
                      "non_thinking_output_tokens_per_api_call",
                      "thinking_share_of_output",
                      "output_share_of_consumed")
        }
    return {"split_at_epoch": ts, "pre": a, "post": b, "relative_delta": delta}


def main():
    split_at = None
    if "--since" in sys.argv:
        split_at = float(sys.argv[sys.argv.index("--since") + 1])

    files = sorted(glob.glob(os.path.join(RUNS_DIR, "fbuild*.jsonl")))
    rows = [analyze(p) for p in files]
    rows = [r for r in rows if r["api_calls"] and r["output_tokens"]]

    clean = [r for r in rows if r["file"] in CLEAN_SET]
    other = [r for r in rows if r["file"] not in CLEAN_SET]

    fw = fworker_sessions()
    by_model = {}
    for r in fw:
        by_model.setdefault(r["model"] or "unknown", []).append(r)

    print(json.dumps({
        "note": "wq#632 output-token baseline. Re-run after N post-change "
                "tickets close and diff `source_a.rollup_all` to get the "
                "measured delta. Zero new Anthropic spend.",
        "source_a": {
            "what": "fworker per-attempt usage sidecars -- real worker.md "
                    "sessions, authoritative totals, no content",
            "dir": FWORKER_DIR,
            "rollup_all": fworker_rollup("all fworker sessions", fw),
            "rollup_success_only": fworker_rollup(
                "SUCCESS attempts", [r for r in fw if r["classification"] == "SUCCESS"]),
            "rollup_by_model": {
                m: fworker_rollup(m, v) for m, v in sorted(by_model.items())},
            "per_closed_ticket": per_closed_ticket(fw),
            "since_split": since_split(fw, split_at) if split_at else None,
            "per_session": fw,
        },
        "source_b": {
            "what": "stream-json transcripts -- content decomposition, i.e. "
                    "which slice of output the worker.md rules can reach",
            "dir": RUNS_DIR,
            "rollup_clean_set": rollup("clean_sonnet5_postdiet (wq#704 set)", clean),
            "rollup_all_fbuild": rollup("all fbuild sessions", rows),
            "rollup_other": rollup("non-clean-set fbuild sessions", other),
            "per_session": rows,
        },
    }, indent=2))


if __name__ == "__main__":
    main()
