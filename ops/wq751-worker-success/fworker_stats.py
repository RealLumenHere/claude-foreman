#!/usr/bin/env python3
"""wq#751 (ops-audit A1): fworker success-rate-vs-max_turns analysis.

Read-only. Mines /home/lumen/foreman/_runs/wq*/attempt-*.json (the per-attempt
records fworker.py writes at the end of every run) joined against the wq task
metadata in /home/lumen/agents/work-queue/queue.db, and prints a markdown
report to stdout. Re-run any time to refresh reports/opsaudit_a1_worker_success.md
as more attempts land — the numbers will move as the dataset grows.

Every claim this script prints — table cells AND surrounding prose — must be
derived from the loaded attempts/tickets on this run. A literal count, rate or
comparative assertion baked into a string is a bug (wq#839): it survives the
rerun the report header promises and then contradicts the tables printed
beside it. Reach for `_rate()` and a computed branch instead.

Usage: fworker_stats.py [--runs-glob GLOB] [--db PATH]
"""
import argparse
import datetime as dt
import glob
import json
import sqlite3
import statistics as st
from collections import Counter, defaultdict
from pathlib import Path
from typing import NamedTuple

DEFAULT_RUNS_GLOB = "/home/lumen/foreman/_runs/wq*/attempt-*.json"
DEFAULT_DB = "/home/lumen/agents/work-queue/queue.db"

TURN_DRIFT_MARGIN = 1.3   # headroom over max observed SUCCESS turns, for the §2 drift
CEILING_ROUNDING = 10     # recommended ceilings are rounded to this, for legibility


class Loaded(NamedTuple):
    attempts: list
    unreadable: list


def load_attempts(runs_glob):
    """Unreadable attempt files are reported, not swallowed: silently dropping
    one shrinks every denominator in a report whose premise is that its numbers
    are complete (wq#839)."""
    attempts, unreadable = [], []
    for f in sorted(glob.glob(runs_glob)):
        try:
            attempts.append(json.load(open(f)))
        except (json.JSONDecodeError, OSError):
            unreadable.append(f)
    return Loaded(attempts, unreadable)


def load_tickets(db_path, ticket_ids):
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    ids = ",".join(str(int(t)) for t in ticket_ids)
    rows = con.execute(f"select * from tasks where id in ({ids})").fetchall()
    return {r["id"]: r for r in rows}


def by_ticket(attempts):
    bt = defaultdict(list)
    for a in attempts:
        bt[a["ticket"]].append(a)
    for t in bt:
        bt[t].sort(key=lambda x: x["attempt"])
    return bt


def _self_path():
    """This script's path relative to its repo root, so the generated header is
    identical whichever worktree the rerun happened in."""
    me = Path(__file__).resolve()
    for parent in me.parents:
        if (parent / ".git").exists():
            return str(me.relative_to(parent))
    return str(me)


def _rate(num, den):
    return f"{num}/{den} ({num/den:.0%})" if den else f"{num}/{den} (n/a)"


def first_retry_pairs(attempts):
    """The MAX_ATTEMPTS=2 mechanism. Shared by §4 and §6 so both quote the same
    numbers."""
    return [(alist[0], alist[1]) for alist in by_ticket(attempts).values()
            if len(alist) >= 2 and alist[0]["classification"] != "SUCCESS"]


class KilledThenFC(NamedTuple):
    killed_first: int
    same_ceiling: int
    ever_fc: int


def killed_then_false_complete(attempts):
    """§3 characterises this pattern and §6 prediction 1 leans on it as a causal
    mechanism, so both read it from here rather than asserting it (wq#839)."""
    killed_first = same_ceiling = 0
    ever_fc = [alist for alist in by_ticket(attempts).values()
               if any(a["classification"] == "FALSE_COMPLETE" for a in alist)]
    for alist in ever_fc:
        i = next(i for i, a in enumerate(alist) if a["classification"] == "FALSE_COMPLETE")
        prior_kills = [a for a in alist[:i] if a["classification"] == "KILLED"]
        if prior_kills:
            killed_first += 1
            if any(a["max_turns"] == alist[i]["max_turns"] for a in prior_kills):
                same_ceiling += 1
    return KilledThenFC(killed_first, same_ceiling, len(ever_fc))


def pct(values, p):
    if not values:
        return None
    values = sorted(values)
    k = (len(values) - 1) * p / 100
    f, c = int(k), min(int(k) + 1, len(values) - 1)
    if f == c:
        return values[f]
    return values[f] + (values[c] - values[f]) * (k - f)


def sum_token_usage(transcript_path):
    tot = {"input": 0, "output": 0, "cache_read": 0, "cache_write": 0}
    with open(transcript_path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
            except json.JSONDecodeError:
                continue
            usage = (d.get("message") or {}).get("usage") if isinstance(d.get("message"), dict) else None
            if not usage:
                continue
            tot["input"] += usage.get("input_tokens", 0) or 0
            tot["output"] += usage.get("output_tokens", 0) or 0
            tot["cache_read"] += usage.get("cache_read_input_tokens", 0) or 0
            tot["cache_write"] += usage.get("cache_creation_input_tokens", 0) or 0
    return tot


def find_transcript(session_id):
    matches = glob.glob(f"/home/lumen/.claude/projects/*/{session_id}.jsonl")
    return matches[0] if matches else None


def section_success_rates(attempts):
    lines = ["## 1. Success rate per archetype and per model (attempt-level)\n"]
    lines.append("| archetype | SUCCESS | FALSE_COMPLETE | TURNS | KILLED | total | success rate |")
    lines.append("|---|---|---|---|---|---|---|")
    by_arch = defaultdict(Counter)
    for a in attempts:
        by_arch[a["archetype"]][a["classification"]] += 1
    for arch, c in sorted(by_arch.items(), key=lambda kv: -sum(kv[1].values())):
        total = sum(c.values())
        rate = c.get("SUCCESS", 0) / total
        lines.append(f"| {arch} | {c.get('SUCCESS',0)} | {c.get('FALSE_COMPLETE',0)} | "
                      f"{c.get('TURNS',0)} | {c.get('KILLED',0)} | {total} | {rate:.0%} |")
    lines.append("")
    lines.append("| model | SUCCESS | FALSE_COMPLETE | TURNS | KILLED | total | success rate |")
    lines.append("|---|---|---|---|---|---|---|")
    by_model = defaultdict(Counter)
    for a in attempts:
        by_model[a["model"]][a["classification"]] += 1
    for model, c in sorted(by_model.items(), key=lambda kv: -sum(kv[1].values())):
        total = sum(c.values())
        rate = c.get("SUCCESS", 0) / total
        lines.append(f"| {model} | {c.get('SUCCESS',0)} | {c.get('FALSE_COMPLETE',0)} | "
                      f"{c.get('TURNS',0)} | {c.get('KILLED',0)} | {total} | {rate:.0%} |")
    lines.append("")
    return "\n".join(lines)


def section_turns_distribution(attempts):
    lines = ["## 2. turns_used distribution: SUCCESS vs LIMIT failures\n"]
    succ_attempts = [a for a in attempts if a["classification"] == "SUCCESS"]
    turns_attempts = [a for a in attempts if a["classification"] == "TURNS"]
    succ_turns = [a["turns"] for a in succ_attempts]
    turns_cls = [a["turns"] for a in turns_attempts]
    killed_te = [a for a in attempts if a["classification"] == "KILLED" and a.get("killed_reason") == "turns_exceeded"]

    if succ_turns:
        lines.append(f"- SUCCESS attempts (n={len(succ_turns)}): turns_used "
                      f"p50={pct(succ_turns,50):.0f} p75={pct(succ_turns,75):.0f} "
                      f"p90={pct(succ_turns,90):.0f} p95={pct(succ_turns,95):.0f} "
                      f"max={max(succ_turns)}")
    else:
        lines.append("- SUCCESS attempts (n=0): no successes in this dataset, so there is no "
                     "turns_used population for a ceiling to respect.")
    if turns_cls:
        lines.append(f"- TURNS-classified attempts (n={len(turns_cls)}, CLI self-reported "
                      f"error_max_turns): turns_used p50={pct(turns_cls,50):.0f} "
                      f"p90={pct(turns_cls,90):.0f}")
    else:
        lines.append("- TURNS-classified attempts (n=0): the CLI never self-reported "
                     "error_max_turns in this dataset.")
    overshoot = sorted(set(a["turns"] - a["max_turns"] for a in killed_te))
    if not overshoot:
        kill_point = "none in this dataset"
    elif len(overshoot) == 1:
        kill_point = (f"every single one was killed at exactly max_turns{overshoot[0]:+d}, "
                      f"never earlier, never later")
    else:
        kill_point = (f"turns_used - max_turns at the kill spanned {overshoot[0]:+d} to "
                      f"{overshoot[-1]:+d} ({len(overshoot)} distinct overshoots), so the "
                      f"backstop no longer fires at one fixed offset")
    lines.append(f"- KILLED/turns_exceeded attempts (n={len(killed_te)}, fworker's own "
                  f"TURN_BACKSTOP_BUFFER=5 supervisor kill fired — CLI never emitted a "
                  f"terminal frame): {kill_point}.")
    ratios = [a["turns"] / a["max_turns"] for a in killed_te]
    if ratios:
        lines.append(f"  - turns/max_turns at kill: min={min(ratios):.3f} "
                      f"max={max(ratios):.3f} (i.e. the CLI ran "
                      f"{(min(ratios)-1)*100:.0f}-{(max(ratios)-1)*100:.0f}% past its own "
                      f"ceiling before fworker's backstop, not double as the old code "
                      f"comment guessed)")
    ratios2 = [a["turns"] / a["max_turns"] for a in turns_attempts]
    if ratios2:
        lines.append(f"  - For comparison, TURNS-classified (clean self-stop) turns/max_turns: "
                      f"min={min(ratios2):.3f} max={max(ratios2):.3f} — the CLI's own turn "
                      f"counter can overshoot the requested ceiling by up to "
                      f"{(max(ratios2)-1)*100:.0f}% even when it DOES self-report.")
    lines.append("")
    lines.append("Per-archetype SUCCESS turns_used (sorted, model in parens) — this is the "
                  "population any new ceiling must not truncate:")
    by_arch_succ = defaultdict(list)
    for a in attempts:
        if a["classification"] == "SUCCESS":
            by_arch_succ[a["archetype"]].append((a["turns"], a["model"]))
    for arch, vals in sorted(by_arch_succ.items()):
        vals.sort()
        rendered = ", ".join(f"{t}({m.replace('claude-','')})" for t, m in vals)
        lines.append(f"- {arch} (n={len(vals)}): {rendered}")
    lines.append("")
    return "\n".join(lines)


def section_false_complete(attempts, tickets):
    lines = ["## 3. What FALSE_COMPLETE runs have in common\n"]
    fc_tickets = sorted(set(a["ticket"] for a in attempts if a["classification"] == "FALSE_COMPLETE"))
    bt = by_ticket(attempts)
    final_cls = {t: alist[-1]["classification"] for t, alist in bt.items()}

    detail_lens = defaultdict(list)
    for t in bt:
        r = tickets.get(t)
        if r:
            detail_lens[final_cls[t]].append(len(r["detail"] or ""))

    lines.append("Ticket detail length by FINAL classification (proxy for spec vagueness):")
    lines.append("| final classification | n tickets | mean detail chars | median |")
    lines.append("|---|---|---|---|")
    for cls, lens in sorted(detail_lens.items(), key=lambda kv: -len(kv[1])):
        lines.append(f"| {cls} | {len(lens)} | {st.mean(lens):.0f} | {st.median(lens):.0f} |")
    lines.append("")
    fc_lens = detail_lens.get("FALSE_COMPLETE") or []
    other_means = {c: st.mean(v) for c, v in detail_lens.items()
                   if c != "FALSE_COMPLETE" and v}
    if not fc_lens or not other_means:
        verdict = ("Too few final-FALSE_COMPLETE tickets to compare spec length against the "
                   "other buckets.")
    elif st.mean(fc_lens) < min(other_means.values()):
        verdict = ("That final-FALSE_COMPLETE bucket had the shortest tickets on average of "
                   "any final-classification bucket — vague/short specs correlate with the "
                   "worker declaring victory without doing the work.")
    else:
        shortest = min(other_means, key=other_means.get)
        verdict = (f"That final-FALSE_COMPLETE bucket averaged {st.mean(fc_lens):.0f} detail "
                   f"chars, which is NOT the shortest bucket ({shortest} is, at "
                   f"{other_means[shortest]:.0f}) — the short-spec correlation does not hold "
                   f"in the current dataset.")
    lines.append(f"{len(fc_tickets)} distinct tickets hit FALSE_COMPLETE on at least one "
                  f"attempt; {len(fc_lens)} of them still carried that as their FINAL "
                  f"classification (others recovered on a later attempt — see §4). {verdict}")
    lines.append("")

    gates = {t: (tickets[t]["gate"] or "").strip() for t in bt if t in tickets}
    populated = [t for t, g in gates.items() if g]
    empty = [t for t, g in gates.items() if not g]
    if not gates:
        lines.append("gate field: no ticket in this dataset resolved against the wq database, "
                     "so gate coverage cannot be measured.")
    elif not populated:
        lines.append(f"gate field: empty string for 100% of the {len(gates)} tickets in this "
                     f"dataset — not populated at all yet, so it cannot explain "
                     f"FALSE_COMPLETE (nor anything else) in this sample; wq's gate mechanism "
                     f"is unused, not protective.")
    else:
        fc_pop = sum(1 for t in populated if final_cls[t] == "FALSE_COMPLETE")
        fc_emp = sum(1 for t in empty if final_cls[t] == "FALSE_COMPLETE")
        caveat = ("the gate mechanism now has enough coverage to test whether it is protective"
                  if len(populated) >= 5 else
                  "still too few gated tickets to call the gate protective or not")
        lines.append(f"gate field: populated on {_rate(len(populated), len(gates))} of the "
                     f"tickets in this dataset, empty on {len(empty)}. Final-FALSE_COMPLETE "
                     f"rate is {_rate(fc_pop, len(populated))} with a gate vs "
                     f"{_rate(fc_emp, len(empty))} without — {caveat}.")
    lines.append("")
    lane_all = Counter(tickets[t]["lane"] for t in bt if t in tickets)
    lane_fc_final = Counter(tickets[t]["lane"] for t in bt if t in tickets and final_cls[t] == "FALSE_COMPLETE")
    lines.append("Lane distribution, all tickets vs tickets whose FINAL attempt was FALSE_COMPLETE:")
    lines.append("| lane | all tickets | FALSE_COMPLETE (final) | rate |")
    lines.append("|---|---|---|---|")
    for lane, n in sorted(lane_all.items(), key=lambda kv: -kv[1]):
        fc = lane_fc_final.get(lane, 0)
        lines.append(f"| {lane} | {n} | {fc} | {fc/n:.0%} |")
    lines.append("")
    fleet_n = sum(lane_all.values())
    fleet_fc = sum(lane_fc_final.values())
    base = fleet_fc / fleet_n if fleet_n else 0
    elevated = sorted(((lane, n, lane_fc_final.get(lane, 0)) for lane, n in lane_all.items()
                       if lane_fc_final.get(lane, 0) / n > base),
                      key=lambda x: -x[2] / x[1])
    if not elevated:
        lines.append(f"No lane sits above the fleet-wide final-FALSE_COMPLETE rate of "
                     f"{_rate(fleet_fc, fleet_n)}.")
    else:
        thin = [lane for lane, n, _ in elevated if n < 10]
        caveat = (f" — but {', '.join('`' + l + '`' for l in thin)} carr"
                  f"{'y' if len(thin) > 1 else 'ies'} fewer than 10 tickets, so "
                  f"{'those are' if len(thin) > 1 else 'that is'} suggestive, not conclusive."
                  if thin else ".")
        lines.append(f"Lanes above the fleet-wide final-FALSE_COMPLETE rate of "
                     f"{_rate(fleet_fc, fleet_n)}: "
                     + "; ".join(f"`{lane}` {_rate(fc, n)}" for lane, n, fc in elevated)
                     + caveat)
    lines.append("")

    ktf = killed_then_false_complete(attempts)
    if not ktf.ever_fc:
        lines.append("No ticket in this dataset ever hit FALSE_COMPLETE, so there is no "
                     "KILLED-then-FALSE_COMPLETE pattern to characterise.")
    else:
        reading = (" — consistent with a worker that ran out of turns, got killed mid-task, "
                   "and on the next attempt reported done against partial state rather than "
                   "re-verifying from scratch."
                   if ktf.killed_first * 2 >= ktf.ever_fc else
                   " — a minority pattern here, so turn exhaustion is not the main "
                   "FALSE_COMPLETE driver in the current dataset.")
        lines.append(f"KILLED-then-FALSE_COMPLETE: of the {ktf.ever_fc} tickets that ever hit "
                     f"FALSE_COMPLETE, {_rate(ktf.killed_first, ktf.ever_fc)} had a KILLED "
                     f"attempt first, and {ktf.same_ceiling} of those "
                     f"{'was' if ktf.same_ceiling == 1 else 'were'} killed at the SAME "
                     f"max_turns the FALSE_COMPLETE attempt later ran under{reading}")
    lines.append("")
    return "\n".join(lines)


def section_retries(attempts):
    lines = ["## 4. Does attempt 2 ever succeed after attempt 1 fails?\n"]
    bt = by_ticket(attempts)
    trans = Counter()
    for alist in bt.values():
        for cur, nxt in zip(alist, alist[1:]):
            if cur["classification"] != "SUCCESS":
                trans[(cur["classification"], nxt["classification"])] += 1
    pairs = first_retry_pairs(attempts)

    lines.append("Attempt N -> attempt N+1 classification transitions, all N (fails only):")
    lines.append("| prev | next | count |")
    lines.append("|---|---|---|")
    for (a, b), n in sorted(trans.items(), key=lambda kv: -kv[1]):
        lines.append(f"| {a} | {b} | {n} |")
    lines.append("")

    same_ceiling = sum(1 for c, n in pairs if c["max_turns"] == n["max_turns"])
    succ_on_retry = sum(1 for c, n in pairs if n["classification"] == "SUCCESS")
    same_class = sum(1 for c, n in pairs if c["classification"] == n["classification"])

    lines.append(f"Within-dispatch retry (attempt 1 -> attempt 2, the actual MAX_ATTEMPTS=2 "
                  f"mechanism): {len(pairs)} pairs.")
    lines.append(f"- {_rate(same_ceiling, len(pairs))} reused the IDENTICAL max_turns "
                  f"ceiling on attempt 2 — there is no auto-escalation today.")
    lines.append(f"- {_rate(same_class, len(pairs))} repeated the EXACT SAME failure "
                  f"classification on attempt 2.")
    lines.append(f"- {_rate(succ_on_retry, len(pairs))} succeeded on attempt 2 with the SAME "
                  f"ceiling as the failed attempt 1.")
    lines.append("")

    ultimate_success = [t for t, alist in bt.items()
                        if any(a["classification"] == "SUCCESS" for a in alist)]
    won_at = {t: next(i for i, a in enumerate(bt[t]) if a["classification"] == "SUCCESS")
              for t in ultimate_success}
    first_attempt_success = sum(1 for t in ultimate_success if won_at[t] == 0)
    later = [t for t in ultimate_success if won_at[t] > 0]
    escalated = sum(1 for t in later
                    if bt[t][won_at[t]]["max_turns"] != bt[t][won_at[t] - 1]["max_turns"])
    tail = (f" — {_rate(escalated, len(later))} of those later successes ran under a DIFFERENT "
            f"max_turns than the attempt immediately before them, so a ceiling change, not the "
            f"repeat itself, is what the recoveries have in common." if later else ".")
    lines.append(f"Across ALL attempts on a ticket (including cross-cycle re-dispatch after "
                  f"config changes, attempts 3/4+): {_rate(len(ultimate_success), len(bt))} "
                  f"tickets ever succeeded. {first_attempt_success} on attempt 1, "
                  f"{len(later)} needed a later attempt{tail}")
    lines.append("")
    lever = ("raising max_turns before the retry, not retrying per se"
             if escalated * 2 >= len(later) and later else
             "not identifiable from ceiling changes alone in the current dataset")
    lines.append(f"**Conclusion**: MAX_ATTEMPTS=2 under an unchanged ceiling recovered "
                 f"{_rate(succ_on_retry, len(pairs))} of retries — the real recovery lever "
                 f"measured in this data is {lever}.")
    lines.append("")
    return "\n".join(lines)


def section_cost(attempts):
    lines = ["## 5. Cost per SUCCESSFUL ticket by model\n"]
    n_oauth = sum(1 for a in attempts if a.get("oauth_token_used"))
    if n_oauth == len(attempts):
        lines.append("Every attempt in this dataset has `oauth_token_used: true` — these run on "
                      "the Max subscription, not per-token API billing (audit R2). There is no "
                      "marginal dollar cost to compare, so the honest cost proxy is token volume "
                      "and wall-clock against the shared weekly quota (audit F4), not USD.")
    else:
        metered = [a for a in attempts if not a.get("oauth_token_used")]
        billed = sum(a.get("total_cost_usd") or 0 for a in metered)
        lines.append(f"{_rate(n_oauth, len(attempts))} of attempts have `oauth_token_used: "
                      f"true` — those run on the Max subscription, not per-token API billing "
                      f"(audit R2), and carry no marginal dollar cost. The remaining "
                      f"{len(metered)} were metered and booked ${billed:,.2f}. Token volume and "
                      f"wall-clock against the shared weekly quota (audit F4) remain the only "
                      f"proxy comparable across both.")
    lines.append("")
    succ = [a for a in attempts if a["classification"] == "SUCCESS"]
    by_model_tokens = defaultdict(list)
    by_model_elapsed = defaultdict(list)
    missing = 0
    for a in succ:
        by_model_elapsed[a["model"]].append(a["elapsed_s"])
        path = find_transcript(a["session_id"])
        if not path:
            missing += 1
            continue
        tot = sum_token_usage(path)
        by_model_tokens[a["model"]].append(tot)

    lines.append("| model | n success | mean elapsed_s | mean output tokens | mean cache_read tokens | mean cache_write tokens |")
    lines.append("|---|---|---|---|---|---|")
    for model in by_model_elapsed:
        toks = by_model_tokens.get(model, [])
        mean_out = st.mean(t["output"] for t in toks) if toks else float("nan")
        mean_cr = st.mean(t["cache_read"] for t in toks) if toks else float("nan")
        mean_cw = st.mean(t["cache_write"] for t in toks) if toks else float("nan")
        lines.append(f"| {model} | {len(by_model_elapsed[model])} | "
                      f"{st.mean(by_model_elapsed[model]):.0f} | {mean_out:,.0f} | "
                      f"{mean_cr:,.0f} | {mean_cw:,.0f} |")
    lines.append("")
    if missing:
        lines.append(f"({missing} successful attempt(s) had no matching transcript on disk — "
                      "excluded from the token averages, included in elapsed_s.)")
        lines.append("")

    total_elapsed = sum(a["elapsed_s"] for a in attempts)
    succ_elapsed = sum(a["elapsed_s"] for a in attempts if a["classification"] == "SUCCESS")
    lines.append(f"Fleet-wide: {len(attempts)} attempts consumed {total_elapsed/3600:.1f} "
                 f"worker-hours; only {succ_elapsed/3600:.1f}h ({succ_elapsed/total_elapsed:.0%}) "
                 f"produced a SUCCESS. {100-succ_elapsed/total_elapsed*100:.0f}% of measured "
                 f"worker time bought nothing shippable.")
    lines.append("")
    return "\n".join(lines)


def section_recommendation(attempts):
    lines = ["## 6. Recommended ARCHETYPE_MAX_TURNS and MAX_ATTEMPTS\n"]
    in_force = {}
    for a in sorted(attempts, key=lambda x: (x.get("finished_at") or 0, x["attempt"])):
        in_force[a["archetype"]] = a["max_turns"]
    ceilings = sorted(set(in_force.values()))
    ceil_desc = (f"{ceilings[0]} for every archetype" if len(ceilings) == 1 else
                 ", ".join(f"`{arch}`={mt}" for arch, mt in sorted(in_force.items())))
    newest = max((a.get("finished_at") or 0) for a in attempts)
    as_of = (dt.datetime.fromtimestamp(newest, dt.timezone.utc).strftime("%Y-%m-%d")
             if newest else "unknown date")
    lines.append(f"Ceilings in force as of the most recent attempt in this dataset ({as_of}): "
                 f"{ceil_desc}. These were lifted wholesale as a stopgap (`fix(harness): lift "
                 f"turn ceilings until real fix`) pending the mechanics audit that will count "
                 f"real assistant turns instead of hook frames. The numbers below are what the "
                 f"ceiling SHOULD converge to once that accounting fix lands — sized off "
                 f"measured SUCCESS turns_used with headroom for the turn-count drift measured "
                 f"in §2, not off the current placeholder.")
    lines.append("")
    by_arch_succ = defaultdict(list)
    by_arch_all = Counter()
    by_arch_cls = defaultdict(Counter)
    by_arch_ceilings = defaultdict(set)
    for a in attempts:
        by_arch_all[a["archetype"]] += 1
        by_arch_cls[a["archetype"]][a["classification"]] += 1
        by_arch_ceilings[a["archetype"]].add(a["max_turns"])
        if a["classification"] == "SUCCESS":
            by_arch_succ[a["archetype"]].append(a["turns"])

    lines.append("| archetype | n success | max observed success turns | recommended ceiling | confidence |")
    lines.append("|---|---|---|---|---|")
    recs = {}
    for arch, turns in sorted(by_arch_succ.items()):
        n = len(turns)
        m = max(turns)
        rec = round((m * TURN_DRIFT_MARGIN) / CEILING_ROUNDING) * CEILING_ROUNDING
        conf = "low (n<5)" if n < 5 else "medium"
        recs[arch] = rec
        lines.append(f"| {arch} | {n} | {m} | {rec} | {conf} |")
    for arch, total in sorted(by_arch_all.items()):
        if arch not in by_arch_succ:
            tried = max(by_arch_ceilings[arch])
            lines.append(f"| {arch} | 0 | n/a | keep >={tried}, re-measure after first success | "
                          f"NONE — {total} attempt{'s' if total != 1 else ''}, zero successes |")
    lines.append("")
    zero_succ = sorted(arch for arch in by_arch_all if arch not in by_arch_succ)
    if zero_succ:
        substantive = [a for a in zero_succ if by_arch_all[a] >= 5]
        thin = [a for a in zero_succ if by_arch_all[a] < 5]
        render = lambda group: ", ".join(
            f"`{a}` ({by_arch_all[a]} attempt{'s' if by_arch_all[a] != 1 else ''})"
            for a in group)
        if substantive:
            lines.append(f"{render(substantive)} — zero measured successes over enough attempts "
                         f"to mean something. That is a bigger problem than any ceiling number, "
                         f"and should block on its own investigation rather than a turns bump.")
        if thin:
            lines.append(f"{render(thin)} — zero measured successes, but on too few attempts to "
                         f"distinguish a broken archetype from an unlucky sample. Re-measure "
                         f"before acting on the ceiling above.")
    else:
        lines.append("Every archetype in this dataset has at least one measured SUCCESS, so no "
                     "archetype is blocked on an investigation ahead of a ceiling decision.")
    lines.append("")
    pairs = first_retry_pairs(attempts)
    same_ceiling = sum(1 for c, n in pairs if c["max_turns"] == n["max_turns"])
    succ_on_retry = sum(1 for c, n in pairs if n["classification"] == "SUCCESS")
    lines.append(f"**MAX_ATTEMPTS**: keep at 2, but make attempt 2's max_turns escalate over "
                 f"attempt 1's when attempt 1 failed via TURNS or KILLED/turns_exceeded "
                 f"(e.g. x1.5) instead of re-dispatching under the identical ceiling that just "
                 f"failed. §4 measured {_rate(same_ceiling, len(pairs))} of attempt-1->2 "
                 f"retries reusing the identical ceiling and only "
                 f"{_rate(succ_on_retry, len(pairs))} recovering under it.")
    lines.append("")
    lines.append("### Falsifiable predictions\n")
    preds = []
    if by_arch_all:
        top = max(by_arch_all, key=lambda k: by_arch_all[k])
        ktf = killed_then_false_complete(attempts)
        measured = _rate(ktf.killed_first, ktf.ever_fc)
        mechanism = (
            f"because fewer runs will be forcibly killed mid-task and then misreport partial "
            f"state as done on the next attempt — §3 measured that pattern on {measured} of "
            f"FALSE_COMPLETE tickets"
            if ktf.killed_first * 2 >= ktf.ever_fc else
            f"but only weakly: §3 measured the KILLED-then-FALSE_COMPLETE mechanism on just "
            f"{measured} of FALSE_COMPLETE tickets, so ceilings can only reach that minority "
            f"and the drop should be small")
        preds.append(
            f"If ARCHETYPE_MAX_TURNS is set per the table above (once real assistant-turn "
            f"counting lands), FALSE_COMPLETE rate for `{top}` — the highest-volume archetype "
            f"here — will drop below its current "
            f"{_rate(by_arch_cls[top]['FALSE_COMPLETE'], by_arch_all[top])} attempt-level rate, "
            f"{mechanism}. Falsified if that rate is unchanged or higher after 20 more `{top}` "
            f"attempts at the new ceilings.")
    if pairs:
        base = succ_on_retry / len(pairs)
        preds.append(
            f"If attempt 2 escalates max_turns by >=1.5x on a TURNS/KILLED attempt 1 instead of "
            f"repeating it, the attempt-2 success rate will exceed the measured "
            f"{_rate(succ_on_retry, len(pairs))} same-ceiling baseline. Falsified if the next 20 "
            f"escalated retries succeed at <={base:.0%}.")
    for arch in zero_succ:
        seen = sorted(by_arch_ceilings[arch])
        n = by_arch_all[arch]
        plural = "s" if n != 1 else ""
        if len(seen) > 1:
            preds.append(
                f"`{arch}`'s 0/{n} success rate is NOT a turns problem ({n} attempt{plural} "
                f"spanned max_turns {seen[0]}-{seen[-1]} with no successes at any ceiling) — "
                f"raising its ceiling alone will not move its success rate. Falsified if a "
                f"`{arch}` ticket succeeds at a ceiling <={seen[-1]} with no other change.")
        else:
            preds.append(
                f"`{arch}` has 0/{n} successes but every attempt ran at the single ceiling "
                f"max_turns={seen[0]}, so this dataset cannot yet separate a turns problem from "
                f"any other cause. Dispatch it at a higher ceiling before drawing a conclusion; "
                f"the zero-success reading is falsified the moment a `{arch}` ticket succeeds at "
                f"{seen[0]} unchanged.")
    if not zero_succ:
        preds.append(
            "Every archetype here has at least one measured success, so this dataset offers no "
            "zero-success archetype to predict about; re-check on the next rerun.")
    for i, p in enumerate(preds, 1):
        lines.append(f"{i}. {p}")
    lines.append("")
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs-glob", default=DEFAULT_RUNS_GLOB)
    ap.add_argument("--db", default=DEFAULT_DB)
    args = ap.parse_args()

    attempts, unreadable = load_attempts(args.runs_glob)
    ticket_ids = sorted(set(a["ticket"] for a in attempts))
    tickets = load_tickets(args.db, ticket_ids)

    skipped = (f" {len(unreadable)} attempt file(s) were unreadable and are excluded from "
               f"every count below." if unreadable else "")
    print(f"# fworker success rate vs max_turns — measured dataset\n")
    print(f"Generated by `{_self_path()}` from {len(attempts)} attempt "
          f"records across {len(ticket_ids)} tickets in {args.runs_glob}, joined against "
          f"{args.db}. Re-run any time; every number and every sentence below is recomputed "
          f"from the data on each run (wq#839).{skipped}\n")
    print(section_success_rates(attempts))
    print(section_turns_distribution(attempts))
    print(section_false_complete(attempts, tickets))
    print(section_retries(attempts))
    print(section_cost(attempts))
    print(section_recommendation(attempts))


if __name__ == "__main__":
    main()
