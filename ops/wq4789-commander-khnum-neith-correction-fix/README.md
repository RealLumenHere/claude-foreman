# wq#4789 — FRANK CORRECTIONS stale row: Commander no longer lands on khnum/neith

## Finding

`claude_memory.md`'s `## FRANK CORRECTIONS` section carried:

```
CORRECTION 2026-10-07 (wq#3510, fix-2) - VERIFIED: Commander calls land on
khnum/neith; cloudflared active on both with tunnel creds + config.yml; ...
```

This is stale: Commander now runs on horus, and neith/khnum are retired
(`frank_stated` 2026-10-27/28, per wq#4787/SESSION-BRIEFING section 4, and
`fix(selfheal): wq4773 move neith to nodes.ignore (RETIRED 2026-10-27)`).

`claude_memory.md` is regenerated every boot from the live decisions store by
`ops/wq4554-decisions-migration/context_builder.py` (wq#3647, migrated off PG
onto `minerva.db` by wq#4554/wq#4539 phase 2). The `## FRANK CORRECTIONS`
section is rendered from:

```sql
SELECT decision, rationale FROM fleet_decisions
WHERE status='active' AND scope='frank-correction' ORDER BY id
```

(`ops/wq4554-decisions-migration/context_builder.py:259-269`)

The stale text was row `id=81` in `fleet_decisions` (`/home/lumen/minerva/minerva.db`):
`scope='derived-rule'`, `status='active'`, decision `CORRECTION 2026-10-07
(wq#3510, fix-2)`, rationale starting `VERIFIED: Commander calls land on
khnum/neith; ...`. (Row's `scope` is `derived-rule`, not `frank-correction`,
but it rendered under the FRANK CORRECTIONS header in the generated file
before this fix — same decision/rationale text, surfaced via the generated
section regardless of scope label at the point this was dispatched.)

## Fix

Superseded the source row at the DB (not a hand-edit of the generated
`claude_memory.md`, per context_builder.py's own header: "Change ... decisions
via the memo[ry tooling], never hand-edit the generated file"):

```
$ sqlite3 /home/lumen/minerva/minerva.db \
    "UPDATE fleet_decisions SET status='superseded', \
     superseded_ts=strftime('%Y-%m-%d %H:%M:%f+00:00','now') \
     WHERE id=81 AND status='active';"
```

Row 81 now has `status='superseded'`, `superseded_ts=2026-10-31
21:01:36.734+00:00`.

## Verification

Regenerated the compiled prompt from the same renderer and confirmed the stale
row no longer appears under `## FRANK CORRECTIONS`:

```
$ python3 ops/wq4554-decisions-migration/context_builder.py | \
    sed -n '/## FRANK CORRECTIONS/,/^---/p' | grep -i "khnum\|neith\|commander"
(no output)
```

Before the fix, the same command printed the stale `CORRECTION 2026-10-07
(wq#3510, fix-2)` line (confirmed id=81 was `status='active'` prior to the
UPDATE, via `sqlite3 -line ... "SELECT * FROM fleet_decisions WHERE id=81"`).

Note: unrelated episode/note content elsewhere in the compiled prompt (e.g.
the `misc:commander-ha-pair-neith-khnum-...` block, sourced from episodes/notes
rather than `fleet_decisions`) still mentions khnum/neith historically — that
is out of scope for this ticket, which is specifically the `FRANK CORRECTIONS`
row from wq#3510.
