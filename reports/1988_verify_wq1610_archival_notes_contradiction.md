# wq#1988 — verify-pass: wq#1610 extend contradiction detection to archival_notes (PR#614)

**Verdict: CONFIRM** — wq#1610's shipped diff deletes a broken duplicate scanner and
points the ticket's acceptance criteria at wq#1609's already-generalised scanner, which
does in fact cover `archival_notes` by default, is deployed and running daily on this
box, and is writing real rows to `note_contradictions`. Verified against live production
state, not just the diff.

PR: http://100.121.17.5:3000/lumen/claude-foreman/pulls/614 (commit `562757a`, on
`origin/main`, confirmed via `git branch --contains`). Author: claude-sonnet-5.

## What the diff does

7 insertions, 566 deletions:
- Adds a 7-line comment in `ops/wq1609-minerva-contradiction-scan/lumen_contradiction_scan.py:38-46`
  cross-referencing wq#1610 to the pre-existing `scan_notes()`/`write_note_contradictions()`
  functions in that file.
- Deletes the entire `ops/wq1610-minerva-archival-contradictions/` tree (README, install.sh,
  systemd service+timer, a standalone `note_contradiction_scan.py`, its test file) —
  wq#1610's own attempt-2 implementation, never installed, incompatible schema, crashed
  against the live DB per the ticket's done-notes.

## Verification performed (adversarial, cold read + live probe)

1. **Read wq#1610's ticket acceptance criteria**: write rows to
   `note_contradictions(note_id,fact_id,dead_value,current_value,detected_at,cleared_at)`.
2. **Checked `scan_notes`/`write_note_contradictions` are not new/fabricated for this PR** —
   `git log -p --follow` on the scanner file shows both functions were added in the
   *original* wq#1609 commit `7d0f359` ("contradiction scan over all closed facts, on a
   daily timer"), well before wq#1610 existed as a ticket. The cross-reference comment is
   accurate, not a claim invented to close the ticket cheaply.
3. **Confirmed `archival_notes` is scanned by default, not behind a flag**: `scan()` calls
   `scan_notes(conn, index) + scan_files(...)` unconditionally (line 527); `scan_notes`
   selects `archival_notes` rows `where revoked_at is null and superseded_by is null` — 1744
   of 1750 rows in the live DB today, consistent with the ticket's "1686 unscanned" baseline
   from 2026-09-10.
4. **Confirmed deployment, not just code**: `systemctl is-active lumen-contradiction-scan.timer`
   → `active`, last fired 2026-09-18 06:22 UTC, next 2026-09-19 06:20 UTC. The service unit
   runs `lumen-contradiction-scan --apply` (oneshot). Checked `systemctl list-unit-files` for
   any leftover `minerva-archival-contradictions*` unit from wq#1610's deleted attempt — none
   found; no leftover script in `/home/lumen/bin` either. The "dead scanner" claim holds.
5. **Read the last live run's journal** (`journalctl -u lumen-contradiction-scan.service`):
   real actioned hits against `archival_notes` (e.g. `isis.ollama_version`,
   `llama33-local.status`, `nephthys.ts_ip`), not a dry no-op.
6. **Queried the live DB directly**: `note_contradictions` has 20 open rows across 17
   distinct notes, matching the done-notes claim exactly. Table schema
   (`ops/wq1609.../lumen_contradiction_scan.py:395-411`) covers `note_id, fact_id, subject,
   predicate, dead_value, current_value, valid_to, severity, excerpt, detected_at,
   resolved_at` — a superset of the ticket's named columns, with one naming deviation:
   **`resolved_at`, not the ticket's `cleared_at`**.
7. **Ran the probe myself, read-only, against the live DB**:
   `python3 lumen_contradiction_scan.py --db /home/lumen/minerva/minerva.db --json
   --no-files` — returns real, structured hits (subject/predicate/dead_value/current_value)
   matching what's already in the table. Scanner is not broken, not a stub.
8. **Ran the test suite**: `python3 -m pytest -q test_lumen_contradiction_scan.py` → 27
   passed, including `test_notes_are_scanned_and_revoked_notes_are_skipped` and the
   `note_contradictions` dedup/idempotency pair (`test...` lines 275-277: second run of the
   same hits writes 0 new rows — `unique(note_id, fact_id) on conflict ignore` is doing its
   job).
9. **Checked for dangling references** to the deleted `wq1610-minerva-archival-contradictions`
   tree across the whole repo (`.py/.md/.sh/.service/.timer/.yaml/.yml`) — zero hits. Clean
   deletion.

## Noted but not a contradiction

The `cleared_at` vs `resolved_at` column-name mismatch (finding 6) is real, but it is not a
live bug and not something this verify-pass needs to escalate: wq#1614's downstream
consumer (`ops/wq1614-search-v3-contradiction-penalty/`) already discovered and defensively
handles it — `lumen_recall2.py:444` introspects both `resolved_at` and `cleared_at`, and
`test_contra_penalty.py:187` (`test_cleared_at_schema_is_accepted`) pins the behavior. wq#1610's
done-notes don't mention the rename, but nothing downstream is broken by it today.

## Conclusion

CONFIRM. The "point AC at existing code and delete the dead duplicate" approach is
legitimate here — verified with live systemd state, live journal output, a live DB query,
a self-run probe against the live DB, and a green test suite, not just by reading the diff.
No scope creep, no regression, ticket's stated row-producing acceptance criterion is met
today (20 rows/17 notes, growing daily). Did not re-implement — verify only.
