#!/usr/bin/env python3
"""session_economics.py — wq#734 / foreman-tok T8.

Re-denominates the fleet's token program from DOLLARS into the currency it
actually pays in: percentage points of the Max 20x plan's rolling usage
windows.

Data source: /home/lumen/agents/work-queue/_runs/*.jsonl — the corpus
CALL_AUDIT.md (wq#706) audited. Two signals are joined here that no earlier
ticket joined:

  1. per-API-call `usage` (input / cache_creation / cache_read), summed per
     unique message id exactly as call_audit.py does; and
  2. `rate_limit_event` frames, which the CLI emits whenever plan-limit state
     changes and which carry `{rateLimitType, utilization, resetsAt}` — the
     account's real position in the 5h and 7d windows.

Joining them gives consumption per percentage point of a usage window: the
exchange rate between CALL_AUDIT.md's dollars and the constraint that binds.

Read-only. Touches no fleet state, spends no Anthropic quota.

Known limits of the measurement, all of which push the same way (see
SESSION_ECONOMICS.md §"What the measurement cannot do"):
  - the 7-day window is ROLLING, so a net advance of one point understates
    gross consumption by whatever aged out of the window meanwhile;
  - the corpus sees only `-p` fleet workers, not Frank's interactive sessions
    or claude.ai, which draw on the same pool;
  - `utilization` is reported to two decimals, so one point is the quantum.
Together these make every "per point" figure below a LOWER bound.

Re-run: python3 session_economics.py > session_economics.json
"""
from __future__ import annotations

import datetime as dt
import glob
import json
import os
import statistics
import sys

RUNS = os.environ.get("WQ734_RUNS", "/home/lumen/agents/work-queue/_runs")

# Solved in CALL_AUDIT.md §"Prices are solved, not looked up": these four rates
# reproduce the CLI's reported total_cost_usd to 0.0 relative error on every
# sonnet-5 clean-set session. USD per million tokens. Sessions on other models
# are rescaled to their own reported total_cost_usd (see session_dollars).
PRICE = {"input": 3.00, "output": 15.00, "cache_read": 0.30, "cache_write": 6.00}

# Candidate metering hypotheses, as multiples of one fresh input token.
HYPOTHESES = {
    # everything weighs the same — "the limit meters raw throughput"
    "raw": {"input": 1.0, "cache_write": 1.0, "cache_read": 1.0, "output": 1.0},
    # weighted the way dollars are — "the limit meters the same thing the bill does"
    "cost_weighted": {"input": 1.0, "cache_write": 2.0, "cache_read": 0.1, "output": 5.0},
    # cache reads do not count at all — the hypothesis wq#734 was filed to test
    "free_cache_read": {"input": 1.0, "cache_write": 2.0, "cache_read": 0.0, "output": 5.0},
}
KINDS = ("input", "cache_write", "cache_read", "output")


def iter_lines(path):
    with open(path) as f:
        for i, line in enumerate(f):
            line = line.strip()
            if not line:
                continue
            try:
                yield i, json.loads(line)
            except json.JSONDecodeError:
                continue


def load_session(path):
    """Per-call usage series, plus this session's rate_limit_event frames.

    Input-side counts are exact: `input_tokens`, `cache_creation_input_tokens`
    and `cache_read_input_tokens` in the streamed `message_start` usage are
    final for that call, and summing them per unique message id reproduces the
    result frame (asserted in `integrity` below).

    `output_tokens` in that same frame is NOT final — it is the placeholder the
    stream opens with (1,642 against a billed 119,138 on fbuild618). The
    session total from the result frame is therefore spread uniformly across
    the session's calls. Output is ~0.2% of raw tokens and ~10% of dollars, and
    intervals below mostly contain whole sessions, so the allocation error is
    small; it is flagged rather than hidden.

    Wall clock is reconstructed as mtime - duration_ms, with a frame at line i
    of n placed at start + duration * i/n. Cross-validated in
    `clock_crossvalidation`: concurrent sessions independently report the same
    utilization within a few minutes of each other.
    """
    lines = list(iter_lines(path))
    if not lines:
        return None
    n_lines = lines[-1][0] + 1

    result = None
    calls, seen_ids, frames = [], set(), []
    for i, o in lines:
        typ = o.get("type")
        if typ == "result":
            result = o
        elif typ == "rate_limit_event":
            frames.append((i, o.get("rate_limit_info", {})))
        elif typ == "assistant":
            msg = o.get("message") or {}
            mid = msg.get("id")
            if not mid or mid in seen_ids:
                continue                      # one frame per content block
            seen_ids.add(mid)
            u = msg.get("usage") or {}
            calls.append((i, {
                "input": u.get("input_tokens") or 0,
                "cache_write": u.get("cache_creation_input_tokens") or 0,
                "cache_read": u.get("cache_read_input_tokens") or 0,
                "output": 0,
            }))
    if result is None or not calls:
        return None

    total_output = ((result.get("usage") or {}).get("output_tokens")) or 0
    share = total_output / len(calls)
    for _, u in calls:
        u["output"] = share

    end = os.path.getmtime(path)
    duration = (result.get("duration_ms") or 0) / 1000.0
    start = end - duration

    def at(line_no):
        return start + duration * (line_no / n_lines)

    mu = result.get("modelUsage") or {}
    model = sorted(mu, key=lambda m: -(mu[m].get("costUSD") or 0))[0] if mu else None

    agg = {k: sum(u[k] for _, u in calls) for k in KINDS}
    reported = result.get("total_cost_usd")
    # Rescale so every session's dollars equal what the CLI billed, whatever
    # model it ran on. For sonnet-5 the factor is ~1.0 by construction.
    solved = list_price(agg)
    scale = (reported / solved) if (reported and solved) else 1.0

    return {
        "name": os.path.basename(path),
        "start": start, "end": end, "duration_s": duration,
        "model": model, "cost_usd": reported, "cost_scale": scale,
        "num_turns": result.get("num_turns"), "agg": agg,
        "calls": [(at(i), u) for i, u in calls],
        "frames": [(at(i), fr) for i, fr in frames],
    }


def list_price(u):
    return sum(u[k] * PRICE[k] for k in KINDS) / 1e6


def weigh(u, w):
    return sum(u[k] * w[k] for k in KINDS)


def consumed_between(sessions, t0, t1):
    """Fleet-wide usage the corpus records over [t0, t1)."""
    tot = {k: 0.0 for k in KINDS}
    usd, calls = 0.0, 0
    for s in sessions:
        for t, u in s["calls"]:
            if t0 <= t < t1:
                for k in KINDS:
                    tot[k] += u[k]
                usd += list_price(u) * s["cost_scale"]
                calls += 1
    return tot, usd, calls


def summarize(tot, usd, points):
    row = {name: round(weigh(tot, w) / points) for name, w in HYPOTHESES.items()}
    row["usd"] = round(usd / points, 2)
    return row


def reads_behind(usd, tokens_added):
    """Recover the cache-read token-reads implied by a dollar figure.

    CALL_AUDIT.md prices a chunk's lifetime as one cache write plus every later
    read: usd = (2*tokens + 0.1*reads) * 3/1e6 in input-equivalents. Inverting
    it recovers `reads`, which is what a re-weighting needs and what
    call_audit.json only stores for the four top-level categories.
    """
    input_equiv = usd * 1e6 / PRICE["input"]
    return max(0.0, (input_equiv - 2.0 * tokens_added) / 0.1)


def rerank_levers(call_audit_path):
    """Express CALL_AUDIT.md's category split and R1-R8 in each currency.

    A chunk of context costs `w_write*tokens + w_read*reads` under weighting w;
    assistant-generated chunks additionally cost `w_out*tokens` to produce.
    Under `cost_weighted` this reproduces CALL_AUDIT's dollars exactly, which
    is the check that the arithmetic is the same arithmetic.
    """
    if not os.path.exists(call_audit_path):
        return {"error": "call_audit.json not found"}
    a = json.load(open(call_audit_path))["audit"]
    cats, lev = a["categories"], a["levers"]
    calls = a["api_calls"]
    boot = a["fixed_prefix"]["total_tokens"]

    def units(w, tokens, reads, generated=0.0):
        return w["cache_write"] * tokens + w["cache_read"] * reads + w["output"] * generated

    rows = {}
    for name, c in cats.items():
        generated = c["tokens_added"] if name.startswith("assistant") else 0.0
        rows[name] = {"tokens_added": c["tokens_added"], "reads": c["carry_token_reads"],
                      "generated": generated}
    rows["boot_payload"] = {"tokens_added": boot, "reads": boot * (calls - 1), "generated": 0.0}

    category_share = {}
    for hname, w in HYPOTHESES.items():
        vals = {k: units(w, v["tokens_added"], v["reads"], v["generated"]) for k, v in rows.items()}
        tot = sum(vals.values())
        category_share[hname] = {k: round(100 * v / tot, 1) for k, v in
                                  sorted(vals.items(), key=lambda kv: -kv[1])}

    # R1-R6: each lever's saving, re-weighted. `reads` is recovered from the
    # dollar saving call_audit.py already simulated.
    levers = []

    def add(rid, label, tokens, usd, generated=0.0):
        reads = reads_behind(usd, tokens)
        per_h = {h: round(units(w, tokens, reads, generated)) for h, w in HYPOTHESES.items()}
        levers.append({"id": rid, "lever": label, "tokens_removed": round(tokens),
                       "usd": round(usd, 4), "implied_reads": round(reads),
                       "units_saved": per_h})

    caps = {c["cap_tokens"]: c for c in lev["tool_result_cap"]}
    add("R1", "cap any tool result at 2,000 tokens",
        caps[2000]["tokens_removed"], caps[2000]["saving_usd"])
    add("R2", "cap any tool result at 1,000 tokens",
        caps[1000]["tokens_removed"], caps[1000]["saving_usd"])
    ur = lev["unbounded_whole_file_reads"]
    add("R3", "bound Read — grep/offset before whole-file", ur["tokens"], ur["carry_cost_usd"])
    bcaps = {c["cap_tokens"]: c for c in lev["bash_output_cap"]}
    add("R4", "cap Bash output at 1,000 tokens",
        bcaps[1000]["tokens_removed"], bcaps[1000]["saving_usd"])
    add("R5", "suppress re-reading a file already in context",
        a["duplication"]["redundant_tokens"], lev["dedupe_repeat_reads_usd"])
    add("R6", "keep errored tool results out of the carry",
        a["error_waste"]["tokens"], lev["errored_results_usd"])
    add("R8", "delete every word we wrote (slab + CLAUDE.md + trailer)",
        a["fixed_prefix"]["ours_cl100k_tokens"], lev["whole_fixed_prefix_if_deleted_usd"])

    session_units = {h: sum(units(w, v["tokens_added"], v["reads"], v["generated"])
                            for v in rows.values()) for h, w in HYPOTHESES.items()}
    for row in levers:
        row["pct_of_session"] = {h: round(100 * row["units_saved"][h] / session_units[h], 1)
                                 for h in HYPOTHESES}
        row["rank"] = {}
    for h in HYPOTHESES:
        order = sorted(levers, key=lambda r: -r["units_saved"][h])
        for i, row in enumerate(order, 1):
            row["rank"][h] = i

    return {
        "session": a["file"], "api_calls": calls,
        "reconstruction_check_usd": round(session_units["cost_weighted"] * PRICE["input"] / 1e6, 4),
        "reported_session_usd": lev["session_cost_usd"],
        "session_units": {h: round(v) for h, v in session_units.items()},
        "category_share_pct": category_share,
        "levers": levers,
    }


def main():
    sessions = [s for s in (load_session(p) for p in sorted(glob.glob(f"{RUNS}/*.jsonl"))) if s]

    # ---- integrity: input-side per-call sums reproduce the result frame ----
    errs = []
    for s in sessions:
        if s["cost_usd"]:
            errs.append(abs(list_price(s["agg"]) * s["cost_scale"] - s["cost_usd"]) / s["cost_usd"])
    sonnet5 = [s for s in sessions if s["model"] == "claude-sonnet-5"]
    scale_err = [abs(s["cost_scale"] - 1.0) for s in sonnet5]

    # ---- the utilization trajectory ----
    obs = []
    for s in sessions:
        for t, fr in s["frames"]:
            if fr.get("utilization") is None:
                continue
            obs.append({
                "t": t, "iso": dt.datetime.fromtimestamp(t).isoformat(timespec="minutes"),
                "session": s["name"], "pool": fr.get("rateLimitType"),
                "utilization": fr["utilization"], "status": fr.get("status"),
                "resets_at": fr.get("resetsAt"), "overage": fr.get("isUsingOverage"),
            })
    obs.sort(key=lambda o: o["t"])

    # Independent sessions reporting the same (pool, window, utilization) at
    # nearly the same moment is the check on the reconstructed clock. Only
    # runs of the value that are contiguous in time count — the 7d window is
    # rolling, so a utilization value legitimately recurs days later.
    spreads = []
    by_pool = {}
    for o in obs:
        by_pool.setdefault((o["pool"], o["resets_at"]), []).append(o)
    for key, seq in by_pool.items():
        run = [seq[0]]
        for o in seq[1:]:
            if o["utilization"] == run[-1]["utilization"]:
                run.append(o)
            else:
                if len({r["session"] for r in run}) > 1:
                    spreads.append(run[-1]["t"] - run[0]["t"])
                run = [o]
        if len({r["session"] for r in run}) > 1:
            spreads.append(run[-1]["t"] - run[0]["t"])

    # ---- exchange rate: consumption per net utilization point ----
    # Collapse each pool's trajectory to first-sighting-of-each-change, then
    # measure consumption between successive crossings. First-to-first is the
    # right pair: with several workers live, the same crossing is reported by
    # each of them within seconds, and the consumption that caused it happened
    # before the earliest report.
    intervals, trajectories = [], {}
    for (pool, resets), seq in sorted(by_pool.items(), key=lambda kv: str(kv[0])):
        steps = [seq[0]]
        for o in seq[1:]:
            if o["utilization"] != steps[-1]["utilization"]:
                steps.append(o)
        trajectories[f"{pool}@{resets}"] = [
            {"iso": s_["iso"], "utilization": s_["utilization"], "session": s_["session"]}
            for s_ in steps
        ]
        for a, b in zip(steps, steps[1:]):
            d_util = round(b["utilization"] - a["utilization"], 4)
            points = round(d_util * 100, 2)
            tot, usd, ncalls = consumed_between(sessions, a["t"], b["t"])
            intervals.append({
                "pool": pool, "resets_at": resets,
                "from_util": a["utilization"], "to_util": b["utilization"],
                "net_points": points,
                "t0": a["iso"], "t1": b["iso"],
                "elapsed_h": round((b["t"] - a["t"]) / 3600, 2),
                "calls": ncalls,
                "tokens": {k: round(v) for k, v in tot.items()},
                "usd": round(usd, 2),
                "per_point": summarize(tot, usd, points) if points > 0 else None,
            })

    # ---- span totals ----
    spans = {}
    for (pool, resets), seq in by_pool.items():
        if len(seq) < 2:
            continue
        a, b = seq[0], seq[-1]
        points = round((b["utilization"] - a["utilization"]) * 100, 2)
        if points <= 0:
            continue
        tot, usd, ncalls = consumed_between(sessions, a["t"], b["t"])
        per = summarize(tot, usd, points)
        spans[f"{pool}@{resets}"] = {
            "from_util": a["utilization"], "to_util": b["utilization"],
            "net_points": points, "t0": a["iso"], "t1": b["iso"],
            "elapsed_h": round((b["t"] - a["t"]) / 3600, 2),
            "calls": ncalls, "tokens": {k: round(v) for k, v in tot.items()},
            "usd": round(usd, 2),
            "per_net_point": per,
            "implied_window_floor": {k: (round(v * 100) if k != "usd" else round(v * 100, 2))
                                     for k, v in per.items()},
        }

    # ---- stability: does any weighting hold constant across intervals? ----
    # A correct metering hypothesis predicts a constant per-point rate. Only
    # intervals with real duration and real traffic can test it.
    usable = [r for r in intervals
              if r["pool"] == "seven_day" and r["net_points"] > 0
              and r["elapsed_h"] >= 0.5 and r["calls"] >= 50]
    stability = {}
    for name in list(HYPOTHESES) + ["usd"]:
        vals = [r["per_point"][name] for r in usable if r["per_point"][name] > 0]
        if len(vals) >= 3:
            stability[name] = {
                "n": len(vals), "mean": round(statistics.mean(vals), 1),
                "median": round(statistics.median(vals), 1),
                "min": round(min(vals), 1), "max": round(max(vals), 1),
                "cv": round(statistics.pstdev(vals) / statistics.mean(vals), 3),
            }

    # ---- are the hypotheses even separable by this corpus? ----
    # raw / cost_weighted / free_cache_read differ only in how they treat
    # cache reads relative to everything else. If every interval has near the
    # same cache-read share, the three weightings are collinear and no amount
    # of this data can tell them apart. Measure that share directly.
    mixes = []
    for r in usable:
        t = r["tokens"]
        raw_tot = sum(t.values())
        if raw_tot:
            mixes.append(t["cache_read"] / raw_tot)
    separability = {
        "n_intervals": len(mixes),
        "cache_read_share_of_raw_min": round(min(mixes), 4) if mixes else None,
        "cache_read_share_of_raw_max": round(max(mixes), 4) if mixes else None,
        "note": "the three weightings are collinear to the extent this share is constant",
    }
    if mixes:
        ratios = [r["per_point"]["raw"] / r["per_point"]["cost_weighted"] for r in usable]
        separability["raw_over_cost_weighted_min"] = round(min(ratios), 3)
        separability["raw_over_cost_weighted_max"] = round(max(ratios), 3)

    # ---- re-rank CALL_AUDIT's levers under each metering hypothesis ----
    reranking = rerank_levers(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                            "call_audit.json"))

    # ---- per-session cost in each currency ----
    per_session = []
    for s in sorted(sessions, key=lambda x: -(x["cost_usd"] or 0))[:12]:
        raw = sum(s["agg"].values())
        per_session.append({
            "session": s["name"], "model": s["model"], "calls": len(s["calls"]),
            "usd": round(s["cost_usd"] or 0, 2),
            "tokens": {k: round(v) for k, v in s["agg"].items()},
            "cache_read_share_of_raw": round(s["agg"]["cache_read"] / max(1, raw), 4),
            "weighted": {n: round(weigh(s["agg"], w)) for n, w in HYPOTHESES.items()},
        })

    json.dump({
        "integrity": {
            "sessions": len(sessions),
            "sonnet5_sessions": len(sonnet5),
            "max_scale_error_sonnet5": round(max(scale_err), 6) if scale_err else None,
            "max_rel_cost_error_after_rescale": round(max(errs), 9) if errs else None,
            "note": "input-side usage summed per unique message id; dollars rescaled "
                    "per session to the CLI's own total_cost_usd",
        },
        "clock_crossvalidation": {
            "contiguous_runs_spanning_multiple_sessions": len(spreads),
            "max_spread_s": round(max(spreads), 1) if spreads else None,
            "median_spread_s": round(statistics.median(spreads), 1) if spreads else None,
        },
        "trajectories": trajectories,
        "spans": spans,
        "intervals": intervals,
        "stability_across_intervals": stability,
        "separability": separability,
        "lever_reranking": reranking,
        "per_session": per_session,
    }, sys.stdout, indent=2, default=str)
    print()


if __name__ == "__main__":
    main()
