#!/usr/bin/env python3
"""wq#737: cheap re-check of whether wq#736's usage sidecars have naturally
broken the cache-read/weighting collinearity that blocked SESSION_ECONOMICS.md
claim 1.9 (research/SESSION_ECONOMICS.md §6, §7, §10).

Reads every RUNS_ROOT/wq{id}/usage-*.json sidecar, reports the aggregate
cache-read fraction of raw tokens and how many seven_day.used_percentage
points moved across the observed span. Zero new Anthropic spend.

Re-run before spending wq#737's two-session budget:
    python3 sidecar_collinearity_check.py

Trigger to actually run the controlled probe (see SESSION_ECONOMICS.md §10):
  (a) a meaningful slice of sessions drops below ~90% cache-read fraction
      (collinearity already broken for free, probe unneeded), or
  (b) span_hours >= 168 (one week) and cache-read fraction is still 90%+
      (free path exhausted as designed) -- and only once the dispatch lands
      inside an active off-peak/quiesced window (see floors.json; as of
      2026-08-23 that window's enforcement is disabled per Frank -- check
      governor/floors.json's off_peak_days_pt and state.json's stop_active
      before relying on it again).
"""
import glob
import json
import os
import statistics

RUNS_ROOT = os.environ.get("FOREMAN_RUNS_ROOT", "/home/lumen/foreman/_runs")


def load_records():
    recs = []
    for f in glob.glob(os.path.join(RUNS_ROOT, "*", "usage-*.json")):
        try:
            d = json.load(open(f))
        except (OSError, json.JSONDecodeError):
            continue
        rs = d.get("rate_limits_start") or {}
        rex = d.get("rate_limits_exit") or {}
        sd, ed = rs.get("seven_day") or {}, rex.get("seven_day") or {}
        if sd.get("used_percentage") is None or ed.get("used_percentage") is None:
            continue
        u = d.get("usage") or {}
        inp = u.get("input_tokens", 0)
        cw = u.get("cache_creation_input_tokens", 0)
        cr = u.get("cache_read_input_tokens", 0)
        out = u.get("output_tokens", 0)
        recs.append({
            "ticket": d.get("ticket"),
            "start_ts": rs.get("captured_at"),
            "exit_ts": rex.get("captured_at"),
            "sd_start": sd["used_percentage"],
            "sd_exit": ed["used_percentage"],
            "input": inp, "cache_write": cw, "cache_read": cr, "output": out,
            "raw": inp + cw + cr + out,
        })
    return recs


def main():
    recs = load_records()
    if not recs:
        print("no sidecar records with matched start/exit seven_day readings yet")
        return
    recs.sort(key=lambda r: r["start_ts"] or 0)
    span_hours = (max(r["exit_ts"] for r in recs) - recs[0]["start_ts"]) / 3600
    tot = {k: sum(r[k] for r in recs) for k in ("input", "cache_write", "cache_read", "output", "raw")}
    cr_frac = tot["cache_read"] / tot["raw"] if tot["raw"] else 0.0

    nonzero = [r for r in recs if r["raw"] > 0]
    fracs = sorted(r["cache_read"] / r["raw"] for r in nonzero)
    below_90 = sum(1 for f in fracs if f < 0.90)

    deltas = [r["sd_exit"] - r["sd_start"] for r in recs]

    print(f"sessions: {len(recs)}  span: {span_hours:.2f}h")
    print(f"raw tokens: {tot['raw']:,}  cache-read fraction (aggregate): {cr_frac:.4f}")
    if fracs:
        print(f"per-session cache-read fraction: min={fracs[0]:.4f} median={statistics.median(fracs):.4f} "
              f"max={fracs[-1]:.4f}  below-90%: {below_90}/{len(fracs)}")
    print(f"seven_day.used_percentage delta distribution: "
          f"{ {v: deltas.count(v) for v in sorted(set(deltas))} }")
    print()
    if span_hours < 168:
        print(f"NOT YET: {span_hours:.1f}h of {168}h (one week) elapsed -- free path still accumulating.")
    elif below_90 / max(1, len(fracs)) < 0.1:
        print("FREE PATH EXHAUSTED: a week has passed and the mix is still >=90% cache-read "
              "in nearly every session -- collinearity not broken for free. Controlled probe "
              "is the next step (confirm off-peak/quiescence mechanism is live first).")
    else:
        print("COLLINEARITY LIKELY BROKEN: enough low-cache-read sessions exist to attempt "
              "regressing the two coefficients directly from this data -- do that before "
              "spending the controlled-probe budget.")


if __name__ == "__main__":
    main()
