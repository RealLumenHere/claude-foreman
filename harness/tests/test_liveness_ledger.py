"""wq#2105: the credential registry must never claim a credential is alive on
file existence alone. wq#2109: nor may it call one dead on the wrong verify endpoint.

`/home/lumen/mailbot/.cf_deploy_token` looked healthy to the only liveness the fleet
had on it — `path_exists(/home/lumen/mailbot/.cf_deploy_token)` (ledger binding for
wq#1269) — which answers SATISFIED for a file full of a rejected token. An API token
with no fixed expiry has no other tell: only asking the issuer finds it.

Asking the *wrong* issuer endpoint is the mirror-image failure, and wq#2105 shipped it.
Cloudflare's `/user/tokens/verify` knows user-owned tokens only; for the account-owned
cf-deploy token it answers 401 "Invalid API Token" forever, which read as a dead token
(false URGENT page every sentinel cycle, an unsatisfiable binding, and a spurious
physical re-mint ticket). The token was alive the whole time — `/accounts/<id>/tokens/
verify` 200 `status: active` — and its real defect is a missing D1 scope, which is what
broke the wq#1517 wrangler d1 heal.

These rows pin the invariant that closes both classes:
  (i)   every `credentials:` record names a path and a probe;
  (ii)  no `credentials:` record is probed by path_exists;
  (iii) no `bindings:` entry path_exists-checks a file the registry knows is a
        credential (the wq#1269 shape);
  (iv)  cf-deploy carries the verify_url + bearer scheme that the sentinel
        probe-credential-expiry (`check_bearer`) needs to live-verify it;
  (v)   every Cloudflare credential declares its token_type and its verify_url is the
        endpoint that token type answers on;
  (vi)  a credential's `scope:` text claims no scope that is not itself probed — the
        registry describes what the token can do, not what it was asked for.
"""
import os
import re

import pytest

yaml = pytest.importorskip("yaml")

LEDGER = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "liveness_ledger.yaml"
)
CF_ACCOUNT = "3f413ce08c3163f99984a78720fa8b97"
CF_USER_VERIFY = "https://api.cloudflare.com/client/v4/user/tokens/verify"
CF_ACCOUNT_VERIFY = f"https://api.cloudflare.com/client/v4/accounts/{CF_ACCOUNT}/tokens/verify"
CF_ACCOUNT_VERIFY_RE = re.compile(
    r"^https://api\.cloudflare\.com/client/v4/accounts/[0-9a-f]{32}/tokens/verify$"
)


@pytest.fixture(scope="module")
def ledger():
    with open(LEDGER) as fh:
        return yaml.safe_load(fh) or {}


def test_every_credential_record_names_a_path_and_a_probe(ledger):
    for name, rec in (ledger.get("credentials") or {}).items():
        assert rec.get("path"), f"{name}: no path — nothing to verify"
        assert rec.get("probe"), f"{name}: no probe — no way to verify"


def test_no_credential_is_probed_by_path_exists(ledger):
    weak = [n for n, r in (ledger.get("credentials") or {}).items()
            if str(r.get("probe", "")).startswith("path_exists(")]
    assert weak == [], (
        f"path_exists is not liveness for a credential (wq#2105): {weak}. "
        "Use cred_alive(path, verify_url) or a probe that asks the issuer."
    )


def test_no_binding_path_exists_checks_a_registered_credential(ledger):
    cred_paths = {str(r.get("path")) for r in (ledger.get("credentials") or {}).values()}
    offenders = []
    for tid, probe in (ledger.get("bindings") or {}).items():
        for arg in re.findall(r"path_exists\(([^)]*)\)", str(probe)):
            if arg.strip() in cred_paths:
                offenders.append((tid, arg.strip()))
    assert offenders == [], (
        f"binding proves a credential by file existence (wq#1269 shape): {offenders}"
    )


def test_cf_deploy_is_live_verifiable_by_the_sentinel_probe(ledger):
    rec = (ledger.get("credentials") or {}).get("cf-deploy")
    assert rec, "cf-deploy missing — mailbot's E1 heals run on an unwatched token"
    assert rec["path"] == "/home/lumen/mailbot/.cf_deploy_token"
    # check_bearer() in /home/lumen/sentinel/probes/credential_expiry.py returns []
    # (no record, so no alarm) unless verify_url and scheme=bearer are both set, and
    # grades the token dead on any 401 — so the URL has to be one this token answers.
    assert rec.get("verify_url") == CF_ACCOUNT_VERIFY
    assert rec.get("scheme") == "bearer"
    assert rec.get("token_type") == "account"


def _cloudflare_creds(ledger):
    return {n: r for n, r in (ledger.get("credentials") or {}).items()
            if "api.cloudflare.com" in str(r.get("verify_url", ""))}


def test_cloudflare_verify_url_matches_the_token_type(ledger):
    """wq#2109: /user/tokens/verify 401s on a live account-owned token and
    /accounts/<id>/tokens/verify 401s on a user-owned one. A verify URL picked for the
    wrong type reports a permanent false death, which is how wq#2105 lost four days."""
    creds = _cloudflare_creds(ledger)
    assert creds, "no Cloudflare credential records — did the ledger schema move?"
    for name, rec in creds.items():
        ttype = rec.get("token_type")
        assert ttype in ("user", "account"), (
            f"{name}: token_type must be user or account, got {ttype!r} — without it "
            "nothing can tell whether verify_url is the right endpoint"
        )
        url = rec["verify_url"]
        if ttype == "user":
            assert url == CF_USER_VERIFY, f"{name}: user token verified at {url}"
        else:
            assert CF_ACCOUNT_VERIFY_RE.match(url), (
                f"{name}: account-owned token must verify at "
                f"/accounts/<account_id>/tokens/verify, got {url}"
            )


def test_credential_probe_uses_the_records_own_verify_url(ledger):
    """The `probe:` string is what a human reruns by hand; a stale copy of an old
    verify_url there sends the next investigator back to the wrong endpoint."""
    for name, rec in (ledger.get("credentials") or {}).items():
        url = rec.get("verify_url")
        if url:
            assert url in str(rec.get("probe", "")), f"{name}: probe does not use verify_url"


def test_cf_deploy_scope_text_claims_only_what_is_probed(ledger):
    """A token verify proves the token lives, never which scopes it carries. cf-deploy's
    scope line claimed D1:Edit it does not have (401 code 10000 on the lumen-mail
    database, 2026-09-20) — the real cause of the wq#1517 heal failure. Any D1 claim
    must therefore come with a probe that would fail if the scope were still missing."""
    rec = (ledger.get("credentials") or {})["cf-deploy"]
    d1_probe = (rec.get("scope_probes") or {}).get("d1_edit", "")
    assert "/d1/database/" in d1_probe, (
        "cf-deploy needs a scope_probes.d1_edit hitting /accounts/<id>/d1/database/<id> — "
        "the Workers-scoped token verify says nothing about D1"
    )
    assert d1_probe.startswith("cred_alive(" + rec["path"])
    assert "D1:Edit" not in rec["scope"], (
        "scope: still claims D1:Edit, which this token does not carry"
    )
