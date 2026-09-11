#!/usr/bin/env python3
"""liveness_clear.py — Liveness Gate Slices 3-4: the ONE write path (LIVENESS_GATE.md §12).

A Frank-gated ticket whose premise is already met on live state is closed BEFORE Frank
sees it; a genuinely blocked one is left alone; one the probes cannot decide is left
alone and flagged once. Three entry points share `clear_if_stale()`:

    sweep (timer)      python3 liveness_clear.py --apply          (§12.3)
    dispatcher hook    dispatch_hook(tid, ticket, fetch, log)     (§12.4)
    creation gate      creation_check(title, detail, lane, ...)   (§12.5, called by wq)

SAFETY (Frank's build order): deterministic probes only; FIREWALLED from the exec
allow/deny path (imports capability.liveness_verdict + the `wq liveness-close` verb, nothing that grants);
FAIL-SAFE (UNKNOWN / NO_TAG never close; a close that does not land restores the prior
status); every close audited with the probe verdict verbatim; reversible
(FOREMAN_LIVENESS_CLEAR=0 -> every write path is report-only).
"""
import argparse
import json
import os
import re
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "dispatcher"))
import capability  # noqa: E402  (liveness_verdict only — see test_liveness_clear firewall)
import liveness  # noqa: E402
import liveness_sweep  # noqa: E402  (frank_gated_tickets + load_bindings, imported not copied)

WQ_BIN = os.environ.get("WQ_BIN", "/home/lumen/bin/wq")
NTFY_BIN = os.environ.get("FOREMAN_NTFY_BIN", "/home/lumen/bin/ntfy-send")  # same default as the dispatcher
RUNTIME = os.environ.get("FOREMAN_LIVENESS_RUNTIME", os.path.join(HERE, "runtime"))
STATE_PATH = os.path.join(RUNTIME, "liveness_state.json")
AUDIT_PATH = os.path.join(RUNTIME, "liveness_audit.jsonl")
LEDGER = liveness_sweep.LEDGER
DEFAULT_AGENT = "liveness-clear"
TTL_MIN = int(os.environ.get("FOREMAN_LIVENESS_TTL_MIN", "30"))
NTFY_TOPIC = os.environ.get("FOREMAN_LIVENESS_NTFY_TOPIC", "lumen-fleet")

# Close verb: `wq liveness-close` (ops/wq1478-liveness-gate/apply_wq_patch.py). No claim —
# the user lane is never agent-claimable by design (wq:1078) and a claim would be a lie
# anyway: nobody is doing the work, the work is already done. The verb has its own three
# guards (caller base, Liveness line/binding on the ticket, Frank-visible status).


def writes_enabled():
    return os.environ.get("FOREMAN_LIVENESS_CLEAR", "1") != "0"


def now_iso():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


# ---------------------------------------------------------------------------
# state + audit
# ---------------------------------------------------------------------------

def load_state(path=None):
    path = path or STATE_PATH
    try:
        with open(path) as f:
            return json.load(f) or {}
    except (OSError, ValueError):
        return {}


def save_state(state, path=None):
    path = path or STATE_PATH
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(state, f, indent=1, sort_keys=True)
    os.replace(tmp, path)


def audit(rec, path=None):
    path = path or AUDIT_PATH
    rec = {"ts": now_iso(), **rec}
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "a") as f:
            f.write(json.dumps(rec, sort_keys=True) + "\n")
    except OSError as e:  # audit is evidence, but a broken disk must not stop the sweep
        print(f"[liveness_clear] WARN audit append failed: {e}", file=sys.stderr)
    return rec


def _wq(*args, agent=None):
    env = dict(os.environ)
    env["WQ_AGENT"] = agent or env.get("WQ_AGENT", "").strip() or DEFAULT_AGENT
    try:
        return subprocess.run([WQ_BIN] + [str(a) for a in args],
                              capture_output=True, text=True, env=env, timeout=60)
    except (OSError, subprocess.TimeoutExpired) as e:
        return subprocess.CompletedProcess(args, 99, "", f"wq call failed: {e}")


def row_status(tid, agent=None):
    """The ticket row's own status (wq#1479: the row is the verdict, not CLI stdout)."""
    p = _wq("show", tid, "--json", agent=agent)
    try:
        return (json.loads(p.stdout).get("status") or "").strip()
    except (ValueError, AttributeError):
        return ""


def _ntfy(msg, title="liveness-clear", priority="default"):
    try:
        subprocess.run([NTFY_BIN, "-p", priority, "-T", title, "-t", NTFY_TOPIC, msg],
                       capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.TimeoutExpired):
        pass  # telemetry, never load-bearing


def probe_summary(results):
    return "; ".join(f"{p}({a}) -> {v}: {d}" for p, a, v, d in results)


def _fresh(entry, ttl_min):
    """True when the state entry carries a verdict newer than ttl_min minutes."""
    import calendar
    try:
        t = calendar.timegm(time.strptime(entry.get("ts", ""), "%Y-%m-%dT%H:%M:%SZ"))
    except (ValueError, TypeError):
        return False
    return (time.time() - t) < ttl_min * 60


# ---------------------------------------------------------------------------
# the write path
# ---------------------------------------------------------------------------

def close_ticket(ticket, results, bindings=None, agent=None, apply=True):
    """SATISFIED path. Returns (closed: bool, detail: dict). Never raises. The close is
    confirmed against the ticket row, never against the CLI's stdout (wq#1479)."""
    tid = int(ticket["id"])
    summary = probe_summary(results)
    steps = []
    if not apply:
        return False, {"steps": ["dry-run: would close"], "close": "DRY-RUN"}
    args = ["liveness-close", tid, "--verdict", summary]
    if "Liveness:" not in (ticket.get("detail") or ""):
        bound = (bindings or {}).get(str(tid))
        if not bound:
            return False, {"steps": ["no Liveness line and no binding"], "close": "NOT-CLOSED no-premise"}
        args += ["--binding", bound]
    p = _wq(*args, agent=agent)
    first = next((ln.strip() for ln in (p.stderr + "\n" + p.stdout).splitlines() if ln.strip()), "")
    steps.append(f"liveness-close: rc={p.returncode} {first[:200]}")
    final = row_status(tid, agent=agent)
    steps.append(f"confirm: row status={final!r}")
    if p.returncode == 0 and final == "done":
        return True, {"steps": steps, "close": f"CLOSED wq#{tid}"}
    return False, {"steps": steps, "close": f"NOT-CLOSED wq#{tid} rc={p.returncode} status={final!r}: {first[:160]}"}


AQ_BIN = os.environ.get("FOREMAN_AQ_BIN", "aq")
UNTAGGED_AGE_DAYS = int(os.environ.get("FOREMAN_LIVENESS_UNTAGGED_AGE_DAYS", "7"))


def _ticket_age_days(ticket):
    """Days since created_at, or None when it can't be parsed (fail-safe: no alarm)."""
    created = (ticket or {}).get("created_at")
    if not created:
        return None
    import calendar
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%SZ"):
        try:
            t = calendar.timegm(time.strptime(created, fmt))
            return (time.time() - t) / 86400.0
        except ValueError:
            continue
    return None


def _aq_untagged_gate(tid, ticket, age_days):
    """wq#1676 item 5: one keyed aq row per tagless gate past the age threshold —
    visible without paging, never load-bearing (aq trouble must not touch the sweep)."""
    try:
        subprocess.run(
            [AQ_BIN, "add", "liveness", f"untagged-gate-{tid}",
             f"wq#{tid} is a Frank-gate with no Liveness: tag, {age_days:.0f}d old",
             f"wq#{tid} [{ticket.get('lane')}/{ticket.get('status')}] "
             f"{(ticket.get('title') or '')[:80]} carries no Liveness: line, no ledger "
             "binding, and no [NO-PROBE: ...] escape hatch — file --liveness/--no-probe "
             "or `wq decide --accept-probe`."],
            capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        pass  # telemetry, never load-bearing


def clear_if_stale(ticket, bindings=None, apply=True, agent=None, state=None, log=None):
    """One ticket's decision. Returns the audit record (also written when anything
    changed or was flagged). `apply=False` = report-only. `state` is the shared
    liveness_state dict (mutated in place; caller saves)."""
    tid = str(ticket["id"])
    status = (ticket.get("status") or "").strip()
    apply = bool(apply and writes_enabled())
    state = state if state is not None else {}
    bindings = bindings if bindings is not None else liveness_sweep.load_bindings()
    verdict, results = capability.liveness_verdict(ticket, bindings)
    prev_verdict = (state.get(tid) or {}).get("verdict")
    rec = {"ticket": int(tid), "prior_status": status, "lane": ticket.get("lane"),
           "title": (ticket.get("title") or "")[:80],
           "verdict": verdict, "prev_verdict": prev_verdict, "apply": apply,
           "probes": [{"probe": p, "args": a, "verdict": v, "detail": d} for p, a, v, d in results],
           "action": "none", "actor": agent or os.environ.get("WQ_AGENT") or DEFAULT_AGENT}
    if verdict != capability.LIVENESS_NO_TAG:
        state[tid] = {"verdict": verdict, "ts": now_iso(), "status": status,
                      "detail": probe_summary(results)[:300]}
    if verdict == liveness.SATISFIED:
        if apply:  # the note goes first so a failed close still leaves the reasoning on record
            _wq("note", tid, f"[liveness] auto-clear SATISFIED: {probe_summary(results)[:300]}", agent=agent)
        closed, detail = close_ticket(ticket, results, bindings, agent=agent, apply=apply)
        rec.update(detail)
        rec["action"] = "closed" if closed else ("would_close" if not apply else "close_failed")
        audit(rec)
        if closed:
            state[tid]["status"] = "done"
            msg = (f"auto-closed wq#{tid} [{ticket.get('lane')}/{status}] "
                   f"{(ticket.get('title') or '')[:60]} — {probe_summary(results)[:160]}")
            _ntfy(msg)
        elif apply:
            _ntfy(f"liveness close FAILED wq#{tid}: {detail.get('close')}", priority="high")
        if log:
            log(f"wq#{tid} {rec['action']} {rec.get('close', '')}")
    elif verdict == liveness.UNKNOWN:
        rec["action"] = "flagged" if prev_verdict != liveness.UNKNOWN else "flag_deduped"
        if prev_verdict != liveness.UNKNOWN:
            if apply:
                _wq("note", tid, f"[liveness] UNKNOWN — left as-is, probe could not decide: "
                    f"{probe_summary(results)[:300]}", agent=agent)
            audit(rec)
            if log:
                log(f"wq#{tid} UNKNOWN flagged: {probe_summary(results)[:120]}")
    elif verdict == liveness.STILL_BLOCKED:
        rec["action"] = "kept"
        if prev_verdict not in (None, liveness.STILL_BLOCKED):
            audit(rec)  # verdict changed (e.g. SATISFIED->STILL_BLOCKED): worth a line
    elif verdict == capability.LIVENESS_NO_TAG:
        no_probe = "[NO-PROBE:" in (ticket.get("detail") or "")
        age_days = None if no_probe else _ticket_age_days(ticket)
        alarmed = state.setdefault("_no_tag_alarmed", {})
        if age_days is not None and age_days > UNTAGGED_AGE_DAYS and tid not in alarmed:
            if apply:
                _aq_untagged_gate(tid, ticket, age_days)
            alarmed[tid] = now_iso()
            rec["action"] = "age_alarmed"
            audit(rec)
            if log:
                log(f"wq#{tid} untagged gate {age_days:.0f}d old -> aq")
    return rec


# ---------------------------------------------------------------------------
# bindings -> tickets (§10 / §12.2)
# ---------------------------------------------------------------------------

def write_binding(ticket, bindings, agent=None, apply=True):
    """Append the ledger binding to the ticket detail as a `Liveness:` line when the
    ticket has none. Returns True when written."""
    tid = str(ticket["id"])
    detail = ticket.get("detail") or ""
    if "Liveness:" in detail or tid not in bindings:
        return False
    if not (apply and writes_enabled()):
        return False
    new_detail = detail.rstrip("\n") + "\nLiveness: " + bindings[tid]
    p = _wq("edit", tid, "--detail", new_detail, agent=agent)
    if p.returncode == 0:
        _wq("note", tid, f"[liveness] binding written from ledger: {bindings[tid]}", agent=agent)
        audit({"ticket": int(tid), "action": "binding_written", "binding": bindings[tid]})
        return True
    return False


# ---------------------------------------------------------------------------
# sweep (timer) — §12.3
# ---------------------------------------------------------------------------

def write_derived(ticket, agent=None, apply=True, ledger=None):
    """wq#1676 item 2 backfill: propose+write a Liveness: line from the ticket's own
    title/detail text via derive_probe(), for a ticket with neither a Liveness: line
    nor a ledger binding nor a --no-probe stamp. Returns the proposal string when
    written, else None (nothing parseable, already tagged, or apply=False)."""
    tid = str(ticket["id"])
    detail = ticket.get("detail") or ""
    if "Liveness:" in detail or "[NO-PROBE:" in detail:
        return None
    proposal = derive_probe(f"{ticket.get('title') or ''}\n{detail}", ledger)
    if not proposal:
        return None
    if not (apply and writes_enabled()):
        return proposal
    new_detail = detail.rstrip("\n") + "\nLiveness: " + proposal
    p = _wq("edit", tid, "--detail", new_detail, agent=agent)
    if p.returncode != 0:
        return None
    _wq("note", tid, f"[liveness] derived tag from ticket text: {proposal}", agent=agent)
    audit({"ticket": int(tid), "action": "derived_binding_written", "binding": proposal})
    return proposal


def sweep(apply=False, ids=None, db=None, ledger=None, agent=None, write_bindings=False,
          derive=False, log=None, state_path=None, scope="frank"):
    """scope='frank' (default, the timer) = the dispatcher's frank-gated predicate;
    scope='all' (wq#1492 backlog sweep) = every open|blocked|frank_gated|pending_review
    ticket. The per-ticket decision is identical either way (clear_if_stale).

    derive=True (wq#1676 item 2 backfill): for a ticket write_bindings left tagless,
    try derive_probe() on its own text before probing — report-only unless apply."""
    bindings = liveness_sweep.load_bindings(ledger or LEDGER)
    state = load_state(state_path)
    out = []
    tickets = liveness_sweep.tickets_in_scope(scope, db or liveness_sweep.WQ_DB, ids)
    for t in tickets:
        if write_bindings:
            if write_binding(t, bindings, agent=agent, apply=apply):
                t["detail"] = (t.get("detail") or "").rstrip("\n") + "\nLiveness: " + bindings[str(t["id"])]
        derived = None
        if derive and "Liveness:" not in (t.get("detail") or ""):
            derived = write_derived(t, agent=agent, apply=apply, ledger=ledger or LEDGER)
            if derived and apply:
                t["detail"] = (t.get("detail") or "").rstrip("\n") + "\nLiveness: " + derived
        rec = clear_if_stale(t, bindings, apply=apply, agent=agent, state=state, log=log)
        if derived:
            rec["derived_probe"] = derived
        out.append(rec)
    save_state(state, state_path)
    return out


# ---------------------------------------------------------------------------
# dispatcher hook — §12.4
# ---------------------------------------------------------------------------

def dispatch_hook(tid, ticket=None, fetch=None, log=None, ttl_min=None, state_path=None):
    """Called by the dispatcher right before it logs a frank_gated skip. Cheap: only a
    ticket already known to carry a liveness condition, with no verdict fresher than
    TTL, is re-probed. Returns True when the ticket was closed (caller skips its skip)."""
    if not writes_enabled() or liveness.disabled():
        return False
    tid = str(tid)
    ttl_min = TTL_MIN if ttl_min is None else ttl_min
    state = load_state(state_path)
    bindings = liveness_sweep.load_bindings()
    known = tid in state or tid in bindings
    if ticket is not None and "Liveness:" in (ticket.get("detail") or ""):
        known = True
    if not known:
        return False
    entry = state.get(tid) or {}
    if entry.get("status") == "done" or _fresh(entry, ttl_min):
        return False
    if ticket is None:
        if fetch is None:
            return False
        ticket = fetch(tid)
        if not ticket or not ticket.get("id"):
            return False
    rec = clear_if_stale(ticket, bindings, apply=True, state=state)
    save_state(state, state_path)
    if log:
        log("liveness", ticket=int(tid), verdict=rec["verdict"], action=rec["action"],
            detail=probe_summary([(p["probe"], p["args"], p["verdict"], p["detail"])
                                  for p in rec["probes"]])[:200])
    return rec["action"] == "closed"


# ---------------------------------------------------------------------------
# creation gate — §12.5 (called by /home/lumen/bin/wq cmd_add / cmd_decide)
# ---------------------------------------------------------------------------

def ledger_done_probes(ledger=None):
    try:
        import yaml
        with open(ledger or LEDGER) as f:
            doc = yaml.safe_load(f) or {}
    except Exception:
        return {}
    out = {}
    for name, rec in (doc.get("done_capabilities") or {}).items():
        for probe, arg in _parse(rec.get("probe", "")):
            out[(probe, arg)] = f"{name} since {rec.get('since', '?')}"
    return out


def _parse(text):
    import needs as needs_mod
    return list(needs_mod.parse_liveness(liveness_field=text))


def ledger_credentials(ledger=None):
    """path -> the ledger's own `probe:` string, e.g. cred_alive(path, verify_url)."""
    try:
        import yaml
        with open(ledger or LEDGER) as f:
            doc = yaml.safe_load(f) or {}
    except Exception:
        return {}
    out = {}
    for rec in (doc.get("credentials") or {}).values():
        path, probe = rec.get("path"), rec.get("probe")
        if path and probe:
            out[path] = probe
    return out


_DERIVE_TICKET_RE = re.compile(r"\bblocked on wq#(\d+)\b", re.IGNORECASE)
_DERIVE_PR_RE = re.compile(r"\b([\w.-]+/[\w.-]+)/pull/(\d+)\b")
_DERIVE_TOKEN_RE = re.compile(r"(/\S*\.secrets/\S+\.token)\b")


def derive_probe(text, ledger=None):
    """wq#1676 item 2: propose a `Liveness:` tag from patterns the ticket text already
    states. Returns a probe string ('probe(args)') or None when nothing parses. Only
    the three shapes where the match IS the stated precondition, not merely a path
    mentioned in passing: a `blocked on wq#N` reference, an `owner/repo/pull/N` URL,
    and a `.secrets/*.token` path resolved against the ledger's own credential record.

    A generic 'any absolute path -> path_exists' fallback was tried and DROPPED
    (wq#1676 backfill dry-run against the real 33 frank-gated tickets, 2026-09-10):
    real ticket prose routinely mentions an unrelated file/binary/doc path with no
    relation to the actual blocker (e.g. wq#339 'resubscribe your phone' mentions
    /home/lumen/security/ntfy-rename; wq#1121 a guard-fix ticket mentions
    /home/lumen/bin/lumen-pr) — auto-tagging those would be actively wrong, not just
    unhelpful. A human still can (`--liveness "path_exists(...)"`); the deriver won't
    guess it."""
    text = text or ""
    m = _DERIVE_TICKET_RE.search(text)
    if m:
        return f"ticket_done({m.group(1)})"
    m = _DERIVE_PR_RE.search(text)
    if m:
        # args are positional only (split_args does a plain comma split — no kwargs),
        # so a non-default forge means spelling out `want` too.
        forge = "github" if "github.com" in text else "gitea"
        return f"pr_merged({m.group(1)}, {m.group(2)}, merged, {forge})"
    m = _DERIVE_TOKEN_RE.search(text)
    if m:
        probe = ledger_credentials(ledger).get(m.group(1))
        if probe:
            return probe
    return None


def _no_tag_strict():
    """wq#1676 item 1: the tag becomes mandatory at the on-ramp, but NOT by silently
    flipping the shipped default — a same-day dry-run of this change (2026-09-10)
    against the real test suite showed `wq block`/`wq decide` callers all over
    fworker_integration/shim_matrix/token_injection/wq1479/wq1539 that never pass a
    tag; a hard default would refuse ordinary blocks fleet-wide today, not just in
    tests. So the default stays warn-only (unchanged from pre-1676) for the 7-day
    rollout window; FOREMAN_LIVENESS_CLEAR=strict is the explicit flip (set it once
    the rollout window has passed and callers have been updated). =warn is the
    explicit no-op spelling. FOREMAN_LIVENESS_LINT_MODE=strict|warn is the older,
    still-honored override, and wins either way."""
    lint = os.environ.get("FOREMAN_LIVENESS_LINT_MODE")
    if lint == "strict":
        return True
    if lint == "warn":
        return False
    return os.environ.get("FOREMAN_LIVENESS_CLEAR") == "strict"


def creation_check(title, detail, lane=None, frank_gated=False, ledger=None, run_probes=True):
    """(ok, verdict, message). ok=False means REFUSE the add/decide. Only Frank-gated
    shapes are checked; anything else returns (True, 'NOT_GATED', '')."""
    try:
        import foreman_dispatcher as fd
        human = (lane or "") in fd.HUMAN_LANES or bool(fd.FRANK_TITLE_RE.search(title or ""))
    except Exception:
        human = (lane or "").startswith("frank-") or (lane or "") in ("user", "user-projects")
    if not (human or frank_gated):
        return True, "NOT_GATED", ""
    import needs as needs_mod
    lv = list(needs_mod.parse_liveness(detail))
    if not lv:
        strict = _no_tag_strict()
        msg = ("Frank-gate has no `Liveness:` line — it can never auto-clear "
               "(LIVENESS_GATE.md §5: `Liveness: probe(args), ...` in the detail)")
        return (not strict), "NO_TAG", msg
    done = ledger_done_probes(ledger)
    covered = [f"{p}({a}) = {done[(p, a)]}" for p, a in lv if (p, a) in done]
    results = []
    if run_probes:
        for p, a in lv:
            v, d = liveness.run(p, a)
            results.append((p, a, v, d))
    verdict = liveness.aggregate([(v, d) for _, _, v, d in results]) if results else liveness.UNKNOWN
    if covered and len(covered) == len(lv):
        return False, "LEDGER_DONE", ("ledger records every probe as a done capability: "
                                      + "; ".join(covered) + " — file nothing"
                                      + (f" (live: {probe_summary(results)})" if results else ""))
    if verdict == liveness.SATISFIED:
        return False, "SATISFIED", ("premise already met on live state: " + probe_summary(results)
                                    + " — the gate would be stale the moment it was filed")
    return True, verdict, probe_summary(results)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--apply", action="store_true", help="write: close SATISFIED, flag UNKNOWN")
    ap.add_argument("--dry-run", action="store_true", help="(default) report only, zero writes")
    ap.add_argument("--write-bindings", action="store_true",
                    help="copy ledger bindings into tickets as Liveness: lines (with --apply)")
    ap.add_argument("--derive", action="store_true",
                    help="wq#1676: propose/write a Liveness: line from the ticket's own text "
                         "(derive_probe) for anything still tagless after --write-bindings")
    ap.add_argument("--ids", nargs="*", type=int)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--agent", default=None)
    ap.add_argument("--db", default=None)
    ap.add_argument("--ledger", default=None)
    ap.add_argument("--scope", choices=liveness_sweep.SCOPES, default="frank",
                    help="frank (default, what the timer runs) or all (every open|blocked|"
                         "frank_gated|pending_review ticket — wq#1492 backlog sweep)")
    a = ap.parse_args(argv)
    apply = bool(a.apply and not a.dry_run)
    if apply and not writes_enabled():
        print("FOREMAN_LIVENESS_CLEAR=0 — forced report-only", file=sys.stderr)
        apply = False
    lines = []
    recs = sweep(apply=apply, ids=a.ids, db=a.db, ledger=a.ledger, agent=a.agent,
                 write_bindings=a.write_bindings, derive=a.derive, log=lines.append, scope=a.scope)
    if a.json:
        print(json.dumps(recs, indent=1))
        return 0
    counts = {}
    for r in recs:
        counts[r["verdict"]] = counts.get(r["verdict"], 0) + 1
    mode = "APPLY" if apply else "DRY-RUN (no writes)"
    what = "frank-gated ticket(s)" if a.scope == "frank" else "backlog ticket(s) [scope=all]"
    print(f"liveness clear [{mode}]: {len(recs)} {what} "
          + " ".join(f"{k}={v}" for k, v in sorted(counts.items())))
    for r in recs:
        if r["verdict"] == capability.LIVENESS_NO_TAG:
            if r.get("derived_probe"):
                print(f"  wq#{r['ticket']:<5} {'NO_TAG':<13} derive-only  [{r['lane']}/{r['prior_status']}]"
                      f" proposal: Liveness: {r['derived_probe']} (--apply to write)")
            continue
        print(f"  wq#{r['ticket']:<5} {r['verdict']:<13} {r['action']:<12} [{r['lane']}/{r['prior_status']}]"
              f" {r.get('close', '')}")
        for p in r["probes"]:
            print(f"           {p['verdict']:<13} {p['probe']}({p['args']}) -> {p['detail']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
