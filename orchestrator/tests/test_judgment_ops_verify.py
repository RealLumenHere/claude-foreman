"""wq#1696 item 3: a [verify] ticket for an ops-bundle fix probes the box.

file_verify_pass must attach Liveness: ops_verified(<bundle>) when the shipped
ticket cites a bundle that honours the --verify contract, and must say so plainly
when a cited bundle has no handler. Router/manifest are stubbed.
"""
import os
import sys
import types

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import judgment  # noqa: E402


@pytest.fixture
def ops(tmp_path, monkeypatch):
    root = tmp_path / "ops"
    for name, handler in (("wq10-good", True), ("wq11-legacy", False)):
        d = root / name
        d.mkdir(parents=True)
        body = 'if [ "${1:-}" = --verify ]; then exit 0; fi\n' if handler else "cp x /y\n"
        (d / "install.sh").write_text("#!/usr/bin/env bash\n" + body)
    monkeypatch.setattr(judgment, "OPS_ROOT", str(root))
    return root


def _stub(monkeypatch, ticket, calls):
    rung = types.SimpleNamespace(model="claude-sonnet-5", effort="high",
                                 spec=lambda: "sonnet@high", as_dict=lambda: {})
    monkeypatch.setattr(judgment.router, "load_manifest", lambda: types.SimpleNamespace(verify_policy={}))
    monkeypatch.setattr(judgment.router, "wq_show", lambda i: ticket)
    monkeypatch.setattr(judgment.router, "repo_for_ticket", lambda t: None)
    monkeypatch.setattr(judgment, "verify_required", lambda *a: (True, "test"))
    monkeypatch.setattr(judgment, "existing_verify_ticket", lambda i: None)
    monkeypatch.setattr(judgment, "verifier_tuple", lambda *a: (rung, "test"))
    monkeypatch.setattr(judgment.router, "wq", lambda *a: calls.append(a) or
                        types.SimpleNamespace(stdout="added task 999 [build]", stderr=""))


def test_cited_bundles_found_only_if_they_exist(ops):
    t = {"notes": "staged at foreman/ops/wq10-good/install.sh and ops/wq11-legacy/, "
                  "also ops/wq99-gone/install.sh"}
    assert judgment.ops_bundles_cited(t) == ["wq10-good", "wq11-legacy"]


def test_verify_ticket_carries_box_liveness(ops, monkeypatch):
    calls = []
    _stub(monkeypatch, {"title": "fix x", "archetype": "build", "model": "m", "lane": "build",
                        "notes": "PR#5 merged; bundle foreman/ops/wq10-good/install.sh"}, calls)
    out = judgment.file_verify_pass(10)
    assert out["liveness"] == "ops_verified(wq10-good)"
    args = calls[-1]
    assert "--liveness" in args and args[args.index("--liveness") + 1] == "ops_verified(wq10-good)"
    detail = args[args.index("--detail") + 1]
    assert "LIVE target" in detail


def test_legacy_bundle_is_called_out_not_probed(ops, monkeypatch):
    calls = []
    _stub(monkeypatch, {"title": "fix y", "archetype": "build", "model": "m", "lane": "build",
                        "notes": "ops/wq11-legacy/install.sh"}, calls)
    out = judgment.file_verify_pass(11)
    assert out["liveness"] is None
    args = calls[-1]
    assert "--liveness" not in args
    assert "without a --verify handler: wq11-legacy" in args[args.index("--detail") + 1]


def test_non_ops_ticket_unchanged(ops, monkeypatch):
    calls = []
    _stub(monkeypatch, {"title": "plain", "archetype": "build", "model": "m", "lane": "build",
                        "notes": "PR#7 merged"}, calls)
    out = judgment.file_verify_pass(12)
    assert out["liveness"] is None and "--liveness" not in calls[-1]
