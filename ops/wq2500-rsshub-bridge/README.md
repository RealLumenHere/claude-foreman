# wq#2500 — RSSHub as the velocity feed

> **RETIRED (wq#3486).** Replaced by `ops/wq3576-trending-velocity-feed/`, which
> needs no bridge at all. `trending_vs_scout_diff.py` and `test_diff_report.py`
> moved there (wq#3576); the rows below describe this bundle as it shipped.

Cites `O2 L1523-1613 §"Local RSS Bridges (e.g., RSSHub)"` (compose block `L1544-1582`),
`research/DIGEST_Orchestation_2_md.md` R38, and the measured defect in
`research/TARGETED_CRAWL_SOURCE_RANKING.md:17-22`: `github_scout` sorts
`(keyword_score, stars)` descending over `topic:X pushed:>7d stars:>50`
(`autonomy/lumen_scout_graph.py:147,151,194`) — a star floor plus a star sort cannot
surface a two-week-old, fast-rising project. GitHub Trending ranks by star *velocity*,
the axis the scout does not sample.

## Why this ships as a bundle, not a live deploy

Two hard walls, both already logged:

1. `harness/guard.sh` denies the container CLI to workers except `ps`/`logs`
   (`kb/ISSUE-LOG.md` #653) — a worker cannot bring up the doc's `docker compose` stack.
2. The BUILD-lane worker profile (`harness/worker-settings.json`) carries **no `ssh`
   entry at all** — a worker cannot reach any fleet node other than itself to install
   or enable anything, the same wall `kb/ISSUE-LOG.md` #39 (wq#358) hit trying to
   install an efficiency pack on Sia. Miniflux itself lives on Sia
   (100.111.15.120, wq#82/wq#122), so subscribing feeds via its HTTP API is the one
   remote action this bundle *can* do unattended once a token exists — everything that
   needs root on Sia (cloning RSSHub, writing the unit, `systemctl enable --now`) has
   to be run by an on-host operator.

So per the ticket's own stated resolution: **RSSHub ships as a plain Node systemd
unit**, not the doc's Docker Compose + Redis + Chromium stack — the
`github/trending/daily/python` route is plain HTML, not JS-rendered, so the
Puppeteer/chromium image the doc bundles is unnecessary weight for this route.

## What's in this bundle

| File | Purpose |
|---|---|
| `rsshub.service` | systemd unit, `User=sia`, binds `127.0.0.1:1200` only (loopback — Miniflux is on the same box, so no tailnet exposure is even needed; "never public" per acceptance is satisfied by construction) |
| `rsshub.env.example` | template for the 0600 env file the unit's `EnvironmentFile=` reads — **no token committed**; see Credentials below |
| `install.sh` | on-host install script with a `--verify` handler per `ops/VERIFY_CONTRACT.md`; clones RSSHub, `npm ci --omit=dev`, installs the unit, enables it |
| `miniflux_subscribe.py` | subscribes the two required routes to Miniflux via its REST API (`POST /v1/feeds`) — the one step this bundle *can* run unattended, since it only needs network, not root |
| `trending_vs_scout_diff.py` | the acceptance measurement: diffs a week of captured Trending entries against `github_scout`'s candidate pool, reports what the scout missed and at what star counts |
| `test_diff_report.py` | unit tests for the diff/star-bucketing logic against synthetic fixtures (no network, no live Miniflux needed to prove the logic is correct) |
| `test_verify_handler_wq2500.py` | proves `install.sh --verify` follows the deploy-verify contract |

## Deployment (on-host operator, maintenance window)

Target: **Sia** (100.111.15.120), the box that already runs Miniflux (wq#82/wq#122) —
co-locating means Miniflux polls RSSHub over loopback, no new tailnet surface.
`sia-news-pull` (`bin/sia-news-pull:6`) confirms the SSH user is `sia`; this bundle
assumes home `/home/sia` and working dir `/home/sia/services/rsshub`, mirroring the
`/home/lumen/services/<name>` convention used for Node services on lumen itself
(`ops/wq859-renovate-gitea/renovate-gitea.service:9`). **This worker has no `ssh`
grant and cannot confirm that path exists on Sia** — the on-host operator should
check `ssh sia@100.111.15.120 'echo $HOME'` before running `install.sh`, and edit
`WORKDIR` at the top of `install.sh` / `rsshub.service` if it differs.

```
scp -r ops/wq2500-rsshub-bridge sia@100.111.15.120:~/wq2500-rsshub-bridge
ssh sia@100.111.15.120
cd ~/wq2500-rsshub-bridge
sudo ./install.sh                      # clone, npm ci, install unit, enable
sudo ./install.sh --verify             # read-only re-check, exit 0 == live
```

Then, from anywhere with the Miniflux API key exported (never inline):

```
MINIFLUX_BASE_URL=http://100.111.15.120:8081 \
MINIFLUX_API_KEY="$(cat /home/lumen/.secrets/miniflux-lumen.token)" \
  python3 miniflux_subscribe.py
```

## Credentials (instinct 4 — never inline, never in a URL, never in an env dump)

- **GitHub PAT**: the Trending route works fully unauthenticated at low poll rates
  (`gh` REST limits: 60/hr unauth is plenty for one route polled a few times/day) — so
  **this bundle does not require a PAT to ship or to run for the acceptance week**.
  `research/TARGETED_CRAWL_SOURCE_RANKING.md:45-50` already flags `fleet-search github`
  as unauthenticated; when Frank mints a PAT (FRANK-PHYSICAL, queued, not a gate here)
  it should land at `/home/lumen/.secrets/github-lumen.token` (0600) and cover both
  `fleet-search` and this bridge's release-route polling — one mint, two consumers.
- **Miniflux API key**: read from an env var the *caller* exports from a 0600 file
  (e.g. `/home/lumen/.secrets/miniflux-lumen.token`); `miniflux_subscribe.py` never
  opens a credential file itself, matching the existing convention in
  `bin/fleet-update-check:8-9` (`GH_TOKEN` — "caller's job to read the secret and
  export it — this script never reads secrets itself").
- Neither script ever logs, prints, or echoes the token value; only its presence/length
  is checked before use.

## Routes subscribed (acceptance criterion 2)

1. `http://127.0.0.1:1200/github/trending/daily/python` — the velocity feed itself.
2. `http://127.0.0.1:1200/github/release/BerriAI/litellm` — one release route. LiteLLM
   is the fleet's LLM gateway (`research/FLEET_SERVICE_ATLAS.md:33`, `litellm:4000`
   docker container) — a real, already-self-hosted dependency, not an arbitrary pick.

Both are added to Miniflux via `miniflux_subscribe.py`, matching acceptance criterion
2 without a container CLI call (Miniflux's HTTP API, not `docker exec`).

## The measurement (acceptance criterion 4)

`trending_vs_scout_diff.py` is the tool that answers "does this stay": after one week
of Miniflux capturing the trending feed, run it against a dump of that feed's entries
and the scout's candidate pool for the same week. It reports, per acceptance:

- how many Trending items the scout's candidate pool never queued, and
- the star-count distribution of those missed items.

This worker cannot produce that number today — the feed has not existed for a week,
because it has not been deployed yet, because deployment needs root on Sia this
worker does not have (see above). Shipping the *tool*, proven correct against
synthetic fixtures in `test_diff_report.py`, is the deliverable this session can make
real; running it for a week is on-host follow-up, tracked as the physical/ops step in
the PR description, not gated back onto this ticket.

## Out of scope (per ticket)

NATS JetStream trigger (no NATS on this fleet, `research/DIGEST_Orchestation_1_md.md`
R6) and the doc's DSPy judge stage.
