#!/usr/bin/env python3
"""
wq#763 A13 GAUGE — fleet flow metrics over queue.db.

Read-only against queue.db (opened with mode=ro; this script issues SELECT only).
Two output modes:
  --baseline   emit the frozen 2026-08-21 baseline JSON (deterministic, cutoff-bounded)
  --live       emit the current point-in-time snapshot (for the textfile exporter)

Campaign: /home/lumen/instructions/FLEET_OPS_AUDIT_2026-08-21.md
Baseline discipline mirrors wq#595's golden-set freeze: a dated, sha256-recorded artifact
that later tickets are measured against, not silently recomputed.
"""
import argparse
import json
import sqlite3
import statistics
import sys
from datetime import datetime, timezone

DB = "/home/lumen/agents/work-queue/queue.db"
# Earliest created_at among the ops-audit campaign tickets (wq#751) — the moment the
# campaign was seeded. Baseline population = everything that existed strictly before this.
CUTOFF = "2026-08-21 18:06:42"
FMT = "%Y-%m-%d %H:%M:%S"


def connect():
    return sqlite3.connect(f"file:{DB}?mode=ro", uri=True)


def parse(ts):
    if not ts:
        return None
    try:
        return datetime.strptime(ts.strip(), FMT)
    except ValueError:
        return None


def hours_between(a, b):
    da, db_ = parse(a), parse(b)
    if not da or not db_:
        return None
    return (db_ - da).total_seconds() / 3600.0


def pctile(vals, p):
    if not vals:
        return None
    s = sorted(vals)
    k = (len(s) - 1) * p
    f, c = int(k), min(int(k) + 1, len(s) - 1)
    if f == c:
        return s[f]
    return s[f] + (s[c] - s[f]) * (k - f)


def summarize(vals):
    vals = [v for v in vals if v is not None]
    if not vals:
        return {"n": 0}
    return {
        "n": len(vals),
        "mean_h": round(statistics.mean(vals), 2),
        "median_h": round(statistics.median(vals), 2),
        "p90_h": round(pctile(vals, 0.90), 2),
        "max_h": round(max(vals), 2),
    }


def iso_week(ts):
    d = parse(ts)
    if not d:
        return None
    y, w, _ = d.isocalendar()
    return f"{y}-W{w:02d}"


def lead_cycle_by_lane(cur, before):
    rows = cur.execute(
        "select lane, created_at, claimed_at, done_at from tasks "
        "where status='done' and done_at is not null and done_at < ?",
        (before,),
    ).fetchall()
    by_lane = {}
    for lane, created_at, claimed_at, done_at in rows:
        d = by_lane.setdefault(lane, {"lead": [], "cycle": []})
        d["lead"].append(hours_between(created_at, done_at))
        if claimed_at:
            d["cycle"].append(hours_between(claimed_at, done_at))
    return {
        lane: {
            "lead_time_hours": summarize(d["lead"]),
            "cycle_time_hours": summarize(d["cycle"]),
        }
        for lane, d in sorted(by_lane.items())
    }


def throughput_per_week(cur, before):
    rows = cur.execute(
        "select lane, done_at from tasks where status='done' and done_at is not null and done_at < ?",
        (before,),
    ).fetchall()
    overall, by_lane = {}, {}
    for lane, done_at in rows:
        wk = iso_week(done_at)
        if not wk:
            continue
        overall[wk] = overall.get(wk, 0) + 1
        lane_weeks = by_lane.setdefault(lane, {})
        lane_weeks[wk] = lane_weeks.get(wk, 0) + 1
    weeks = sorted(overall)
    return {
        "weekly_counts_overall": {w: overall[w] for w in weeks},
        "mean_per_week_overall": round(statistics.mean(overall.values()), 1) if overall else None,
        "weekly_counts_by_lane": {lane: dict(sorted(wc.items())) for lane, wc in sorted(by_lane.items())},
    }


def wip_at_cutoff(cur, cutoff):
    """In-flight count at cutoff: created before cutoff, not yet done (or done after cutoff).
    Cannot split by open/claimed/blocked sub-state historically — no status-transition log
    exists in queue.db, only current status. See report §2 for why."""
    (n,) = cur.execute(
        "select count(*) from tasks where created_at < ? and (done_at is null or done_at >= ?) and status != 'rejected'",
        (cutoff, cutoff),
    ).fetchone()
    return n


def rework_rate(cur, before):
    total = cur.execute(
        "select count(*) from tasks where created_at < ?", (before,)
    ).fetchone()[0]
    reopened = cur.execute(
        "select count(*) from tasks where created_at < ? and notes like '%REOPENED%'",
        (before,),
    ).fetchone()[0]
    rejected = cur.execute(
        "select count(*) from tasks where created_at < ? and status='rejected'",
        (before,),
    ).fetchone()[0]
    return {
        "population": total,
        "reopened_marker_in_notes": reopened,
        "rejected_status": rejected,
        "rework_rate_pct": round(100.0 * (reopened + rejected) / total, 2) if total else None,
    }


def live_snapshot(cur):
    now = datetime.now(timezone.utc).strftime(FMT)
    status_counts = dict(cur.execute("select status, count(*) from tasks group by status").fetchall())
    wip_by_lane = dict(
        cur.execute(
            "select lane, count(*) from tasks where status in ('open','claimed','blocked') group by lane"
        ).fetchall()
    )
    blocked_ages = []
    for (created_at,) in cur.execute("select created_at from tasks where status='blocked'"):
        h = hours_between(created_at, now)
        if h is not None:
            blocked_ages.append(h / 24.0)
    frank_rows = cur.execute(
        "select lane, notes, created_at from tasks where status in ('open','blocked') "
        "and (lane='user' or notes like '%rank%' or notes like '%gate%' or notes like '%approval%')"
    ).fetchall()
    frank_ages = []
    for lane, notes, created_at in frank_rows:
        h = hours_between(created_at, now)
        if h is not None:
            frank_ages.append(h / 24.0)
    reopened_live = cur.execute("select count(*) from tasks where notes like '%REOPENED%'").fetchone()[0]
    rejected_live = cur.execute("select count(*) from tasks where status='rejected'").fetchone()[0]
    total_live = cur.execute("select count(*) from tasks").fetchone()[0]
    return {
        "captured_at_utc": now,
        "status_counts": status_counts,
        "wip_total": sum(v for k, v in status_counts.items() if k in ("open", "claimed", "blocked")),
        "wip_by_lane": wip_by_lane,
        "blocked_age_days": summarize_days(blocked_ages),
        "frank_parked_count": len(frank_rows),
        "frank_parked_age_days": summarize_days(frank_ages),
        "rework_rate_pct": round(100.0 * (reopened_live + rejected_live) / total_live, 2) if total_live else None,
    }


def summarize_days(vals):
    if not vals:
        return {"n": 0}
    return {
        "n": len(vals),
        "mean_d": round(statistics.mean(vals), 1),
        "median_d": round(statistics.median(vals), 1),
        "max_d": round(max(vals), 1),
    }


def build_baseline():
    con = connect()
    cur = con.cursor()
    out = {
        "artifact": "opsaudit_a13_baseline_20260821",
        "campaign_doc": "/home/lumen/instructions/FLEET_OPS_AUDIT_2026-08-21.md",
        "cutoff_utc": CUTOFF,
        "cutoff_rationale": (
            "earliest created_at among the ops-audit campaign tickets (wq#751); "
            "baseline population is every task row that existed strictly before the "
            "campaign was seeded, so the campaign's own output cannot contaminate its "
            "own baseline"
        ),
        "lead_cycle_time_by_lane": lead_cycle_by_lane(cur, CUTOFF),
        "throughput": throughput_per_week(cur, CUTOFF),
        "wip_in_flight_at_cutoff": {
            "count": wip_at_cutoff(cur, CUTOFF),
            "caveat": (
                "total in-flight count only (created<cutoff, not done by cutoff). "
                "queue.db has no status-transition log, so open/claimed/blocked "
                "sub-state AT cutoff cannot be reconstructed from current rows alone — "
                "only CURRENT status is stored. See report for what this blocks."
            ),
        },
        "rework_rate": rework_rate(cur, CUTOFF),
        "blocked_age_distribution": {
            "not_reconstructable_at_cutoff": True,
            "reason": "no blocked_at column / no status-transition log in queue.db",
            "substitute": (
                "campaign doc F1 (measured 2026-08-21, same day as this cutoff): "
                "52 blocked tickets fleet-wide, 23 open+blocked in lane=user, "
                "avg age of open user ticket 23 days, oldest blocked 41 days, "
                "wq#383 33 days FRANK DECISION PENDING as of audit time"
            ),
        },
        "time_parked_on_frank": {
            "source": "campaign doc F1, measured 2026-08-21 (this cutoff's day)",
            "items_parked": 74,
            "blocked_naming_frank_gate_approval": 23,
            "open_blocked_in_user_lane": 23,
            "avg_age_open_user_ticket_days": 23,
            "oldest_blocked_days": 41,
            "wq383_days_pending_at_audit": 33,
        },
        "worker_success_rate": {
            "source": "reports/opsaudit_a1_worker_success.md (wq#751, same campaign, same day)",
            "attempts": 149,
            "tickets": 69,
            "attempt_level_success_rate_pct": 22,
            "killed_by_turn_backstop_pct": 44,
            "same_ceiling_retry_recovery_pct": 4.8,
            "worker_hours_total": 11.3,
            "worker_hours_on_success": 2.5,
        },
        "cost_per_successful_ticket": {
            "basis": "Max subscription (oauth_token_used=true for 100% of A1's sample) — "
            "no marginal per-token dollar cost, so cost is worker-time against the shared "
            "weekly quota, not USD (audit R2)",
            "worker_hours_per_success": round(11.3 / 14, 2),
            "successful_tickets_in_a1_sample": 14,
            "mean_output_tokens_by_model": {
                "claude-sonnet-5": 56825,
                "claude-opus-5": 186644,
                "claude-fable-5": 142454,
            },
        },
    }
    con.close()
    return out


def build_live():
    con = connect()
    cur = con.cursor()
    out = live_snapshot(cur)
    con.close()
    return out


def to_prom(snap):
    ts = parse(snap["captured_at_utc"]).replace(tzinfo=timezone.utc)
    lines = []

    def gauge(name, help_text):
        lines.append(f"# HELP {name} {help_text}")
        lines.append(f"# TYPE {name} gauge")

    gauge("lumen_queue_flow_captured_at_seconds", "unix time this snapshot was captured (wq#763)")
    lines.append(f"lumen_queue_flow_captured_at_seconds {ts.timestamp():.0f}")

    gauge("lumen_queue_status_total", "current task count by status (wq#763)")
    for status, n in snap["status_counts"].items():
        lines.append(f'lumen_queue_status_total{{status="{status}"}} {n}')

    gauge("lumen_queue_wip_total", "current WIP (open+claimed+blocked) by lane (wq#763)")
    for lane, n in snap["wip_by_lane"].items():
        lines.append(f'lumen_queue_wip_total{{lane="{lane}"}} {n}')

    gauge("lumen_queue_blocked_age_days", "age in days of currently-blocked tickets (wq#763)")
    for stat, v in snap["blocked_age_days"].items():
        if stat == "n":
            continue
        lines.append(f'lumen_queue_blocked_age_days{{stat="{stat}"}} {v}')

    gauge("lumen_queue_frank_parked_total", "tickets currently parked on Frank (wq#763, campaign F1 definition)")
    lines.append(f"lumen_queue_frank_parked_total {snap['frank_parked_count']}")

    gauge("lumen_queue_frank_parked_age_days", "age in days of tickets parked on Frank (wq#763)")
    for stat, v in snap["frank_parked_age_days"].items():
        if stat == "n":
            continue
        lines.append(f'lumen_queue_frank_parked_age_days{{stat="{stat}"}} {v}')

    if snap["rework_rate_pct"] is not None:
        gauge("lumen_queue_rework_rate_pct", "pct of all-time tickets reopened or rejected (wq#763)")
        lines.append(f"lumen_queue_rework_rate_pct {snap['rework_rate_pct']}")

    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--baseline", action="store_true")
    g.add_argument("--live", action="store_true")
    g.add_argument("--prom", action="store_true", help="live snapshot as Prometheus text exposition format")
    args = ap.parse_args()
    if args.baseline:
        json.dump(build_baseline(), sys.stdout, indent=2, sort_keys=False)
        print()
    elif args.live:
        json.dump(build_live(), sys.stdout, indent=2, sort_keys=False)
        print()
    else:
        sys.stdout.write(to_prom(build_live()))
