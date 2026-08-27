"""wq#992 — \\bchain\\b must not force long-horizon-campaign (DECOMPOSE) on its own.

The word "chain" appears in ordinary ticket prose ("chain tickets", "routing chain",
"tool-chain"); on its own it must fall through to standard-engineering, not decompose.
Corroborated by a real campaign word it still classifies as a campaign. The strong
campaign signals and the wq#778 opus gate are unchanged.
"""
import os
import sys

import pytest

ORCH_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ORCH_DIR)
import router  # noqa: E402


@pytest.fixture(scope="module")
def manifest():
    return router.load_manifest()


def _arch(manifest, title, detail="", lane=""):
    a, _sig = router.classify_archetype(title, detail, lane, manifest)
    return a


def test_bare_chain_does_not_decompose(manifest):
    a = _arch(manifest, "dispatcher must chain tickets without 20min gaps",
              "The dispatcher should chain the next ticket immediately.", "build")
    assert a != "long-horizon-campaign"


def test_chain_with_campaign_word_still_campaign(manifest):
    a = _arch(manifest, "chain sessions across the uplift campaign",
              "multi-session checkpoint work")
    assert a == "long-horizon-campaign"


def test_bare_campaign_still_campaign(manifest):
    assert _arch(manifest, "run the migration campaign") == "long-horizon-campaign"


def test_bare_multisession_still_campaign(manifest):
    assert _arch(manifest, "multi-session refactor") == "long-horizon-campaign"


def test_wq778_bare_audit_still_gated(manifest):
    assert _arch(manifest, "audit the config") != "rca-investigation"


def test_wq778_two_weak_signals_still_qualify(manifest):
    assert _arch(manifest, "audit why the gate fails") == "rca-investigation"
