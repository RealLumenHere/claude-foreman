"""wq#2105: the credential registry must never claim a credential is alive on
file existence alone.

`/home/lumen/mailbot/.cf_deploy_token` died some time before 2026-09-08 and nothing
noticed: the only liveness the fleet had on it was
`path_exists(/home/lumen/mailbot/.cf_deploy_token)` (ledger binding for wq#1269),
which answers SATISFIED for a file full of a rejected token. An API token with no
fixed expiry has no other tell — only asking the issuer finds it.

These rows pin the invariant that closes that class:
  (i)   every `credentials:` record names a path and a probe;
  (ii)  no `credentials:` record is probed by path_exists;
  (iii) no `bindings:` entry path_exists-checks a file the registry knows is a
        credential (the wq#1269 shape);
  (iv)  cf-deploy carries the verify_url + bearer scheme that the sentinel
        probe-credential-expiry (`check_bearer`) needs to live-verify it.
"""
import os
import re

import pytest

yaml = pytest.importorskip("yaml")

LEDGER = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "liveness_ledger.yaml"
)
CF_VERIFY = "https://api.cloudflare.com/client/v4/user/tokens/verify"


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
    # (no record, so no alarm) unless verify_url and scheme=bearer are both set.
    assert rec.get("verify_url") == CF_VERIFY
    assert rec.get("scheme") == "bearer"
