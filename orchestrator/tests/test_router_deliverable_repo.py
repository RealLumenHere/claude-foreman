"""wq#1446: repo_for_ticket resolves the repo that holds the ticket's named deliverable."""
import os
import sys

import pytest

ORCH_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ORCH_DIR)
import router  # noqa: E402

BIN = "/home/lumen/bin"
MINERVA = "/home/lumen/minerva"


@pytest.fixture(autouse=True)
def managed(monkeypatch):
    monkeypatch.setattr(router.repo_sweep, "managed_repos",
                        lambda: {router.FOREMAN_ROOT, BIN, MINERVA, "/home/lumen/pokemon"})
    monkeypatch.delenv("FOREMAN_DELIVERABLE_REPO_DISABLE", raising=False)


def t(**kw):
    base = {"lane": "fleet", "title": "x", "detail": "", "deliverable": None, "needs": ""}
    base.update(kw)
    return base


def test_column_in_other_repo_wins_over_lane():
    assert router.repo_for_ticket(t(deliverable=BIN + "/fleet-exec")) == BIN


def test_deliverable_marker_in_detail():
    d = "Some text.\nDeliverable: `/home/lumen/minerva/minerva_verify.py` and more"
    assert router.repo_for_ticket(t(detail=d)) == MINERVA


def test_longest_repo_prefix_wins(monkeypatch):
    monkeypatch.setattr(router.repo_sweep, "managed_repos",
                        lambda: {router.FOREMAN_ROOT, "/home/lumen", BIN})
    assert router.deliverable_repo_hint(t(deliverable=BIN + "/x.py")) == BIN


def test_unmanaged_path_falls_through_to_lane():
    assert router.repo_for_ticket(t(deliverable="/tmp/elsewhere/x.py")) == router.LANE_REPO["fleet"]


def test_relative_and_missing_deliverable_fall_through():
    assert router.deliverable_repo_hint(t(deliverable="reports/x.md")) is None
    assert router.deliverable_repo_hint(t()) is None


def test_prefix_is_a_path_boundary_not_a_string_prefix():
    assert router.deliverable_repo_hint(t(deliverable=BIN + "-old/x.py")) is None


def test_outputs_and_arrow_text_do_not_count():
    d = "Output: /home/lumen/bin/x.py\nreport -> /home/lumen/minerva/y.md"
    assert router.deliverable_repo_hint(t(detail=d)) is None


def test_kill_switch(monkeypatch):
    monkeypatch.setenv("FOREMAN_DELIVERABLE_REPO_DISABLE", "1")
    assert router.repo_for_ticket(t(deliverable=BIN + "/fleet-exec")) == router.LANE_REPO["fleet"]
