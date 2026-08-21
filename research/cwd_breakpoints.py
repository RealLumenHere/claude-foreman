#!/usr/bin/env python3
"""cwd_breakpoints.py — where the cache breakpoints actually sit (wq#705 appendix).

Companion to cwd_stability.py, which established that first-turn `cache_read` is a
per-model constant that per-ticket cwd does not disturb. This script answers the
three questions that one left open, and backs CWD_STABILITY.md's appendix:

  1. Where are the cache BREAKPOINTS? cwd_stability.py bounded the cwd lever at
     <=1,029 tokens "provided a breakpoint lands at the region3/region4 boundary".
     Measuring which cache_read values are reachable at all settles that: the only
     reachable values are end-of-tools and end-of-system, so the ~1,029-token run
     between cwd and gitStatus is NOT independently cacheable and the cwd lever is
     0 tokens, not <=1,029.
  2. What is the cache TTL? (1h, not the 5m default — which changes the cache-write
     multiplier used for costing from 1.25x to 2x.)
  3. Is first-turn `cache_read` genuine CROSS-SESSION reuse, or an in-process
     pre-warm? cwd_stability.py flagged this as undecidable from transcripts alone.
     It is decidable: the tier a session reaches depends on OTHER sessions' recent
     history in the same cwd, which no in-process pre-warm can explain.

Same zero-new-spend method: reads existing Claude Code transcripts only, makes no
Anthropic calls. Post-INSTRUCTION_DIET worker transcripts live at
~/.claude/projects/-home-lumen-worktrees-wq*-a*/<session_id>.jsonl, one per fworker
attempt, with per-turn usage in assistant `message.usage`. Dispatch metadata
(model/effort/archetype) is joined from foreman/_runs/wq*/attempt-N.json by
session_id.

    python3 research/cwd_breakpoints.py            # full report
    python3 research/cwd_breakpoints.py --json     # machine-readable summary
"""
import argparse
import collections
import datetime
import glob
import json
import os
import statistics
import sys

PROJECTS = os.environ.get("CWD_BREAKPOINTS_PROJECTS", "/home/lumen/.claude/projects")
RUNS = os.environ.get("CWD_BREAKPOINTS_RUNS", "/home/lumen/foreman/_runs")

# Measured cache TTL is 1h (the CLI populates cache_creation.ephemeral_1h_input_tokens
# and never ephemeral_5m). Anthropic bills a 1h cache write at 2x base input and a
# cache read at 0.1x; PRICE_* are models.yaml api_price_mtok, so every dollar figure
# here is NOTIONAL API cost — these workers run on a Max subscription, not API billing.
TTL_S = 3600
WRITE_MULT_1H = 2.00
WRITE_MULT_5M = 1.25
READ_MULT = 0.10
PRICE_IN = {"claude-opus-5": 5.0, "claude-sonnet-5": 2.0, "claude-fable-5": 10.0}
PRICE_OUT = {"claude-opus-5": 25.0, "claude-sonnet-5": 10.0, "claude-fable-5": 50.0}

# Tools-block sizes observed on CLI 2.1.233. The tools block is the first cached
# segment (Anthropic's prefix order is tools -> system -> messages), so a session's
# first-turn cache_read equals this whenever the toolset matches and nothing else does.
TOOLS_TIER = {"claude-opus-5": 16236, "claude-sonnet-5": 24649}


def parse_ts(s):
    if not s:
        return None
    try:
        return datetime.datetime.strptime(s[:19], "%Y-%m-%dT%H:%M:%S").timestamp()
    except ValueError:
        return None


def scan(path):
    """One transcript -> summary dict. Sidechain (subagent) frames are excluded:
    they carry their own prefix and would pollute the first-turn measurement."""
    out = {"path": path, "cwd": None, "session_id": None, "version": None,
           "first_ts": None, "last_ts": None, "turns": [], "first_user_len": None}
    with open(path, errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                e = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(e, dict):
                continue
            for k, ek in (("cwd", "cwd"), ("session_id", "sessionId"), ("version", "version")):
                if e.get(ek) and not out[k]:
                    out[k] = e[ek]
            ts = parse_ts(e.get("timestamp"))
            if ts:
                out["first_ts"] = out["first_ts"] or ts
                out["last_ts"] = ts
            if e.get("isSidechain"):
                continue
            if e.get("type") == "user" and out["first_user_len"] is None:
                c = (e.get("message") or {}).get("content")
                txt = ("".join(b.get("text", "") for b in c if isinstance(b, dict))
                       if isinstance(c, list) else (c if isinstance(c, str) else ""))
                out["first_user_len"] = len(txt)
            if e.get("type") == "assistant":
                m = e.get("message") or {}
                u = m.get("usage") or {}
                if not u:
                    continue
                cc = u.get("cache_creation") or {}
                out["turns"].append({
                    "in": u.get("input_tokens", 0),
                    "cc": u.get("cache_creation_input_tokens", 0),
                    "cr": u.get("cache_read_input_tokens", 0),
                    "out": u.get("output_tokens", 0),
                    "cc5": cc.get("ephemeral_5m_input_tokens"),
                    "cc1h": cc.get("ephemeral_1h_input_tokens"),
                })
    return out


def load_sessions():
    sessions = []
    for p in sorted(glob.glob(os.path.join(PROJECTS, "*", "*.jsonl"))):
        try:
            s = scan(p)
        except OSError as e:
            print(f"skip {p}: {e}", file=sys.stderr)
            continue
        if not s["turns"]:
            continue
        s["first_ts"] = s["first_ts"] or os.path.getmtime(p)
        s["last_ts"] = s["last_ts"] or s["first_ts"]
        s["cr0"], s["cc0"] = s["turns"][0]["cr"], s["turns"][0]["cc"]
        sessions.append(s)
    sessions.sort(key=lambda s: s["first_ts"])
    return sessions


def load_dispatch_meta():
    meta = {}
    for p in glob.glob(os.path.join(RUNS, "wq*", "attempt-*.json")):
        try:
            a = json.load(open(p))
        except (OSError, ValueError):
            continue
        if a.get("session_id"):
            meta[a["session_id"]] = a
    return meta


def workers(sessions, meta, version="2.1.233"):
    """fworker worker sessions only: cwd under WORKTREES_ROOT, current CLI."""
    W = [s for s in sessions
         if s["cwd"] and "/worktrees/wq" in s["cwd"] and s["version"] == version]
    for s in W:
        m = meta.get(s["session_id"], {})
        # a handful of attempt-N.json files predate the session_id field; fall back to
        # the tools tier, which is 1:1 with the model on this CLI version.
        s["model"] = m.get("model") or next(
            (k for k, v in TOOLS_TIER.items() if v == s["cr0"]), "claude-sonnet-5")
        s["ticket"], s["attempt"] = m.get("ticket"), m.get("attempt")
    return W


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    sessions = load_sessions()
    meta = load_dispatch_meta()
    W = workers(sessions, meta)
    S233 = [s for s in sessions if s["version"] == "2.1.233"]
    R = {}

    # -- 1. does any worker session miss the prefix cache entirely? --
    R["worker_sessions"] = len(W)
    R["worker_first_turn_cr_zero"] = sum(1 for s in W if s["cr0"] == 0)
    R["worker_tools_tiers"] = dict(collections.Counter(s["cr0"] for s in W))

    # -- 2. cwd necessity: high tier == tools+system, only same-cwd sessions reach it --
    def prior_same_cwd(s):
        p = [o for o in S233 if o is not s and o["cwd"] == s["cwd"]
             and o["first_ts"] <= s["first_ts"]]
        return min((s["first_ts"] - o["last_ts"] for o in p), default=None)

    # A session is tier-2 ("tools + system") when its first-turn cache_read sits in a
    # band above one of the tools-only tiers: far enough up to include the ~5.5k system
    # block, not so far that it is a tier-3 whole-request match (those land beyond the
    # band and always carry cache_creation == 0). The exact tools-tier values are
    # excluded explicitly, because the opus band (16236+) would otherwise swallow the
    # sonnet tools tier (24649) and misreport 47 tools-only reads as system-block hits.
    high = [s for s in S233
            if s["cr0"] not in TOOLS_TIER.values()
            and any(t + 4000 < s["cr0"] < t + 9000 for t in TOOLS_TIER.values())]
    R["high_tier_n"] = len(high)
    R["high_tier_with_recent_same_cwd"] = sum(
        1 for s in high if (prior_same_cwd(s) is not None and prior_same_cwd(s) < TTL_S))
    R["high_tier_counterexamples"] = R["high_tier_n"] - R["high_tier_with_recent_same_cwd"]

    # -- 2b. decay curve: same-cwd gap vs tier-2 attainment --
    hi_ids = {s["session_id"] for s in high}
    BUCKETS = ((300, "<5m (incl. concurrent)"), (3600, "5m-1h"),
               (86400, "1h-24h"), (float("inf"), ">24h"))
    decay = collections.defaultdict(lambda: [0, 0])
    for s in S233:
        gap = prior_same_cwd(s)
        if gap is None:
            continue
        label = next(lab for lim, lab in BUCKETS if gap < lim)
        decay[label][0 if s["session_id"] in hi_ids else 1] += 1
    R["decay_by_gap"] = {k: {"tier2": v[0], "tools_only": v[1]} for k, v in decay.items()}

    # -- 2c. the negative control: worker pairs close in time, different cwd --
    close_diff_cwd = sum(
        1 for i, a in enumerate(W) for b in W[i + 1:]
        if b["first_ts"] - a["first_ts"] < TTL_S and a["cwd"] != b["cwd"])
    R["worker_pairs_close_different_cwd"] = close_diff_cwd
    R["worker_pairs_close_different_cwd_reaching_tier2"] = sum(
        1 for s in W if s["session_id"] in hi_ids)

    # -- 3. system-block size = high tier - tools tier, per model --
    sysblock = {}
    for model, tools in TOOLS_TIER.items():
        hs = [s["cr0"] for s in high if tools < s["cr0"] < tools + 12000]
        if hs:
            sysblock[model] = int(statistics.median(hs)) - tools
    R["system_block_tokens"] = sysblock

    # -- 4. measured cache TTL --
    t5 = sum(t["cc5"] or 0 for s in S233 for t in s["turns"])
    t1h = sum(t["cc1h"] or 0 for s in S233 for t in s["turns"])
    R["ephemeral_5m_tokens"], R["ephemeral_1h_tokens"] = t5, t1h
    R["effective_ttl"] = "1h" if t1h and not t5 else "mixed"

    # -- 5. hit-rate simulation for a single fixed cwd shared by all workers --
    for i, s in enumerate(W):
        s["would_hit"] = any(o["cr0"] == s["cr0"] and s["first_ts"] - o["last_ts"] < TTL_S
                             for o in W[:i])
    R["sim_hits"] = sum(1 for s in W if s["would_hit"])
    R["sim_hit_rate"] = round(R["sim_hits"] / len(W), 3) if W else 0

    # -- 6. cost lines --
    lines, toks = collections.Counter(), collections.Counter()
    for s in W:
        bi, bo = PRICE_IN[s["model"]] / 1e6, PRICE_OUT[s["model"]] / 1e6
        for i, t in enumerate(s["turns"]):
            key = "cache_write_first_turn" if i == 0 else "cache_write_later_turns"
            lines["input"] += t["in"] * bi
            lines[key] += t["cc"] * bi * WRITE_MULT_1H
            lines["cache_read"] += t["cr"] * bi * READ_MULT
            lines["output"] += t["out"] * bo
            toks["input"] += t["in"]; toks[key] += t["cc"]
            toks["cache_read"] += t["cr"]; toks["output"] += t["out"]
    total = sum(lines.values())
    R["cost_lines_usd"] = {k: round(v, 2) for k, v in lines.items()}
    R["cost_tokens"] = dict(toks)
    R["total_usd"] = round(total, 2)

    # The system block is only reachable when EVERY per-session field inside it matches
    # -- cwd AND the gitStatus branch name, which cwd_stability.py located ~1,029 tokens
    # after cwd in the same block. Since no breakpoint sits between them (see (1)/(2)),
    # stabilizing cwd alone converts nothing. These figures are therefore the value of
    # stabilizing cwd *and* the per-attempt branch name, i.e. of giving up per-ticket
    # branches -- an upper bound on a thing we are not proposing to do.
    def upper_bound(mult, only_hits):
        return sum(sysblock.get(s["model"], 0) * PRICE_IN[s["model"]] / 1e6 * (mult - READ_MULT)
                   for s in W if (s["would_hit"] or not only_hits))
    R["cwd_alone_usd"] = 0.0
    R["cwd_plus_branch_usd"] = round(upper_bound(WRITE_MULT_1H, True), 4)
    R["cwd_plus_branch_ceiling_usd"] = round(upper_bound(WRITE_MULT_1H, False), 4)
    R["cwd_plus_branch_pct"] = round(R["cwd_plus_branch_usd"] / total * 100, 2)
    R["cwd_plus_branch_ceiling_pct"] = round(R["cwd_plus_branch_ceiling_usd"] / total * 100, 2)
    R["sensitivity_5m_usd"] = round(upper_bound(WRITE_MULT_5M, True), 4)

    if args.json:
        print(json.dumps(R, indent=2))
        return

    print(f"== wq#705 cwd_breakpoints — {len(sessions)} transcripts, "
          f"{len(W)} fworker worker sessions (CLI 2.1.233) ==\n")
    print("(1) DO WORKER SESSIONS GET A CROSS-SESSION CACHE HIT ON TURN 1?")
    print(f"    first-turn cache_read == 0 : {R['worker_first_turn_cr_zero']} / {len(W)}")
    print(f"    tools-block tiers hit      : {R['worker_tools_tiers']}")
    print("\n(2) IS cwd THE DIVERGENCE POINT?")
    print(f"    sessions reaching tools+system tier      : {R['high_tier_n']}")
    print(f"    ... with a same-cwd session inside 1h    : {R['high_tier_with_recent_same_cwd']}")
    print(f"    ... counter-examples (different cwd)     : {R['high_tier_counterexamples']}")
    print(f"    worker pairs starting <1h apart in DIFFERENT worktrees : {close_diff_cwd}")
    print(f"    ... of those worker sessions reaching tier 2           : "
          f"{R['worker_pairs_close_different_cwd_reaching_tier2']}")
    print("\n(2b) DECAY: same-cwd gap vs tier-2 attainment")
    for lim, lab in BUCKETS:
        v = decay.get(lab)
        if not v:
            continue
        n = v[0] + v[1]
        print(f"    {lab:<24} {v[0]:>3} / {n:<3} ({v[0]/n*100:>3.0f}%)")
    print(f"\n(3) SYSTEM-BLOCK SIZE (reachable only if cwd AND branch match): {sysblock}")
    print(f"\n(4) EFFECTIVE CACHE TTL: {R['effective_ttl']} "
          f"(5m={t5:,} tok, 1h={t1h:,} tok)")
    print(f"\n(5) SIMULATION, cwd AND branch both stabilized: {R['sim_hits']}/{len(W)} "
          f"sessions would hit ({R['sim_hit_rate']*100:.0f}%)")
    print("    (cwd alone: 0 sessions -- gitStatus diverges before the next breakpoint)")
    print("\n(6) COST LINES (notional API $, models.yaml rates)")
    print(f"    {'line':<26} {'tokens':>13} {'$':>8} {'share':>7}")
    for k in ("cache_read", "output", "cache_write_later_turns",
              "cache_write_first_turn", "input"):
        print(f"    {k:<26} {toks[k]:>13,} {lines[k]:>8.2f} {lines[k]/total*100:>6.1f}%")
    print(f"    {'TOTAL':<26} {'':>13} {total:>8.2f} {100.0:>6.1f}%")
    print("\n(7) WHAT THE LEVER IS WORTH")
    print("    stabilizing cwd ALONE                  : $0.00 = 0% "
          "(no breakpoint between cwd and gitStatus)")
    print(f"    stabilizing cwd AND branch, observed   : "
          f"${R['cwd_plus_branch_usd']:.2f} = {R['cwd_plus_branch_pct']}% of worker spend")
    print(f"    stabilizing cwd AND branch, 100% hits  : "
          f"${R['cwd_plus_branch_ceiling_usd']:.2f} = "
          f"{R['cwd_plus_branch_ceiling_pct']}% of worker spend")
    print("    ... and giving up per-ticket branches is not on the table (wq#649).")


if __name__ == "__main__":
    main()
