"""wq#2020 — hallucination guard on the local stack.

Zero quota, zero network: the transport is a fake `opener` (or a fake `check`), so the
logic is exercised deterministically. The live proof against isis ollama, a seeded
hallucination caught by the real model, is reports/2020_hallucination_guard_live_proof.md.
"""
import http.client
import io
import json
import math
import os
import sys

import pytest

HARNESS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HARNESS_DIR)
import acceptance  # noqa: E402
import hallucination_guard as hg  # noqa: E402


def tok(text, p):
    return {"token": text, "logprob": math.log(p), "top": []}


def completion(text, first_p=0.99):
    return {"choices": [{"message": {"content": text}, "logprobs": {"content": [
        {"token": text, "logprob": math.log(first_p), "top_logprobs": []}]}}]}


class FakeOpener:
    """Stands in for urlopen; replies from a queue and records every request body."""

    def __init__(self, *replies):
        self.replies = list(replies)
        self.bodies = []

    def __call__(self, req, timeout=None):
        self.bodies.append(json.loads(req.data))
        reply = self.replies.pop(0) if len(self.replies) > 1 else self.replies[0]
        if isinstance(reply, Exception):
            raise reply
        return io.BytesIO(json.dumps(reply).encode())


# --- transport ---------------------------------------------------------------

def test_chat_asks_ollama_for_logprobs_and_parses_tokens():
    op = FakeOpener(completion("Paris", 0.9))
    out = hg.chat([{"role": "user", "content": "x"}], opener=op)
    body = op.bodies[0]
    assert body["logprobs"] is True and body["top_logprobs"] == 2
    assert body["reasoning_effort"] == "none"
    assert out[0]["text"] == "Paris"
    assert hg.token_prob(out[0]["tokens"][0]) == pytest.approx(0.9)


def test_chat_makes_n_separate_requests_and_wraps_failures():
    op = FakeOpener(completion("a"))
    assert len(hg.chat([], n=3, opener=op)) == 3 and len(op.bodies) == 3
    with pytest.raises(OSError):
        hg.chat([], opener=FakeOpener({"choices": []}))


@pytest.mark.parametrize("exc", [TimeoutError("read timed out"),
                                 ConnectionResetError("reset"),
                                 http.client.BadStatusLine("junk")])
def test_read_timeouts_and_protocol_errors_surface_as_oserror(exc):
    """live 2026-09-19: a urllib read timeout is a bare TimeoutError, not a URLError,
    and escaped chat() — check_claim must turn it into `inconclusive`, never raise."""
    with pytest.raises(OSError):
        hg.chat([], opener=FakeOpener(exc))
    assert hg.check_claim("c", "s", opener=FakeOpener(exc))["status"] == "inconclusive"


# --- A: token confidence -----------------------------------------------------

def test_a_run_of_unlikely_tokens_is_one_span_a_single_one_is_not():
    toks = [tok("The", .99), tok(" year", .95), tok(" 19", .2), tok("87", .1),
            tok(" was", .9), tok(" odd", .05), tok(" but", .99)]
    spans = hg.low_confidence_spans(toks)
    assert [s["text"] for s in spans] == ["1987"]
    assert spans[0]["start"] == len("The year") and spans[0]["tokens"] == 2


def test_confident_generation_has_no_spans():
    assert hg.low_confidence_spans([tok("a", .9), tok("b", .8)]) == []


def test_guarded_generate_regenerates_once_when_it_finds_a_guess(monkeypatch):
    guessed = {"text": "built in 1987", "tokens": [tok("built", .9), tok(" 19", .1), tok("87", .1)]}
    sure = {"text": "unknown", "tokens": [tok("unknown", .95)]}
    calls = []

    def fake_chat(messages, **kw):
        calls.append(messages)
        return [guessed if len(calls) == 1 else sure]

    monkeypatch.setattr(hg, "chat", fake_chat)
    out = hg.guarded_generate("when was it built?")
    assert out["regenerated"] and out["text"] == "unknown" and out["final_spans"] == []
    assert "low-confidence" in calls[1][-1]["content"] and "'1987'" in calls[1][-1]["content"]


# --- B: self-consistency -----------------------------------------------------

def test_stable_answers_agree_and_invented_specifics_diverge():
    stable = ["The port is 4000 in litellm_config.", "It uses port 4000 in litellm_config."]
    score, div = hg.agreement(stable)
    assert score > 0.9 and div == []
    invented = ["Set MAX_RETRIES_7 in retry.py", "Set BACKOFF_3 in net/backoff.py",
                "Set JITTER_9 in util.py"]
    score, div = hg.agreement(invented)
    assert score < hg.MIN_AGREE and "backoff_3" in div


def test_self_consistency_flags_a_divergent_prompt(monkeypatch):
    answers = iter(["The CVE is CVE-2026-1111", "It is CVE-2026-2222", "CVE-2026-3333 applies"])
    monkeypatch.setattr(hg, "chat", lambda *a, **k: [{"text": next(answers), "tokens": []}
                                                     for _ in range(3)])
    out = hg.self_consistency("which CVE?", n=3)
    assert out["flagged"] and out["agreement"] < hg.MIN_AGREE


# --- C: claim check ----------------------------------------------------------

def _model(verdicts, p=0.97):
    it = iter(verdicts)
    return lambda messages, **kw: [
        {"text": v, "tokens": [tok(v, p)]} for v in [next(it) for _ in range(kw.get("n", 1))]]


def test_seeded_hallucination_is_flagged_and_a_true_claim_passes(monkeypatch):
    monkeypatch.setattr(hg, "chat", _model(["UNSUPPORTED"] * 3))
    fake = hg.check_claim("harness/acceptance.py retries failed tests 3 times", "def verify(): ...")
    assert fake["status"] == "unsupported" and fake["agreement"] == 1.0
    monkeypatch.setattr(hg, "chat", _model(["SUPPORTED"] * 3))
    assert hg.check_claim("verify() replays tests", "def verify(): ...")["status"] == "supported"


def test_split_or_hesitant_verdicts_are_unstable_not_supported(monkeypatch):
    monkeypatch.setattr(hg, "chat", _model(["SUPPORTED", "UNSUPPORTED", "SUPPORTED"]))
    assert hg.check_claim("c", "s", min_agree=0.9)["status"] == "unstable"
    monkeypatch.setattr(hg, "chat", _model(["SUPPORTED"] * 3, p=0.4))
    assert hg.check_claim("c", "s")["status"] == "unstable"


def test_unreachable_guard_model_is_inconclusive(monkeypatch):
    def down(*a, **k):
        raise OSError("connection refused")
    monkeypatch.setattr(hg, "chat", down)
    out = hg.check_claim("c", "s")
    assert out["status"] == "inconclusive" and "refused" in out["error"]


# --- the verify gate ---------------------------------------------------------

@pytest.fixture
def gate(tmp_path, monkeypatch):
    (tmp_path / "mod.py").write_text("def add(a, b):\n    return a + b\n")
    monkeypatch.setattr(acceptance, "changed_files", lambda *a, **k: ["mod.py"])
    ac = {"criteria": [{"id": "AC1", "proof_type": "citation", "text": "add sums its args"}]}
    ev = {"criteria": [{"id": "AC1", "citation": "mod.py:1-2",
                        "claim": "add() multiplies and caches the result"}]}
    return ac, ev, str(tmp_path)


def _checker(status, guard_mode):
    return lambda ac, ev, wt: hg.claim_check(
        ac, ev, wt, guard_mode=guard_mode,
        check=lambda claim, src: {"status": status, "verdicts": [status], "agreement": 1.0,
                                  "confidence": 0.9, "claim": claim})


def test_enforce_mode_fails_the_gate_on_an_unsupported_claim(gate):
    ac, ev, wt = gate
    r = acceptance.verify(ac, ev, wt, claim_check=_checker("unsupported", "enforce"))
    assert not r["ok"] and r["failed"][0]["id"] == "AC1"
    assert "unsupported" in r["failed"][0]["observed"]


def test_advisory_mode_records_but_does_not_block(gate):
    ac, ev, wt = gate
    r = acceptance.verify(ac, ev, wt, claim_check=_checker("unsupported", "advisory"))
    assert r["ok"] and len(r["claim_check"]["flagged"]) == 1


def test_inconclusive_never_blocks_even_in_enforce(gate):
    ac, ev, wt = gate
    r = acceptance.verify(ac, ev, wt, claim_check=_checker("inconclusive", "enforce"))
    assert r["ok"] and r["claim_check"]["inconclusive"] == 1


def test_first_inconclusive_short_circuits_the_remaining_claims(gate):
    ac, ev, wt = gate
    ev["criteria"] += [dict(ev["criteria"][0], id="AC2"), dict(ev["criteria"][0], id="AC3")]
    calls = []

    def check(claim, src):
        calls.append(claim)
        return {"status": "inconclusive", "error": "timed out", "claim": claim}

    cc = hg.claim_check(ac, ev, wt, guard_mode="advisory", check=check)
    assert len(calls) == 1
    assert [v["status"] for v in cc["checked"]] == ["inconclusive", "skipped", "skipped"]
    assert cc["inconclusive"] == 1 and cc["skipped"] == 2 and not cc["flagged"]


def test_no_claim_check_means_the_original_verdict(gate):
    ac, ev, wt = gate
    assert "claim_check" not in acceptance.verify(ac, ev, wt)


def test_claim_check_is_skipped_when_the_mechanical_replay_already_failed(gate, monkeypatch):
    ac, ev, wt = gate
    monkeypatch.setattr(acceptance, "changed_files", lambda *a, **k: [])
    called = []
    r = acceptance.verify(ac, ev, wt, claim_check=lambda *a: called.append(1) or {})
    assert not r["ok"] and not called


def test_mode_env_and_off_switch(monkeypatch):
    monkeypatch.delenv("FOREMAN_HALLUCINATION_GUARD", raising=False)
    assert hg.mode() == "advisory" and hg.claim_checker() is hg.claim_check
    monkeypatch.setenv("FOREMAN_HALLUCINATION_GUARD", "off")
    assert hg.claim_checker() is None
    monkeypatch.setenv("FOREMAN_HALLUCINATION_GUARD", "bogus")
    assert hg.mode() == "advisory"


def test_read_source_stays_inside_the_worktree(tmp_path):
    (tmp_path / "f.py").write_text("a\nb\nc\n")
    assert hg.read_source(str(tmp_path), "f.py:2-3") == "2: b\n3: c"
    assert hg.read_source(str(tmp_path), "../etc/passwd:1") is None
    assert hg.read_source(str(tmp_path), "not-a-citation") is None
