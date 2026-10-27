"""wq#4686 (design/orchestrator_verify_lane.md S5.1-5.3): file_verify_pass derives a
verify ticket's OWN needs from the shipped ticket, routes verify-probe only when those
needs demand reach, and files status=blocked (never spawned) when a derived need is
unsatisfiable. Router/manifest/wq are stubbed; capability_lint/fleet_exec_policy run
for real against this repo's own fleet-exec-policy.yaml, which is the whole point:
ssh(thor) and a credential-prefix path must be refused by the REAL policy, not a mock
of it.
"""
import os
import sys
import types

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import judgment  # noqa: E402


def _stub(monkeypatch, ticket, calls):
    rung = types.SimpleNamespace(model="claude-sonnet-5", effort="high",
                                 spec=lambda: "sonnet@high", as_dict=lambda: {})

    def _wq(*a):
        calls.append(a)
        if a and a[0] == "add":
            return types.SimpleNamespace(stdout="added task 999 [build]", stderr="")
        if a and a[0] == "block":
            return types.SimpleNamespace(stdout="blocked 999", stderr="")
        return types.SimpleNamespace(stdout="", stderr="")

    monkeypatch.setattr(judgment.router, "load_manifest",
                        lambda: types.SimpleNamespace(verify_policy={}))
    monkeypatch.setattr(judgment.router, "wq_show", lambda i: ticket)
    monkeypatch.setattr(judgment.router, "repo_for_ticket", lambda t: None)
    monkeypatch.setattr(judgment, "verify_required", lambda *a: (True, "test"))
    monkeypatch.setattr(judgment, "existing_verify_ticket", lambda i: None)
    monkeypatch.setattr(judgment, "verifier_tuple", lambda *a: (rung, "test"))
    monkeypatch.setattr(judgment.router, "wq", _wq)


def _add_args(calls):
    return next(a for a in calls if a and a[0] == "add")


BASE_TICKET = {"title": "fix x", "archetype": "build", "model": "m", "lane": "build",
               "notes": "", "detail": ""}


def test_shipped_ssh_tag_copied_verbatim_and_routes_verify_probe(monkeypatch):
    calls = []
    t = dict(BASE_TICKET, needs="ssh(anubis)")
    _stub(monkeypatch, t, calls)
    out = judgment.file_verify_pass(20)
    assert out["verify_archetype"] == "verify-probe"
    assert ("ssh", "anubis") in out["derived_needs"]
    assert out["block_reasons"] == []
    args = _add_args(calls)
    assert args[args.index("--archetype") + 1] == "verify-probe"
    assert args[args.index("--needs") + 1] == "ssh(anubis)"
    assert not any(a[0] == "block" for a in calls)


def test_shipped_ssh_thor_is_filed_blocked_never_spawned(monkeypatch):
    calls = []
    t = dict(BASE_TICKET, needs="ssh(thor)")
    _stub(monkeypatch, t, calls)
    out = judgment.file_verify_pass(21)
    assert out["blocked"] is True
    assert out["block_reason"].startswith("orchestrator-only: ")
    assert "thor" in out["block_reason"]
    args = _add_args(calls)
    # never filed as verify-probe -- an unsatisfiable need gets verify-pass's own
    # archetype, since it could never dispatch to either lane anyway (§5.3).
    assert args[args.index("--archetype") + 1] == "verify-pass"
    assert "--needs" not in args
    block_call = next(a for a in calls if a[0] == "block")
    assert block_call[1] == "999"
    assert block_call[2] == out["block_reason"]


def test_cited_allowlisted_homeroot_file_routes_verify_probe(monkeypatch):
    calls = []
    t = dict(BASE_TICKET, detail="see /home/lumen/claude_memory.md for context")
    _stub(monkeypatch, t, calls)
    out = judgment.file_verify_pass(22)
    assert out["verify_archetype"] == "verify-probe"
    assert ("homeroot", "/home/lumen/claude_memory.md") in out["derived_needs"]
    assert out["block_reasons"] == []
    args = _add_args(calls)
    assert "homeroot(/home/lumen/claude_memory.md)" in args[args.index("--needs") + 1]


def test_cited_credential_prefix_path_is_filed_blocked(monkeypatch):
    calls = []
    t = dict(BASE_TICKET, detail="probe /home/lumen/.config/systemd/user/foo.service")
    _stub(monkeypatch, t, calls)
    out = judgment.file_verify_pass(23)
    assert out["blocked"] is True
    assert "/home/lumen/.config/systemd/user/foo.service" in out["block_reason"]
    assert any(a[0] == "block" for a in calls)


def test_quoted_mention_of_a_home_root_path_is_not_cited(monkeypatch):
    # capability_lint._MENTION_RE's class: a path inside a double-quoted span is
    # discussed, not reached -- it must not turn into a needs:homeroot tag or a block.
    calls = []
    t = dict(BASE_TICKET, detail='the log line reads "/home/lumen/.config/nope" verbatim')
    _stub(monkeypatch, t, calls)
    out = judgment.file_verify_pass(24)
    assert out["block_reasons"] == []
    assert out["derived_needs"] == []


def test_derive_needs_disabled_restores_pre_wq4686_shape(monkeypatch):
    calls = []
    t = dict(BASE_TICKET, needs="ssh(thor)")
    _stub(monkeypatch, t, calls)
    monkeypatch.setenv("FOREMAN_VERIFY_DERIVE_NEEDS", "off")
    out = judgment.file_verify_pass(25)
    assert out["verify_archetype"] == "verify-pass"
    assert out["derived_needs"] == []
    assert out.get("blocked") is None
    args = _add_args(calls)
    assert args[args.index("--archetype") + 1] == "verify-pass"
    assert "--needs" not in args
    assert not any(a[0] == "block" for a in calls)
