#!/usr/bin/env bash
# wq#3576 velocity feed for github_scout: Miniflux on Sia subscribes to the static
# GitHub Trending feeds in feeds.txt (mshibanami/GitHubTrendingRSS, rebuilt daily on
# GitHub Pages). Replaces the retired wq#2500 RSSHub bridge: there is no service, no
# unit and no container to run, so the whole install is one Miniflux API call per feed
# and needs no root anywhere. See README.md for the prior-art comparison.
#
# Install (from any box that can reach Miniflux's API; key exported, never inline):
#   MINIFLUX_BASE_URL=... MINIFLUX_API_KEY="$(cat <0600 key file>)" bash install.sh
set -euo pipefail

SRC="$(cd "$(dirname "$0")" && pwd)"
SIA=sia@100.111.15.120
# Miniflux's Postgres container on Sia; same read path as bin/wq122-stale-sweep:5.
MFDB=miniflux-db
# A daily feed with no new entry for this long means capture stopped.
MAX_AGE_S=$((48 * 3600))

mapfile -t FEEDS < <(grep -Ev '^[[:space:]]*(#|$)' "$SRC/feeds.txt")

# wq#1696 deploy-verify contract (ops/_verify.sh). Read-only: one SELECT per feed.
# Live means: each feed is subscribed, Miniflux parses it without error, and it
# captured an entry within MAX_AGE_S. Sia unreachable or the query failing is ERR.
if [ "${1:-}" = --verify ]; then
  . "$SRC/../_verify.sh"
  if vremote_up "$SIA"; then
    for url in "${FEEDS[@]}"; do
      case "$url" in
        *[!A-Za-z0-9:/._-]*) echo "ERR unsafe feed url in feeds.txt: $url"; _V_ERR=1; continue ;;
      esac
      sql="SELECT max(f.parsing_error_count), COALESCE(EXTRACT(EPOCH FROM now() - max(e.created_at))::bigint, -1) FROM feeds f LEFT JOIN entries e ON e.feed_id = f.id WHERE f.feed_url = '$url' HAVING count(f.id) > 0"
      if ! out="$($_VSSH "$SIA" "docker exec $MFDB psql -U miniflux -t -A -F ' ' -c \"$sql\"" 2>/dev/null)"; then
        echo "ERR $SIA: miniflux query failed for $url"; _V_ERR=1; continue
      fi
      if [ -z "$out" ]; then
        _vmiss "$SIA: not subscribed in Miniflux: $url"; continue
      fi
      read -r errs age <<<"$out"
      if [ "${errs:-0}" -gt 0 ]; then
        _vmiss "$SIA: Miniflux parsing errors ($errs) on $url"
      elif [ "${age:--1}" -lt 0 ] || [ "$age" -gt "$MAX_AGE_S" ]; then
        _vmiss "$SIA: no entry captured in 48h on $url (age_s=$age)"
      fi
    done
  fi
  vdone
fi

if [ -z "${MINIFLUX_BASE_URL:-}" ] || [ -z "${MINIFLUX_API_KEY:-}" ]; then
  echo "export MINIFLUX_BASE_URL and MINIFLUX_API_KEY first (see README.md)" >&2
  exit 2
fi
python3 "$SRC/miniflux_subscribe.py"
echo "next: bash $SRC/install.sh --verify   (expect MISS 'no entry captured' until the first poll)"
