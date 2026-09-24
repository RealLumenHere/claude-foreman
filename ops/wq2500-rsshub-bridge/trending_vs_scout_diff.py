#!/usr/bin/env python3
"""wq#2500 — acceptance criterion 4: is the velocity feed worth keeping.

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


def repos_from_scout_prompts(prompt_dir, pattern="*.txt"):
    """github_scout writes its gap-analysis queue as prompt files (per
    research/TARGETED_CRAWL_SOURCE_RANKING.md:58) — any repo the file names counts
    as something the scout *did* surface, regardless of the prompt's exact shape.
    """
    repos = set()
    for path in glob.glob(os.path.join(prompt_dir, pattern)):
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
    network; the CLI wires this to fetch_stars)."""
    missed = missed_repos(trending_repos, scout_repos)
    stars = [star_lookup(r) for r in missed]
    return {
        "trending_total": len(trending_repos),
        "scout_total": len(scout_repos),
        "missed_total": len(missed),
        "missed_repos": missed,
        "missed_star_distribution": star_distribution(stars),
    }


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--miniflux-entries-json", required=True,
                    help="JSON dump of GET /v1/feeds/{id}/entries for the trending feed")
    p.add_argument("--scout-prompt-dir", default="/home/lumen/scratch/research_prompts",
                    help="github_scout's candidate-prompt directory "
                         "(TARGETED_CRAWL_SOURCE_RANKING.md:58 — confirm this path on "
                         "the live box, it was not verified from this worktree)")
    p.add_argument("--github-token", default=os.environ.get("GITHUB_ACCESS_TOKEN"))
    args = p.parse_args(argv)

    with open(args.miniflux_entries_json, encoding="utf-8") as f:
        payload = json.load(f)
    trending = repos_from_miniflux_entries(payload.get("entries", payload))
    scout = repos_from_scout_prompts(args.scout_prompt_dir)

    report = build_report(
        trending, scout,
        star_lookup=lambda r: fetch_stars(r, args.github_token),
    )
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
