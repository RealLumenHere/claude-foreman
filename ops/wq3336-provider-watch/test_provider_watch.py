import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(__file__))
import provider_watch as pw


CF_RSS = """<?xml version="1.0"?>
<rss version="2.0"><channel>
<item><title>New MCP server</title><link>https://blog.cloudflare.com/mcp</link>
<guid>guid-mcp</guid><description>Launching an MCP server for Workers</description>
<pubDate>Mon, 01 Sep 2026 00:00:00 GMT</pubDate></item>
<item><title>Unrelated marketing post</title><link>https://blog.cloudflare.com/x</link>
<guid>guid-x</guid><description>Nothing relevant here</description>
<pubDate>Tue, 02 Sep 2026 00:00:00 GMT</pubDate></item>
</channel></rss>"""

PLAIN_RSS = """<?xml version="1.0"?>
<rss version="2.0"><channel>
<item><title>Tailscale 1.90</title><link>https://tailscale.com/blog/1-90</link>
<guid>ts-1</guid><description>release notes</description>
<pubDate>Mon, 01 Sep 2026 00:00:00 GMT</pubDate></item>
</channel></rss>"""

SITEMAP = """<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
<url><loc>https://www.cerebras.ai/blog/some-post</loc><lastmod>2026-09-01</lastmod></url>
<url><loc>https://www.cerebras.ai/press-release/other</loc></url>
</urlset>"""


def fetcher(table):
    def _fetch(url, timeout=15, headers=None):
        for prefix, (text, err) in table.items():
            if url.startswith(prefix):
                return text, err
        return None, f"no fixture for {url}"
    return _fetch


# ---- source-type fetchers -------------------------------------------------

def test_rss_findings_keyword_filter_matches_only_relevant_items():
    fetch = fetcher({"https://blog.cloudflare.com/rss": (CF_RSS, None)})
    findings, err = pw.rss_findings("cloudflare", "cf-blog", "https://blog.cloudflare.com/rss",
                                     keywords=pw.KEYWORDS, fetch=fetch)
    assert err is None
    assert len(findings) == 1
    assert findings[0]["title"] == "New MCP server"
    assert "MCP" in findings[0]["keywords"]


def test_rss_findings_no_keywords_returns_every_item():
    fetch = fetcher({"https://tailscale.com/blog/index.xml": (PLAIN_RSS, None)})
    findings, err = pw.rss_findings("tailscale", "tailscale-blog",
                                     "https://tailscale.com/blog/index.xml", fetch=fetch)
    assert err is None
    assert len(findings) == 1
    assert findings[0]["provider"] == "tailscale"
    assert findings[0]["source_type"] == "rss"
    assert findings[0]["keywords"] == []


def test_rss_findings_fetch_error_propagates():
    fetch = fetcher({})
    findings, err = pw.rss_findings("tailscale", "tailscale-blog", "https://nope", fetch=fetch)
    assert findings == []
    assert err


def test_npm_findings_builds_version_finding():
    fetch = fetcher({"https://registry.npmjs.org": (json.dumps({"version": "2.3.4"}), None)})
    findings, err = pw.npm_findings("cloudflare", "@cloudflare/agents", fetch=fetch)
    assert err is None
    assert findings[0]["title"] == "@cloudflare/agents@2.3.4 published"
    assert findings[0]["key"] == pw.dedup_key("npm", "@cloudflare/agents", "2.3.4")


def test_pypi_findings_builds_version_finding():
    fetch = fetcher({"https://pypi.org/pypi": (json.dumps({"info": {"version": "1.2.3"}}), None)})
    findings, err = pw.pypi_findings("litellm", "litellm", fetch=fetch)
    assert err is None
    assert findings[0]["title"] == "litellm==1.2.3 published"
    assert findings[0]["source_type"] == "pypi"


def test_github_releases_findings_builds_release_finding():
    body = json.dumps({"tag_name": "v1.2.3", "name": "v1.2.3", "html_url": "https://x",
                        "published_at": "2026-09-01T00:00:00Z", "body": "notes"})
    fetch = fetcher({"https://api.github.com": (body, None)})
    findings, err = pw.github_releases_findings("ollama", "ollama/ollama", fetch=fetch)
    assert err is None
    assert findings[0]["key"] == pw.dedup_key("gh-release", "ollama/ollama", "v1.2.3")
    assert findings[0]["link"] == "https://x"


def test_github_releases_findings_404_is_a_source_error_not_a_crash():
    fetch = fetcher({"https://api.github.com": (None, "HTTPError: HTTP Error 404: Not Found")})
    findings, err = pw.github_releases_findings("pikvm", "pikvm/kvmd-armbian", fetch=fetch)
    assert findings == []
    assert "404" in err


def test_sitemap_findings_filters_by_path():
    fetch = fetcher({"https://www.cerebras.ai/sitemap.xml": (SITEMAP, None)})
    findings, err = pw.sitemap_findings("cerebras", "cerebras-blog",
                                         "https://www.cerebras.ai/sitemap.xml", "/blog/",
                                         fetch=fetch)
    assert err is None
    assert len(findings) == 1
    assert findings[0]["link"] == "https://www.cerebras.ai/blog/some-post"


# ---- relevance tiering ------------------------------------------------------

def test_relevance_high_for_mcp_oauth():
    tier, _ = pw.relevance({"keywords": ["MCP"]})
    assert tier == "high"


def test_relevance_medium_for_generic_access_keyword():
    tier, _ = pw.relevance({"keywords": ["Workers"]})
    assert tier == "medium"


def test_relevance_low_for_other_keyword():
    tier, _ = pw.relevance({"keywords": ["tunnel"]})
    assert tier == "low"


def test_relevance_default_for_no_keywords_by_source_type():
    tier, rationale = pw.relevance({"keywords": [], "source_type": "github_releases"})
    assert tier == "medium"
    assert "GitHub release" in rationale


# ---- dispatch ---------------------------------------------------------------

def test_findings_for_source_dispatches_each_type():
    npm_fetch = fetcher({"https://registry.npmjs.org": (json.dumps({"version": "1.0.0"}), None)})
    source = {"provider": "cloudflare", "type": "npm", "pkg": "@cloudflare/agents"}
    findings, err = pw.findings_for_source(source, fetch=npm_fetch)
    assert err is None
    assert findings[0]["source_type"] == "npm"


def test_findings_for_source_unknown_type_raises():
    with pytest.raises(ValueError):
        pw.findings_for_source({"provider": "x", "type": "bogus"})


def test_default_providers_excludes_cloudflare():
    assert "cloudflare" not in pw.DEFAULT_PROVIDERS
    assert "tailscale" in pw.DEFAULT_PROVIDERS
    assert "pikvm" in pw.DEFAULT_PROVIDERS


def test_every_source_has_a_known_type_and_provider():
    known_types = {"rss", "npm", "pypi", "github_releases", "sitemap"}
    for s in pw.SOURCES:
        assert s["type"] in known_types
        assert s["provider"]


# ---- run(): first-run baseline, dedup, error accounting --------------------

def _finding(key, provider="tailscale", source="tailscale-blog"):
    return {"provider": provider, "source": source, "source_type": "rss", "key": key,
            "title": f"item {key}", "link": "https://x", "pub_date": "",
            "summary": "", "keywords": []}


def test_run_first_run_seeds_baseline_without_filing(tmp_path):
    state_path = str(tmp_path / "state.json")
    sources = [{"provider": "tailscale", "type": "rss", "label": "tailscale-blog", "url": "u"}]
    calls = []

    def source_fetcher(source):
        return [_finding("a"), _finding("b")], None

    def ticket_filer(*a, **k):
        calls.append(a)
        return True, "999"

    rc = pw.run(sources, state_path=state_path, source_fetcher=source_fetcher,
                ticket_filer=ticket_filer)
    assert rc == 0
    assert calls == []
    state = json.loads(open(state_path).read())
    assert set(state["seen"]) == {"a", "b"}
    assert all(v["seeded"] for v in state["seen"].values())


def test_run_second_run_files_only_new_findings(tmp_path):
    state_path = str(tmp_path / "state.json")
    sources = [{"provider": "tailscale", "type": "rss", "label": "tailscale-blog", "url": "u"}]

    pw.run(sources, state_path=state_path,
           source_fetcher=lambda s: ([_finding("a")], None),
           ticket_filer=lambda *a, **k: (True, "1"))

    calls = []

    def ticket_filer(finding, *a, **k):
        calls.append(finding["key"])
        return True, "2"

    rc = pw.run(sources, state_path=state_path,
                source_fetcher=lambda s: ([_finding("a"), _finding("b")], None),
                ticket_filer=ticket_filer)
    assert rc == 0
    assert calls == ["b"]


def test_run_source_error_is_a_warning_unless_all_sources_fail(tmp_path):
    state_path = str(tmp_path / "state.json")
    pw.save_state(state_path, {"seen": {}})
    sources = [{"provider": "a", "type": "rss", "label": "a", "url": "u"},
               {"provider": "b", "type": "rss", "label": "b", "url": "u"}]

    rc = pw.run(sources, state_path=state_path,
                source_fetcher=lambda s: ([], "boom"),
                ticket_filer=lambda *a, **k: (True, None))
    assert rc == 1

    rc = pw.run(sources, state_path=state_path,
                source_fetcher=lambda s: (([], "boom") if s["provider"] == "a" else ([], None)),
                ticket_filer=lambda *a, **k: (True, None))
    assert rc == 0


def test_run_corrupt_state_file_rebaselines_instead_of_reflooding(tmp_path):
    state_path = str(tmp_path / "state.json")
    with open(state_path, "w") as f:
        f.write("{not json")
    sources = [{"provider": "tailscale", "type": "rss", "label": "tailscale-blog", "url": "u"}]
    calls = []
    rc = pw.run(sources, state_path=state_path,
                source_fetcher=lambda s: ([_finding("a")], None),
                ticket_filer=lambda *a, **k: calls.append(1) or (True, "1"))
    assert rc == 0
    assert calls == []


# ---- CLI --------------------------------------------------------------------

def test_main_run_rejects_unknown_provider(capsys, monkeypatch):
    monkeypatch.setattr(sys, "argv", ["provider_watch.py", "run", "--providers", "bogus",
                                       "--dry-run"])
    rc = pw.main()
    assert rc == 2
    assert "unknown provider" in capsys.readouterr().err


def test_main_list_sources_prints_every_provider(capsys, monkeypatch):
    monkeypatch.setattr(sys, "argv", ["provider_watch.py", "list-sources"])
    rc = pw.main()
    out = capsys.readouterr().out
    assert rc == 0
    for provider in {s["provider"] for s in pw.SOURCES}:
        assert provider in out
