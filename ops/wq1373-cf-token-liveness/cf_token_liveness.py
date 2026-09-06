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
its file is missing, 2 if the pass itself could not complete. TTL warnings
never affect the exit code — they alert at `high` priority (not `urgent`) and
are informational.

Exit 2 is load-bearing (wq#1433/wq#1434): cf-token-liveness.service carries
`SuccessExitStatus=0 1` so the intentional dead-token exit 1 doesn't trip
OnFailure=, and Python's default uncaught-exception status is also 1 — a
crashed pass would otherwise be indistinguishable from a healthy one to both
systemd and lumen-cron-deadman (wq#1184).
"""
import argparse
import json
import os
import subprocess
import sys
import traceback
import urllib.error
import urllib.request
from datetime import datetime, timezone

CF_API = "https://api.cloudflare.com/client/v4"
HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_MANIFEST = os.path.join(HERE, "tokens.json")
NTFY_BIN = "/home/lumen/bin/ntfy-send"
TTL_WARN_DAYS = 3

EXIT_OK, EXIT_DEAD_TOKEN, EXIT_ABORTED = 0, 1, 2

# A manifest entry missing a required key, holding a wrong-typed value, or
# naming a resource-id file that doesn't parse. json.JSONDecodeError and
# datetime.fromisoformat's complaint are both ValueError; a non-object entry
# in the `tokens` array surfaces as AttributeError/TypeError.
CONFIG_ERRORS = (KeyError, AttributeError, TypeError, ValueError)


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
    """Returns (ok: bool, detail: str|None, ttl_msg: str|None).

    A malformed manifest entry is this token's failure, not the pass's. The
    resource-id lookups live inside the probe_* functions, so an entry missing
    `account_id_file`/`database_id_file`/`zone_id_file` — the exact config-only
    edit README.md prescribes for wiring up each pending token — used to raise
    an uncaught KeyError out of run(): the pass died before any alert fired and
    every later entry in the manifest went unchecked (wq#1433). Catching
    CONFIG_ERRORS here turns that into one loud dead-token failure and lets the
    loop finish.

    The ttl_msg is returned already name-prefixed so the caller never has to
    reach back into `entry` for a key that may not be there.
    """
    name = "<unnamed entry>"
    try:
        name = entry.get("name") or name
        token_file = entry["token_file"]
        if not os.path.exists(token_file):
            return False, f"{name}: token file missing ({token_file})", None
        scope = entry["scope"]
        probe = PROBES.get(scope)
        if probe is None:
            return False, f"{name}: unknown scope {scope!r} in manifest", None
        try:
            probe(entry, _read(token_file))
        except (urllib.error.HTTPError, urllib.error.URLError, RuntimeError, OSError) as e:
            return False, f"{name}: {scope} probe failed: {e}", None
        ttl_msg = ttl_warning(entry, now)
        return True, None, f"{name}: {ttl_msg}" if ttl_msg else None
    except CONFIG_ERRORS as e:
        return False, f"{name}: malformed manifest entry: {type(e).__name__}: {e}", None


def _alert(priority, title, message, dry_run):
    if dry_run:
        print(f"[dry-run] would alert priority={priority} title={title!r}\n{message}")
        return
    subprocess.run([NTFY_BIN, "-p", priority, "-T", title, message], check=False)


def run(manifest_path, dry_run=False):
    with open(manifest_path) as f:
        tokens = json.load(f).get("tokens")
    if not isinstance(tokens, list):
        # Manifest-level breakage, unlike a single bad entry, leaves nothing to
        # check — abort loudly rather than report a vacuous "0/0 tokens live".
        raise RuntimeError(f"{manifest_path}: manifest has no `tokens` array")
    now = datetime.now(timezone.utc)
    failures, warnings = [], []
    for entry in tokens:
        ok, detail, ttl_msg = check_one(entry, now)
        if not ok:
            failures.append(detail)
        elif ttl_msg:
            warnings.append(ttl_msg)
    if failures:
        _alert("urgent", "CF token liveness: DEAD token(s)", "\n".join(failures), dry_run)
    if warnings:
        _alert("high", "CF token liveness: nearing TTL", "\n".join(warnings), dry_run)
    print(f"cf_token_liveness: {len(tokens) - len(failures)}/{len(tokens)} tokens live"
          + (f" — FAILED: {'; '.join(failures)}" if failures else "")
          + (f" — TTL warnings: {'; '.join(warnings)}" if warnings else ""))
    return EXIT_DEAD_TOKEN if failures else EXIT_OK


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                  formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--manifest", default=DEFAULT_MANIFEST,
                     help="path to tokens.json manifest (default: alongside this script)")
    ap.add_argument("--dry-run", action="store_true",
                     help="print alerts instead of sending them via ntfy-send")
    args = ap.parse_args(argv)
    try:
        return run(args.manifest, args.dry_run)
    except Exception as e:
        # Backstop for anything check_one() doesn't own: a missing/unparseable
        # manifest, or a future bug in the loop itself. Exiting EXIT_ABORTED
        # rather than letting the exception set Python's default status 1 is
        # what keeps `SuccessExitStatus=0 1` from swallowing a crashed pass
        # (wq#1433) — a non-whitelisted status fails the unit, which also skips
        # the ExecStartPost= stamp so lumen-cron-deadman (wq#1184) sees it too.
        traceback.print_exc()
        _alert("urgent", "CF token liveness: monitor ABORTED",
               f"pass aborted before completion, tokens left unchecked: "
               f"{type(e).__name__}: {e}", args.dry_run)
        print(f"cf_token_liveness: pass ABORTED: {type(e).__name__}: {e}", file=sys.stderr)
        return EXIT_ABORTED


if __name__ == "__main__":
    sys.exit(main())
