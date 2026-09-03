#!/usr/bin/env python3
"""gitea_version_check.py — wq#1397.

The fleet's Gitea (Anubis, http://100.121.17.5:3000) was bumped to 1.27.3
to fix CVE-2026-66877, but nothing kept checking that it *stayed* there —
a rollback, a host rebuild from an older snapshot, or a manual `git pull`
of an old release tarball would silently reintroduce the CVE with no
signal until someone thought to check by hand (this is exactly the gap
wq#1396's promoter README calls out: security debt "rediscovered... every
cycle" because nothing closes the loop).

This is the closing loop: query the live instance's own version endpoint
(GET /api/v1/version, unauthenticated, read-only) and alert if it is below
the CVE-patched floor. Complements ops/wq859-renovate-gitea (which bumps
image/package tags *in* repos) — this checks the Gitea application itself.

Transition-based (kb/ISSUE-LOG.md #10 pattern, same as
ops/wq1298-gitleaks-detect): alerts only on a change in vulnerable/safe
status, so a standing regression pages once, not every cadence, and a fix
gets an explicit recovery notice instead of silently going quiet.

Usage:
  gitea_version_check.py run [--url URL] [--min-version X.Y.Z] [--state PATH] [--dry-run]
"""
import argparse
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request

DEFAULT_URL = "http://100.121.17.5:3000"
# CVE-2026-66877 fix floor (wq#1397 / minerva proposal p-cve).
DEFAULT_MIN_VERSION = "1.27.3"
DEFAULT_STATE = "/home/lumen/logs/.gitea-version-check.state.json"
NTFY_BIN = "/home/lumen/bin/ntfy-send"


def parse_version(v):
    """'1.27.3' -> (1, 27, 3). Drops any non-numeric suffix (e.g. '-dev',
    '+gitea-1.27.3' style build metadata) from the trailing component."""
    core = v.split("+")[0].split("-")[0]
    parts = []
    for p in core.split("."):
        digits = "".join(ch for ch in p if ch.isdigit())
        parts.append(int(digits) if digits else 0)
    while len(parts) < 3:
        parts.append(0)
    return tuple(parts[:3])


def fetch_version(url, timeout=15):
    """Returns (version_string, error). error is None on success."""
    req = urllib.request.Request(f"{url.rstrip('/')}/api/v1/version")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            body = json.loads(r.read().decode("utf-8"))
        version = body.get("version")
        if not version:
            return None, "response had no 'version' field"
        return version, None
    except (urllib.error.URLError, urllib.error.HTTPError) as e:
        return None, str(e)
    except (ValueError, OSError) as e:
        return None, f"bad response: {e}"


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


def ntfy(title, message, priority="high", dry_run=False):
    if dry_run:
        print(f"[dry-run] ntfy {priority} {title!r}: {message}")
        return True
    try:
        subprocess.run([NTFY_BIN, "-p", priority, "-T", title, message],
                        capture_output=True, text=True, timeout=15)
        return True
    except (OSError, subprocess.TimeoutExpired):
        return False


def run(url=DEFAULT_URL, min_version=DEFAULT_MIN_VERSION, state_path=DEFAULT_STATE,
        dry_run=False):
    state = load_state(state_path)
    prev_status = state.get("status")

    version, error = fetch_version(url)
    if error:
        if prev_status != "unreachable":
            ntfy("gitea-version-check: unreachable",
                 f"{url}/api/v1/version -- {error}", priority="default", dry_run=dry_run)
        if not dry_run:
            state["status"] = "unreachable"
            save_state(state_path, state)
        print(f"gitea-version-check: unreachable ({error})")
        return 1

    safe = parse_version(version) >= parse_version(min_version)
    status = "safe" if safe else "vulnerable"

    if status != prev_status:
        if status == "vulnerable":
            ntfy("gitea-version-check: CVE-2026-66877 regression",
                 f"Gitea at {url} reports version {version}, below the patched "
                 f"floor {min_version} -- CVE-2026-66877 is unpatched on this instance",
                 priority="high", dry_run=dry_run)
        elif prev_status is not None:
            # Don't fire a "recovered" notice on the very first run (no prior
            # baseline to recover from) -- only on an actual vulnerable->safe
            # transition.
            ntfy("gitea-version-check: recovered",
                 f"Gitea at {url} reports version {version} ({min_version}+, patched)",
                 priority="default", dry_run=dry_run)

    if not dry_run:
        state["status"] = status
        state["last_version"] = version
        save_state(state_path, state)

    print(f"gitea-version-check: version={version} min={min_version} status={status}")
    return 0 if safe else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)
    p_run = sub.add_parser("run", help="check live Gitea version, alert on regression")
    p_run.add_argument("--url", default=DEFAULT_URL)
    p_run.add_argument("--min-version", default=DEFAULT_MIN_VERSION)
    p_run.add_argument("--state", default=DEFAULT_STATE)
    p_run.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if args.cmd == "run":
        return run(url=args.url, min_version=args.min_version,
                    state_path=args.state, dry_run=args.dry_run)


if __name__ == "__main__":
    sys.exit(main())
