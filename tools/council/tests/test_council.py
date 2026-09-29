import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import council as c  # noqa: E402

GOOD_REPORT = "## Consensus\nx\n## Disagreements\nNone.\n## Synthesis\ny\n## Confidence\nhigh"


def make_council(models=("m1", "m2", "m3"), chairman=("m1", "m2"), min_responses=2):
    return c.Council(seats=[c.Seat(m, m, 5) for m in models], chairman=list(chairman),
                     min_responses=min_responses)


class FakeChat:
    """Scripted gateway: per-model reply string, GatewayError instance, or callable."""

    def __init__(self, members, chairs=None):
        self.members, self.chairs = members, chairs or {}
        self.calls = []

    def __call__(self, model, system, user, *, max_tokens, timeout_s):
        is_chair = system == c.CHAIRMAN_SYSTEM
        self.calls.append((model, "chair" if is_chair else "member", user))
        reply = (self.chairs if is_chair else self.members)[model]
        if isinstance(reply, Exception):
            raise reply
        return reply


def test_fan_out_labels_only_successful_seats():
    chat = FakeChat({"m1": "a1", "m2": c.GatewayError("timeout"), "m3": "a3"},
                    {"m1": GOOD_REPORT})
    r = c.run("q?", make_council(), chat)
    assert [(a.model, a.ok, a.label) for a in r.answers] == [
        ("m1", True, "A"), ("m2", False, ""), ("m3", True, "B")]
    assert r.chairman == "m1" and r.synthesis == GOOD_REPORT


def test_chairman_prompt_is_anonymized():
    chat = FakeChat({"m1": "alpha", "m2": "beta", "m3": "gamma"}, {"m1": GOOD_REPORT})
    c.run("the question", make_council(), chat)
    chair_user = [u for m, kind, u in chat.calls if kind == "chair"][0]
    assert "Response A" in chair_user and "Response C" in chair_user
    assert "alpha" in chair_user and "the question" in chair_user
    assert "m1" not in chair_user and "m3" not in chair_user


def test_chairman_that_failed_as_seat_is_skipped_without_a_call():
    chat = FakeChat({"m1": c.GatewayError("down"), "m2": "b", "m3": "c"},
                    {"m2": GOOD_REPORT})
    r = c.run("q", make_council(), chat)
    assert r.chairman == "m2"
    assert r.chairman_attempts[0] == {"model": "m1", "skipped": "failed as a seat"}
    assert ("m1", "chair") not in [(m, k) for m, k, _ in chat.calls]


def test_chairman_missing_sections_falls_through():
    chat = FakeChat({"m1": "a", "m2": "b", "m3": "c"},
                    {"m1": "just prose, no headings", "m2": GOOD_REPORT})
    r = c.run("q", make_council(), chat)
    assert r.chairman == "m2"
    assert r.chairman_attempts[0]["missing_sections"] == list(c.REQUIRED_SECTIONS)


def test_below_min_responses_skips_synthesis():
    chat = FakeChat({"m1": "a", "m2": c.GatewayError("x"), "m3": c.GatewayError("y")})
    r = c.run("q", make_council(), chat)
    assert r.synthesis == "" and all(k == "member" for _, k, _ in chat.calls)


def test_load_config_drops_disabled_and_applies_overrides(tmp_path):
    cfg = tmp_path / "c.yaml"
    cfg.write_text(
        "members:\n"
        "  - {seat: a, model: ma, timeout_s: 9}\n"
        "  - {seat: b, model: mb, enabled: false}\n"
        "  - {model: mc}\n"
        "chairman: [mc]\ntimeout_s: 30\nmin_responses: 1\n")
    council = c.load_config(cfg)
    assert [(s.seat, s.model, s.timeout_s) for s in council.seats] == [
        ("a", "ma", 9.0), ("mc", "mc", 30.0)]
    assert council.chairman == ["mc"] and council.min_responses == 1
    over = c.load_config(cfg, ["x", "y"])
    assert [s.model for s in over.seats] == ["x", "y"]


def test_load_config_drops_disabled_seat_from_chairman_list(tmp_path):
    """wq#3168: a disabled seat's model must never survive into council.chairman — before
    this, synthesize() had no way to know the model was retired and would call it for a
    real timeout even though fan_out() never touches it."""
    cfg = tmp_path / "c.yaml"
    cfg.write_text(
        "members:\n"
        "  - {seat: a, model: ma}\n"
        "  - {seat: dead, model: md, enabled: false}\n"
        "chairman: [md, ma]\n")
    council = c.load_config(cfg)
    assert council.chairman == ["ma"]


def test_shipped_config_loads():
    council = c.load_config(c.DEFAULT_CONFIG)
    assert len(council.seats) >= 3 and council.chairman


def test_main_writes_record_and_exit_codes(tmp_path, monkeypatch, capsys):
    cfg = tmp_path / "c.yaml"
    cfg.write_text("members:\n  - {model: m1}\n  - {model: m2}\nchairman: [m1]\n")
    monkeypatch.setattr(c, "gateway_chat", FakeChat({"m1": "a", "m2": "b"},
                                                     {"m1": GOOD_REPORT}))
    rc =c.main(["q", "--config", str(cfg), "--out-dir", str(tmp_path / "out")])
    assert rc == 0
    assert "## Synthesis" in capsys.readouterr().out
    record = json.loads(next((tmp_path / "out").glob("council-*.json")).read_text())
    assert record["chairman"] == "m1" and len(record["answers"]) == 2

    monkeypatch.setattr(c, "gateway_chat", FakeChat({"m1": "a", "m2": "b"},
                                                     {"m1": "no headings"}))
    assert c.main(["q", "--config", str(cfg), "--out-dir", str(tmp_path / "out")]) == 4


def test_gateway_chat_rejects_empty_completion(monkeypatch):
    class Resp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return json.dumps({"choices": [{"message": {"content": None},
                                            "finish_reason": "length"}]}).encode()

    monkeypatch.setenv("LITELLM_API_KEY", "k")
    monkeypatch.setattr(c.urllib.request, "urlopen", lambda req, timeout: Resp())
    with pytest.raises(c.GatewayError, match="finish_reason=length"):
        c.gateway_chat("m", "s", "u", max_tokens=5, timeout_s=1)


def test_429_retried_once_other_errors_final():
    calls = {"n": 0}

    def flaky(model, system, user, *, max_tokens, timeout_s):
        calls["n"] += 1
        if calls["n"] == 1:
            raise c.GatewayError("quota", status=429)
        return "ok"

    council = make_council()
    slept = []
    ans = c.ask_seat(council.seats[0], "q", council, flaky, sleep=slept.append)
    assert ans.ok and calls["n"] == 2 and slept == [council.retry_429_s]

    def always_500(model, system, user, *, max_tokens, timeout_s):
        calls["n"] += 1
        raise c.GatewayError("boom", status=500)

    calls["n"] = 0
    ans = c.ask_seat(council.seats[0], "q", council, always_500, sleep=slept.append)
    assert not ans.ok and calls["n"] == 1


def test_seat_max_tokens_overrides_council_default():
    seen = {}

    def chat(model, system, user, *, max_tokens, timeout_s):
        seen[model] = max_tokens
        return "x"

    council = make_council(models=("a", "b"))
    council.seats[0].max_tokens = 111
    c.fan_out("q", council, chat)
    assert seen == {"a": 111, "b": council.member_max_tokens}


def test_dry_run_calls_nothing(capsys):
    assert c.main(["q", "--dry-run", "--models", "a,b"]) == 0
    assert json.loads(capsys.readouterr().out)["seats"][1]["model"] == "b"
