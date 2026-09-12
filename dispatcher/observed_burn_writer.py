#!/usr/bin/env python3
"""observed_burn_writer.py — wq#1722. Timer-driven job (see
dispatcher/systemd/lumen-foreman-burnrate.{service,timer}) that keeps
dispatcher/observed_burn.json current, replacing the one-night measurement
burn_rate.py ships as a default (wq#1699 "Measurement method").

Each poll:
  1. reads governor state.json's limits[pool] = {percent, resets_at} (same shape
     burn_rate.pool_spend_rate reads);
  2. gathers cloud fworker unit lifetimes since the last poll from `lumen-run list`
     (units still running) + `journalctl` (units that already exited), resolving
     each unit's ticket -> model via concurrency.ticket_model so local-rung units
     (concurrency.is_local_unit) are excluded and fable units are split out for
     fable_weekly_scoped (concurrency.LOCAL_POOL_MODELS / burn_rate.pools_for_model's
     "fable" in model rule — see observed_burn_core.is_local_ticket/is_fable_ticket);
  3. hands (prev sample, percent, resets_at, worker_hours) to
     observed_burn_core.update_pool_sample for the EMA/reset-crossing/min-sample math;
  4. atomically writes both the internal sampler state (this module's own memory of
     "percent/resets_at/sampled_at/ema" per pool, across polls) and the public
     observed_burn.json burn_rate.load_observed() reads.

Frank's own interactive claude.ai use draws on the SAME session/weekly_all/fable
pools this measures (state.json's percent doesn't distinguish who spent it), which
inflates the measured %/worker-hour — the safe direction, since it makes the
controller more conservative, not less. Recorded in the written file's `_note`.

Usage:
  observed_burn_writer.py run --once   # one poll, exit (systemd timer invokes this)
  observed_burn_writer.py status       # print current sampler state + written file
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import subprocess
import sys

DISPATCHER_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.normpath(os.path.join(DISPATCHER_DIR, ".."))
sys.path.insert(0, DISPATCHER_DIR)
sys.path.insert(0, os.path.join(REPO_ROOT, "harness"))
import observed_burn_core as core  # noqa: E402
import concurrency                # noqa: E402
from burn_rate import PASS_POOLS, FABLE_POOL  # noqa: E402

FOREMAN_DIR = os.environ.get("FOREMAN_DIR", "/home/lumen/foreman")
GOVERNOR_STATE_PATH = os.path.join(FOREMAN_DIR, "state.json")
SAMPLER_STATE_PATH = os.environ.get("FOREMAN_OBSERVED_BURN_SAMPLER_STATE",
                                     os.path.join(DISPATCHER_DIR, "observed_burn_sampler_state.json"))
OUTPUT_PATH = os.environ.get("FOREMAN_OBSERVED_BURN",
                              os.path.join(DISPATCHER_DIR, "observed_burn.json"))
JOURNALCTL_BIN = os.environ.get("FOREMAN_JOURNALCTL_BIN", "journalctl")
LUMEN_RUN_BIN = os.environ.get("FOREMAN_LUMEN_RUN_BIN", concurrency.LUMEN_RUN_BIN)
POOLS = PASS_POOLS + (FABLE_POOL,)

# `lumen-job-fworker-wq<ticket>-<start_epoch>.service`, the exact name lumen-run
# start builds (bin/lumen-run:70: `unit="lumen-job-${slug}-$(date +%s)"`).
UNIT_RE = re.compile(r"lumen-job-(?P<name>fworker-wq\d+)-(?P<epoch>\d+)\.service")
# `journalctl -o short-unix` prefixes every line with "<secs>.<usecs> ".
SHORT_UNIX_RE = re.compile(r"^(?P<epoch>\d+)(?:\.\d+)?\s")

_NOTE = ("EMA of measured %/worker-hour per pool (dispatcher/observed_burn_writer.py, "
         "wq#1722), overriding burn_rate.py's one-night 2026-09-10 defaults. Frank's "
         "own interactive claude.ai use draws the same session/weekly_all/fable pools "
         "these percentages come from, so the measured rate overstates a single "
         "worker's true share — safe direction (more conservative spawn concurrency), "
         "never understates it.")


def log(msg):
    ts = dt.datetime.now(dt.timezone.utc).isoformat()
    print(f"[{ts}] {msg}", flush=True)


def atomic_write_json(path, obj):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = f"{path}.tmp.{os.getpid()}"
    with open(tmp, "w") as f:
        json.dump(obj, f, indent=2, default=str)
        f.write("\n")
    os.replace(tmp, path)


def load_json(path):
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return None


def parse_unit_lines(lines):
    """Any journalctl/`lumen-run list` text -> {unit_name: start_epoch}, from the
    epoch embedded in the transient unit's own name. Pure text parsing, independent
    of whichever command produced the lines — kept separate from subprocess calls so
    it's testable without a live systemd."""
    out = {}
    for line in lines:
        m = UNIT_RE.search(line)
        if m:
            out[m.group("name")] = int(m.group("epoch"))
    return out


def last_seen_epoch(lines):
    """Max short-unix timestamp across journalctl lines for one unit, or None if the
    unit never logged anything in the queried range (start == end, zero duration)."""
    best = None
    for line in lines:
        m = SHORT_UNIX_RE.match(line)
        if m:
            e = int(m.group("epoch"))
            best = e if best is None else max(best, e)
    return best


def running_units():
    """[(unit_name, start_epoch), ...] for units `lumen-run list` reports as
    running right now."""
    try:
        p = subprocess.run([LUMEN_RUN_BIN, "list"], capture_output=True, text=True, timeout=15)
    except (OSError, subprocess.TimeoutExpired) as e:
        log(f"WARN: lumen-run list failed: {e}")
        return []
    if p.returncode != 0:
        log(f"WARN: lumen-run list exited {p.returncode}")
        return []
    starts = parse_unit_lines(l for l in p.stdout.splitlines() if "running" in l)
    return list(starts.items())


def exited_units(since_iso):
    """[(unit_name, start_epoch, end_epoch), ...] for fworker units that logged
    anything since `since_iso` — catches units that started AND finished between
    polls, which `lumen-run list`'s live snapshot can never see."""
    try:
        p = subprocess.run([JOURNALCTL_BIN, "--since", since_iso, "-o", "short-unix",
                            "--no-pager"], capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired) as e:
        log(f"WARN: journalctl failed: {e}")
        return []
    if p.returncode != 0:
        log(f"WARN: journalctl exited {p.returncode}")
        return []
    by_unit = {}
    for line in p.stdout.splitlines():
        if "lumen-job-fworker-wq" not in line:
            continue
        m = UNIT_RE.search(line)
        if not m:
            continue
        name, start = m.group("name"), int(m.group("epoch"))
        ts = SHORT_UNIX_RE.match(line)
        end = int(ts.group("epoch")) if ts else start
        prev = by_unit.get(name)
        by_unit[name] = (start, end if prev is None else max(prev[1], end))
    return [(name, start, end) for name, (start, end) in by_unit.items()]


def gather_unit_series(since_iso, now_epoch):
    """Cloud fworker unit windows since `since_iso`: {"model", "start", "end"} per
    unit, ticket model resolved once per unit (concurrency.ticket_model). Currently
    running units get end=now (the last exit line hasn't been written yet)."""
    windows = {}
    for name, start in running_units():
        windows[name] = [start, now_epoch]
    for name, start, end in exited_units(since_iso):
        w = windows.get(name)
        if w is None:
            windows[name] = [start, end]
        else:
            w[0] = min(w[0], start)

    series = []
    for name, (start, end) in windows.items():
        m = concurrency.TICKET_UNIT_RE.match(name)
        model = concurrency.ticket_model(m.group("id")) if m else None
        series.append({"model": model, "start": start, "end": end})
    return series


def poll_once(now=None):
    now = now or dt.datetime.now(dt.timezone.utc)
    governor_state = load_json(GOVERNOR_STATE_PATH) or {}
    limits = governor_state.get("limits") or {}
    sampler_state = load_json(SAMPLER_STATE_PATH) or {}

    # Each pool's own worker-hours window starts at ITS OWN last sample (or a 1h
    # lookback the first time this ever runs) — never a window shared across pools.
    # A shared/global since_dt would re-count worker-hours a busy pool already
    # consumed in its last "updated" window whenever a quieter pool (e.g.
    # fable_weekly_scoped on a night with no fable tickets) is still stuck in
    # "insufficient_sample" and hasn't advanced its own sampled_at — inflating that
    # pool's window and silently pulling every OTHER pool's rate toward zero.
    default_since = now - dt.timedelta(hours=1)
    since_by_pool = {}
    earliest_since = default_since
    for pool in POOLS:
        sampled_at = (sampler_state.get(pool) or {}).get("sampled_at")
        d = core._parse_iso(sampled_at)
        since_by_pool[pool] = d if d is not None else default_since
        earliest_since = min(earliest_since, since_by_pool[pool])
    # journalctl/lumen-run are queried once, wide enough to cover every pool's window.
    since_iso = earliest_since.strftime("%Y-%m-%d %H:%M:%S")

    unit_series = gather_unit_series(since_iso, now.timestamp())

    events = {}
    for pool in POOLS:
        b = limits.get(pool) or {}
        percent, resets_at = b.get("percent"), b.get("resets_at")
        prev = sampler_state.get(pool)
        if percent is None or not resets_at:
            events[pool] = "no_governor_data"
            continue
        wh = core.worker_hours(unit_series, since_by_pool[pool].timestamp(), now.timestamp(), pool)
        sample, event = core.update_pool_sample(prev, float(percent), resets_at, wh, now)
        sampler_state[pool] = sample
        events[pool] = event

    atomic_write_json(SAMPLER_STATE_PATH, sampler_state)

    observed = {pool: sampler_state[pool]["ema"] for pool in POOLS
                if sampler_state.get(pool) and sampler_state[pool].get("ema") is not None}
    observed["_note"] = _NOTE
    observed["_updated_at"] = now.isoformat()
    observed["_events"] = events
    atomic_write_json(OUTPUT_PATH, observed)
    log(f"poll_once: events={events} written={OUTPUT_PATH}")
    return observed


def cmd_run(args):
    poll_once()
    return 0


def cmd_status(args):
    print(json.dumps({"sampler_state": load_json(SAMPLER_STATE_PATH),
                       "observed_burn": load_json(OUTPUT_PATH)}, indent=2))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description="observed_burn.json writer (wq#1722)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="run one poll and exit")
    r.add_argument("--once", action="store_true",
                   help="documentational: a poll is always one-shot; the cadence lives in the systemd timer")
    sub.add_parser("status", help="print sampler state + the last written observed_burn.json")
    a = ap.parse_args(argv)
    return {"run": cmd_run, "status": cmd_status}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
