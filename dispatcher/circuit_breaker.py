#!/usr/bin/env python3
"""circuit_breaker.py — dispatcher dead-man's switch (wq#718 AUTO-6).

Computes the fleet-wide fworker attempt failure rate over a trailing window from
harness/fworker.py's own attempt-N.json records (RUNS_ROOT/wq<id>/attempt-N.json, one
file per finished attempt, written once at completion — see self_heal.py's module
docstring for why this is the authoritative per-attempt ledger rather than a second
counter this module would have to keep in sync).

Two failure definitions coexist on purpose:
  - GENERAL_SUCCESS_CLASSES (SUCCESS/DEGRADED_SUCCESS = success, everything else in
    harness/classifier.py's verdict set = failure): the broad "did this attempt
    succeed" ops-health number — what dashboard_data.py shows as "failure rate".
  - CATASTROPHIC_CLASSES (LAUNCH_FAILURE/KILLED/OOM — harness/fworker.py's
    classify_block_reason() INFRA_CRASH bucket, wq#725): the narrow "something is
    systemically broken" signal this breaker actually trips on. Calibrated against
    this fleet's own history at build time (2026-08-22): the broad definition's
    ambient rate over the trailing 24h was ~77% (111/145 attempts, dominated by
    ordinary TURNS/FALSE_COMPLETE outcomes on hard multi-attempt tickets) — a
    threshold under "always tripped" would be meaningless against that baseline. The
    narrow definition's ambient rate over the same window was ~36% (52/145, all
    KILLED) — high, but this fleet's whole history is heavy iteration; the point is
    the gap between "ambient" and "threshold" needs real margin so ordinary bad
    stretches don't trip the breaker and erode trust in it. See
    test_circuit_breaker.py for the fworker cross-check that keeps CATASTROPHIC_CLASSES
    from silently drifting out of sync with fworker.py's own taxonomy.

`check_and_trip()` is called by foreman_dispatcher.py at the very top of every pass —
before kill_switch()'s own DISARM check has any chance to matter and before config
load, so a broken config.json can never suppress a trip. When the window has enough
samples (MIN_SAMPLES) and the failure rate exceeds FAILURE_RATE_THRESHOLD, `disarm()`
touches DISPATCH_DISARM FIRST, unconditionally — the guardrail is explicit that this
MUST fail safe even if notify_sinks can't reach ntfy or Frank's desktop, so the disarm
write happens before either notification attempt and neither attempt's outcome can
undo it.

CLI:  circuit_breaker.py status              # print current window stats + armed state
      circuit_breaker.py test-ntfy           # send a clearly-labeled test critical
                                              # notification through both sinks (never
                                              # touches DISARM_PATH)
"""
import argparse
import json
import os
import re
import sys
import time

DISPATCHER_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, DISPATCHER_DIR)
import notify_sinks  # noqa: E402

FOREMAN_ROOT = os.environ.get("FOREMAN_ROOT", "/home/lumen/foreman")
RUNS_ROOT = os.environ.get("FOREMAN_RUNS_ROOT", os.path.join(FOREMAN_ROOT, "_runs"))
DISARM_PATH = os.environ.get("FOREMAN_DISPATCH_DISARM",
                             "/home/lumen/autonomy/DISPATCH_DISARM")

WINDOW_MINUTES = int(os.environ.get("FOREMAN_BREAKER_WINDOW_MIN", "90"))
MIN_SAMPLES = int(os.environ.get("FOREMAN_BREAKER_MIN_SAMPLES", "8"))
FAILURE_RATE_THRESHOLD = float(os.environ.get("FOREMAN_BREAKER_THRESHOLD", "0.75"))

# harness/classifier.py's verdict set — everything not listed here is a (broad) failure.
GENERAL_SUCCESS_CLASSES = {"SUCCESS", "DEGRADED_SUCCESS"}
# harness/fworker.py's classify_block_reason() INFRA_CRASH bucket (wq#725) — the only
# classifications this breaker's own trip decision counts as failure. See module
# docstring for why the broad GENERAL_SUCCESS_CLASSES definition is unsuitable here.
CATASTROPHIC_CLASSES = {"LAUNCH_FAILURE", "KILLED", "OOM"}

RUN_DIR_RE = re.compile(r"^wq(\d+)$")
ATTEMPT_FILE_RE = re.compile(r"^attempt-(\d+)\.json$")


def iter_attempts(runs_root=None):
    """Yield (finished_at: float epoch seconds, classification: str, ticket: int) for
    every readable attempt-N.json under runs_root. A corrupt or partial record is
    skipped, not fatal — one bad file must never blind the breaker to real signal."""
    runs_root = runs_root if runs_root is not None else RUNS_ROOT
    try:
        entries = os.listdir(runs_root)
    except OSError:
        return
    for name in entries:
        m = RUN_DIR_RE.match(name)
        if not m:
            continue
        d = os.path.join(runs_root, name)
        try:
            files = os.listdir(d)
        except OSError:
            continue
        for f in files:
            if not ATTEMPT_FILE_RE.match(f):
                continue
            try:
                with open(os.path.join(d, f)) as fh:
                    rec = json.load(fh)
            except (OSError, ValueError):
                continue
            finished = rec.get("finished_at")
            classification = rec.get("classification")
            if not isinstance(finished, (int, float)) or not classification:
                continue
            yield finished, classification, int(m.group(1))


def _windowed_rate(is_failure, window_minutes, now, runs_root):
    since = now - window_minutes * 60
    total = failures = 0
    for finished, classification, _tid in iter_attempts(runs_root):
        if finished < since or finished > now:
            continue
        total += 1
        if is_failure(classification):
            failures += 1
    rate = (failures / total) if total else 0.0
    return {"total": total, "failures": failures, "rate": rate,
            "window_minutes": window_minutes}


def failure_rate(window_minutes=None, now=None, runs_root=None):
    """The breaker's own trip-relevant rate: CATASTROPHIC_CLASSES only. {'total',
    'failures', 'rate', 'window_minutes'} over the trailing window ending at `now`
    (default: real time)."""
    window_minutes = window_minutes if window_minutes is not None else WINDOW_MINUTES
    now = now if now is not None else time.time()
    return _windowed_rate(lambda c: c in CATASTROPHIC_CLASSES, window_minutes, now,
                          runs_root)


def general_failure_rate(window_minutes, now=None, runs_root=None):
    """The broad "did this attempt succeed" rate dashboard_data.py reports — NOT what
    the breaker trips on (see module docstring)."""
    now = now if now is not None else time.time()
    return _windowed_rate(lambda c: c not in GENERAL_SUCCESS_CLASSES, window_minutes,
                          now, runs_root)


def is_armed(disarm_path=None):
    return not os.path.exists(disarm_path if disarm_path is not None else DISARM_PATH)


def evaluate(now=None):
    """(tripped: bool, stats: dict). Requires >= MIN_SAMPLES before a rate is trusted —
    one failed attempt out of one sample must never trip the breaker."""
    stats = failure_rate(now=now)
    tripped = stats["total"] >= MIN_SAMPLES and stats["rate"] > FAILURE_RATE_THRESHOLD
    return tripped, stats


def disarm(reason):
    """FAIL-SAFE ORDER: touch DISARM_PATH unconditionally first — this must succeed
    independent of whether any notification sink is reachable — then best-effort
    notify. Returns notify_sinks' delivery dict; a notify exception is swallowed here
    too so it can never mask (or appear to undo) the disarm that already happened."""
    d = os.path.dirname(DISARM_PATH)
    if d:
        os.makedirs(d, exist_ok=True)
    with open(DISARM_PATH, "a"):
        os.utime(DISARM_PATH, None)
    msg = (f"foreman-dispatcher circuit breaker TRIPPED: {reason}. "
           f"DISPATCH_DISARM set at {DISARM_PATH} — dispatch halted until Frank clears it.")
    try:
        return notify_sinks.notify_critical(msg, title="foreman circuit breaker TRIPPED")
    except Exception:  # noqa: BLE001 — notification is never load-bearing here
        return {"ntfy": False, "desktop": False}


def check_and_trip(now=None, dry_run=False):
    """Called once at the top of every dispatcher pass. dry_run mirrors
    foreman_dispatcher.py's --dry-run contract: evaluate and report, zero side effects
    (no DISARM write, no notification). Returns (tripped, stats)."""
    tripped, stats = evaluate(now)
    if tripped and not dry_run:
        reason = (f"{stats['failures']}/{stats['total']} attempts failed in the last "
                  f"{stats['window_minutes']}m (>{FAILURE_RATE_THRESHOLD:.0%} threshold)")
        disarm(reason)
    return tripped, stats


def cmd_status(_args):
    tripped, stats = evaluate()
    print(json.dumps({"armed": is_armed(), "tripped_now": tripped, **stats}, indent=2))
    return 0


def cmd_test_ntfy(_args):
    result = notify_sinks.notify_critical(
        "TEST (wq#718 build verification) — ignore: foreman circuit breaker critical "
        "notification path check. No action needed; DISPATCH_DISARM was NOT touched.",
        title="foreman circuit breaker: TEST")
    print(json.dumps(result))
    return 0 if result["ntfy"] else 1


def main(argv=None):
    ap = argparse.ArgumentParser(description="foreman dispatcher circuit breaker (wq#718)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status", help="print current window stats + armed state")
    sub.add_parser("test-ntfy", help="send a labeled test critical notification")
    a = ap.parse_args(argv)
    return {"status": cmd_status, "test-ntfy": cmd_test_ntfy}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
