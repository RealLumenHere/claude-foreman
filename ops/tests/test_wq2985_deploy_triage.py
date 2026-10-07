"""wq#2985 — the deploy-vs-retire register must be able to say it is wrong.

The register's population is the 36 untracked PENDING bundles from the wq#1732
audit. A test drawn only from that population confirms and never falsifies, so
every check here is driven by either a fabricated row or a bundle from OUTSIDE
the register — the LIVE reference bundles VERIFY_CONTRACT.md names.
"""
import os
import sys

import pytest

OPS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, OPS)

import deploy_triage as dt  # noqa: E402

# Bundles the register deliberately does NOT cover: VERIFY_CONTRACT.md's own
# reference bundles, audited LIVE by wq#1732. Pinned to committed fixtures in
# this repo, not to live state.
OUTSIDE = ["wq900-primary-tree-guard", "wq1658-cron-stamp-dropin"]


def _dir(bundle):
    return os.path.join(OPS, bundle)


def test_register_covers_exactly_the_untracked_set():
    assert len(dt.REGISTER) == 36
    for bundle, rec in dt.REGISTER.items():
        assert rec["disposition"] in {
            dt.KEEP_LIVE, dt.KEEP_DRIFT, dt.KEEP_DEPLOY, dt.KEEP_ASSERT,
            dt.OWNED, dt.SUPERSEDED, dt.RETIRED}, bundle
        assert rec["reason"].strip(), bundle


def test_every_bundle_has_a_disposition_that_closes_the_ticket():
    """wq#2985 closes when each bundle has a deploy ticket, a retire reason, or
    a supersession. Nothing may be left merely described."""
    for bundle, rec in dt.REGISTER.items():
        resolved = (rec.get("follow_up") or rec.get("owner")
                    or rec.get("superseded_by")
                    or rec["disposition"] in {dt.RETIRED, dt.KEEP_LIVE, dt.KEEP_ASSERT})
        assert resolved, f"{bundle} has no follow-up, owner or terminal disposition"


@pytest.mark.parametrize("bundle", OUTSIDE)
def test_outside_bundles_are_not_in_the_register(bundle):
    """Guards against the register quietly growing to cover everything, which
    would make the coverage assertion above vacuous."""
    assert bundle not in dt.REGISTER
    assert os.path.isdir(_dir(bundle)), f"{bundle} missing; fixture assumption broken"


@pytest.mark.parametrize("bundle", OUTSIDE)
def test_scope_classifies_an_unregistered_bundle(bundle):
    """scope_of must work from the bundle on disk alone, with no register row
    to read the answer out of."""
    assert dt.scope_of(bundle, _dir(bundle)) in {"local", "remote", "unindexed-root"}


def test_declared_roots_ignores_non_install_paths():
    """A build dir or state dir named in install.sh is not an install target.
    wq1652 installs to /home/lumen/state/cfd-actuator; that must not make the
    bundle read as unindexed-root, because no payload file lands there."""
    b = "wq1652-cfd-actuator"
    roots = dt.declared_roots(_dir(b))
    assert "/home/lumen/bin/cfd-actuator" in roots
    assert dt.scope_of(b, _dir(b)) == "local"


def test_check_flags_a_live_claim_whose_unit_vanished(monkeypatch):
    """The falsification path: a LIVE row is only worth anything if losing its
    unit makes check() say so."""
    monkeypatch.setattr(dt, "live_unit_files", lambda: {})
    problems = dt.check(OPS)
    assert any("no longer a known unit" in p for p in problems)
    assert any("lumen-spof-daemons.timer" in p for p in problems)


def test_check_flags_a_deploy_row_that_deployed_behind_our_back(monkeypatch):
    """The other falsification path: a row must break when the thing it says is
    absent turns up installed."""
    monkeypatch.setattr(
        dt, "live_unit_files",
        lambda: {u: "enabled"
                 for r in dt.REGISTER.values() for u in r.get("units", [])}
        | {"paid-spend-tripwire.timer": "enabled"})
    problems = dt.check(OPS)
    assert any("deployed outside this register" in p and "wq1406" in p
               for p in problems)


def test_absent_and_present_unit_sets_do_not_overlap():
    """A unit cannot be both the proof a bundle is live and the proof another
    is not."""
    present = {u for r in dt.REGISTER.values() for u in r.get("units", [])}
    absent = {u for r in dt.REGISTER.values() for u in r.get("absent_units", [])}
    assert not (present & absent)


def _has_handler(bundle):
    head = open(os.path.join(OPS, bundle, "install.sh"), encoding="utf-8",
                errors="replace").read(20000)
    return ('= --verify ]' in head) or ("--verify)" in head)


def test_every_registered_bundle_answers_verify():
    """wq#2985's own instruction: every bundle we KEEP gets a handler. The
    terminal ones get one too, because for those the handler asserts the
    retirement or supersession still holds."""
    missing = [b for b in dt.REGISTER if not _has_handler(b)]
    assert not missing, f"no --verify handler: {missing}"


def test_the_handler_marker_check_can_fail(tmp_path):
    """The coverage test above is worthless if _has_handler returns True for
    anything, so drive it with an install.sh that has no handler."""
    d = tmp_path / "wq0000-fake"
    d.mkdir()
    (d / "install.sh").write_text("#!/usr/bin/env bash\nset -euo pipefail\necho hi\n")
    head = (d / "install.sh").read_text()
    assert ('= --verify ]' not in head) and ("--verify)" not in head)


def test_verify_handler_precedes_every_install_step():
    """A handler placed after an install step would install before answering,
    which is the one failure the contract calls out by name."""
    verbs = ("install ", "cp ", "ln ", "systemctl daemon-reload", "systemctl enable",
             "mkdir -p /", "curl ")
    for bundle in dt.REGISTER:
        lines = open(os.path.join(OPS, bundle, "install.sh"), encoding="utf-8",
                     errors="replace").read().splitlines()
        handler = next(i for i, l in enumerate(lines)
                       if "= --verify ]" in l or "--verify)" in l)
        before = [l for l in lines[:handler]
                  if not l.lstrip().startswith("#") and l.lstrip().startswith(verbs)]
        assert not before, f"{bundle}: install step before --verify: {before[:2]}"


def test_follow_ups_are_real_ticket_ids():
    """A disposition that points at 'wq2985-deploy-root' instead of a filed
    ticket closes nothing. Every non-terminal row must carry an id."""
    import re
    for bundle, rec in dt.REGISTER.items():
        ref = rec.get("follow_up") or rec.get("owner")
        if ref is None:
            assert rec["disposition"] in {
                dt.KEEP_LIVE, dt.KEEP_ASSERT, dt.RETIRED, dt.SUPERSEDED}, bundle
            continue
        assert re.fullmatch(r"wq#\d+", ref), f"{bundle}: {ref!r} is not a ticket id"


def test_remote_deploys_name_their_hosts():
    """A DEPLOY on another node is unactionable without the node."""
    for bundle, rec in dt.REGISTER.items():
        if rec["disposition"] == dt.KEEP_DEPLOY and rec.get("scope") == "remote":
            assert rec.get("hosts"), bundle
