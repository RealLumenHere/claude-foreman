#!/usr/bin/env python3
"""capability_profiles.py — `fleet-node capabilities refresh` (wq#2252, C5 of wq#2241).
research/INFERENCE_NODE_REGISTRY.md §7.2; acceptance is the capability-refresh third of T9 (§8).

Recomputes the capability rows of research/inference_nodes.yaml from the attempt-N.json records
under _runs/wq<id>/ (read with self_heal.attempt_records()), grouped by (model, archetype) over
a 30-day window; success = the record's classification is SUCCESS or DEGRADED_SUCCESS.

The flip rules are asymmetric on purpose:
  n >= 10 and success <  0.30  DEMOTE automatically: trusted false, source measured, sample recorded.
  n >= 10 and success >= 0.50  on an untrusted row, PROPOSE only: a candidate goes to
                               _runs/capability_proposals.json and is printed; the bit is not flipped.
  n >= 10, anything else       sample recorded (n/success/window_days/updated), trusted and source kept.
  n <  10                      nothing moves; source stays declared.

The registry is edited line-wise, not re-dumped: its rows carry file:line provenance and
one-line flow mappings that a YAML round-trip would reflow.

CLI:  python3 dispatcher/capability_profiles.py refresh [--path P] [--dry-run]
"""
import argparse
import json
import os
import re
import sys
import time
from datetime import date, datetime, timezone

import yaml

DISPATCHER_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.normpath(os.path.join(DISPATCHER_DIR, ".."))
sys.path.insert(0, DISPATCHER_DIR)
sys.path.insert(0, os.path.join(REPO_ROOT, "harness"))
import charging  # noqa: E402
import inference_registry  # noqa: E402
import self_heal  # noqa: E402

WINDOW_DAYS = 30
MIN_N = 10
DEMOTE_BELOW = 0.30
PROPOSE_AT = 0.50
SUCCESS_CLASSES = ("SUCCESS", "DEGRADED_SUCCESS")
PROPOSALS_NAME = "capability_proposals.json"
NOT_CAPABILITY_EVIDENCE = (charging.CHARGE_RUNG, charging.CHARGE_HARNESS)

_RUN_DIR_RE = re.compile(r"^wq(\d+)$")
_NODE_RE = re.compile(r"^  ([\w.-]+):\s*(?:#.*)?$")
_FAMILY_RE = re.compile(r"^\s*- family:\s*([\w.-]+)")
_CAPS_RE = re.compile(r"^(\s*)capabilities:\s*(?:#.*)?$")
_ROW_RE = re.compile(r"^(\s+)([\w-]+)(:\s*)(\{.*)$")
_BARE_RE = re.compile(r"^[A-Za-z][\w-]*$")


def _split_flow_row(rest):
    """(mapping, tail) for text that starts with a one-line flow mapping; the tail is whatever
    follows the closing brace (spacing, a comment). None when it is not one."""
    try:
        node = yaml.compose(rest, Loader=yaml.SafeLoader)
    except yaml.YAMLError:
        return None
    if not (isinstance(node, yaml.MappingNode) and node.flow_style):
        return None
    end = node.end_mark.index
    return yaml.safe_load(rest[:end]) or {}, rest[end:]


def _finished_at(rec, path):
    for key in ("finished_at", "started_at"):
        if isinstance(rec.get(key), (int, float)):
            return float(rec[key])
    try:
        return os.path.getmtime(path)
    except OSError:
        return 0.0


def collect(now, window_days=WINDOW_DAYS):
    """{(model, archetype): [n, successes]} over the window. A record with no model, archetype
    or classification (unreadable, or a signal-kill stub) is not evidence, and neither is one
    charged to the rung or the harness: a backend fault says nothing about the model's ability,
    and counting it would demote a row that then never routes again to earn its way back."""
    cutoff = now - window_days * 86400
    tally = {}
    try:
        names = sorted(os.listdir(self_heal.RUNS_ROOT))
    except OSError:
        return tally
    for name in names:
        m = _RUN_DIR_RE.match(name)
        if not m:
            continue
        for path, rec in self_heal.attempt_records(int(m.group(1))):
            model, archetype, cls = rec.get("model"), rec.get("archetype"), rec.get("classification")
            if not (model and archetype and cls) or _finished_at(rec, path) < cutoff:
                continue
            if rec.get("charged_to") in NOT_CAPABILITY_EVIDENCE:
                continue
            cell = tally.setdefault((model, archetype), [0, 0])
            cell[0] += 1
            cell[1] += cls in SUCCESS_CLASSES
    return tally


def plan(reg, tally, now, window_days=WINDOW_DAYS):
    """(edits, proposals). edits: {(node, family, archetype): {field: value}} to merge into the
    row; proposals: candidates for untrusted rows the evidence would promote."""
    updated = datetime.fromtimestamp(now, timezone.utc).strftime("%Y-%m-%d")
    edits, proposals = {}, []
    for node, spec in (reg.get("nodes") or {}).items():
        for model in (spec or {}).get("models") or []:
            ids = {model.get("family"), model.get("id")} - {None}
            for archetype, row in (model.get("capabilities") or {}).items():
                cells = [tally.get((mid, archetype), (0, 0)) for mid in ids]
                n, succ = sum(c[0] for c in cells), sum(c[1] for c in cells)
                if n < MIN_N:
                    continue
                rate = succ / n
                sample = {"n": n, "success": round(rate, 3), "window_days": window_days,
                          "updated": updated}
                if rate < DEMOTE_BELOW and row.get("trusted"):
                    sample.update(trusted=False, source="measured",
                                  reason=f"auto-demoted: {succ}/{n} succeeded over {window_days}d "
                                         f"(< {DEMOTE_BELOW:.2f})")
                elif rate >= PROPOSE_AT and not row.get("trusted"):
                    proposals.append({"node": node, "family": model.get("family"),
                                      "archetype": archetype, "propose": {"trusted": True},
                                      **sample})
                edits[(node, model.get("family"), archetype)] = sample
    return edits, proposals


def _scalar(v):
    if isinstance(v, bool):
        return "true" if v else "false"
    if v is None:
        return "null"
    if isinstance(v, str):
        bare = _BARE_RE.match(v) and yaml.safe_load(v) == v
        return v if bare else json.dumps(v)
    if isinstance(v, float) and (v != v or v in (float("inf"), float("-inf"))):
        return ".nan" if v != v else (".inf" if v > 0 else "-.inf")
    if isinstance(v, (int, float)):
        return str(v)
    if isinstance(v, (date, datetime)):
        return v.isoformat()
    return json.dumps(v)


def apply_edits(text, edits):
    """Merge `edits` into the matching one-line capability rows of the registry text. A row
    that is not a one-line flow mapping raises RegistryError rather than being skipped."""
    todo, node, family, caps_indent, in_nodes = dict(edits), None, None, None, False
    out = []
    for line in text.split("\n"):
        if line and not line[0].isspace() and not line.startswith("#"):
            in_nodes = line.startswith("nodes:")
        if in_nodes and line.strip() and not line.lstrip().startswith("#"):
            indent = len(line) - len(line.lstrip())
            if _NODE_RE.match(line):
                node, family, caps_indent = _NODE_RE.match(line).group(1), None, None
            elif _FAMILY_RE.match(line):
                family, caps_indent = _FAMILY_RE.match(line).group(1), None
            elif _CAPS_RE.match(line):
                caps_indent = len(_CAPS_RE.match(line).group(1))
            elif caps_indent is not None and indent <= caps_indent:
                caps_indent = None
            elif caps_indent is not None:
                row = _ROW_RE.match(line)
                flow = _split_flow_row(row.group(4)) if row else None
                key = (node, family, row.group(2)) if flow else None
                if key in todo:
                    merged, tail = flow
                    merged.update(todo.pop(key))
                    body = ", ".join(f"{_scalar(k)}: {_scalar(v)}" for k, v in merged.items())
                    line = f"{row.group(1)}{row.group(2)}{row.group(3)}{{{body}}}{tail}"
        out.append(line)
    if todo:
        raise inference_registry.RegistryError(
            f"capability rows not editable as one-line flow mappings: {sorted(todo)}")
    return "\n".join(out)


def _atomic_write(path, data):
    tmp = f"{path}.tmp.{os.getpid()}"
    with open(tmp, "w") as f:
        f.write(data)
    os.replace(tmp, path)


def refresh(path=None, now=None, dry_run=False):
    """Recompute profiles from attempt records. Returns {"demoted", "proposals", "measured"};
    unless dry_run, rewrites the registry rows and the proposals snapshot."""
    path = path or inference_registry.REGISTRY_YAML
    now = time.time() if now is None else now
    reg = inference_registry.load(path)
    edits, proposals = plan(reg, collect(now), now)
    demoted = [{"node": k[0], "family": k[1], "archetype": k[2], "n": v["n"], "success": v["success"]}
               for k, v in sorted(edits.items()) if v.get("trusted") is False]
    if not dry_run:
        if edits:
            with open(path) as f:
                _atomic_write(path, apply_edits(f.read(), edits))
        _atomic_write(os.path.join(self_heal.RUNS_ROOT, PROPOSALS_NAME),
                      json.dumps({"generated_at": now, "proposals": proposals}, indent=2) + "\n")
    return {"demoted": demoted, "proposals": proposals, "measured": len(edits)}


def main(argv=None):
    ap = argparse.ArgumentParser(prog="capability_profiles")
    sub = ap.add_subparsers(dest="cmd", required=True)
    rp = sub.add_parser("refresh", help="recompute capability rows from attempt records")
    rp.add_argument("--path", help="registry yaml (default: research/inference_nodes.yaml)")
    rp.add_argument("--dry-run", action="store_true", help="report only; write nothing")
    args = ap.parse_args(argv)
    try:
        res = refresh(path=args.path, dry_run=args.dry_run)
    except inference_registry.RegistryError as e:
        print(f"capability_profiles: {e}", file=sys.stderr)
        return 1
    for d in res["demoted"]:
        print(f"DEMOTED  {d['node']}/{d['family']}/{d['archetype']}  n={d['n']} success={d['success']}")
    for p in res["proposals"]:
        print(f"PROPOSAL {json.dumps(p)}")
    print(f"{res['measured']} rows measured, {len(res['demoted'])} demoted, "
          f"{len(res['proposals'])} promotion proposals"
          + (" (dry run, nothing written)" if args.dry_run else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
