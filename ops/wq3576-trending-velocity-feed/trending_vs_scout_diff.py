#!/usr/bin/env python3
"""wq#2500/wq#3576 — is the velocity feed worth keeping.

One week of Miniflux-captured Trending entries, diffed against github_scout's
candidate pool for the same week: how many Trending repos did the scout never
queue, and at what star counts. That number decides whether this stays
(research/DIGEST_Orchestation_2_md.md:249-251).

Parsing is decoupled from I/O so the logic is provable without a live Miniflux
instance or scout log (see test_diff_report.py, run entirely against fixtures).
"""
import argparse
import glob
import json
import os
import re
import statistics
import sys
import urllib.request
import xml.etree.ElementTree as ET

REPO_RE = re.compile(r"github\.com/([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+?)(?:[/)\s\"'>]|$)")

STAR_BUCKETS = [(0, 50), (50, 200), (200, 1000), (1000, 10_000), (10_000, None)]


def repos_from_text(text):
    """Every distinct owner/repo a github.com URL in `text` names."""
    return {m.group(1).rstrip(".") for m in REPO_RE.finditer(text)}


def repos_from_miniflux_entries(entries):
    """entries: the Miniflux `entries` array (GET /v1/feeds/{id}/entries).

    Each entry's `url` and `title` are checked — RSSHub's github/trending route sets
    both to the repo, but title-only matching covers a route shape change cheaply.
    """
    repos = set()
    for e in entries:
        repos |= repos_from_text(e.get("url", "") + " " + e.get("title", ""))
    return repos


def repos_from_rss(xml_text):
    """Every repo an RSS <item><link> names (a GitHubTrendingRSS snapshot)."""
    channel = ET.fromstring(xml_text).find("channel")
    links = " ".join(i.findtext("link") or "" for i in channel.iter("item"))
    return repos_from_text(links)


def repos_from_trending_file(path):
    """A Miniflux entries JSON dump, an RSS snapshot, or plain text of repo URLs
    (e.g. a psql `SELECT url FROM entries` dump); sniffed by the first character."""
    with open(path, encoding="utf-8", errors="replace") as f:
        text = f.read()
    head = text.lstrip()[:1]
    if head and head in "{[":
        payload = json.loads(text)
        return repos_from_miniflux_entries(
            payload.get("entries", []) if isinstance(payload, dict) else payload)
    if head == "<":
        return repos_from_rss(text)
    return repos_from_text(text)


def repos_from_scout_prompts(prompt_dirs, pattern="00_gap_*.txt"):
    """github_scout writes one `00_gap_<owner>_<repo>.txt` per queued repo into
    research_prompts/, and the fleet moves each to research_prompts_done/ once it is
    consumed (autonomy/lumen_scout_graph.py:24,53,263). Both dirs together are the
    pool; reading only the first finds it empty and reports every Trending repo as
    missed (wq#3576). Any repo a matching file names counts as surfaced.
    """
    if isinstance(prompt_dirs, str):
        prompt_dirs = [prompt_dirs]
    repos = set()
    for d in prompt_dirs:
        for path in glob.glob(os.path.join(d, pattern)):
            with open(path, encoding="utf-8", errors="replace") as f:
                repos |= repos_from_text(f.read())
    return repos


def missed_repos(trending_repos, scout_repos):
    return sorted(trending_repos - scout_repos)


def star_bucket(stars):
    for lo, hi in STAR_BUCKETS:
        if stars >= lo and (hi is None or stars < hi):
            return f"{lo}-{hi if hi is not None else '+'}"
    raise ValueError(stars)  # unreachable: last bucket has hi=None


def star_distribution(star_counts):
    """{"n": ..., "median": ..., "buckets": {"0-50": n, ...}}. Empty input -> n=0,
    median=None, all buckets 0 (the caller can render "the scout missed nothing").
    """
    buckets = {f"{lo}-{hi if hi is not None else '+'}": 0 for lo, hi in STAR_BUCKETS}
    for s in star_counts:
        buckets[star_bucket(s)] += 1
    return {
        "n": len(star_counts),
        "median": statistics.median(star_counts) if star_counts else None,
        "buckets": buckets,
    }


def fetch_stars(repo_full_name, github_token=None):
    """One unauthenticated (or token-authed, if minted later) GitHub API call.

    Per the ticket: the Trending route and this lookup both work unauthenticated at
    low volume (research/TARGETED_CRAWL_SOURCE_RANKING.md:45-50) — a week's worth of
    missed repos is a handful of calls/week, nowhere near the 60/hr unauth ceiling.
    """
    req = urllib.request.Request(f"https://api.github.com/repos/{repo_full_name}")
    if github_token:
        req.add_header("Authorization", f"Bearer {github_token}")
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read())["stargazers_count"]


def build_report(trending_repos, scout_repos, star_lookup):
    """star_lookup: repo_full_name -> int stars (injected so tests never hit the
    network; the CLI wires this to fetch_stars). None skips the lookup."""
    missed = missed_repos(trending_repos, scout_repos)
    stars = [star_lookup(r) for r in missed] if star_lookup else []
    return {
        "trending_total": len(trending_repos),
        "scout_total": len(scout_repos),
        "missed_total": len(missed),
        "missed_repos": missed,
        "missed_star_distribution": star_distribution(stars),
    }


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--trending", action="append", required=True, metavar="FILE",
                    help="Miniflux entries JSON, RSS snapshot, or text of repo URLs; "
                         "repeatable, repos are unioned")
    p.add_argument("--scout-prompt-dir", action="append", metavar="DIR",
                    help="github_scout prompt dir, repeatable (default: "
                         "research_prompts and research_prompts_done under scratch)")
    p.add_argument("--no-stars", action="store_true",
                    help="skip the GitHub star lookup (offline; distribution reads n=0)")
    p.add_argument("--github-token", default=os.environ.get("GITHUB_ACCESS_TOKEN"))
    args = p.parse_args(argv)

    trending = set()
    for path in args.trending:
        trending |= repos_from_trending_file(path)
    scout = repos_from_scout_prompts(args.scout_prompt_dir or [
        "/home/lumen/scratch/research_prompts",
        "/home/lumen/scratch/research_prompts_done",
    ])

    report = build_report(
        trending, scout,
        star_lookup=None if args.no_stars else lambda r: fetch_stars(r, args.github_token),
    )
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
