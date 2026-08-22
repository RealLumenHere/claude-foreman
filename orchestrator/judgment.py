#!/usr/bin/env python3
"""judgment — the two orchestrator-tier behaviours that are not plain routing:

  1. verify-pass dispatch (archetype 8): when a ticket SHIPS a diff, file the adversarial
     re-check ticket — sonnet-5@high, fresh instance, deps on the shipped ticket, the PR /
     branch named in the detail. Idempotent per shipped ticket. Called by fworker on
     SUCCESS reconcile (best-effort, never fails the harness) or by hand.
  2. Fable-5@high judgment sessions: `due` says whether the foreman should open one now,
     from cadence (hours / tickets closed since last) OR a burn-down trigger (a blocked
     ticket, a verify contradiction, a governor floor breach, ...). `mark` records that one
     ran. fable-5@max is reserved for `hardest_investigation_triggers` and only after CAL-1
     has written measured[] rows — until then `due` never returns @max.

Policy lives in research/models.yaml (`verify_pass:` / `judgment:` blocks) so it is
edited next to the routing table it modifies, not in code.

CLI:
    judgment.py verify-pass --for <ticket-id> [--pr URL] [--author-model M] [--dry-run]
    judgment.py verify-required <archetype> [--author-model M]
    judgment.py due   [--trigger NAME ...] [--json]
    judgment.py mark  [--note TEXT]
    judgment.py triggers-scan            # derive triggers from _runs/ + state.json
"""
import argparse
import datetime as dt
import glob
import json
import os
import re
import subprocess
import sys
from typing import Any, Dict, Optional, Tuple

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import router  # noqa: E402

FOREMAN_ROOT = router.FOREMAN_ROOT
RUNS_ROOT = os.environ.get("FOREMAN_RUNS_ROOT", os.path.join(FOREMAN_ROOT, "_runs"))
STATE_JSON = router.STATE_JSON
JUDGMENT_STATE = os.environ.get("FOREMAN_JUDGMENT_STATE",
                                os.path.join(RUNS_ROOT, "judgment.json"))
LOCAL_POOL_MODELS = ("deepseek-v4-flash", "qwen36-local")


def _now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def _iso(t: dt.datetime) -> str:
    return t.replace(microsecond=0).isoformat()


# ---- verify-pass ---------------------------------------------------------------------

def verify_required(manifest: Any, archetype: str, author_model: Optional[str] = None) -> Tuple[bool, str]:
    """-> (bool, reason). Policy from models.yaml verify_pass:."""
    pol = manifest.verify_policy or {}
    if archetype in (pol.get("never_for") or []):
        return False, f"{archetype} never gets a verify-pass"
    if author_model and pol.get("local_author_always", True) and \
            any(author_model.startswith(m) for m in LOCAL_POOL_MODELS):
        return True, f"authored on the local rung ({author_model}) — verify gate mandatory"
    if archetype in (pol.get("required_for") or []):
        return True, f"{archetype} shipped diffs always get a verify-pass"
    return False, f"{archetype} is not in verify_pass.required_for"


def verifier_tuple(manifest: Any, author_model: Optional[str] = None) -> Tuple[Any, str]:
    """sonnet-5@high, fresh instance. Model distinct from the author only if the policy
    asks for it (see models.yaml verify_pass.prefer_distinct_model)."""
    pol = manifest.verify_policy or {}
    rung = router.parse_rung(pol.get("tuple", "sonnet-5@high"))
    note = "fresh instance"
    if author_model and router.resolve_alias(author_model) == rung.model:
        if pol.get("prefer_distinct_model"):
            alt = router.parse_rung(pol.get("distinct_model_alternate", "opus-5@high"))
            rung, note = alt, f"distinct model from author {author_model}"
        else:
            note = f"fresh instance, same model as author ({author_model}); escalate " \
                   f"{pol.get('escalate_on_contradiction', 'opus-5@high')} on contradiction"
    why = manifest.is_dominated(rung.model, rung.effort, "verify-pass")
    if why:
        raise router.ManifestError(f"verify_pass tuple is dominated: {why}")
    return rung, note


def existing_verify_ticket(shipped_id: int) -> Optional[int]:
    """The verify-pass ticket already filed for wq#<shipped_id>, if any (idempotence)."""
    p = router.wq("list")
    for line in p.stdout.splitlines():
        if re.search(rf"\[verify\] wq#{shipped_id}\b", line):
            m = re.match(r"\s*\[\s*(\d+)\]", line)
            if m:
                return int(m.group(1))
    return None


def file_verify_pass(shipped_id: int, *, pr: Optional[str] = None, author_model: Optional[str] = None, manifest: Optional[Any] = None, dry_run: bool = False) -> Dict[str, Any]:
    """File the archetype-8 ticket for a shipped diff. Returns a decision dict."""
    manifest = manifest or router.load_manifest()
    t = router.wq_show(shipped_id)
    archetype = t.get("archetype") if t.get("archetype") not in (None, "", "None") else None
    if not archetype:
        archetype, _ = router.classify_archetype(t.get("title", ""), t.get("detail", ""), t.get("lane", ""))
    author_model = author_model or (t.get("model") if t.get("model") not in (None, "", "None") else None)
    need, why = verify_required(manifest, archetype, author_model)
    out = {"shipped": int(shipped_id), "archetype": archetype, "author_model": author_model,
           "required": need, "reason": why, "filed": None}
    if not need:
        return out
    already = existing_verify_ticket(shipped_id)
    if already:
        out.update(filed=already, reason=f"already filed as wq#{already}")
        return out
    rung, note = verifier_tuple(manifest, author_model)
    pol = manifest.verify_policy or {}
    pr_ref = pr or _pr_from_notes(t.get("notes", ""))
    title = f"[verify] wq#{shipped_id} {t.get('title', '')[:60]}"
    detail = (
        f"Adversarial verify-pass (archetype 8) of the diff shipped by wq#{shipped_id}"
        f"{' — ' + pr_ref if pr_ref else ''}. Author model: {author_model or 'unknown'}; "
        f"verifier: {rung.spec()} ({note}). Read the PR/diff cold, try to break it, run "
        f"its probe, and either confirm on the board or contradict with evidence. A "
        f"contradiction escalates to {pol.get('escalate_on_contradiction', 'opus-5@high')} "
        f"and is a judgment trigger. Do NOT re-implement; verify."
    )
    out.update(verifier=rung.as_dict(), verifier_note=note, title=title, pr=pr_ref)
    if dry_run:
        return out
    p = router.wq("add", "--lane", str(pol.get("lane", "build")), "--pri", str(pol.get("pri", 2)),
                  "--title", title, "--detail", detail, "--deps", str(shipped_id),
                  "--model", rung.model, "--effort", rung.effort or "", "--archetype", "verify-pass")
    m = re.search(r"added task (\d+)", p.stdout)
    out["filed"] = int(m.group(1)) if m else None
    out["wq"] = (p.stdout + p.stderr).strip()
    return out


def _pr_from_notes(notes: Optional[str]) -> Optional[str]:
    m = re.search(r"https?://\S+/pull/\d+|PR#\d+|https?://\S+/pulls/\d+", notes or "")
    return m.group(0) if m else None


# ---- judgment sessions ---------------------------------------------------------------

def load_judgment_state() -> Dict[str, Any]:
    try:
        with open(JUDGMENT_STATE) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {"last_run_at": None, "last_run_tickets_done": None, "runs": []}


def save_judgment_state(st: Dict[str, Any]) -> None:
    os.makedirs(os.path.dirname(JUDGMENT_STATE), exist_ok=True)
    tmp = JUDGMENT_STATE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(st, f, indent=1)
    os.replace(tmp, JUDGMENT_STATE)


def tickets_done_count() -> int:
    p = router.wq("list", "--status", "done")
    return sum(1 for line in p.stdout.splitlines() if re.match(r"\s*\[\s*\d+\]", line))


def calibrated(manifest: Any) -> bool:
    """CAL-1 has landed when routing rows carry measured[] blocks (BUILD_PLAN CAL-1)."""
    raw = manifest.raw or {}
    if raw.get("measured"):
        return True
    for m in raw.get("models", []):
        b = m.get("burn") or {}
        if isinstance(b, dict) and isinstance(b.get("measured"), (list, dict)) and b.get("measured"):
            return True
    return False


def scan_triggers(runs_root: str = RUNS_ROOT, state_json: str = STATE_JSON, since: Optional[float] = None) -> Dict[str, Any]:
    """Derive burn-down triggers from what the harness and governor already write."""
    found = {}
    for path in glob.glob(os.path.join(runs_root, "wq*", "attempt-*.json")):
        try:
            with open(path) as f:
                a = json.load(f)
        except (OSError, ValueError):
            continue
        fin = a.get("finished_at") or 0
        if since is not None and fin and fin < since:
            continue
        tid = a.get("ticket")
        if a.get("attempt", 0) >= 2 and a.get("classification") not in ("SUCCESS", "LIMIT"):
            found.setdefault("ticket_blocked_twice", []).append(tid)
        if a.get("refusal_no_fallback") or a.get("classification") == "REFUSED":
            found.setdefault("refusal_no_fallback", []).append(tid)
        # wq#687: an UNEXPLAINED model downgrade (fworker compared observed vs
        # dispatched and ruled out sanctioned redirects/announced fallbacks) means
        # a verdict was produced by a weaker model than routed — the orchestrator
        # must see it, since a "SUCCESS" from the wrong model is not the success
        # the routing table intended.
        if a.get("model_downgraded"):
            found.setdefault("model_downgraded", []).append(tid)
        if a.get("archetype") == "rca-investigation" and a.get("cross_subsystem"):
            found.setdefault("rca_cross_subsystem", []).append(tid)
        if a.get("verify_contradiction"):
            found.setdefault("verify_contradiction", []).append(tid)
        if a.get("decompose_needed"):
            found.setdefault("decompose_needed", []).append(tid)
    try:
        with open(state_json) as f:
            st = json.load(f)
        if st.get("floor_breaches"):
            found["governor_floor_breach"] = st["floor_breaches"]
    except (OSError, ValueError):
        pass
    return found


def _in_quiet_hours(now_utc: dt.datetime, spec: Optional[str]) -> bool:
    """'05:00-11:00 weekdays' in America/Los_Angeles."""
    if not spec:
        return False
    try:
        from zoneinfo import ZoneInfo
        local = now_utc.astimezone(ZoneInfo("America/Los_Angeles"))
    except Exception:
        return False
    m = re.match(r"(\d{2}):(\d{2})-(\d{2}):(\d{2})\s*(weekdays)?", spec)
    if not m:
        return False
    h1, m1, h2, m2, wk = m.groups()
    if wk and local.weekday() > 4:
        return False
    t = local.hour * 60 + local.minute
    return int(h1) * 60 + int(m1) <= t < int(h2) * 60 + int(m2)


def due(manifest: Any, *, triggers: Optional[Dict[str, Any]] = None, now: Optional[dt.datetime] = None, state: Optional[Dict[str, Any]] = None, tickets_done: Optional[int] = None) -> Dict[str, Any]:
    """-> dict {due: bool, tuple: 'claude-fable-5@high'|'...@max'|None, why: [...]}"""
    pol = manifest.judgment_policy or {}
    now = now or _now()
    st = state if state is not None else load_judgment_state()
    triggers = dict(triggers or {})
    why, is_due = [], False
    tup = router.parse_rung(pol.get("tuple", "fable-5@high"))

    last = st.get("last_run_at")
    last_dt = dt.datetime.fromisoformat(last) if last else None
    gap_min = pol.get("min_gap_minutes", 60)
    if last_dt and (now - last_dt).total_seconds() < gap_min * 60:
        return {"due": False, "tuple": None,
                "why": [f"last judgment {_iso(last_dt)} < min_gap {gap_min}m ago"],
                "triggers": triggers}

    known = set(pol.get("burn_down_triggers") or [])
    fired = {k: v for k, v in triggers.items() if k in known and v}
    if fired:
        is_due = True
        why.append(f"burn-down trigger(s): {sorted(fired)}")

    cad_h = pol.get("cadence_hours")
    if cad_h and (last_dt is None or (now - last_dt).total_seconds() >= cad_h * 3600):
        if _in_quiet_hours(now, pol.get("quiet_hours_pt")) and not fired:
            why.append("cadence elapsed but inside quiet hours (weekday 05-11 PT); waiting")
        else:
            is_due = True
            why.append(f"cadence: {cad_h}h since last judgment" if last_dt else "cadence: no judgment recorded yet")
    cad_t = pol.get("cadence_tickets_closed")
    if cad_t and tickets_done is not None and st.get("last_run_tickets_done") is not None:
        delta = tickets_done - st["last_run_tickets_done"]
        if delta >= cad_t:
            is_due = True
            why.append(f"cadence: {delta} tickets closed since last judgment (>= {cad_t})")

    if not is_due:
        return {"due": False, "tuple": None, "why": why or ["nothing due"], "triggers": triggers}

    hardest = set(pol.get("hardest_investigation_triggers") or [])
    if fired.keys() & hardest and calibrated(manifest):
        tup = router.parse_rung(pol.get("max_tuple", "fable-5@max"))
        why.append("hardest-investigation trigger + CAL-1 measured data present -> @max")
    elif fired.keys() & hardest:
        why.append("hardest-investigation trigger but @max reserved until CAL-1 -> @high")
    if manifest.is_dominated(tup.model, tup.effort):
        raise router.ManifestError("judgment tuple is dominated")
    return {"due": True, "tuple": tup.spec(), "model": tup.model, "effort": tup.effort,
            "why": why, "triggers": triggers}


def mark(note: Optional[str] = None, tickets_done: Optional[int] = None, now: Optional[dt.datetime] = None) -> Dict[str, Any]:
    st = load_judgment_state()
    now = now or _now()
    st["last_run_at"] = _iso(now)
    st["last_run_tickets_done"] = tickets_done if tickets_done is not None else tickets_done_count()
    st.setdefault("runs", []).append({"at": st["last_run_at"], "note": note})
    st["runs"] = st["runs"][-50:]
    save_judgment_state(st)
    return st


# ---- CLI -----------------------------------------------------------------------------

def main(argv: Optional[list] = None) -> int:
    ap = argparse.ArgumentParser(prog="judgment.py")
    ap.add_argument("--manifest")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("verify-pass"); s.add_argument("--for", dest="shipped", type=int, required=True)
    s.add_argument("--pr"); s.add_argument("--author-model"); s.add_argument("--dry-run", action="store_true")
    s = sub.add_parser("verify-required"); s.add_argument("archetype"); s.add_argument("--author-model")
    s = sub.add_parser("due"); s.add_argument("--trigger", action="append", default=[])
    s.add_argument("--no-scan", action="store_true", help="don't derive triggers from _runs/state.json")
    s = sub.add_parser("mark"); s.add_argument("--note")
    sub.add_parser("triggers-scan")
    args = ap.parse_args(argv)
    manifest = router.load_manifest(args.manifest, force=bool(args.manifest))

    if args.cmd == "verify-pass":
        d = file_verify_pass(args.shipped, pr=args.pr, author_model=args.author_model,
                             manifest=manifest, dry_run=args.dry_run)
        print(json.dumps(d))
        return 0
    if args.cmd == "verify-required":
        need, why = verify_required(manifest, args.archetype, args.author_model)
        print(json.dumps({"required": need, "reason": why}))
        return 0 if need else 1
    if args.cmd == "triggers-scan":
        print(json.dumps(scan_triggers()))
        return 0
    if args.cmd == "due":
        # triggers older than the last judgment session were that session's input;
        # only what happened SINCE counts toward the next one.
        st = load_judgment_state()
        since = None
        if st.get("last_run_at"):
            since = dt.datetime.fromisoformat(st["last_run_at"]).timestamp()
        trig = {} if args.no_scan else scan_triggers(since=since)
        for t in args.trigger:
            trig[t] = trig.get(t) or ["--trigger"]
        d = due(manifest, triggers=trig, tickets_done=tickets_done_count())
        print(json.dumps(d))
        return 0 if d["due"] else 1
    if args.cmd == "mark":
        print(json.dumps(mark(args.note)))
        return 0
    return 2


if __name__ == "__main__":
    sys.exit(main())
