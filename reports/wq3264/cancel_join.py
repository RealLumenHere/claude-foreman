#!/usr/bin/env python3
"""wq#3264: join cloudflared `context canceled` on the Commander ingress (:3100)
to in-flight Desktop Commander calls in ~/.dc-handles.db.

  snapshot  read the live journal + handle store, write a pinned fixture
            (timestamps and a command category only, never command text)
  analyze   read the fixture, print the call-age-at-cancel histogram next to
            the same statistic for uniformly random instants (null baseline)

If the 70s Worker abort (router.ts TOOLS_CALL_TIMEOUT_MS) were not the cause,
real cancels would show the same age spread as random instants.
"""
import argparse
import bisect
import json
import random
import re
import sqlite3
import subprocess
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone

DB = "/home/lumen/.dc-handles.db"
INGRESS = "originService=http://localhost:3100"

CATEGORIES = [
    ("lumen-run wait", re.compile(r"\blumen-run\s+wait\b")),
    ("sleep", re.compile(r"(^|[;&|]\s*)sleep\s+\d")),
    ("cargo", re.compile(r"\bcargo\s+(test|build)\b")),
    ("dc-upgrade", re.compile(r"\bdc-upgrade\b")),
    ("restic", re.compile(r"restic")),
]


def category(cmd):
    for name, rx in CATEGORIES:
        if rx.search(cmd or ""):
            return name
    # binary basename only: argv can carry credentials, the program name does not
    m = re.match(r"\s*(?:\w+=\S*\s+)*(\S+)", cmd or "")
    base = re.sub(r"[^\w.-]", "", m.group(1).rsplit("/", 1)[-1])[:24] if m else ""
    return f"other:{base or '?'}"


def ts(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def snapshot(out, hours):
    since = f"{hours} hours ago"
    j = subprocess.run(
        ["journalctl", "-u", "cloudflared", "--since", since, "--no-pager",
         "-o", "short-iso"], capture_output=True, text=True, check=True).stdout
    cancels = sorted({m.group(1) for line in j.splitlines()
                      if "context canceled" in line and INGRESS in line
                      for m in [re.search(r"(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ) ERR", line)] if m})
    t0 = (datetime.now(timezone.utc) - timedelta(hours=hours + 1)).strftime("%Y-%m-%dT%H:%M:%S")
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    rows = [{"started_at": s, "done_at": d, "cat": category(c)}
            for s, d, c in con.execute(
                "select started_at, done_at, cmd from proc where host='lumen' "
                "and started_at >= ? order by started_at", (t0,))]
    window = {"since": (datetime.now(timezone.utc) - timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M:%SZ"),
              "until": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}
    with open(out, "w") as f:
        json.dump({"window": window, "cancels": cancels, "calls": rows}, f, indent=0)
    print(f"wrote {out}: {len(cancels)} cancel seconds, {len(rows)} calls")


def in_flight(t, calls, starts):
    """(age_s, cat) of every call started at most 900s before t and not done by t-1s,
    most recently started first."""
    i = bisect.bisect_right(starts, t)
    out = []
    for c in reversed(calls[:i]):
        age = (t - c["_start"]).total_seconds()
        if age > 900:
            break
        done = c["_done"]
        if done is None or done >= t - timedelta(seconds=1):
            out.append((age, c["cat"]))
    return out


def analyze(path, seed):
    data = json.load(open(path))
    calls = data["calls"]
    for c in calls:
        c["_start"] = ts(c["started_at"])
        c["_done"] = ts(c["done_at"]) if c["done_at"] else None
    starts = [c["_start"] for c in calls]
    lo, hi = ts(data["window"]["since"]), ts(data["window"]["until"])

    def stats(instants):
        """Per instant with any call in flight: the newest call's age, and whether
        ANY in-flight call is aged 66-72s (the oldest one is what a 70s abort hits)."""
        newest, oldest, cats, band = [], [], Counter(), 0
        for t in instants:
            fl = in_flight(t, calls, starts)
            if not fl:
                continue
            newest.append(int(fl[0][0]))
            oldest.append(int(fl[-1][0]))
            hit = [c for a, c in fl if 66 <= a <= 72]
            if hit:
                band += 1
                cats[hit[0]] += 1
        return newest, oldest, cats, band

    real = [ts(s) for s in data["cancels"]]
    newest, oldest, cats, band = stats(real)
    rng = random.Random(seed)
    span = (hi - lo).total_seconds()
    null = [lo + timedelta(seconds=rng.uniform(0, span)) for _ in range(2000)]
    nnewest, _, _, nband = stats(null)
    n, nn = len(newest), len(nnewest)

    print(f"window {data['window']['since']} .. {data['window']['until']}")
    print(f"cancel seconds on :3100: {len(real)}; with a call in flight: {n}")
    print(f"ANY in-flight call aged 66-72s: {band}/{n} ({100 * band / max(n, 1):.0f}%)")
    print("  category of that call:", dict(cats.most_common()))
    print("newest in-flight age hist:", dict(sorted(Counter(newest).items())))
    print("oldest in-flight age hist:", dict(sorted(Counter(oldest).items())))
    print(f"null (2000 random instants, seed {seed}): with a call in flight {nn}, "
          f"ANY aged 66-72s {nband}/{nn} ({100 * nband / max(nn, 1):.1f}%)")
    return band, n, nband, nn


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("snapshot")
    s.add_argument("--out", required=True)
    s.add_argument("--hours", type=int, default=24)
    a = sub.add_parser("analyze")
    a.add_argument("fixture")
    a.add_argument("--seed", type=int, default=3264)
    args = ap.parse_args()
    if args.cmd == "snapshot":
        snapshot(args.out, args.hours)
    else:
        analyze(args.fixture, args.seed)


if __name__ == "__main__":
    sys.exit(main())
