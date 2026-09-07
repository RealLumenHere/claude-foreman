#!/usr/bin/env python3
"""add_access_hostname.py — idempotently add a hostname to an existing
Cloudflare Access application (wq#1017).

Why: prompts.lumennode.uk (wq#957's lumen-prompt-forge.service) must never
resolve before it is covered by Cloudflare Access — the generated prompts
embed live node IPs and autonomy grants (prompt-forge/INSTALL.md step 4).
Doing this by hand in the CF dashboard is the same one-off manual step that
went unnoticed for the #37 Access-mgmt token (`/home/lumen/kb/ISSUE-LOG.md`
#37); scripting it makes the add idempotent, reviewable, and safe to rerun.

This does NOT create a new Access application (contrast
`agents/work-queue/reports/mission_control_build.md`, which minted a
dedicated app per hostname for mctl.lumennode.uk). wq#1017 asks to fold
prompts.lumennode.uk into the application that already fronts
home.lumennode.uk, so this script looks that app up by an existing domain it
already covers (`--front-hostname`, default `home.lumennode.uk`) via
`GET .../access/apps` rather than taking an app id on faith — no id is
fabricated (instinct 7). If more than one app matches, it refuses and lists
the candidates rather than guessing.

Cloudflare Access apps carry their domain(s) in `self_hosted_domains`
(list) on current API versions, with `domain` kept as a legacy single-value
mirror of the first entry. This script only ever appends; it never removes
an existing domain.

Request shape (`_cf_get`/`_cf_post`/token-file convention) is reused
verbatim from `foreman/ops/wq1373-cf-token-liveness/cf_token_liveness.py:64-79`,
the only other CF-API caller in this repo.

Token: no Access-mgmt token file was found in any directory this worker's
sandbox could reach (`/home/lumen/mailbot`, `foreman`, `bin`, `kb`,
`agents/work-queue`, `projects/nest`, this worktree) — see
`foreman/ops/wq1373-cf-token-liveness/README.md`'s "what's wired up vs.
what's pending" for the same gap. This script is therefore a config-only,
ready-to-run artifact per that same precedent: point `--token-file` at the
real Access-mgmt token once its path is known and this runs as-is, no code
change required.

Run:
  add_access_hostname.py --add-hostname prompts.lumennode.uk \
      --token-file /path/to/access_mgmt_token \
      --account-id-file /home/lumen/mailbot/.cf_account_id \
      [--front-hostname home.lumennode.uk] [--dry-run]

Exit 0 on success or already-present (idempotent no-op), 1 on API/lookup
failure, 2 on bad invocation.
"""
import argparse
import json
import sys
import urllib.error
import urllib.request

CF_API = "https://api.cloudflare.com/client/v4"

EXIT_OK, EXIT_FAILED, EXIT_USAGE = 0, 1, 2


def _read(path):
    with open(path) as f:
        return f.read().strip()


def _cf_get(url, token):
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.load(r)


def _cf_put(url, token, body):
    req = urllib.request.Request(
        url, method="PUT", data=json.dumps(body).encode(),
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.load(r)


def _require_success(out, what):
    if not out.get("success"):
        raise RuntimeError(f"{what} rejected: {out.get('errors')}")


def _domains(app):
    domains = list(app.get("self_hosted_domains") or [])
    legacy = app.get("domain")
    if legacy and legacy not in domains:
        domains.append(legacy)
    return domains


def find_front_app(account, token, front_hostname):
    out = _cf_get(f"{CF_API}/accounts/{account}/access/apps", token)
    _require_success(out, "Access apps list")
    matches = [app for app in out.get("result", []) if front_hostname in _domains(app)]
    if not matches:
        raise RuntimeError(f"no Access app currently covers {front_hostname!r}")
    if len(matches) > 1:
        ids = ", ".join(f"{a.get('id')} ({a.get('name')})" for a in matches)
        raise RuntimeError(f"{len(matches)} Access apps cover {front_hostname!r}, refusing to "
                            f"guess which one: {ids}")
    return matches[0]


def add_hostname(account, token, app, add_hostname, dry_run):
    domains = _domains(app)
    if add_hostname in domains:
        print(f"{add_hostname} already present on Access app {app['id']} ({app.get('name')}) "
              f"— no-op")
        return
    new_domains = domains + [add_hostname]
    if dry_run:
        print(f"[dry-run] would PUT Access app {app['id']} ({app.get('name')}) "
              f"self_hosted_domains: {domains} -> {new_domains}")
        return
    out = _cf_put(f"{CF_API}/accounts/{account}/access/apps/{app['id']}", token,
                   {**app, "self_hosted_domains": new_domains})
    _require_success(out, "Access app update")
    print(f"added {add_hostname} to Access app {app['id']} ({app.get('name')})")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                  formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--add-hostname", required=True,
                     help="hostname to add to the Access app, e.g. prompts.lumennode.uk")
    ap.add_argument("--token-file", required=True,
                     help="path to a 0600 file holding the Access-mgmt CF API token")
    ap.add_argument("--account-id-file", required=True,
                     help="path to a file holding the CF account id "
                          "(e.g. /home/lumen/mailbot/.cf_account_id)")
    ap.add_argument("--front-hostname", default="home.lumennode.uk",
                     help="existing hostname used to identify the target Access app "
                          "(default: home.lumennode.uk)")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    try:
        token = _read(args.token_file)
        account = _read(args.account_id_file)
    except OSError as e:
        print(f"add_access_hostname: cannot read config: {e}", file=sys.stderr)
        return EXIT_USAGE

    try:
        app = find_front_app(account, token, args.front_hostname)
        add_hostname(account, token, app, args.add_hostname, args.dry_run)
    except (urllib.error.HTTPError, urllib.error.URLError, RuntimeError) as e:
        print(f"add_access_hostname: {e}", file=sys.stderr)
        return EXIT_FAILED
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
