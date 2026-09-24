"""wq#2500 — proves the acceptance-4 diff/star-bucketing logic against synthetic
fixtures. No network, no live Miniflux/scout needed: the real week-long measurement
is on-host follow-up (README.md), this is what makes the tool itself real today.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from trending_vs_scout_diff import (  # noqa: E402
    build_report,
    repos_from_miniflux_entries,
    repos_from_scout_prompts,
    repos_from_text,
    star_distribution,
)


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


def test_repos_from_scout_prompts_reads_glob(tmp_path):
    (tmp_path / "news_1.txt").write_text("gap analysis on https://github.com/known/project")
    (tmp_path / "ignored.md").write_text("https://github.com/should-not/count")
    assert repos_from_scout_prompts(str(tmp_path)) == {"known/project"}


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
