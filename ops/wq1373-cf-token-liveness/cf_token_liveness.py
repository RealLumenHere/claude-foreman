#!/usr/bin/env python3
"""cf_token_liveness.py — periodic liveness probe for stored Cloudflare API
tokens (wq#1373).

Why: the #37 Access-mgmt token went dead (revoked/expired) and nothing
noticed until Frank re-minted it by hand (/home/lumen/kb/ISSUE-LOG.md #37).
`GET /user/tokens/verify` cannot be used to detect this class of failure: it
requires a User-level read permission that narrowly-scoped tokens don't carry,
so it returns "Invalid" for perfectly healthy scoped tokens (false positive)
just as readily as for actually-dead ones (true positive) — it cannot tell
the two apart. This script instead probes each token with one real,
read-only operation drawn from its *own* scope (D1 query, Workers list, DNS
list, R2 bucket list, Access apps list) — the same class of call the token
was minted to make, reusing the request shape already proven live in
mailbot/mail_recall.py:38-46 and
foreman/ops/wq1252-lumen-public-guardian/checks_email.py:47-65.

Token inventory lives in tokens.json (see tokens.json.example and README.md
for the manifest schema). Each entry names a scope, the 0600 file holding the
token, and the resource id file(s) needed to build the probe URL — the same
"push the secret into a 0600 file, read it by path" convention every other
CF-calling script in this repo already uses. No token value is ever printed;
alerts carry only the token's manifest `name` and the HTTP/scope error.

Run:
  cf_token_liveness.py [--manifest tokens.json] [--dry-run]

Exit 0 if every configured token's probe succeeds, 1 if any token is dead or
its file is missing. TTL warnings never affect the exit code — they alert at
`high` priority (not `urgent`) and are informational.
"""
import argparse
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone

CF_API = "https://api.cloudflare.com/client/v4"
HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_MANIFEST = os.path.join(HERE, "tokens.json")
NTFY_BIN = "/home/lumen/bin/ntfy-send"
TTL_WARN_DAYS = 3


def _read(path):
    with open(path) as f:
        return f.read().strip()


def _cf_get(url, token):
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.load(r)


def _cf_post(url, token, body):
    req = urllib.request.Request(
        url, method="POST", data=json.dumps(body).encode(),
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.load(r)


def _require_success(out, what):
    if not out.get("success"):
        raise RuntimeError(f"{what} rejected: {out.get('errors')}")


def probe_d1(entry, token):
    """Same call shape as mailbot/mail_recall.py:38-46: POST .../d1/database/{id}/query."""
    account = _read(entry["account_id_file"])
    dbid = _read(entry["database_id_file"])
    out = _cf_post(f"{CF_API}/accounts/{account}/d1/database/{dbid}/query", token,
                    {"sql": "SELECT 1", "params": []})
    _require_success(out, "D1 query")


def probe_workers(entry, token):
    account = _read(entry["account_id_file"])
    out = _cf_get(f"{CF_API}/accounts/{account}/workers/scripts", token)
    _require_success(out, "Workers scripts list")


def probe_dns(entry, token):
    zone = _read(entry["zone_id_file"])
    out = _cf_get(f"{CF_API}/zones/{zone}/dns_records?per_page=1", token)
    _require_success(out, "DNS records list")


def probe_r2(entry, token):
    account = _read(entry["account_id_file"])
    out = _cf_get(f"{CF_API}/accounts/{account}/r2/buckets", token)
    _require_success(out, "R2 buckets list")


def probe_access(entry, token):
    account = _read(entry["account_id_file"])
    out = _cf_get(f"{CF_API}/accounts/{account}/access/apps", token)
    _require_success(out, "Access apps list")


PROBES = {
    "d1": probe_d1,
    "workers": probe_workers,
    "dns": probe_dns,
    "r2": probe_r2,
    "access": probe_access,
}


def ttl_warning(entry, now):
    """None if no TTL is configured or the token isn't close to it yet."""
    minted, ttl_days = entry.get("minted"), entry.get("ttl_days")
    if not minted or not ttl_days:
        return None
    minted_dt = datetime.fromisoformat(minted).replace(tzinfo=timezone.utc)
    remaining = ttl_days - (now - minted_dt).total_seconds() / 86400
    if remaining <= TTL_WARN_DAYS:
        return f"expires in {remaining:.1f}d (minted {minted}, ttl {ttl_days}d)"
    return None


def check_one(entry, now):
    """Returns (ok: bool, detail: str|None, ttl_msg: str|None)."""
    name, token_file = entry["name"], entry["token_file"]
    if not os.path.exists(token_file):
        return False, f"{name}: token file missing ({token_file})", None
    probe = PROBES.get(entry["scope"])
    if probe is None:
        return False, f"{name}: unknown scope {entry['scope']!r} in manifest", None
    try:
        probe(entry, _read(token_file))
    except (urllib.error.HTTPError, urllib.error.URLError, RuntimeError, OSError) as e:
        return False, f"{name}: {entry['scope']} probe failed: {e}", None
    return True, None, ttl_warning(entry, now)


def _alert(priority, title, message, dry_run):
    if dry_run:
        print(f"[dry-run] would alert priority={priority} title={title!r}\n{message}")
        return
    subprocess.run([NTFY_BIN, "-p", priority, "-T", title, message], check=False)


def run(manifest_path, dry_run=False):
    with open(manifest_path) as f:
        tokens = json.load(f)["tokens"]
    now = datetime.now(timezone.utc)
    failures, warnings = [], []
    for entry in tokens:
        ok, detail, ttl_msg = check_one(entry, now)
        if not ok:
            failures.append(detail)
        elif ttl_msg:
            warnings.append(f"{entry['name']}: {ttl_msg}")
    if failures:
        _alert("urgent", "CF token liveness: DEAD token(s)", "\n".join(failures), dry_run)
    if warnings:
        _alert("high", "CF token liveness: nearing TTL", "\n".join(warnings), dry_run)
    print(f"cf_token_liveness: {len(tokens) - len(failures)}/{len(tokens)} tokens live"
          + (f" — FAILED: {'; '.join(failures)}" if failures else "")
          + (f" — TTL warnings: {'; '.join(warnings)}" if warnings else ""))
    return 1 if failures else 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                  formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--manifest", default=DEFAULT_MANIFEST,
                     help="path to tokens.json manifest (default: alongside this script)")
    ap.add_argument("--dry-run", action="store_true",
                     help="print alerts instead of sending them via ntfy-send")
    args = ap.parse_args(argv)
    return run(args.manifest, args.dry_run)


if __name__ == "__main__":
    sys.exit(main())
