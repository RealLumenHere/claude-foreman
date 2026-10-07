# wq#3576: velocity feed for github_scout, no bridge

The need from wq#2500 still stands. `github_scout` queries `topic:X pushed:>7d stars:>50`,
sorted by stars, then re-sorts by `(relevance_score, stars)`
(`/home/lumen/autonomy/lumen_scout_graph.py:163,167,210`). A star floor combined with a
star sort can't surface a repo that is two weeks old and climbing fast. GitHub Trending
ranks by star velocity, which is the dimension the scout never samples.

wq#2500 tried to fill this with a self-hosted RSSHub. That bundle was retired (wq#3486)
because its unit ran `node lib/index.js`, which upstream doesn't ship any more, and it
was never deployed on Sia.

## Prior art (2026-10-07, `fleet-search`)

| Option | Runs on Sia | Verdict |
|---|---|---|
| RSSHub from a node build (wq#2500) | clone, `npm ci`, `npm run build`, a systemd unit | Rejected. The entrypoint has already moved once under us (wq#2505), and every upstream release is a rebuild. |
| RSSHub's official image (`ghcr.io/diygod/rsshub`) | a container next to Miniflux | Works. It's still a service to babysit, though, and workers can't deploy containers (`kb/ISSUE-LOG.md` #653). |
| [mshibanami/GitHubTrendingRSS](https://github.com/mshibanami/GitHubTrendingRSS) (369★, pushed 2026-09-29) | **nothing** | **Chosen.** It's a static RSS feed per language/period, rebuilt daily on GitHub Pages. Fetched 2026-10-07: `daily/all.xml` and `daily/python.xml` returned 200 with 12 items each and a `pubDate` from the same morning. |

The chosen feeds are plain RSS on GitHub Pages, so Miniflux subscribes to them directly.
No process, unit or container runs anywhere, the install needs no root, and there's no
upstream entrypoint left to drift. The tradeoff: if the upstream project stops rebuilding,
the feed goes stale. `--verify` catches that as "no entry captured in 48h".

## Files

| File | Purpose |
|---|---|
| `feeds.txt` | the feed URLs. Both `install.sh --verify` and `miniflux_subscribe.py` read them from here. |
| `install.sh` | the install step runs `miniflux_subscribe.py`. `--verify` is read-only and uses `vremote_up` on Sia plus one `SELECT` per feed against `miniflux-db`, the same read path as `bin/wq122-stale-sweep:5`. |
| `miniflux_subscribe.py` | `POST /v1/feeds` for each feed into a `velocity-feed` category. It's idempotent. |
| `trending_vs_scout_diff.py` | the measurement, moved here from the retired wq#2500 bundle (details below) |
| `fixtures/daily_all_2026-10-07.xml` | the real `daily/all.xml` from 2026-10-07, trimmed to title and link, pinned for tests |
| `test_*.py` | `python3 -m pytest -q ops/wq3576-trending-velocity-feed` |

`--verify` exit codes follow `ops/VERIFY_CONTRACT.md`:
- **0**: every feed is subscribed, has `parsing_error_count = 0`, and has an entry `created_at` within 48h.
- **1**: a feed fails one of those checks.
- **2**: Sia can't be reached or the query fails.

## Deploy

This doesn't need root. Run it from any box that can reach Miniflux's API, with the key
exported from its 0600 file and never put on the command line:

```
MINIFLUX_BASE_URL=<miniflux url> MINIFLUX_API_KEY="$(cat <0600 key file>)" \
  bash ops/wq3576-trending-velocity-feed/install.sh
bash ops/wq3576-trending-velocity-feed/install.sh --verify   # after Miniflux's first poll
```

The base URL is not verified. The retired README's `http://100.111.15.120:8081` refused
connections from Lumen on 2026-10-07 (curl exit 7), so Miniflux is probably bound to
loopback or another port. Use the URL news-intake uses on Sia. The BUILD worker that
wrote this bundle has no ssh to Sia and no read access to `.secrets`, so it couldn't run
either step.

## The measurement

`trending_vs_scout_diff.py --trending FILE [--trending FILE ...]` accepts three input
formats, detected from the file's first character:
- a Miniflux entries JSON
- an RSS snapshot
- plain text of repo URLs, such as a psql `SELECT url FROM entries` dump

It reports which trending repos the scout never queued, with their star counts. Two
changes from wq#2500:

- **The scout pool is `research_prompts/` and `research_prompts_done/`, `00_gap_*.txt` only.**
  Consumed prompts move to `_done/`. The old single-directory default read 0 files on
  2026-10-07 (206 were in `_done/`), so it would have reported every trending repo as
  missed.
- **`--no-stars`** runs the diff offline, with no star lookup.

Baseline on 2026-10-07, from the three live snapshots (`daily/all`, `daily/python`,
`weekly/all`) against the scout's 206 queued repos:
- 30 trending repos
- **29 never queued by the scout**
- star counts of the missed repos: median 18,492. 7 fall in 1k–10k and 22 are above 10k.

Star counts come from the lookup, which runs at report time, not at trending time. A
two-week-old repo that trends quickly is already past 1k when it's measured. The week of
Miniflux capture this bundle enables is the real test. Run the diff over that week's
entries before deciding whether the feed stays. The diff doesn't filter for relevance,
so off-topic trending repos (games, courses) also count as misses. Read `missed_repos`
before reading the headline number.

## Not done here

- Feeding trending repos into `lumen_scout_graph.py` as a candidate source. That's a
  change to the scout itself, and it should wait until the week-long diff shows the
  misses are on-topic.
