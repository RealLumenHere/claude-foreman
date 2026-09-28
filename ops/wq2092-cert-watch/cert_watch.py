#!/usr/bin/env python3
"""cert_watch.py — wq#2092.

Gatus probes fleet HTTPS endpoints for health but nothing watches the leaf
certificate itself, so a cert that lapses (or a renewal that silently stops)
is invisible until browsers start refusing the host. This opens a TLS
connection to each endpoint, reads the leaf cert's notAfter, and alerts when
it crosses the 21 / 7 / 1 day thresholds (or has already expired).

Targets (the planner's "no endpoint inventory" gap, closed the same way
/home/lumen/bin/ui-inventory does it):
  * every non-wildcard `hostname:` in the cloudflared ingress
    (/etc/cloudflared/config.yml, direct read then `sudo -n cat`), port 443;
  * extra internal HTTPS endpoints from targets.json ("extra": [{"host",
    "port"}]) and/or --extra host[:port]. Empty by default: no internal
    nginx TLS listener has a verified host:port yet, so none are invented.

Verification is disabled on purpose: the point is to read notAfter from an
expired or self-signed leaf, not to be refused by it.

Transition-based (kb/ISSUE-LOG.md #10 pattern, as ops/wq1397-gitea-version-check):
an alert fires only when a host moves to a *more severe* tier than last
alerted, so a standing 7-day warning pages once, not every run; a renewal
clears the tier and sends a "renewed" notice. An unreachable run carries the
last known tier/not_after/aq_id forward (wq#2983) so it cannot forge either
transition. Alerts go to the aq queue
(source=cert-watch, dedup per host) and to ntfy. Read-only: no cert or
endpoint is touched; state is one JSON file.

Usage:
  cert_watch.py run [--cloudflared-config PATH] [--targets PATH]
                    [--extra HOST[:PORT]]... [--state PATH] [--dry-run]
"""
import argparse
import json
import os
import re
import socket
import ssl
import subprocess
import sys
import tempfile
import time

DEFAULT_CF_CONFIG = "/etc/cloudflared/config.yml"
DEFAULT_TARGETS = "/home/lumen/services/lumen-cert-watch/targets.json"
DEFAULT_STATE = "/home/lumen/logs/.lumen-cert-watch.state.json"
NTFY_BIN = "/home/lumen/bin/ntfy-send"
AQ_DIR = os.environ.get("CERT_WATCH_AQ_DIR", "/home/lumen/agents/alert-queue")
SOURCE = "cert-watch"

# (days-left ceiling, tier name, severity, ntfy priority). Most severe first.
TIERS = [
    (0, "expired", 4, "urgent"),
    (1, "1d", 3, "urgent"),
    (7, "7d", 2, "high"),
    (21, "21d", 1, "default"),
]
SEVERITY = {name: sev for _, name, sev, _ in TIERS}
SEVERITY["ok"] = 0
PRIORITY = {name: prio for _, name, _, prio in TIERS}

_HOSTNAME_RE = re.compile(r"^\s*-?\s*hostname:\s*['\"]?([^\s'\"#]+)")


def classify(days_left):
    for ceiling, name, _, _ in TIERS:
        if days_left <= ceiling:
            return name
    return "ok"


def parse_hostnames(text):
    """Ingress hostnames from cloudflared config text; wildcards skipped
    (a wildcard rule names no concrete host to dial)."""
    hosts = []
    for line in text.splitlines():
        m = _HOSTNAME_RE.match(line)
        if m and "*" not in m.group(1) and m.group(1) not in hosts:
            hosts.append(m.group(1))
    return hosts


def read_cloudflared_hosts(path):
    """Returns (hosts, error). Mirrors ui-inventory: sudo -n cat first, then
    a direct read."""
    text = ""
    try:
        r = subprocess.run(["sudo", "-n", "cat", path], capture_output=True,
                           text=True, timeout=15)
        if r.returncode == 0:
            text = r.stdout
    except (OSError, subprocess.TimeoutExpired):
        pass
    if not text:
        try:
            with open(path) as f:
                text = f.read()
        except OSError as e:
            return [], f"cloudflared config unreadable: {e}"
    return parse_hostnames(text), None


def parse_endpoint(spec, default_port=443):
    host, sep, port = spec.rpartition(":")
    if not sep:
        return spec, default_port
    return host, int(port)


def load_extra_targets(path):
    try:
        with open(path) as f:
            doc = json.load(f)
    except OSError:
        return []
    return [(e["host"], int(e.get("port", 443))) for e in doc.get("extra", [])]


def fetch_not_after(host, port, timeout=10):
    """Returns (not_after_epoch, error) for the leaf cert served on host:port."""
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    try:
        with socket.create_connection((host, port), timeout=timeout) as sock:
            with ctx.wrap_socket(sock, server_hostname=host) as tls:
                der = tls.getpeercert(binary_form=True)
    except (OSError, ssl.SSLError) as e:
        return None, f"{type(e).__name__}: {e}"
    if not der:
        return None, "no peer certificate"
    # CERT_NONE makes getpeercert() return {}; decode the DER ourselves.
    with tempfile.NamedTemporaryFile("w", suffix=".pem") as f:
        f.write(ssl.DER_cert_to_PEM_cert(der))
        f.flush()
        try:
            not_after = ssl._ssl._test_decode_cert(f.name)["notAfter"]
        except (KeyError, ValueError) as e:
            return None, f"cert decode failed: {e}"
    return ssl.cert_time_to_seconds(not_after), None


def load_state(path):
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save_state(path, state):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(state, f, indent=2, sort_keys=True)
    os.replace(tmp, path)


def ntfy(title, message, priority="default", dry_run=False):
    if dry_run:
        print(f"[dry-run] ntfy {priority} {title!r}: {message}")
        return True
    try:
        subprocess.run([NTFY_BIN, "-p", priority, "-T", title, message],
                       capture_output=True, text=True, timeout=15)
        return True
    except (OSError, subprocess.TimeoutExpired):
        return False


def aq_mod():
    sys.path.insert(0, AQ_DIR)
    import aq
    return aq


def aq_ingest(key, title, message, priority):
    """Files the alert in the aq queue; returns the row id, or None when the
    queue is unavailable (ntfy still fires, so the alert is not lost)."""
    try:
        return aq_mod().ingest(source=SOURCE, key=key, title=title,
                               message=message, priority=priority)
    except Exception as e:  # noqa: BLE001 — a queue fault must not stop the sweep
        print(f"cert-watch: aq ingest failed for {key}: {e}", file=sys.stderr)
        return None


def aq_resolve(rid, note):
    try:
        aq_mod().set_status(rid, "resolved", note)
    except Exception as e:  # noqa: BLE001
        print(f"cert-watch: aq resolve failed for #{rid}: {e}", file=sys.stderr)


def check_host(host, port, prev, now, dry_run):
    """Evaluates one endpoint against its previous state entry. Returns the
    new state entry, alerting on a move to a more severe tier."""
    not_after, error = fetch_not_after(host, port)
    if error:
        # wq#2983: a failed fetch says nothing about the cert, so the entry keeps
        # the last known tier/not_after/aq_id and only records the error. Dropping
        # them reset prev_sev to 0, which re-paged the standing alert on the next
        # good run and orphaned the aq row when the next good run was a renewal.
        entry = dict(prev)
        entry["error"] = error
        entry["error_count"] = prev.get("error_count", 0) + 1
        entry.setdefault("tier", "unreachable")  # never seen good: no tier to keep
        return entry

    days_left = (not_after - now) / 86400
    tier = classify(days_left)
    entry = {"tier": tier, "not_after": not_after, "days_left": round(days_left, 2)}
    prev_tier = prev.get("tier", "ok")
    prev_sev = SEVERITY.get(prev_tier, 0)  # unreachable/unknown -> no baseline
    endpoint = f"{host}:{port}"

    if SEVERITY[tier] > prev_sev:
        when = time.strftime("%Y-%m-%d", time.gmtime(not_after))
        if tier == "expired":
            title = f"cert-watch: {endpoint} certificate EXPIRED"
            msg = f"{endpoint} leaf cert expired {when} ({-days_left:.1f} days ago)"
        else:
            title = f"cert-watch: {endpoint} cert expires in {days_left:.1f}d"
            msg = (f"{endpoint} leaf cert expires {when} "
                   f"({days_left:.1f} days left, threshold {tier})")
        rid = None if dry_run else aq_ingest(endpoint, title, msg, PRIORITY[tier])
        ntfy(title, msg, priority=PRIORITY[tier], dry_run=dry_run)
        entry["aq_id"] = rid
    else:
        entry["aq_id"] = prev.get("aq_id")
        if tier == "ok" and prev_sev > 0:
            ntfy(f"cert-watch: {endpoint} renewed",
                 f"{endpoint} leaf cert now valid {days_left:.0f} more days",
                 priority="default", dry_run=dry_run)
            if entry["aq_id"] and not dry_run:
                aq_resolve(entry["aq_id"], "cert renewed")
            entry["aq_id"] = None
    return entry


def run(cf_config=DEFAULT_CF_CONFIG, targets_path=DEFAULT_TARGETS, extra=(),
        state_path=DEFAULT_STATE, dry_run=False, now=None):
    now = time.time() if now is None else now
    state = load_state(state_path)
    prev_hosts = state.get("hosts", {})

    hosts, cf_error = read_cloudflared_hosts(cf_config)
    targets = [(h, 443) for h in hosts]
    targets += load_extra_targets(targets_path)
    targets += [parse_endpoint(e) for e in extra]
    targets = list(dict.fromkeys(targets))

    if cf_error and state.get("cf_error") != cf_error:
        ntfy("cert-watch: cannot enumerate cloudflared hosts", cf_error,
             priority="default", dry_run=dry_run)
    if not targets:
        print(f"cert-watch: no targets ({cf_error or 'empty ingress'})")
        return 1

    new_hosts, newly_unreachable = {}, []
    for host, port in targets:
        key = f"{host}:{port}"
        entry = check_host(host, port, prev_hosts.get(key, {}), now, dry_run)
        new_hosts[key] = entry
        # error_count is the run of consecutive failures, so ==1 is the transition
        # (the tier no longer changes on a failed fetch — see check_host).
        if entry.get("error") and entry["error_count"] == 1:
            newly_unreachable.append(f"{key} ({entry['error']})")
        stale = f" error={entry['error']}" if entry.get("error") else ""
        print(f"cert-watch: {key} tier={entry['tier']} "
              f"days_left={entry.get('days_left', '-')}{stale}")

    # One aggregated notice: cloudflared down makes every host unreachable at once.
    if newly_unreachable:
        ntfy(f"cert-watch: {len(newly_unreachable)} endpoint(s) unreachable",
             "could not read cert from: " + "; ".join(newly_unreachable),
             priority="default", dry_run=dry_run)

    if not dry_run:
        save_state(state_path, {"hosts": new_hosts, "cf_error": cf_error})
    return 0  # the alerts carry the signal; a warning must not fail the unit


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)
    p_run = sub.add_parser("run", help="check every endpoint's cert expiry, alert on thresholds")
    p_run.add_argument("--cloudflared-config", default=DEFAULT_CF_CONFIG)
    p_run.add_argument("--targets", default=DEFAULT_TARGETS)
    p_run.add_argument("--extra", action="append", default=[], metavar="HOST[:PORT]")
    p_run.add_argument("--state", default=DEFAULT_STATE)
    p_run.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if args.cmd == "run":
        return run(cf_config=args.cloudflared_config, targets_path=args.targets,
                   extra=args.extra, state_path=args.state, dry_run=args.dry_run)


if __name__ == "__main__":
    sys.exit(main())
