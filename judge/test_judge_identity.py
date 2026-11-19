"""wq#2821: verify_model_identity must fail closed on a re-tagged or unreachable rung."""
import io
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import judge_common as jc

PIN = json.load(open(jc.IDENTITY_FILE))["hathor-llama8b"]["blob_sha256"]


class _Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _serve(monkeypatch, modelfile=None, exc=None):
    def fake(req, timeout=None):
        if exc:
            raise exc
        return _Resp(json.dumps({"modelfile": modelfile}).encode())
    monkeypatch.setattr(jc.urllib.request, "urlopen", fake)


def test_matching_blob_passes(monkeypatch):
    _serve(monkeypatch, f"FROM /x/blobs/sha256-{PIN}\nPARAMETER num_ctx 32768\n")
    assert jc.verify_model_identity("hathor-llama8b", required=True) is None


def test_retagged_blob_fails_closed(monkeypatch):
    _serve(monkeypatch, "FROM /x/blobs/sha256-" + "6" * 64 + "\n")
    err = jc.verify_model_identity("hathor-llama8b", required=True)
    assert err and "mismatch" in err


def test_unreachable_node_fails_closed(monkeypatch):
    _serve(monkeypatch, exc=OSError("no route"))
    assert "identity check failed" in jc.verify_model_identity("hathor-llama8b", required=True)


def test_no_from_line_fails_closed(monkeypatch):
    _serve(monkeypatch, "PARAMETER num_ctx 8192\n")
    assert "mismatch" in jc.verify_model_identity("hathor-llama8b", required=True)


def test_unlisted_model_only_fails_when_required():
    assert jc.verify_model_identity("some-bench-model") is None
    assert jc.verify_model_identity("some-bench-model", required=True)


def test_fallback_source_with_no_identity_pin_fails_closed():
    """wq#3792: bin/judge now passes required=True for a fallback: source
    (same as pin:), so a fallback rung with no identity entry at all must
    fail closed -- not return None like the old pin:-only required= did."""
    err = jc.verify_model_identity("some-unpinned-fallback-rung", required=True)
    assert err and "no identity pin" in err


def test_nephthys_qwen14b_pending_entry_fails_closed_until_populated(monkeypatch):
    """wq#3792: nephthys has been offline (tailscale last-seen 5d, confirmed
    live during this ticket) so its real served blob digest was never
    captured. The entry's blob_sha256 is deliberately left empty rather than
    guessed -- it must fail closed (mismatch, not a silent pass) even if the
    node answers, until a real live /api/show populates the pinned value."""
    _serve(monkeypatch, "FROM /x/blobs/sha256-" + "a" * 64 + "\n")
    err = jc.verify_model_identity("nephthys-qwen14b", required=True)
    assert err and "mismatch" in err


# --- wq#5027: an unreachable pin names its node, and a retired node says so -------------


def test_unreachable_retired_node_is_named(monkeypatch):
    monkeypatch.setattr(jc, "node_lifecycle", lambda node, timeout=3: "retired")
    _serve(monkeypatch, exc=OSError("timed out"))
    err = jc.verify_model_identity("hathor-llama8b", required=True)
    assert "identity check failed" in err
    assert "on hathor" in err and "RETIRED" in err and "100.80.71.13" in err


def test_unreachable_active_node_names_node_without_retired(monkeypatch):
    monkeypatch.setattr(jc, "node_lifecycle", lambda node, timeout=3: "active")
    _serve(monkeypatch, exc=OSError("no route"))
    err = jc.verify_model_identity("nun1-qwen25coder7b", required=True)
    assert "on nun1" in err and "unreachable" in err and "lifecycle=active" in err
    assert "RETIRED" not in err


def test_unreachable_with_lifecycle_unknown_still_fails_closed(monkeypatch):
    monkeypatch.setattr(jc, "node_lifecycle", lambda node, timeout=3: None)
    _serve(monkeypatch, exc=OSError("no route"))
    err = jc.verify_model_identity("nun1-qwen25coder7b", required=True)
    assert err and "identity check failed" in err and "lifecycle=" not in err


def test_node_lifecycle_never_raises(monkeypatch):
    def boom(*a, **k):
        raise FileNotFoundError("no lumen_state here")
    import subprocess
    monkeypatch.setattr(subprocess, "run", boom)
    assert jc.node_lifecycle("hathor") is None
    assert jc.node_lifecycle(None) is None


def test_node_lifecycle_parses_lumen_state_line(monkeypatch):
    import subprocess

    class _P:
        stdout = ("  nephthys.lan_ip         = 192.168.8.176  (since 2026-06-28)\n"
                  "  nephthys.lifecycle      = retired  (since 2026-10-22)  [UNVERIFIABLE]\n")
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _P())
    assert jc.node_lifecycle("nephthys") == "retired"


def test_live_pin_and_fallback_have_node_tagged_identity():
    """wq#5027: the active pin and fallback must each carry a full identity entry
    naming their node, or the next retirement goes silent again."""
    d = json.load(open(jc.IDENTITY_FILE))
    here = os.path.dirname(os.path.abspath(__file__))
    for f in ("JUDGE_MODEL_PIN", "JUDGE_MODEL_FALLBACK"):
        rung = open(os.path.join(here, f)).read().strip()
        ent = d.get(rung)
        assert ent, f"{f}={rung} has no identity entry"
        assert ent.get("node") and len(ent.get("blob_sha256", "")) == 64, f"{f}={rung} entry incomplete"
