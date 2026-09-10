#!/usr/bin/env python3
"""wq#1654 — record the keep/disable decision for each stale-labelled timer
into the minerva `state_facts` store (lumen_state), via the wq#1632 gated
ingest path (state_lifecycle.ingest).

Decision (2026-09-10, full autonomy — no Frank-gate; see DECISIONS.md):
KEEP all nine. Each is doing real, non-destructive, already-verified-safe
work (row ingests, audits, benches, or is the live dispatch broker itself);
disabling any would either lose data or break production dispatch. The only
defect is the Description=; that requires the fleet-units baseline (outside
this worker's sandbox — queued as wq follow-up, not a Frank-gate).

Usage: MINERVA_DB=/home/lumen/minerva/minerva.db python3 ingest_decisions.py
"""
import os
import sys

sys.path.insert(
    0,
    os.path.join(os.path.dirname(os.path.abspath(__file__)),
                 "..", "wq1632-lumen-state-lifecycle"),
)
import state_lifecycle as sl  # noqa: E402

PREDICATE = "timer_disposition"
NEW_PREDICATE_REASON = (
    "wq#1654 — record keep/disable call on timers with a stale enable-state "
    "label; no existing predicate covers a unit-disposition decision"
)

# (unit, decision, corrected_description, rationale)
DECISIONS = [
    ("lumen-familiarity.timer", "keep_fix_label",
     "Entity-familiarity ingest every 6h",
     "live, ingesting rows (42 rows 01:21Z on capture day) — no harm, real value"),
    ("lumen-model-audit.timer", "keep_fix_label",
     "External-model correctness audit every 12h",
     "live, on run 64 — audit is the point of the unit"),
    ("lumen-skillhex.timer", "keep_fix_label",
     "SkillHEX hypothesis cycle 3x/week",
     "live, ran #15 on schedule — hypothesis cycle is read/propose, not destructive"),
    ("lumen-memory-audit.timer", "keep_fix_label",
     "Weekly embedding integrity audit, quiet window",
     "live, read-only pgvector drift audit"),
    ("lumen-judgearena-bench.timer", "keep_fix_label",
     "Weekly judge-agreement bench",
     "live, weekly bench — no side effects outside its own report"),
    ("lumen-pelican-bench.timer", "keep_fix_label",
     "Weekly pelican probe battery",
     "live, weekly bench — no side effects outside its own report"),
    ("lumen-foreman-dispatch.timer", "keep_fix_label",
     "20-min cadence for lumen-foreman-dispatch (wq#715)",
     "this IS the live dispatch broker; disabling breaks the wq pipeline"),
    ("lumen-foreman-health.timer", "keep_fix_label",
     "5-min cadence for lumen-foreman-health (wq#825)",
     "health check for the broker above; must stay live with it"),
    ("lumen-foreman-selfheal.timer", "keep_fix_label",
     "10-min cadence for lumen-foreman-selfheal (wq#725)",
     "releases INFRA_CRASH blocks; broker-adjacent, must stay live with it"),
]


def ingest_all(conn, registry, source_ref="wq1654:decision"):
    results = []
    for unit, decision, corrected_description, rationale in DECISIONS:
        res = sl.ingest(
            conn, registry, subject=unit, predicate=PREDICATE, value=decision,
            source_ref=source_ref, confidence=0.9, fact_class="config",
            new_predicate_reason=NEW_PREDICATE_REASON,
        )
        results.append({"unit": unit, "decision": decision, **res})
    return results


def main():
    conn = sl.connect(sl.MINERVA_DB)
    registry = sl.Registry(sl.REGISTRY_PATH)
    for row in ingest_all(conn, registry):
        print("%-32s %-16s %s (id=%s)" % (
            row["unit"], row["decision"], row["action"], row["id"]))
    conn.commit()
    return 0


if __name__ == "__main__":
    sys.exit(main())
