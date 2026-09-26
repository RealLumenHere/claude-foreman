"""wq#2753 (ISSUE-LOG #728): repo_for_ticket resolves the repo a 'lands via lumen-pr
against/on <target>' sentence in the ticket detail names — the phrasing wq#2624 used,
which dispatched into a foreman worktree the evidence gate could never verify from."""
import os
import sys

import pytest

ORCH_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ORCH_DIR)
import router  # noqa: E402

BIN = "/home/lumen/bin"
NEST = "/home/lumen/projects/nest/NoLongerEvil-Thermostat"


@pytest.fixture(autouse=True)
def managed(monkeypatch):
    monkeypatch.setattr(router.repo_sweep, "managed_repos",
                        lambda: {router.FOREMAN_ROOT, BIN, NEST, "/home/lumen/pokemon"})
    monkeypatch.delenv("FOREMAN_LANDING_REPO_DISABLE", raising=False)


def t(**kw):
    base = {"lane": "fleet", "title": "x", "detail": "", "deliverable": None, "needs": ""}
    base.update(kw)
    return base


def test_wq2624_phrasing_resolves_the_named_repo():
    # verbatim shape from wq#2624's detail, sentence-final period included
    tk = t(lane="auto", detail="Fix the grep. Lands via lumen-pr against /home/lumen/bin.")
    assert router.landing_repo_hint(tk) == BIN
    assert router.repo_for_ticket(tk) == BIN


def test_bare_repo_name_resolves_by_unique_basename():
    tk = t(detail="Land via lumen-pr on foreman (dispatcher/foreman_dispatcher.py).")
    assert router.landing_repo_hint(tk) == router.FOREMAN_ROOT


def test_path_inside_a_managed_repo_resolves_to_the_repo_root():
    tk = t(detail="landing via lumen-pr to /home/lumen/bin/lumen-run")
    assert router.landing_repo_hint(tk) == BIN


def test_past_tense_narration_of_a_prior_landing_never_matches():
    tk = t(detail="wq#2624 landed via lumen-pr to /home/lumen/bin; now fix the dispatcher.")
    assert router.landing_repo_hint(tk) is None


def test_unmanaged_path_is_refused_not_guessed():
    tk = t(detail="Lands via lumen-pr against /home/lumen/questfree.")
    assert router.landing_repo_hint(tk) is None
    assert router.repo_for_ticket(tk) == router.repo_for_lane("fleet")


def test_ambiguous_bare_name_is_refused(monkeypatch):
    monkeypatch.setattr(router.repo_sweep, "managed_repos",
                        lambda: {"/home/lumen/bin", "/opt/other/bin"})
    tk = t(detail="lands via lumen-pr on bin")
    assert router.landing_repo_hint(tk) is None


def test_plain_prose_without_the_phrase_never_matches():
    tk = t(detail="Refactor the bin scripts; touches /home/lumen/bin/wq indirectly.")
    assert router.landing_repo_hint(tk) is None


def test_kill_switch_disables_the_hint(monkeypatch):
    monkeypatch.setenv("FOREMAN_LANDING_REPO_DISABLE", "1")
    tk = t(detail="Lands via lumen-pr against /home/lumen/bin.")
    assert router.landing_repo_hint(tk) is None


def test_needs_and_deliverable_hints_still_outrank_the_landing_phrase():
    tk = t(detail="Deliverable: /home/lumen/pokemon/tool.py\n"
                  "Lands via lumen-pr against /home/lumen/bin.")
    assert router.repo_for_ticket(tk) == "/home/lumen/pokemon"
