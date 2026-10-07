"""wq#2500/wq#3576 — proves the diff/star-bucketing logic against fixtures: synthetic
ones plus a pinned, trimmed snapshot of the real daily/all feed (2026-10-07). No
network, no live Miniflux/scout needed.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from trending_vs_scout_diff import (  # noqa: E402
    build_report,
    main,
    repos_from_miniflux_entries,
    repos_from_scout_prompts,
    repos_from_text,
    repos_from_trending_file,
    star_distribution,
)

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "daily_all_2026-10-07.xml"


def test_repos_from_text_extracts_owner_repo_from_urls():
    text = "New today: https://github.com/foo-bar/baz_qux (1.2k stars) and dup https://github.com/foo-bar/baz_qux again"
    assert repos_from_text(text) == {"foo-bar/baz_qux"}


def test_repos_from_text_ignores_trailing_punctuation():
    text = "see (https://github.com/acme/widget), also \"github.com/acme/gizmo\" and <github.com/acme/thing>."
    assert repos_from_text(text) == {"acme/widget", "acme/gizmo", "acme/thing"}


def test_repos_from_miniflux_entries_reads_url_and_title():
    entries = [
        {"url": "https://github.com/rising/star", "title": "rising/star"},
        {"url": "https://example.com/not-github", "title": "unrelated"},
    ]
    assert repos_from_miniflux_entries(entries) == {"rising/star"}


def test_repos_from_scout_prompts_reads_only_gap_prompts(tmp_path):
    (tmp_path / "00_gap_known_project.txt").write_text("Repo: https://github.com/known/project")
    (tmp_path / "news_1.txt").write_text("https://github.com/news/not-scout")
    (tmp_path / "ignored.md").write_text("https://github.com/should-not/count")
    assert repos_from_scout_prompts(str(tmp_path)) == {"known/project"}


def test_scout_pool_includes_consumed_prompts(tmp_path):
    """The live queue dir is empty most of the time: consumed prompts move to
    research_prompts_done/. Reading one dir reported every repo as missed."""
    queue, done = tmp_path / "research_prompts", tmp_path / "research_prompts_done"
    queue.mkdir()
    done.mkdir()
    (done / "00_gap_known_project.txt").write_text("Repo: https://github.com/known/project")
    assert repos_from_scout_prompts(str(queue)) == set()
    assert repos_from_scout_prompts([str(queue), str(done)]) == {"known/project"}


def test_trending_file_reads_the_pinned_rss_snapshot():
    repos = repos_from_trending_file(str(FIXTURE))
    assert len(repos) == 12
    assert "earthtojake/text-to-cad" in repos
    assert all("/" in r and not r.startswith("http") for r in repos)


def test_trending_file_sniffs_json_and_text(tmp_path):
    j = tmp_path / "entries.json"
    j.write_text(json.dumps({"total": 1, "entries": [{"url": "https://github.com/a/b", "title": "a/b"}]}))
    t = tmp_path / "urls.txt"
    t.write_text("https://github.com/c/d\nhttps://github.com/e/f\n")
    assert repos_from_trending_file(str(j)) == {"a/b"}
    assert repos_from_trending_file(str(t)) == {"c/d", "e/f"}


def test_cli_offline_against_pinned_fixture(tmp_path, capsys):
    (tmp_path / "00_gap_earthtojake_text_to_cad.txt").write_text(
        "Repo: https://github.com/earthtojake/text-to-cad")
    rc = main(["--trending", str(FIXTURE), "--scout-prompt-dir", str(tmp_path), "--no-stars"])
    report = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert report["trending_total"] == 12
    assert report["missed_total"] == 11
    assert "earthtojake/text-to-cad" not in report["missed_repos"]


def test_star_distribution_buckets_and_median():
    dist = star_distribution([10, 60, 5000, 5000])
    assert dist["n"] == 4
    assert dist["median"] == 2530
    assert dist["buckets"]["0-50"] == 1
    assert dist["buckets"]["50-200"] == 1
    assert dist["buckets"]["1000-10000"] == 2
    assert dist["buckets"]["10000-+"] == 0


def test_star_distribution_empty_input():
    dist = star_distribution([])
    assert dist == {
        "n": 0,
        "median": None,
        "buckets": {"0-50": 0, "50-200": 0, "200-1000": 0, "1000-10000": 0, "10000-+": 0},
    }


def test_build_report_end_to_end_the_measured_defect():
    """Mirrors the actual wq#1717 shape: scout only ever queues high-star repos, so
    a newly-trending, low-star project is exactly what it structurally misses."""
    trending = {"newcomer/fast-riser", "known/big-project"}
    scout = {"known/big-project", "unrelated/also-big"}
    report = build_report(trending, scout, star_lookup=lambda r: 42)

    assert report["trending_total"] == 2
    assert report["missed_total"] == 1
    assert report["missed_repos"] == ["newcomer/fast-riser"]
    assert report["missed_star_distribution"]["n"] == 1
    assert report["missed_star_distribution"]["buckets"]["0-50"] == 1


def test_build_report_nothing_missed_when_scout_already_covers_trending():
    report = build_report({"a/b"}, {"a/b", "c/d"}, star_lookup=lambda r: 1)
    assert report["missed_total"] == 0
    assert report["missed_star_distribution"]["n"] == 0
