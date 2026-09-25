#!/usr/bin/env python3
"""backlog_digest.py — wq#2573 (wq#2188 follow-through).

wq#2188 found 36 unapplied memory-consolidation proposals from
lumen_reflector/lumen_dream, oldest 87 days: proposals are produced faster
than Frank reviews them, and the review side has no funnel at all today —
each dated reflection-*.json sits in dream/reflections/ forever, one file
per cycle, with no ledger of what got reviewed or applied.

This script does two proposal-only things (instinct 7: nothing here ever
touches claude_memory.md or instincts.yaml):

  funnel  — count proposals/week created, and (honestly) reviewed/applied,
            reading an optional --applied-log so the reviewed/applied
            columns are non-zero once such a ledger exists. Today there is
            no ledger, so those columns read 0 — that gap IS the wq#2188
            finding, not a bug in this script.
  digest  — fold every reflection older than --age-days (default 14) into
            ONE combined markdown file for a single Frank review pass,
            instead of leaving N stale files to review separately. Source
            files are left untouched; this only writes the digest.

Usage:
  backlog_digest.py funnel --reflections-dir DIR [--applied-log PATH]
  backlog_digest.py digest --reflections-dir DIR --out PATH [--age-days N]
"""
import argparse
import json
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

STAMP_RE = re.compile(r'^(\d{4}-\d{2}-\d{2}) (\d{2}:\d{2}) PT$')
PT = timezone(timedelta(hours=-7))  # matches lumen_reflector.py's _now_pt()


def parse_generated(stamp):
    m = STAMP_RE.match(stamp or '')
    if not m:
        return None
    naive = datetime.strptime(f'{m.group(1)} {m.group(2)}', '%Y-%m-%d %H:%M')
    return naive.replace(tzinfo=PT)


def load_reflections(reflections_dir):
    out = []
    for p in sorted(Path(reflections_dir).glob('reflection-*.json')):
        try:
            obj = json.loads(p.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        out.append({'path': p, 'ts': parse_generated(obj.get('generated')),
                     'proposals': obj.get('proposals') or []})
    return out


def funnel(reflections, applied_titles=None):
    """{week: {"created": n, "reviewed": n, "applied": n}}. week is ISO
    year-week of the reflection's generated timestamp; undated reflections
    are dropped (can't place them on the funnel, not silently miscounted)."""
    applied_titles = applied_titles or set()
    weeks = {}
    for r in reflections:
        if r['ts'] is None:
            continue
        row = weeks.setdefault(r['ts'].strftime('%G-W%V'),
                                {'created': 0, 'reviewed': 0, 'applied': 0})
        for p in r['proposals']:
            row['created'] += 1
            if p.get('title') in applied_titles:
                row['reviewed'] += 1
                row['applied'] += 1
    return weeks


def aging(reflections, age_days=14, now=None):
    now = now or datetime.now(timezone.utc)
    stale, fresh = [], []
    for r in reflections:
        (stale if r['ts'] is None or (now - r['ts']) > timedelta(days=age_days)
         else fresh).append(r)
    return stale, fresh


def render_digest(stale, generated_at=None):
    generated_at = generated_at or datetime.now(timezone.utc)
    total = sum(len(r['proposals']) for r in stale)
    lines = [f'# Lumen memory-proposal backlog digest — {generated_at:%Y-%m-%d}', '',
             '_Proposal-only (instinct 7) — nothing below has been applied to '
             'claude_memory.md or instincts.yaml; this only re-batches aging '
             'proposals into one review pass. wq#2573 / wq#2188._', '',
             f'{total} proposal(s) across {len(stale)} reflection(s) are past '
             'the aging threshold:', '']
    for r in stale:
        age = 'unknown-age' if r['ts'] is None else f'{(generated_at - r["ts"]).days}d old'
        lines.append(f'## {r["path"].name} ({age})')
        if not r['proposals']:
            lines.append('_(no proposals in this cycle)_')
        for p in r['proposals']:
            lines.append(f'- **[{p.get("type", "?")}]** {p.get("title", "(untitled)")} '
                         f'— target: {p.get("target", "?")}')
        lines.append('')
    return '\n'.join(lines)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                  formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest='cmd', required=True)

    f = sub.add_parser('funnel', help='print proposals/week created vs reviewed vs applied')
    f.add_argument('--reflections-dir', required=True)
    f.add_argument('--applied-log', help='optional file, one applied proposal title per line')

    d = sub.add_parser('digest', help='re-batch aging proposals into one review file')
    d.add_argument('--reflections-dir', required=True)
    d.add_argument('--out', required=True)
    d.add_argument('--age-days', type=int, default=14)

    args = ap.parse_args()
    reflections = load_reflections(args.reflections_dir)

    if args.cmd == 'funnel':
        applied = set()
        if args.applied_log:
            applied = {ln.strip() for ln in Path(args.applied_log).read_text().splitlines() if ln.strip()}
        weeks = funnel(reflections, applied)
        for week in sorted(weeks):
            row = weeks[week]
            print(f'{week}: created={row["created"]} reviewed={row["reviewed"]} applied={row["applied"]}')
        if not weeks:
            print('no dated reflections found', file=sys.stderr)
    elif args.cmd == 'digest':
        stale, fresh = aging(reflections, args.age_days)
        Path(args.out).write_text(render_digest(stale))
        print(f'wrote {args.out}: {len(stale)} stale reflection(s) digested, '
              f'{len(fresh)} still within {args.age_days}d left untouched')


if __name__ == '__main__':
    main()
