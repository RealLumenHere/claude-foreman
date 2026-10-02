"""wq#3335 — cf_changelog_watch tests. Offline: every network call (fetch_url,
wq add) is stubbed; the only I/O is a tmp state file."""
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(__file__))
import cf_changelog_watch as cw  # noqa: E402

BLOG_RSS = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel><title>Cloudflare Blog</title>
<item>
  <title>Announcing McpAgent and the new OAuth provider for Workers</title>
  <link>https://blog.cloudflare.com/mcp-oauth-workers/</link>
  <guid>https://blog.cloudflare.com/mcp-oauth-workers/</guid>
  <pubDate>Wed, 01 Oct 2026 12:00:00 GMT</pubDate>
  <description>&lt;p&gt;Build remote MCP servers with OAuth baked in.&lt;/p&gt;</description>
</item>
<item>
  <title>Cloudflare Birthday Week recap</title>
  <link>https://blog.cloudflare.com/birthday-week/</link>
  <guid>https://blog.cloudflare.com/birthday-week/</guid>
  <pubDate>Tue, 30 Sep 2026 09:00:00 GMT</pubDate>
  <description>&lt;p&gt;A look back at this year's announcements.&lt;/p&gt;</description>
</item>
</channel></rss>"""

CHANGELOG_RSS = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel><title>Cloudflare changelogs</title>
<item>
  <title>Access: SaaS MCP server support</title>
  <link>https://developers.cloudflare.com/changelog/post/2026-10-01-access-mcp/</link>
  <guid isPermaLink="true">https://developers.cloudflare.com/changelog/post/2026-10-01-access-mcp/</guid>
  <pubDate>Thu, 01 Oct 2026 00:00:00 GMT</pubDate>
  <description>&lt;p&gt;Access now fronts MCP servers with OAuth.&lt;/p&gt;</description>
</item>
</channel></rss>"""

NPM_LATEST = json.dumps({"name": "@cloudflare/agents", "version": "0.0.17"})


class KeywordTests(unittest.TestCase):
    def test_matches_word_boundary(self):
        self.assertEqual(cw.match_keywords("New KV namespace binding"), ["KV"])
        self.assertIn("MCP", cw.match_keywords("Announcing MCP support"))
        self.assertIn("Durable Objects", cw.match_keywords("New Durable Objects alarm API"))

    def test_no_false_positive_substring(self):
        # "KV" must not fire inside an unrelated word.
        self.assertEqual(cw.match_keywords("token value review"), [])

    def test_case_insensitive(self):
        self.assertIn("oauth", [k.lower() for k in cw.match_keywords("new oauth flow")])

    def test_no_match_on_unrelated_text(self):
        self.assertEqual(cw.match_keywords("Birthday Week recap"), [])

    def test_early_access_is_not_cf_access(self):
        # wq#3423 fixture: filed solely because 'Early Access' contains 'Access'.
        text = ("WAF - Attack Signature Detection is now available in Early Access. "
                "It evaluates requests against Cloudflare attack signatures and "
                "records matches without applying a mitigation action.")
        self.assertEqual(cw.match_keywords(text), [])

    def test_limited_and_beta_access_also_excluded(self):
        self.assertEqual(cw.match_keywords("Now in Limited Access for all plans"), [])
        self.assertEqual(cw.match_keywords("Beta Access opens next week"), [])

    def test_real_access_post_still_matches(self):
        # wq#3421 fixture: an actual Cloudflare Access change, must still match.
        title = ("Cloudflare One, Access - Simplified permissions for tagging "
                  "targets with Access for Infrastructure")
        self.assertIn("Access", cw.match_keywords(title))


class RelevanceTests(unittest.TestCase):
    def test_mcp_or_oauth_is_high(self):
        tier, _ = cw.relevance(["MCP"])
        self.assertEqual(tier, "high")
        tier, _ = cw.relevance(["OAuth"])
        self.assertEqual(tier, "high")

    def test_access_agents_workers_do_is_medium(self):
        for kw in ["Access", "Agents", "Workers", "Durable Objects"]:
            tier, _ = cw.relevance([kw])
            self.assertEqual(tier, "medium", kw)

    def test_other_keyword_is_low(self):
        tier, _ = cw.relevance(["tunnel"])
        self.assertEqual(tier, "low")

    def test_mixed_keywords_prefers_high(self):
        tier, _ = cw.relevance(["tunnel", "MCP"])
        self.assertEqual(tier, "high")

    def test_no_keywords_is_npm_default_medium(self):
        tier, rationale = cw.relevance([])
        self.assertEqual(tier, "medium")
        self.assertIn("npm", rationale)


class HtmlTruncateTests(unittest.TestCase):
    def test_strip_html_unescapes_and_strips_tags(self):
        self.assertEqual(cw.strip_html("<p>Build &amp; ship</p>"), "Build & ship")

    def test_truncate_short_text_unchanged(self):
        self.assertEqual(cw.truncate("hello world", 50), "hello world")

    def test_truncate_long_text_ellipsizes(self):
        out = cw.truncate("a" * 100, 10)
        self.assertEqual(len(out), 10)
        self.assertTrue(out.endswith("…"))

    def test_dedup_key_stable_and_distinct(self):
        self.assertEqual(cw.dedup_key("a", "b"), cw.dedup_key("a", "b"))
        self.assertNotEqual(cw.dedup_key("a", "b"), cw.dedup_key("a", "c"))


class ParseRssTests(unittest.TestCase):
    def test_parses_items(self):
        items, err = cw.parse_rss(BLOG_RSS)
        self.assertIsNone(err)
        self.assertEqual(len(items), 2)
        self.assertEqual(items[0]["title"],
                         "Announcing McpAgent and the new OAuth provider for Workers")
        self.assertEqual(items[0]["guid"], items[0]["link"])

    def test_malformed_xml_is_an_error_not_a_crash(self):
        items, err = cw.parse_rss("<rss><channel><item><title>unterminated")
        self.assertEqual(items, [])
        self.assertIsNotNone(err)


class RssFindingsTests(unittest.TestCase):
    def test_filters_by_keyword(self):
        findings, err = cw.rss_findings("cf-blog", "http://x",
                                        fetch=lambda url, timeout=15: (BLOG_RSS, None))
        self.assertIsNone(err)
        self.assertEqual(len(findings), 1)
        self.assertIn("MCP", findings[0]["keywords"])
        self.assertIn("OAuth", [k for k in findings[0]["keywords"]])

    def test_fetch_error_propagates_without_crashing(self):
        findings, err = cw.rss_findings("cf-blog", "http://x",
                                        fetch=lambda url, timeout=15: (None, "boom"))
        self.assertEqual(findings, [])
        self.assertEqual(err, "boom")

    def test_keys_are_stable_across_calls(self):
        f1, _ = cw.rss_findings("cf-blog", "http://x",
                                fetch=lambda url, timeout=15: (BLOG_RSS, None))
        f2, _ = cw.rss_findings("cf-blog", "http://x",
                                fetch=lambda url, timeout=15: (BLOG_RSS, None))
        self.assertEqual(f1[0]["key"], f2[0]["key"])

    def test_different_source_label_changes_key(self):
        f1, _ = cw.rss_findings("cf-blog", "http://x",
                                fetch=lambda url, timeout=15: (BLOG_RSS, None))
        f2, _ = cw.rss_findings("cf-changelog", "http://x",
                                fetch=lambda url, timeout=15: (BLOG_RSS, None))
        self.assertNotEqual(f1[0]["key"], f2[0]["key"])


class NpmFindingsTests(unittest.TestCase):
    def test_new_version_is_one_finding(self):
        findings, err = cw.npm_findings("@cloudflare/agents",
                                        fetch=lambda url, timeout=15: (NPM_LATEST, None))
        self.assertIsNone(err)
        self.assertEqual(len(findings), 1)
        self.assertIn("0.0.17", findings[0]["title"])
        self.assertEqual(findings[0]["keywords"], [])

    def test_fetch_error_propagates(self):
        findings, err = cw.npm_findings("@cloudflare/agents",
                                        fetch=lambda url, timeout=15: (None, "boom"))
        self.assertEqual(findings, [])
        self.assertEqual(err, "boom")

    def test_malformed_json_is_an_error_not_a_crash(self):
        findings, err = cw.npm_findings("@cloudflare/agents",
                                        fetch=lambda url, timeout=15: ("not json", None))
        self.assertEqual(findings, [])
        self.assertIsNotNone(err)

    def test_missing_version_field_is_an_error(self):
        findings, err = cw.npm_findings(
            "@cloudflare/agents",
            fetch=lambda url, timeout=15: (json.dumps({"name": "x"}), None))
        self.assertEqual(findings, [])
        self.assertIsNotNone(err)


class FileTicketTests(unittest.TestCase):
    def finding(self, keywords=("MCP",)):
        return {"source": "cf-blog", "key": "abc123", "title": "Announcing MCP support",
                "link": "https://blog.cloudflare.com/x/", "pub_date": "",
                "summary": "summary text", "keywords": list(keywords)}

    def test_dry_run_never_calls_subprocess(self):
        runner = mock.Mock()
        ok, tid = cw.file_ticket(self.finding(), "/bin/wq", "research", "p3",
                                 dry_run=True, runner=runner)
        self.assertTrue(ok)
        self.assertIsNone(tid)
        runner.assert_not_called()

    def test_success_parses_ticket_id(self):
        runner = mock.Mock(return_value=mock.Mock(returncode=0,
                                                   stdout="added task 4242 [research]\n",
                                                   stderr=""))
        ok, tid = cw.file_ticket(self.finding(), "/bin/wq", "research", "p3",
                                 dry_run=False, runner=runner)
        self.assertTrue(ok)
        self.assertEqual(tid, "4242")
        args = runner.call_args.args[0]
        self.assertEqual(args[:2], ["/bin/wq", "add"])
        self.assertIn("--lane", args)
        self.assertIn("research", args)

    def test_nonzero_exit_is_not_ok(self):
        runner = mock.Mock(return_value=mock.Mock(returncode=1, stdout="", stderr="REFUSED"))
        ok, tid = cw.file_ticket(self.finding(), "/bin/wq", "research", "p3",
                                 dry_run=False, runner=runner)
        self.assertFalse(ok)
        self.assertIsNone(tid)

    def test_subprocess_crash_is_not_ok(self):
        runner = mock.Mock(side_effect=OSError("no such file"))
        ok, tid = cw.file_ticket(self.finding(), "/bin/wq", "research", "p3",
                                 dry_run=False, runner=runner)
        self.assertFalse(ok)
        self.assertIsNone(tid)

    def test_high_relevance_in_detail(self):
        runner = mock.Mock(return_value=mock.Mock(returncode=0, stdout="added task 1 [r]\n",
                                                   stderr=""))
        cw.file_ticket(self.finding(keywords=["MCP"]), "/bin/wq", "research", "p3",
                       dry_run=False, runner=runner)
        detail = runner.call_args.args[0][runner.call_args.args[0].index("--detail") + 1]
        self.assertIn("Relevance: high", detail)
        self.assertIn("https://blog.cloudflare.com/x/", detail)


def write_state(path, seen=None):
    """Pre-creates a state file so run() does not treat the call as a first run —
    these tests are about the steady-state new/seen logic, not the bootstrap."""
    with open(path, "w") as f:
        json.dump({"seen": seen or {}}, f)


class RunTests(unittest.TestCase):
    def rss_fetcher_factory(self, mapping):
        def f(label, url):
            return mapping.get(label, ([], None))
        return f

    def test_new_findings_file_tickets_and_persist_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            state_path = os.path.join(tmp, "state.json")
            write_state(state_path)
            findings = [{"source": "cf-blog", "key": "k1", "title": "t1", "link": "l1",
                        "pub_date": "", "summary": "s", "keywords": ["MCP"]}]
            rss_fetcher = self.rss_fetcher_factory({"cf-blog": (findings, None)})
            filer = mock.Mock(return_value=(True, "99"))
            rc = cw.run(state_path=state_path, rss_sources=[("cf-blog", "http://x")],
                       npm_packages=[], rss_fetcher=rss_fetcher, ticket_filer=filer)
            self.assertEqual(rc, 0)
            filer.assert_called_once()
            with open(state_path) as f:
                state = json.load(f)
            self.assertIn("k1", state["seen"])
            self.assertEqual(state["seen"]["k1"]["ticket"], "99")

    def test_already_seen_is_not_refiled(self):
        with tempfile.TemporaryDirectory() as tmp:
            state_path = os.path.join(tmp, "state.json")
            with open(state_path, "w") as f:
                json.dump({"seen": {"k1": {"title": "t1"}}}, f)
            findings = [{"source": "cf-blog", "key": "k1", "title": "t1", "link": "l1",
                        "pub_date": "", "summary": "s", "keywords": ["MCP"]}]
            rss_fetcher = self.rss_fetcher_factory({"cf-blog": (findings, None)})
            filer = mock.Mock(return_value=(True, "1"))
            rc = cw.run(state_path=state_path, rss_sources=[("cf-blog", "http://x")],
                       npm_packages=[], rss_fetcher=rss_fetcher, ticket_filer=filer)
            self.assertEqual(rc, 0)
            filer.assert_not_called()

    def test_dry_run_does_not_persist_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            state_path = os.path.join(tmp, "state.json")
            write_state(state_path)
            before = os.path.getmtime(state_path)
            findings = [{"source": "cf-blog", "key": "k1", "title": "t1", "link": "l1",
                        "pub_date": "", "summary": "s", "keywords": ["MCP"]}]
            rss_fetcher = self.rss_fetcher_factory({"cf-blog": (findings, None)})
            filer = mock.Mock(return_value=(True, None))
            cw.run(state_path=state_path, rss_sources=[("cf-blog", "http://x")],
                  npm_packages=[], rss_fetcher=rss_fetcher, ticket_filer=filer, dry_run=True)
            self.assertEqual(os.path.getmtime(state_path), before)

    def test_one_source_erroring_does_not_block_others(self):
        with tempfile.TemporaryDirectory() as tmp:
            state_path = os.path.join(tmp, "state.json")
            write_state(state_path)
            good = [{"source": "cf-changelog", "key": "k2", "title": "t2", "link": "l2",
                    "pub_date": "", "summary": "s", "keywords": ["OAuth"]}]
            rss_fetcher = self.rss_fetcher_factory({
                "cf-blog": ([], "ConnectionError: boom"),
                "cf-changelog": (good, None),
            })
            filer = mock.Mock(return_value=(True, "7"))
            rc = cw.run(state_path=state_path,
                       rss_sources=[("cf-blog", "http://x"), ("cf-changelog", "http://y")],
                       npm_packages=[], rss_fetcher=rss_fetcher, ticket_filer=filer)
            self.assertEqual(rc, 0)  # partial outage: not every source failed
            filer.assert_called_once()

    def test_all_sources_erroring_is_nonzero_exit(self):
        with tempfile.TemporaryDirectory() as tmp:
            state_path = os.path.join(tmp, "state.json")
            rss_fetcher = self.rss_fetcher_factory({"cf-blog": ([], "boom")})
            rc = cw.run(state_path=state_path, rss_sources=[("cf-blog", "http://x")],
                       npm_packages=["@cloudflare/agents"], rss_fetcher=rss_fetcher,
                       npm_fetcher=lambda pkg: ([], "boom too"))
            self.assertEqual(rc, 1)

    def test_failed_file_ticket_is_not_marked_seen(self):
        """A wq fault must leave the finding unmarked so the next run retries it."""
        with tempfile.TemporaryDirectory() as tmp:
            state_path = os.path.join(tmp, "state.json")
            write_state(state_path)
            findings = [{"source": "cf-blog", "key": "k1", "title": "t1", "link": "l1",
                        "pub_date": "", "summary": "s", "keywords": ["MCP"]}]
            rss_fetcher = self.rss_fetcher_factory({"cf-blog": (findings, None)})
            filer = mock.Mock(return_value=(False, None))
            cw.run(state_path=state_path, rss_sources=[("cf-blog", "http://x")],
                  npm_packages=[], rss_fetcher=rss_fetcher, ticket_filer=filer)
            with open(state_path) as f:
                state = json.load(f)
            self.assertNotIn("k1", state.get("seen", {}))


class FirstRunTests(unittest.TestCase):
    """wq#3335 smoke test: the live changelog RSS carries 761 historical matches.
    A cold run must baseline them silently, not flood the queue on day one."""

    def rss_fetcher_factory(self, mapping):
        def f(label, url):
            return mapping.get(label, ([], None))
        return f

    def test_missing_state_file_seeds_without_filing(self):
        with tempfile.TemporaryDirectory() as tmp:
            state_path = os.path.join(tmp, "state.json")  # never created
            findings = [{"source": "cf-blog", "key": "k1", "title": "t1", "link": "l1",
                        "pub_date": "", "summary": "s", "keywords": ["MCP"]},
                       {"source": "cf-blog", "key": "k2", "title": "t2", "link": "l2",
                        "pub_date": "", "summary": "s", "keywords": ["OAuth"]}]
            rss_fetcher = self.rss_fetcher_factory({"cf-blog": (findings, None)})
            filer = mock.Mock(return_value=(True, "1"))
            rc = cw.run(state_path=state_path, rss_sources=[("cf-blog", "http://x")],
                       npm_packages=[], rss_fetcher=rss_fetcher, ticket_filer=filer)
            self.assertEqual(rc, 0)
            filer.assert_not_called()
            with open(state_path) as f:
                state = json.load(f)
            self.assertEqual(set(state["seen"]), {"k1", "k2"})
            self.assertTrue(state["seen"]["k1"]["seeded"])
            self.assertIsNone(state["seen"]["k1"]["ticket"])

    def test_dry_run_first_run_persists_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            state_path = os.path.join(tmp, "state.json")
            findings = [{"source": "cf-blog", "key": "k1", "title": "t1", "link": "l1",
                        "pub_date": "", "summary": "s", "keywords": ["MCP"]}]
            rss_fetcher = self.rss_fetcher_factory({"cf-blog": (findings, None)})
            filer = mock.Mock(return_value=(True, "1"))
            cw.run(state_path=state_path, rss_sources=[("cf-blog", "http://x")],
                  npm_packages=[], rss_fetcher=rss_fetcher, ticket_filer=filer, dry_run=True)
            filer.assert_not_called()
            self.assertFalse(os.path.exists(state_path))

    def test_second_run_only_alerts_on_truly_new_items(self):
        with tempfile.TemporaryDirectory() as tmp:
            state_path = os.path.join(tmp, "state.json")
            baseline = [{"source": "cf-blog", "key": "k1", "title": "t1", "link": "l1",
                        "pub_date": "", "summary": "s", "keywords": ["MCP"]}]
            rss_fetcher = self.rss_fetcher_factory({"cf-blog": (baseline, None)})
            filer = mock.Mock(return_value=(True, "1"))
            cw.run(state_path=state_path, rss_sources=[("cf-blog", "http://x")],
                  npm_packages=[], rss_fetcher=rss_fetcher, ticket_filer=filer)
            filer.assert_not_called()  # first run: seeded, not filed

            next_batch = baseline + [{"source": "cf-blog", "key": "k2", "title": "t2",
                                      "link": "l2", "pub_date": "", "summary": "s",
                                      "keywords": ["OAuth"]}]
            rss_fetcher2 = self.rss_fetcher_factory({"cf-blog": (next_batch, None)})
            rc = cw.run(state_path=state_path, rss_sources=[("cf-blog", "http://x")],
                       npm_packages=[], rss_fetcher=rss_fetcher2, ticket_filer=filer)
            self.assertEqual(rc, 0)
            filer.assert_called_once()  # only k2, the genuinely new one
            self.assertEqual(filer.call_args.args[0]["key"], "k2")

    def test_deleted_state_file_re_seeds_instead_of_reflooding(self):
        with tempfile.TemporaryDirectory() as tmp:
            state_path = os.path.join(tmp, "state.json")
            write_state(state_path, seen={"old": {"title": "old"}})
            os.remove(state_path)
            findings = [{"source": "cf-blog", "key": "k1", "title": "t1", "link": "l1",
                        "pub_date": "", "summary": "s", "keywords": ["MCP"]}]
            rss_fetcher = self.rss_fetcher_factory({"cf-blog": (findings, None)})
            filer = mock.Mock(return_value=(True, "1"))
            cw.run(state_path=state_path, rss_sources=[("cf-blog", "http://x")],
                  npm_packages=[], rss_fetcher=rss_fetcher, ticket_filer=filer)
            filer.assert_not_called()


if __name__ == "__main__":
    unittest.main()
