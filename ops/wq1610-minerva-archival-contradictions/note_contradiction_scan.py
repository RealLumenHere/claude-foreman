#!/usr/bin/env python3
"""note_contradiction_scan.py — wq#1610.

Ref /home/lumen/foreman/research/MINERVA_STALENESS_REVIEW.md §2.4, §3.5, §3.6, §3.7
(fix #3). lumen_dream.stale_prose_scan() only ever reads claude_memory.md — the
1686-row archival_notes table (machine-retrieved into every precheck via
lumen_episode.recall) has NO invalidation happening on it at all. This script closes
that reach gap for the episodic layer specifically: it builds a dead-value index from
closed (superseded) state_facts rows, scans live archival_notes for hits, and WRITES
rows to a new note_contradictions table. It does not touch search_v3 scoring — the
recall penalty described in §3.7 is wq#1614, gated on this script producing rows.

Dead-value index (§3.5): a closed fact's value qualifies only if it is <=60 chars and
matches structured-token shape (`^[A-Za-z0-9._:/@-]+$`) — no substring matching on
narrative prose, which is how a scanner earns a reputation for noise. Two suppressors
on top of a negation-line skip:
  - changelog: the fact's current value also appears in the same note -> the note is
    documenting the transition, not asserting the dead value. Skip.
  - as-of: the note's created_at predates the fact's valid_to -> correct historical
    record at the time it was written. Skip.

Schema (additive only — no column added to archival_notes; wq#576 precedent is a side
table over the row it annotates rather than schema churn on the hot table, same shape
note_merges already uses):
  note_contradictions(id, note_id, fact_id, dead_value, current_value,
                       detected_at, cleared_at)

Idempotent: a (note_id, fact_id, dead_value) pair is unique; re-running does not
duplicate rows. A previously open contradiction (cleared_at IS NULL) whose note was
re-scanned this run and no longer contains the dead value gets cleared_at set — notes
that became revoked/absorbed/merged between runs are left alone since search_v3 already
excludes them unconditionally regardless of this table.

CLI:
  note_contradiction_scan.py [--db PATH] [--dry-run] [--limit N]
"""
import argparse
import re
import sqlite3
import sys
from datetime import datetime, timezone

DB = '/home/lumen/minerva/minerva.db'

STRUCT_RE = re.compile(r'^[A-Za-z0-9._:/@\-]+$')
TOKEN_RE = re.compile(r'[A-Za-z0-9._:/@\-]+')
MAX_VALUE_LEN = 60

# Mirrors lumen_dream.py's NEGATION_RE (same word list) — a line documenting a value
# as dead/old is not a contradiction hit, it is the correct record of the transition.
NEGATION_RE = re.compile(
    r'\b(dead|retired|removed|former|formerly|superseded|defunct|gone|was |old |'
    r'stale|deprecat|RIP|killed|reinstalled|will be removed)\b', re.I)


def _now():
    return datetime.now(timezone.utc).isoformat()


def ensure_schema(conn):
    conn.executescript('''
    CREATE TABLE IF NOT EXISTS note_contradictions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        note_id TEXT NOT NULL,
        fact_id TEXT NOT NULL,
        dead_value TEXT NOT NULL,
        current_value TEXT,
        detected_at TEXT NOT NULL,
        cleared_at TEXT,
        UNIQUE(note_id, fact_id, dead_value)
    );
    CREATE INDEX IF NOT EXISTS idx_note_contra_note ON note_contradictions(note_id);
    CREATE INDEX IF NOT EXISTS idx_note_contra_open
        ON note_contradictions(note_id) WHERE cleared_at IS NULL;
    ''')
    conn.commit()


def dead_value_index(conn):
    """dead value -> list of {fact_id, subject, predicate, current_value, valid_to}
    for closed state_facts rows whose value is structured-token shaped (§3.5)."""
    idx = {}
    rows = conn.execute(
        'SELECT id, subject, predicate, value, valid_to FROM state_facts '
        'WHERE valid_to IS NOT NULL').fetchall()
    for fid, subj, pred, val, vto in rows:
        v = (val or '').strip()
        if not v or len(v) > MAX_VALUE_LEN or not STRUCT_RE.match(v):
            continue
        cur = conn.execute(
            'SELECT value FROM state_facts WHERE subject=? AND predicate=? '
            'AND valid_to IS NULL', (subj, pred)).fetchone()
        idx.setdefault(v, []).append({
            'fact_id': fid, 'subject': subj, 'predicate': pred,
            'current_value': cur[0] if cur else None, 'valid_to': vto,
        })
    return idx


def live_notes(conn, limit=None):
    """archival_notes not absorbed into another note and not already
    revoked/superseded — matches search_v3's own unconditional exclusion
    (lumen_recall2.py wq#610), so scanning them would only ever produce rows
    for notes nobody is retrieved anyway."""
    q = ('SELECT id, title, content, created_at FROM archival_notes '
         'WHERE id NOT IN (SELECT absorbed_id FROM note_merges) '
         'AND revoked_at IS NULL AND superseded_by IS NULL '
         'ORDER BY id')
    if limit:
        q += f' LIMIT {int(limit)}'
    return conn.execute(q).fetchall()


def find_hits(note, idx):
    """Yield (fact_id, dead_value, current_value) for one note, suppressors applied."""
    nid, title, content, created_at = note
    text = f'{title or ""}\n{content or ""}'
    seen = set()
    for line in text.splitlines():
        for tok in set(TOKEN_RE.findall(line)):
            entries = idx.get(tok)
            if not entries:
                continue
            # Negation check on the line with THIS token's own occurrences masked
            # out: a dead value can itself be a word NEGATION_RE watches for (e.g.
            # a status enum value of "RETIRED"), and the line asserting that exact
            # dead value must not be treated as documenting it as dead.
            remainder = re.sub(r'\b' + re.escape(tok) + r'\b', '', line)
            if NEGATION_RE.search(remainder):
                continue
            for e in entries:
                key = (e['fact_id'], tok)
                if key in seen:
                    continue
                cur = e['current_value']
                if cur and re.search(r'\b' + re.escape(cur) + r'\b', text):
                    continue  # changelog suppressor: transition is documented, not stale
                if created_at and e['valid_to'] and created_at < e['valid_to']:
                    continue  # as-of suppressor: correct historical record
                seen.add(key)
                yield e['fact_id'], tok, cur


def scan(conn, limit=None, dry_run=False):
    ensure_schema(conn)
    idx = dead_value_index(conn)
    notes = live_notes(conn, limit=limit)
    now = _now()
    found = set()
    written = 0
    for note in notes:
        nid = note[0]
        for fact_id, dead_value, current_value in find_hits(note, idx):
            found.add((nid, fact_id, dead_value))
            if dry_run:
                written += 1
                continue
            cur = conn.execute(
                'SELECT id FROM note_contradictions WHERE note_id=? AND fact_id=? '
                'AND dead_value=?', (nid, fact_id, dead_value)).fetchone()
            if cur:
                continue
            conn.execute(
                'INSERT INTO note_contradictions '
                '(note_id, fact_id, dead_value, current_value, detected_at) '
                'VALUES (?,?,?,?,?)',
                (nid, fact_id, dead_value, current_value, now))
            written += 1

    cleared = 0
    if not dry_run and notes:
        scanned_ids = tuple(n[0] for n in notes)
        ph = ','.join('?' * len(scanned_ids))
        open_rows = conn.execute(
            f'SELECT id, note_id, fact_id, dead_value FROM note_contradictions '
            f'WHERE cleared_at IS NULL AND note_id IN ({ph})', scanned_ids).fetchall()
        for row_id, nid, fact_id, dead_value in open_rows:
            if (nid, fact_id, dead_value) not in found:
                conn.execute(
                    'UPDATE note_contradictions SET cleared_at=? WHERE id=?',
                    (now, row_id))
                cleared += 1

    if not dry_run:
        conn.commit()
    return {
        'notes_scanned': len(notes), 'dead_values_indexed': len(idx),
        'written': written, 'cleared': cleared,
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--db', default=DB)
    ap.add_argument('--dry-run', action='store_true')
    ap.add_argument('--limit', type=int, default=None)
    args = ap.parse_args()

    conn = sqlite3.connect(args.db, timeout=30)
    try:
        summary = scan(conn, limit=args.limit, dry_run=args.dry_run)
    finally:
        conn.close()
    tag = 'DRY-RUN ' if args.dry_run else ''
    print(f'{tag}notes_scanned={summary["notes_scanned"]} '
          f'dead_values_indexed={summary["dead_values_indexed"]} '
          f'written={summary["written"]} cleared={summary["cleared"]}')
    sys.exit(0)


if __name__ == '__main__':
    main()
