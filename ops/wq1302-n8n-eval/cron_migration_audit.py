#!/usr/bin/env python3
"""wq#1302 — audit which fleet cron/timer jobs could move to a workflow engine.

Enumerates every systemd timer on this node, pairs it with the service it
activates, and scores that job against the blockers that stop it from being
expressed as an n8n (or Windmill/Temporal/…) workflow. A job with zero
blockers is one an HTTP Request + Schedule Trigger can replace outright; every
blocker names a capability the engine would have to be granted instead.

Read-only: runs `systemctl list-timers` and `systemctl cat`, nothing else.
Every ExecStart/Environment value is masked before it enters a record, so the
JSON output is safe to commit (instinct 3).

Usage:
  cron_migration_audit.py                      # live audit, markdown summary
  cron_migration_audit.py --json out.json      # also write the masked records
  cron_migration_audit.py --units-file f.txt   # offline: parse a captured
                                               # `systemctl cat` dump instead
"""
import argparse
import json
import os
import re
import subprocess
import sys

# --- masking ---------------------------------------------------------------

_SECRET_PREFIXES = re.compile(
    r"\b(ghp_|gho_|ghu_|ghs_|ghr_|github_pat_|sk-|xoxb-|xoxp-|glpat-|AKIA)"
    r"[A-Za-z0-9_\-]{6,}"
)
_SECRET_ASSIGN = re.compile(
    r"(?i)\b([A-Za-z0-9_]*(?:token|secret|password|passwd|apikey|api_key|key|pat)"
    r"[A-Za-z0-9_]*)\s*([=:])\s*(\"?)([^\s\"']{4,})"
)


def mask(text):
    """Redact anything credential-shaped. Applied to every captured value."""
    if not text:
        return text
    out = _SECRET_PREFIXES.sub(lambda m: m.group(1) + "REDACTED", text)
    out = _SECRET_ASSIGN.sub(lambda m: f"{m.group(1)}{m.group(2)}{m.group(3)}REDACTED", out)
    return out


# --- unit parsing ----------------------------------------------------------

_FRAGMENT_HEADER = re.compile(r"^#\s+(/\S+\.(?:timer|service))\s*$")


def parse_units(cat_output):
    """Split `systemctl cat a.timer b.service …` output into unit dicts.

    Returns {unit_name: {"path": str, "keys": {key: [values]}}}. Directive
    values are masked on the way in; nothing unmasked is ever retained.
    Drop-in overrides carry no fragment header, so their directives fold into
    the unit they follow — which is the effective config, and what we want.
    """
    units = {}
    current = None
    for line in cat_output.splitlines():
        header = _FRAGMENT_HEADER.match(line)
        if header:
            path = header.group(1)
            current = {"path": path, "keys": {}}
            units[os.path.basename(path)] = current
            continue
        if current is None or "=" not in line or line.lstrip().startswith("#"):
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        if not key or not key[0].isalpha():
            continue
        current["keys"].setdefault(key, []).append(mask(value.strip()))
    return units


def _first(unit, key, default=""):
    return unit["keys"].get(key, [default])[0] if unit else default


def schedule_of(timer_unit):
    keys = timer_unit["keys"] if timer_unit else {}
    for k in ("OnCalendar", "OnUnitActiveSec", "OnBootSec", "OnActiveSec"):
        if k in keys:
            return f"{k}={keys[k][0]}"
    return "unknown"


def build_jobs(units, timer_to_service):
    """Pair each timer with the service it activates -> job records."""
    jobs = []
    for timer_name, service_name in sorted(timer_to_service.items()):
        timer = units.get(timer_name)
        service = units.get(service_name)
        if timer is None and service is None:
            continue
        execs = (service["keys"].get("ExecStart", []) if service else [])
        jobs.append({
            "timer": timer_name,
            "service": service_name,
            "description": _first(service, "Description") or _first(timer, "Description"),
            "schedule": schedule_of(timer),
            "user": _first(service, "User", "root") or "root",
            "exec_start": execs,
            "environment": ((service["keys"].get("Environment", []) if service else [])
                            + (service["keys"].get("EnvironmentFile", []) if service else [])),
            "fragment_path": (service or timer)["path"],
        })
    return jobs


# --- classification --------------------------------------------------------

# Each blocker names a capability the workflow engine would have to be handed
# before it could run the job. `pattern` is matched against the joined
# ExecStart lines plus the unit name.
BLOCKERS = [
    ("root-privilege", "engine would need root on the node",
     None),
    ("systemd-control", "starts/stops/reads systemd units",
     r"\bsystemctl\b|\bjournalctl\b|\bloginctl\b"),
    ("container-control", "drives docker/podman",
     r"\bdocker\b|\bpodman\b|\bcompose\b"),
    ("local-state", "reads or writes node-local state (db/git/home paths)",
     r"\.db\b|sqlite|\bgit\b|/home/lumen/|/var/log|\.jsonl\b|\bwq\b"),
    ("self-watch", "is itself a watchdog/dead-man for other jobs",
     r"deadman|dead-man|watchdog|failed-units|selfheal|self-heal|tickle|beat|"
     r"guard|sentinel|drift|reconcile"),
    ("bridge-sensitive", "touches the network beyond loopback/tailnet HTTP",
     r"\bollama\b|\bpull\b|speedtest|\bping\b|\bnmap\b|snmp|\bwan\b|\bplug\b|"
     r"\bprobe\b|\bssh\b|\brsync\b|\bscp\b"),
    ("secret-handling", "handles credentials the engine would have to custody",
     r"\bpat\b|token|secret|credential|vault|gitleaks|\bauth\b|\bkey\b"),
    ("local-exec", "runs a local command, not an HTTP call",
     None),
]

_HTTP_CLIENTS = {"curl", "wget", "http", "httpie"}
_OS_UNIT_DIRS = ("/lib/systemd/", "/usr/lib/systemd/")
# --deep only reads scripts under roots the fleet owns.
DEEP_SCAN_ROOTS = ("/home/lumen/", "/opt/lumen/", "/usr/local/bin/")
DEEP_SCAN_BYTES = 65536
# Arguments, not scripts — reading them would tell you nothing about capability.
_DATA_SUFFIXES = {".json", ".jsonl", ".md", ".txt", ".log", ".yaml", ".yml",
                  ".csv", ".db", ".sqlite", ".sqlite3", ".tmp"}


def is_os_managed(job):
    """Distro-shipped units (apt, fstrim, logrotate…) are not fleet glue."""
    return job["fragment_path"].startswith(_OS_UNIT_DIRS)


def is_http_only(exec_lines):
    """True when every ExecStart is an HTTP client call against a URL.

    This is the one shape an n8n HTTP Request node replaces with no new
    capability: no local binary, no local filesystem, no shell.
    """
    if not exec_lines:
        return False
    for line in exec_lines:
        parts = line.split()
        if not parts or any(c in line for c in "|;&><`$"):
            return False
        argv0 = os.path.basename(parts[0].lstrip("-@:+!"))
        if argv0 not in _HTTP_CLIENTS:
            return False
        if not any(p.startswith("http://") or p.startswith("https://") for p in parts[1:]):
            return False
    return True


def script_bodies(job, read_file=None):
    """Bodies of the fleet-owned scripts a job's ExecStart invokes.

    ExecStart alone hides what a wrapper script does — `foo.py` says nothing
    about the systemctl call inside it — so the shallow pass systematically
    undercounts blockers. Only first-level scripts are read: what those scripts
    in turn invoke is not followed, so the deep counts stay a floor.

    Returns (masked_text, [paths it could not read]).
    """
    read_file = read_file or _read_text
    text, unreadable = [], []
    for line in job["exec_start"]:
        for token in line.split():
            path = token.lstrip("-@:+!").rstrip("'\",;)")
            if not path.startswith(DEEP_SCAN_ROOTS) or any(c in path for c in "'\"()"):
                continue
            if os.path.splitext(path)[1] in _DATA_SUFFIXES:
                continue
            body = read_file(path)
            if body is None:
                unreadable.append(path)
            else:
                text.append(mask(body))
    return "\n".join(text), unreadable


def _read_text(path):
    try:
        with open(path, "r", errors="replace") as fh:
            return fh.read(DEEP_SCAN_BYTES)
    except OSError:
        return None


def classify(job, body_text=""):
    """Return (category, [blocker ids]) for one job."""
    if is_os_managed(job):
        return "os-managed", []
    haystack = " ".join(job["exec_start"] + job.get("environment", []))
    haystack += " " + job["timer"] + " " + job["service"] + "\n" + body_text
    http_only = is_http_only(job["exec_start"])
    hits = []
    for blocker_id, _reason, pattern in BLOCKERS:
        if blocker_id == "root-privilege":
            if job["user"] == "root":
                hits.append(blocker_id)
            continue
        if blocker_id == "local-exec":
            if not http_only:
                hits.append(blocker_id)
            continue
        if re.search(pattern, haystack, re.IGNORECASE):
            hits.append(blocker_id)
    return ("movable" if not hits else "blocked"), hits


# --- collection ------------------------------------------------------------

def _run(argv):
    return subprocess.run(argv, capture_output=True, text=True, timeout=120).stdout


def collect_live():
    """(units, timer->service) straight from systemd. Read-only calls."""
    listing = _run(["systemctl", "list-timers", "--all", "--no-pager"])
    timer_to_service = parse_listing(listing)
    names = sorted(set(timer_to_service) | set(timer_to_service.values()))
    chunks = [names[i:i + 40] for i in range(0, len(names), 40)]
    cat_output = "".join(_run(["systemctl", "cat", "--no-pager"] + c) for c in chunks)
    return parse_units(cat_output), timer_to_service


def parse_listing(listing):
    """UNIT/ACTIVATES columns of `systemctl list-timers` -> {timer: service}."""
    mapping = {}
    for line in listing.splitlines():
        fields = line.split()
        timers = [f for f in fields if f.endswith(".timer")]
        services = [f for f in fields if f.endswith(".service")]
        if not timers:
            continue
        mapping[timers[0]] = services[0] if services else timers[0][:-6] + ".service"
    return mapping


# --- reporting -------------------------------------------------------------

def summarize(records):
    counts = {"movable": 0, "blocked": 0, "os-managed": 0}
    blocker_counts = {}
    for r in records:
        counts[r["category"]] = counts.get(r["category"], 0) + 1
        for b in r["blockers"]:
            blocker_counts[b] = blocker_counts.get(b, 0) + 1
    return counts, blocker_counts


def render(records):
    counts, blocker_counts = summarize(records)
    fleet = counts["movable"] + counts["blocked"]
    lines = [
        f"timers: {len(records)} total, {counts['os-managed']} distro-managed, {fleet} fleet glue",
        f"movable as pure HTTP workflow: {counts['movable']}/{fleet}",
        "",
        "| blocker | jobs | meaning |",
        "|---|---|---|",
    ]
    reasons = {b[0]: b[1] for b in BLOCKERS}
    for blocker_id, _reason, _p in BLOCKERS:
        n = blocker_counts.get(blocker_id, 0)
        lines.append(f"| {blocker_id} | {n} | {reasons[blocker_id]} |")
    movable = [r["timer"] for r in records if r["category"] == "movable"]
    unscanned = sorted({p for r in records for p in r.get("unscanned", [])})
    lines.append("")
    lines.append("movable: " + (", ".join(movable) if movable else "(none)"))
    if unscanned:
        lines.append(f"unreadable scripts (blockers may be undercounted): {len(unscanned)}")
    return "\n".join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--units-file", help="offline `systemctl cat` dump")
    ap.add_argument("--listing-file", help="offline `systemctl list-timers` dump")
    ap.add_argument("--json", help="write masked records here")
    ap.add_argument("--deep", action="store_true",
                    help="also scan the bodies of the fleet scripts each job runs")
    args = ap.parse_args(argv)

    if args.units_file:
        with open(args.units_file) as fh:
            units = parse_units(fh.read())
        if args.listing_file:
            with open(args.listing_file) as fh:
                timer_to_service = parse_listing(fh.read())
        else:
            timer_to_service = {n: n[:-6] + ".service" for n in units if n.endswith(".timer")}
    else:
        units, timer_to_service = collect_live()

    records = []
    for job in build_jobs(units, timer_to_service):
        body, unreadable = script_bodies(job) if args.deep else ("", [])
        category, blockers = classify(job, body)
        records.append(dict(job, category=category, blockers=blockers,
                            depth="body" if args.deep else "exec",
                            unscanned=sorted(set(unreadable))))

    if args.json:
        counts, blocker_counts = summarize(records)
        with open(args.json, "w") as fh:
            json.dump({"counts": counts, "blockers": blocker_counts, "jobs": records},
                      fh, indent=2, sort_keys=True)
            fh.write("\n")
    print(render(records))
    return 0


if __name__ == "__main__":
    sys.exit(main())
