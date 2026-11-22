"""wq#5211: api_credit ledger, cycles, pricing, exhaustion, runtime choice. No network."""
import json
import os
import sys
from datetime import date, datetime, timezone

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))


@pytest.fixture
def ac(tmp_path, monkeypatch):
    monkeypatch.setenv("FOREMAN_API_CREDIT_DIR", str(tmp_path / "ac"))
    import importlib
    import api_credit
    importlib.reload(api_credit)
    key = tmp_path / "key"
    key.write_text("fake-key\n")
    os.makedirs(api_credit.STATE_DIR, exist_ok=True)
    with open(api_credit.CONFIG_PATH, "w") as f:
        json.dump({"enabled": True, "key_file": str(key)}, f)
    sent = []
    monkeypatch.setattr(api_credit, "_ntfy", lambda *a, **k: sent.append(a))
    api_credit._sent = sent
    return api_credit


def ts(y, m, d):
    return datetime(y, m, d, 12, tzinfo=timezone.utc).timestamp()


def test_cycles_follow_anchor_and_grant_schedule(ac):
    cfg = ac.load_config()
    c = ac.current_cycle(cfg, date(2026, 11, 21))
    assert (c["start"], c["end"], c["grant_usd"], c["plan"]) == ("2026-11-13", "2026-12-12", 200.0, "Ultra")
    c = ac.current_cycle(cfg, date(2026, 12, 12))
    assert c["id"] == "2026-11-13" and c["grant_usd"] == 200.0
    c = ac.current_cycle(cfg, date(2026, 12, 13))
    assert (c["id"], c["grant_usd"], c["plan"]) == ("2026-12-13", 100.0, "Max")
    c = ac.current_cycle(cfg, date(2027, 1, 5))
    assert c["id"] == "2026-12-13" and c["end"] == "2027-01-12"
    # before the schedule starts there is no grant
    assert ac.current_cycle(cfg, date(2026, 10, 20))["grant_usd"] == 0.0


def test_cost_matches_pricing_page(ac):
    u = {"input_tokens": 1_000_000, "output_tokens": 1_000_000,
         "cache_read_input_tokens": 1_000_000, "cache_creation_input_tokens": 1_000_000}
    assert ac.cost_usd("anthropic/claude-opus-5-5", u) == pytest.approx(4 + 20 + 0.2 + 5)
    small = {"input_tokens": 50_000, "output_tokens": 1000}
    assert ac.cost_usd("claude-haiku-5-5", small) == pytest.approx((50_000 * 0.10 + 1000 * 0.50) / 1e6)
    long = {"input_tokens": 120_000, "output_tokens": 1000}
    assert ac.cost_usd("claude-haiku-5-5", long) == pytest.approx((120_000 * 0.50 + 1000 * 2.50) / 1e6)
    # unknown model: priced at the dearest fallback, never zero
    assert ac.cost_usd("anthropic/claude-new-9", {"output_tokens": 1_000_000}) == pytest.approx(50)


def test_record_accumulates_alerts_once_and_caps(ac):
    cfg = ac.load_config()
    now = ts(2026, 11, 21)
    # $100 of opus output = 5M output tokens -> 50%
    r = ac.record("anthropic/claude-opus-5-5", {"output_tokens": 5_000_000}, ticket="1", cfg=cfg, now=now)
    assert r["pct"] == pytest.approx(50)
    assert len(ac._sent) == 1 and "50%" in ac._sent[0][0]
    ac.record("anthropic/claude-opus-5-5", {"output_tokens": 1}, cfg=cfg, now=now)
    assert len(ac._sent) == 1  # no duplicate alert
    st = ac.status(cfg, now=now)
    assert st["available"] and st["cap_usd"] == 195.0
    ac.record("anthropic/claude-opus-5-5", {"output_tokens": 4_800_000}, cfg=cfg, now=now)  # $196
    st = ac.status(cfg, now=now)
    assert not st["available"] and any("local cap" in r for r in st["reasons"])
    assert [a for a in st["alerts"]] == [50, 80, 95]
    with open(ac.LEDGER_PATH) as f:
        lines = [json.loads(x) for x in f]
    assert len(lines) == 3 and lines[0]["ticket"] == "1" and lines[0]["cycle"] == "2026-11-13"


def test_exhausted_flag_is_per_cycle(ac):
    cfg = ac.load_config()
    assert ac.mark_exhausted("credit balance too low", cfg=cfg, now=ts(2026, 12, 1)) is True
    assert ac.mark_exhausted("again", cfg=cfg, now=ts(2026, 12, 2)) is False
    assert not ac.status(cfg, now=ts(2026, 12, 5))["available"]
    # next billing cycle: flag does not carry over
    nxt = ac.status(cfg, now=ts(2026, 12, 14))
    assert nxt["available"], nxt["reasons"]
    assert nxt["cycle"]["grant_usd"] == 100.0


def test_prior_spend_counts(ac):
    cfg = ac.load_config()
    cfg["prior_spend_usd"] = {"2026-11-13": 196.0}
    st = ac.status(cfg, now=ts(2026, 11, 21))
    assert not st["available"] and st["spend_usd"] == 196.0


def test_error_detection(ac):
    assert ac.is_credit_exhausted_error(
        '{"type":"invalid_request_error","message":"Your credit balance is too low to access the Anthropic API."}')
    assert ac.is_credit_exhausted_error("ExceededBudget: Budget has been exceeded! Current cost: 190.1")
    assert not ac.is_credit_exhausted_error("overloaded_error")


def test_choose_runtime(ac, monkeypatch):
    t = {"title": "do a thing", "notes": "None", "lane": "build"}
    rt, m, why = ac.choose_runtime(t, "claude-opus-5-5", "standard-engineering")
    assert rt == "sdk" and m == "anthropic/claude-opus-5-5", why
    assert ac.choose_runtime(t, "claude-sonnet-5-5", None)[0] == "sdk"
    assert ac.choose_runtime(t, "claude-fable-5-2", None)[0] is None          # Fable stays CLI
    assert ac.choose_runtime(t, "deepseek-v4-flash", None)[0] is None         # local rung stays
    assert ac.choose_runtime(t, "claude-opus-5-5", "fire-response")[0] is None
    assert ac.choose_runtime({"title": "x [no-sdk]"}, "claude-opus-5-5", None)[0] is None
    assert ac.choose_runtime({"title": "x [sdk]"}, "claude-fable-5-2", None)[0] == "sdk"
    assert ac.choose_runtime(t, "claude-opus-5-5", None, explicit="claude")[0] is None
    # "None" strings from `wq show` are empty, not a Claude Code session
    assert ac.choose_runtime(t, "claude-opus-5-5", "None", resume_session="None")[0] == "sdk"
    assert ac.choose_runtime(t, "claude-opus-5-5", None, resume_session="abc-123")[0] is None
    assert ac.choose_runtime(t, "claude-opus-5-5", None, resume_session="sdk-1f2e")[0] == "sdk"
    ac.mark_exhausted("dry")
    rt, _, why = ac.choose_runtime(t, "claude-opus-5-5", None)
    assert rt is None and "unavailable" in why


def test_missing_config_means_disabled(ac):
    os.remove(ac.CONFIG_PATH)
    st = ac.status()
    assert st["available"] is False and "disabled in config" in st["reasons"]


def test_status_fails_closed_on_bad_config(ac):
    with open(ac.CONFIG_PATH, "w") as f:
        f.write("{not json")
    st = ac.status()
    assert st["available"] is False
    assert ac.choose_runtime({"title": "t"}, "claude-opus-5-5", None)[0] is None


def test_ledger_lines_carry_the_pytest_marker_only_under_pytest(ac, monkeypatch):
    """The repo conftest's leak tripwire keys on this marker, so a production SDK worker
    writing the live ledger during a suite run never fails a test (PR #2694 false errors)."""
    monkeypatch.setenv("FOREMAN_API_CREDIT_TEST_RUN", "pytest-1-2")
    ac.record("anthropic/claude-opus-5-5", {"output_tokens": 1})
    monkeypatch.delenv("FOREMAN_API_CREDIT_TEST_RUN")
    ac.record("anthropic/claude-opus-5-5", {"output_tokens": 1})
    with open(ac.LEDGER_PATH) as f:
        lines = [json.loads(x) for x in f]
    assert lines[0]["test_run"] == "pytest-1-2" and "test_run" not in lines[1]


def test_conftest_marks_the_environment():
    assert os.environ.get("FOREMAN_API_CREDIT_TEST_RUN", "").startswith("pytest-")
