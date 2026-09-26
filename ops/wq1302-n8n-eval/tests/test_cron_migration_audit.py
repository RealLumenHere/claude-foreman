#!/usr/bin/env python3
"""Tests for the wq#1302 cron migration audit.

Offline: no systemctl, no network. Every scenario is a synthetic
`systemctl cat` corpus, including one job that IS movable — a classifier that
can only say "blocked" would prove nothing about the fleet.

Run: python3 tests/test_cron_migration_audit.py
"""
import importlib.util
import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
MOD_PATH = os.path.join(HERE, "..", "cron_migration_audit.py")
_spec = importlib.util.spec_from_file_location("cron_migration_audit", MOD_PATH)
audit = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(audit)

_fails = []


def check(name, cond, extra=""):
    print(f"  {'PASS' if cond else 'FAIL'} {name}" + (f" — {extra}" if extra and not cond else ""))
    if not cond:
        _fails.append(name)


CORPUS = """\
# /etc/systemd/system/fleet-http-poll.timer
[Timer]
OnCalendar=*:0/15

# /etc/systemd/system/fleet-http-poll.service
[Unit]
Description=poll a remote status API
[Service]
User=lumen
ExecStart=/usr/bin/curl -sf https://status.example.com/api/v1/summary

# /etc/systemd/system/fleet-piped-poll.timer
[Timer]
OnUnitActiveSec=10min

# /etc/systemd/system/fleet-piped-poll.service
[Service]
User=lumen
ExecStart=/usr/bin/curl -sf https://status.example.com/x | /usr/bin/jq .

# /etc/systemd/system/fleet-restart-guard.timer
[Timer]
OnBootSec=5min

# /etc/systemd/system/fleet-restart-guard.service
[Service]
User=root
Environment=FLEET_API_TOKEN=ghp_""" + """abcdefghijklmnopqrstuvwxyz0123456789
ExecStart=/usr/bin/systemctl restart lumen-thing.service

# /etc/systemd/system/fleet-wrapper.timer
[Timer]
OnCalendar=hourly

# /etc/systemd/system/fleet-wrapper.service
[Service]
User=lumen
ExecStart=/home/lumen/bin/opaque-wrapper.sh --once

# /usr/lib/systemd/system/apt-daily.timer
[Timer]
OnCalendar=*-*-* 6:00

# /usr/lib/systemd/system/apt-daily.service
[Service]
ExecStart=/usr/lib/apt/apt.systemd.daily update
"""

LISTING = """\
NEXT                        LEFT   LAST                        PASSED   UNIT                       ACTIVATES
Thu 2026-09-03 02:29:53 UTC 183ms  Thu 2026-09-03 02:24:53 UTC 4min ago fleet-http-poll.timer      fleet-http-poll.service
Thu 2026-09-03 02:30:00 UTC 6s     Thu 2026-09-03 02:20:00 UTC 9min ago fleet-piped-poll.timer     fleet-piped-poll.service
Thu 2026-09-03 02:31:00 UTC 1min   Thu 2026-09-03 02:21:00 UTC 9min ago fleet-restart-guard.timer  fleet-restart-guard.service
Thu 2026-09-03 02:32:00 UTC 2min   Thu 2026-09-03 02:22:00 UTC 9min ago fleet-wrapper.timer        fleet-wrapper.service
Thu 2026-09-03 06:00:00 UTC 3h     Wed 2026-09-02 06:00:00 UTC 20h ago  apt-daily.timer            apt-daily.service
n/a                         n/a    n/a                         n/a      orphan-no-activates.timer
"""


def records_from(corpus=CORPUS, listing=LISTING, deep_bodies=None):
    units = audit.parse_units(corpus)
    mapping = audit.parse_listing(listing)
    out = {}
    for job in audit.build_jobs(units, mapping):
        body = ""
        if deep_bodies is not None:
            body, _ = audit.script_bodies(job, read_file=lambda p: deep_bodies.get(p))
        category, blockers = audit.classify(job, body)
        out[job["timer"]] = dict(job, category=category, blockers=blockers)
    return out


def test_parsing():
    print("parsing")
    units = audit.parse_units(CORPUS)
    check("every unit block parsed", len(units) == 10, str(sorted(units)))
    svc = units["fleet-http-poll.service"]
    check("ExecStart captured",
          svc["keys"]["ExecStart"] == ["/usr/bin/curl -sf https://status.example.com/api/v1/summary"])
    check("User captured", svc["keys"]["User"] == ["lumen"])
    check("fragment path kept", svc["path"] == "/etc/systemd/system/fleet-http-poll.service")
    timer = units["fleet-http-poll.timer"]
    check("schedule read off the timer", audit.schedule_of(timer) == "OnCalendar=*:0/15")

    mapping = audit.parse_listing(LISTING)
    check("timer -> service from ACTIVATES",
          mapping["fleet-http-poll.timer"] == "fleet-http-poll.service")
    check("missing ACTIVATES falls back to same-name service",
          mapping["orphan-no-activates.timer"] == "orphan-no-activates.service")
    check("header row is not a timer", "UNIT" not in mapping)


def test_masking():
    print("masking")
    check("github token masked",
          "ghp_" + "abcdefghijklmnopqrstuvwxyz0123456789" not in audit.mask(
              "Environment=T=ghp_" + "abcdefghijklmnopqrstuvwxyz0123456789"))
    check("assignment form masked",
          audit.mask("--api-key=s3cr3tvalue").endswith("REDACTED"))
    check("password assignment masked", "hunter2000" not in audit.mask("DB_PASSWORD: " + "hunter2000"))
    check("ordinary text untouched",
          audit.mask("/usr/bin/curl -sf https://x/y") == "/usr/bin/curl -sf https://x/y")

    units = audit.parse_units(CORPUS)
    stored = json.dumps(units["fleet-restart-guard.service"]["keys"])
    check("parse_units stores only masked values", "ghp_abcdef" not in stored, stored)


def test_classification():
    print("classification")
    recs = records_from()
    check("pure HTTP job is movable", recs["fleet-http-poll.timer"]["category"] == "movable",
          str(recs["fleet-http-poll.timer"]["blockers"]))
    check("piped HTTP job needs a shell",
          recs["fleet-piped-poll.timer"]["blockers"] == ["local-exec"])
    guard = recs["fleet-restart-guard.timer"]
    check("root job flagged", "root-privilege" in guard["blockers"])
    check("systemctl job flagged", "systemd-control" in guard["blockers"])
    check("token env flagged", "secret-handling" in guard["blockers"])
    check("distro unit is out of scope", recs["apt-daily.timer"]["category"] == "os-managed")
    check("os-managed carries no blockers", recs["apt-daily.timer"]["blockers"] == [])
    check("wrapper looks clean to the shallow pass",
          recs["fleet-wrapper.timer"]["blockers"] == ["local-state", "local-exec"],
          str(recs["fleet-wrapper.timer"]["blockers"]))


def test_deep_scan():
    print("deep scan")
    bodies = {
        "/home/lumen/bin/opaque-wrapper.sh":
            "#!/bin/bash\ndocker restart sentinel\nollama pull llama3\n"
            "TOKEN=ghp_" + "zzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzz\n",
    }
    recs = records_from(deep_bodies=bodies)
    blockers = recs["fleet-wrapper.timer"]["blockers"]
    check("body reveals container control", "container-control" in blockers, str(blockers))
    check("body reveals bridge-sensitive pull", "bridge-sensitive" in blockers, str(blockers))
    check("body reveals credential handling", "secret-handling" in blockers, str(blockers))

    job = {"exec_start": ["/home/lumen/bin/opaque-wrapper.sh --once"],
           "timer": "t.timer", "service": "t.service"}
    text, unreadable = audit.script_bodies(job, read_file=lambda p: bodies.get(p))
    check("body text is masked", "ghp_zzzz" not in text, text)
    check("nothing unreadable here", unreadable == [])

    missing = {"exec_start": ["/home/lumen/bin/gone.sh"], "timer": "t.timer", "service": "t.service"}
    _, unreadable = audit.script_bodies(missing, read_file=lambda p: None)
    check("unreadable script recorded", unreadable == ["/home/lumen/bin/gone.sh"])

    outside = {"exec_start": ["/etc/shadow-ish/thing.sh"], "timer": "t.timer", "service": "t.service"}
    scanned = []
    audit.script_bodies(outside, read_file=lambda p: scanned.append(p))
    check("deep scan stays inside fleet-owned roots", scanned == [], str(scanned))

    noise = {"exec_start": ["/home/lumen/bin/x.sh --out /home/lumen/state/payload.json "
                           "--doc /home/lumen/kb/README.md).\""],
             "timer": "t.timer", "service": "t.service"}
    seen = []
    _, unreadable = audit.script_bodies(noise, read_file=lambda p: seen.append(p) or "#!/bin/sh\n")
    check("data arguments are not mistaken for scripts", seen == ["/home/lumen/bin/x.sh"], str(seen))
    check("quoted prose paths are not reported unreadable", unreadable == [], str(unreadable))


def test_cli_end_to_end():
    print("cli")
    with tempfile.TemporaryDirectory() as tmp:
        units_file = os.path.join(tmp, "units.txt")
        listing_file = os.path.join(tmp, "listing.txt")
        json_file = os.path.join(tmp, "out.json")
        with open(units_file, "w") as fh:
            fh.write(CORPUS)
        with open(listing_file, "w") as fh:
            fh.write(LISTING)
        rc = audit.main(["--units-file", units_file, "--listing-file", listing_file,
                         "--json", json_file])
        check("exit 0", rc == 0)
        with open(json_file) as fh:
            payload = json.load(fh)
        check("counts add up",
              payload["counts"]["movable"] + payload["counts"]["blocked"]
              + payload["counts"]["os-managed"] == len(payload["jobs"]))
        check("one movable in the fixture", payload["counts"]["movable"] == 1)
        check("blocker tally matches records",
              payload["blockers"]["local-exec"]
              == sum("local-exec" in j["blockers"] for j in payload["jobs"]))


def test_committed_inventory():
    print("committed evidence")
    path = os.path.join(HERE, "..", "inventory.json")
    with open(path) as fh:
        raw = fh.read()
    payload = json.loads(raw)
    counts = payload["counts"]
    fleet = counts["movable"] + counts["blocked"]
    check("inventory is the deep pass",
          all(j["depth"] == "body" for j in payload["jobs"] if j["category"] != "os-managed"))
    check("counts match the job list",
          fleet + counts["os-managed"] == len(payload["jobs"]))
    check("README headline holds: no fleet job is pure-HTTP movable", counts["movable"] == 0)
    check("every fleet job carries at least one blocker",
          all(j["blockers"] for j in payload["jobs"] if j["category"] != "os-managed"))
    check("no credential-shaped strings committed",
          audit.mask(raw) == raw)


def main():
    for fn in (test_parsing, test_masking, test_classification, test_deep_scan,
               test_cli_end_to_end, test_committed_inventory):
        fn()
    print(f"\n{'FAILED: ' + ', '.join(_fails) if _fails else 'all checks passed'}")
    return 1 if _fails else 0


if __name__ == "__main__":
    sys.exit(main())
