#!/usr/bin/env python3
"""drift_plan.py — declared per-rung observation policy for LiteLLM rungs.
(Minerva proposal 9f2866bcda22, factory plan ff23cf7bdebb, wq#2434.)

Gap this fills: lumen-model-watchdog (wq#90) checks LIVENESS, model_audit.py
(wq#449) checks known-answer CORRECTNESS, Langfuse traces every call and
Grafana renders it. What none of them record is the *policy*: how often a rung
should be sampled, over what baseline, against how many samples, who reviews
it, and what it falls back to. Those live implicitly in code today. This emits
them as reviewable JSON artifacts, one per rung, so a human can calibrate them.

Plans are born DRAFT_REQUIRES_CALIBRATION with every tunable null. Nothing here
alerts, gates, or routes: a plan becomes CALIBRATED only once a human has set
reviewer, rollback_target and the five sizing fields, and even then it is still
only an artifact. Wiring thresholds into alerting is a separate, later decision
(proposal step 4).

Regeneration is MERGE, not overwrite: human-set fields on an existing plan are
preserved, so the weekly timer never eats a reviewer's calibration.

Metrics carry their real telemetry source. A metric with no source on this fleet
(unauthorized_tool_effects — nothing observes tool side effects) is emitted with
threshold null and no_telemetry_source true rather than given a fabricated
number, and a threshold override on it is refused.

Inputs : /home/lumen/agent/litellm_config.yaml   (rung list)
         /home/lumen/minerva/drift_plan_overrides.json  (human calibration, optional)
Outputs: /home/lumen/minerva/drift_plans/<rung>.json
         /home/lumen/minerva/drift_plans/index.json     (read-only dashboard feed)
Timer  : lumen-drift-plan.{service,timer}, weekly.
"""
import argparse, datetime, json, os, re, sys, tempfile

import yaml

CONFIG = os.environ.get("DRIFT_PLAN_CONFIG", "/home/lumen/agent/litellm_config.yaml")
OUT_DIR = os.environ.get("DRIFT_PLAN_OUT", "/home/lumen/minerva/drift_plans")
OVERRIDES = os.environ.get("DRIFT_PLAN_OVERRIDES",
                           "/home/lumen/minerva/drift_plan_overrides.json")

SCHEMA_VERSION = 1
DRAFT = "DRAFT_REQUIRES_CALIBRATION"
CALIBRATED = "CALIBRATED"

SYSTEM_TYPES = ("chat", "rag", "tool-agent")

# A rung name becomes a filename. Anything that is not a plain token is refused
# rather than sanitised — a silently renamed plan is worse than a missing one.
SAFE_RUNG = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")

# ---- metric catalog ---------------------------------------------------------
# Naming follows the Drift Planner catalog (10.5281/zenodo.22869651); `source`
# is this fleet's real telemetry, verified against what is actually deployed.
# bounds are (min, max) for any human-supplied threshold; a threshold outside
# them is a hard error, never a clamp.
METRICS = {
    "latency_p95_s": {
        "source": "langfuse", "unit": "seconds", "bounds": (0.1, 600.0),
        "applies_to": SYSTEM_TYPES},
    "task_success_rate": {
        "source": "model_audit", "unit": "ratio", "bounds": (0.0, 1.0),
        "applies_to": SYSTEM_TYPES},
    "schema_conformance_rate": {
        "source": "model_audit", "unit": "ratio", "bounds": (0.0, 1.0),
        "applies_to": SYSTEM_TYPES},
    "benign_refusal_rate": {
        "source": "langfuse", "unit": "ratio", "bounds": (0.0, 1.0),
        "applies_to": SYSTEM_TYPES},
    "input_distribution_shift": {
        "source": "langfuse", "unit": "psi", "bounds": (0.0, 10.0),
        "applies_to": SYSTEM_TYPES},
    "retrieval_recall": {
        "source": "pgvector+memory_audit", "unit": "ratio", "bounds": (0.0, 1.0),
        "applies_to": ("rag",)},
    "grounding_rate": {
        "source": "pgvector+memory_audit", "unit": "ratio", "bounds": (0.0, 1.0),
        "applies_to": ("rag",)},
    "corpus_update_lag_h": {
        "source": "memory_audit", "unit": "hours", "bounds": (0.0, 720.0),
        "applies_to": ("rag",)},
    # No telemetry on this fleet observes tool side effects. Emitted, never valued.
    "unauthorized_tool_effects": {
        "source": None, "unit": "count", "bounds": (0.0, 1000.0),
        "applies_to": ("tool-agent",)},
}

# field -> (python type, min, max). `observation_window` and `baseline_days` are days.
CALIBRATION_FIELDS = {
    "daily_volume": (int, 0, 10_000_000),
    "eval_sampling_pct": (float, 0.0, 100.0),
    "baseline_days": (int, 1, 365),
    "observation_window": (int, 1, 365),
    "min_sample_target": (int, 1, 1_000_000),
}
SIGNOFF_FIELDS = ("reviewer", "rollback_target")


class PlanError(Exception):
    """A human-supplied override is unusable; nothing is written."""


def _now():
    return datetime.datetime.now(datetime.timezone.utc)


def _iso(dt):
    return dt.isoformat().replace("+00:00", "Z")


# ---- inputs -----------------------------------------------------------------

def load_rungs(path):
    """Return [(rung_name, system_type)] from a LiteLLM config, in file order."""
    with open(path) as f:
        cfg = yaml.safe_load(f) or {}
    entries = cfg.get("model_list") if isinstance(cfg, dict) else cfg
    if not isinstance(entries, list):
        raise PlanError(f"{path}: no model_list to read")
    out, seen = [], set()
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        name = str(entry.get("model_name", "")).strip()
        if not name or name in seen:
            continue
        seen.add(name)
        out.append((name, classify(entry)))
    return out


def classify(entry):
    """Map a LiteLLM entry to a system_type.

    Only `embedding` rungs are inferable (they back retrieval, so their plan
    carries the RAG metrics). Everything else is `chat`; tool-agent is not
    detectable from the gateway config and must be declared in the overrides
    file rather than guessed.
    """
    info = entry.get("model_info") or {}
    return "rag" if str(info.get("mode", "")).strip() == "embedding" else "chat"


def load_overrides(path):
    if not path or not os.path.exists(path):
        return {}
    try:
        with open(path) as f:
            data = json.load(f)
    except (OSError, ValueError) as e:
        raise PlanError(f"{path}: unreadable overrides ({e})")
    if not isinstance(data, dict):
        raise PlanError(f"{path}: overrides must be an object keyed by rung")
    # JSON has no comments, and a real rung name can never start with "_"
    # (SAFE_RUNG requires an alphanumeric first character), so underscore keys
    # are free for notes without weakening the unknown-rung check.
    return {k: v for k, v in data.items() if not k.startswith("_")}


def load_existing(out_dir, rung):
    try:
        with open(os.path.join(out_dir, f"{rung}.json")) as f:
            data = json.load(f)
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


# ---- validation -------------------------------------------------------------

def _check_number(where, field, value, typ, lo, hi):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise PlanError(f"{where}: {field} must be a number, got {value!r}")
    if typ is int and float(value) != int(value):
        raise PlanError(f"{where}: {field} must be a whole number, got {value!r}")
    if not lo <= float(value) <= hi:
        raise PlanError(f"{where}: {field}={value} outside declared bounds [{lo}, {hi}]")
    return typ(value)


def validate_threshold(where, metric, value, system_type):
    spec = METRICS.get(metric)
    if spec is None:
        raise PlanError(f"{where}: unknown metric {metric!r}")
    if system_type not in spec["applies_to"]:
        raise PlanError(
            f"{where}: metric {metric!r} does not apply to system_type {system_type!r}")
    if spec["source"] is None:
        raise PlanError(
            f"{where}: metric {metric!r} has no telemetry source on this fleet; "
            "a threshold on it would be unverifiable")
    lo, hi = spec["bounds"]
    return _check_number(where, metric, value, float, lo, hi)


def apply_calibration(plan, values, where):
    """Apply human-supplied calibration onto a plan, validating every field."""
    for field, value in values.items():
        if value is None:
            continue
        if field == "system_type":
            if value not in SYSTEM_TYPES:
                raise PlanError(f"{where}: system_type {value!r} not in {SYSTEM_TYPES}")
            plan["system_type"] = value
        elif field in CALIBRATION_FIELDS:
            typ, lo, hi = CALIBRATION_FIELDS[field]
            plan[field] = _check_number(where, field, value, typ, lo, hi)
        elif field in SIGNOFF_FIELDS:
            if not isinstance(value, str) or not value.strip():
                raise PlanError(f"{where}: {field} must be a non-empty string")
            plan[field] = value.strip()
        elif field == "metrics":
            if not isinstance(value, dict):
                raise PlanError(f"{where}: metrics must be an object")
        elif field in ("schema_version", "status", "generated_at",
                       "first_generated_at", "rung", "generator"):
            continue  # generator-owned; a stale copy never wins
        else:
            raise PlanError(f"{where}: unknown field {field!r}")


def apply_thresholds(plan, metrics, where):
    for metric, value in (metrics or {}).items():
        if isinstance(value, dict):          # a whole metric block from an old plan
            value = value.get("threshold")
        if value is None:
            continue
        checked = validate_threshold(where, metric, value, plan["system_type"])
        plan["metrics"][metric]["threshold"] = checked


# ---- plan construction ------------------------------------------------------

def metric_block(system_type):
    block = {}
    for name, spec in METRICS.items():
        if system_type not in spec["applies_to"]:
            continue
        entry = {
            "threshold": None,
            "source": spec["source"],
            "unit": spec["unit"],
            "min": spec["bounds"][0],
            "max": spec["bounds"][1],
        }
        if spec["source"] is None:
            entry["no_telemetry_source"] = True
        block[name] = entry
    return block


def is_calibrated(plan):
    if not all(plan.get(f) for f in SIGNOFF_FIELDS):
        return False
    return all(plan.get(f) is not None for f in CALIBRATION_FIELDS)


def build_plan(rung, system_type, existing, override, now):
    """One plan. `existing` is the previous file (merged), `override` the human file."""
    plan = {
        "schema_version": SCHEMA_VERSION,
        "rung": rung,
        "system_type": system_type,
        "status": DRAFT,
        "generator": "drift_plan.py (wq#2434)",
        "first_generated_at": existing.get("first_generated_at") or _iso(now),
        "generated_at": _iso(now),
    }
    for field in CALIBRATION_FIELDS:
        plan[field] = None
    for field in SIGNOFF_FIELDS:
        plan[field] = None

    # system_type must settle before the metric block is built: a rung declared
    # tool-agent in the overrides gets the tool-agent metrics, not the chat ones.
    if existing.get("system_type") in SYSTEM_TYPES:
        plan["system_type"] = existing["system_type"]
    declared = override.get("system_type") if isinstance(override, dict) else None
    if declared is not None:
        apply_calibration(plan, {"system_type": declared}, f"overrides[{rung}]")

    plan["metrics"] = metric_block(plan["system_type"])

    # Previous file first, human overrides second, so the overrides file wins.
    apply_calibration(plan, existing, f"{rung}.json")
    apply_thresholds(plan, existing.get("metrics"), f"{rung}.json")
    apply_calibration(plan, override, f"overrides[{rung}]")
    apply_thresholds(plan, override.get("metrics"), f"overrides[{rung}]")

    plan["status"] = CALIBRATED if is_calibrated(plan) else DRAFT
    return plan


def signoff_overdue(plan, now):
    """True when the declared observation window elapsed and nobody signed off.

    Proposal step 3: the dashboard flags these. The window is measured from the
    plan's first generation, which is when observation actually began.
    """
    if plan["status"] == CALIBRATED:
        return False
    window = plan.get("observation_window")
    if not window:
        return False
    try:
        start = datetime.datetime.fromisoformat(
            plan["first_generated_at"].replace("Z", "+00:00"))
    except (KeyError, AttributeError, ValueError):
        return False
    return now >= start + datetime.timedelta(days=window)


def build_index(plans, skipped, now):
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": _iso(now),
        "source": "drift_plan.py (wq#2434)",
        "counts": {
            "total": len(plans),
            "calibrated": sum(1 for p in plans if p["status"] == CALIBRATED),
            "draft": sum(1 for p in plans if p["status"] == DRAFT),
            "signoff_overdue": sum(1 for p in plans if signoff_overdue(p, now)),
            "skipped": len(skipped),
        },
        "plans": [{
            "rung": p["rung"],
            "system_type": p["system_type"],
            "status": p["status"],
            "reviewer": p["reviewer"],
            "rollback_target": p["rollback_target"],
            "observation_window": p["observation_window"],
            "first_generated_at": p["first_generated_at"],
            "signoff_overdue": signoff_overdue(p, now),
            "metrics_without_source": sorted(
                m for m, v in p["metrics"].items() if v.get("no_telemetry_source")),
            "file": f"{p['rung']}.json",
        } for p in plans],
        "skipped_rungs": skipped,
    }


def write_json(path, payload):
    """Atomic write: a half-written plan must never be readable by the dashboard."""
    directory = os.path.dirname(path) or "."
    fd, tmp = tempfile.mkstemp(dir=directory, prefix=".drift-plan-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(payload, f, indent=2, sort_keys=False)
            f.write("\n")
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def generate(config=CONFIG, out_dir=OUT_DIR, overrides_path=OVERRIDES, now=None):
    """Build every plan in memory. Raises PlanError before anything is written."""
    now = now or _now()
    overrides = load_overrides(overrides_path)
    rungs = load_rungs(config)
    known = {name for name, _ in rungs}
    for rung in overrides:
        if rung not in known:
            raise PlanError(
                f"overrides[{rung}]: no such rung in {config} (known: {len(known)})")

    plans, skipped = [], []
    for rung, system_type in rungs:
        if not SAFE_RUNG.match(rung):
            skipped.append({"rung": rung, "reason": "unsafe_rung_name"})
            continue
        plans.append(build_plan(rung, system_type,
                                load_existing(out_dir, rung),
                                overrides.get(rung) or {}, now))

    # rollback_target must name a rung that exists, or the plan's fallback is fiction.
    for plan in plans:
        target = plan["rollback_target"]
        if target and target not in known:
            raise PlanError(
                f"{plan['rung']}: rollback_target {target!r} is not a known rung")

    return plans, skipped, build_index(plans, skipped, now)


def main(argv=None):
    ap = argparse.ArgumentParser(
        prog="drift_plan.py",
        description="Generate per-rung DRAFT_REQUIRES_CALIBRATION drift plans.",
        epilog="examples:\n"
               "  drift_plan.py --dry-run        # show what would change, write nothing\n"
               "  drift_plan.py                  # regenerate plans + index.json\n"
               "  drift_plan.py --json           # regenerate and print the index\n"
               "  drift_plan.py --list-metrics   # the metric catalog and its sources\n"
               "\nexit: 0 ok  2 bad config or override (nothing written)",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", default=CONFIG, help=f"LiteLLM config (default {CONFIG})")
    ap.add_argument("--out-dir", default=OUT_DIR, help=f"plan directory (default {OUT_DIR})")
    ap.add_argument("--overrides", default=OVERRIDES,
                    help=f"human calibration file (default {OVERRIDES})")
    ap.add_argument("--dry-run", action="store_true", help="write nothing")
    ap.add_argument("--json", action="store_true", dest="as_json",
                    help="print index.json to stdout")
    ap.add_argument("--list-metrics", action="store_true",
                    help="print the metric catalog and exit")
    args = ap.parse_args(argv)

    if args.list_metrics:
        for name, spec in METRICS.items():
            lo, hi = spec["bounds"]
            print(f"{name:26} {spec['source'] or 'NO TELEMETRY SOURCE':22} "
                  f"{spec['unit']:8} [{lo}, {hi}]  {','.join(spec['applies_to'])}")
        return 0

    try:
        plans, skipped, index = generate(args.config, args.out_dir, args.overrides)
    except PlanError as e:
        print(f"drift_plan: {e}", file=sys.stderr)
        return 2

    for entry in skipped:
        print(f"drift_plan: WARN skipped rung {entry['rung']!r}: {entry['reason']}",
              file=sys.stderr)

    if not args.dry_run:
        os.makedirs(args.out_dir, exist_ok=True)
        for plan in plans:
            write_json(os.path.join(args.out_dir, f"{plan['rung']}.json"), plan)
        write_json(os.path.join(args.out_dir, "index.json"), index)

    if args.as_json:
        print(json.dumps(index, indent=2))
    else:
        counts = index["counts"]
        verb = "would write" if args.dry_run else "wrote"
        print(f"{verb} {counts['total']} plans to {args.out_dir} "
              f"({counts['calibrated']} calibrated, {counts['draft']} draft, "
              f"{counts['signoff_overdue']} sign-off overdue, "
              f"{counts['skipped']} skipped)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
