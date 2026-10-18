"""wq#4232: tiered research search — DeepSeek primary, caller-injected
stealth-browser hook, SearXNG final fallback. Every tier is mocked here;
no test in this file touches the network.
"""
import os
import sys

RESEARCH = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RESEARCH)
import search_tier  # noqa: E402


def test_no_deepseek_key_falls_through_without_network(monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.setattr(search_tier, "DEEPSEEK_KEY_FILE", "/nonexistent/deepseek.token")
    assert search_tier._deepseek_key("/nonexistent/deepseek.token") is None
    assert search_tier.deepseek_search("q", key_file="/nonexistent/deepseek.token") is None


def test_tiered_search_prefers_deepseek(monkeypatch):
    monkeypatch.setattr(search_tier, "deepseek_search", lambda q, n=6: [{"title": "ds"}])
    monkeypatch.setattr(search_tier, "searxng_search", lambda q, n=6: [{"title": "sx"}])
    results, tier = search_tier.tiered_search("q")
    assert tier == "deepseek"
    assert results == [{"title": "ds"}]


def test_tiered_search_uses_injected_browser_fetch_before_searxng(monkeypatch):
    monkeypatch.setattr(search_tier, "deepseek_search", lambda q, n=6: None)
    monkeypatch.setattr(search_tier, "searxng_search", lambda q, n=6: [{"title": "sx"}])
    browser_fetch = lambda q, n: [{"title": "browser"}]
    results, tier = search_tier.tiered_search("q", browser_fetch=browser_fetch)
    assert tier == "stealth-browser"
    assert results == [{"title": "browser"}]


def test_tiered_search_falls_back_to_searxng_without_browser_fetch(monkeypatch):
    monkeypatch.setattr(search_tier, "deepseek_search", lambda q, n=6: None)
    monkeypatch.setattr(search_tier, "searxng_search", lambda q, n=6: [{"title": "sx"}])
    results, tier = search_tier.tiered_search("q")
    assert tier == "searxng"
    assert results == [{"title": "sx"}]


def test_tiered_search_all_tiers_empty_returns_none_tier(monkeypatch):
    monkeypatch.setattr(search_tier, "deepseek_search", lambda q, n=6: None)
    monkeypatch.setattr(search_tier, "searxng_search", lambda q, n=6: None)
    results, tier = search_tier.tiered_search("q")
    assert results == []
    assert tier == "none"


def test_findings_block_empty_when_no_results():
    assert search_tier.findings_block("q", search=lambda q, n: ([], "none")) == ""


def test_findings_block_renders_tier_and_entries():
    fake = lambda q, n: ([{"title": "T", "url": "http://x", "snippet": "s"}], "deepseek")
    block = search_tier.findings_block("q", search=fake)
    assert "tier: deepseek" in block
    assert "T (http://x): s" in block


def test_searxng_search_missing_binary_returns_none():
    assert search_tier.searxng_search("q", fleet_search_bin="/nonexistent/fleet-search") is None


def test_deepseek_status_no_key():
    assert search_tier.deepseek_status(key_file="/nonexistent/deepseek.token") == "no_key"


def test_deepseek_status_distinguishes_unfunded_from_unauthorized(monkeypatch):
    import urllib.error

    def fake_urlopen(req, timeout=None):
        raise urllib.error.HTTPError(req.full_url, 402, "Payment Required", {}, None)

    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk" + "-" + "test" * 4)
    monkeypatch.setattr(search_tier.urllib.request, "urlopen", fake_urlopen)
    assert search_tier.deepseek_status() == "unfunded"


def test_deepseek_status_unreachable_on_network_error(monkeypatch):
    import urllib.error

    def fake_urlopen(req, timeout=None):
        raise urllib.error.URLError("no route")

    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk" + "-" + "test" * 4)
    monkeypatch.setattr(search_tier.urllib.request, "urlopen", fake_urlopen)
    assert search_tier.deepseek_status() == "unreachable"
