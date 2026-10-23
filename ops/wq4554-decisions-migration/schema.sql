-- wq#4554 (wq#4539 phase 2): fleet_decisions in minerva.db, mirroring PG
-- fleet_decisions columns (id, decision, rationale, scope, status, created_at,
-- supersedes) plus superseded_by/superseded_ts, derived at migration time from
-- the reverse of `supersedes` (the PG schema records "this row supersedes X",
-- not "X is superseded by this row").
CREATE TABLE IF NOT EXISTS fleet_decisions (
    id              INTEGER PRIMARY KEY,   -- same id as the PG source row
    decision        TEXT NOT NULL,
    rationale       TEXT,
    scope           TEXT NOT NULL,
    status          TEXT NOT NULL,
    created_at      TEXT NOT NULL,
    supersedes      INTEGER,
    superseded_by   INTEGER,
    superseded_ts   TEXT
);

-- context_builder.py's "FLEET FACTS" render section reads fleet_facts rows
-- with source LIKE 'claude_memory.md%' — 206 live rows on PG at migration
-- time (checked 2026-10-23), outside the design doc's roster/episodes/
-- decisions table but still live content the freeze would otherwise silently
-- drop. Migrated for the same reason decisions are: nothing authoritative
-- may live only behind a table this ticket is about to rename away.
CREATE TABLE IF NOT EXISTS fleet_facts (
    id          INTEGER PRIMARY KEY,   -- same id as the PG source row
    subject     TEXT NOT NULL,
    attribute   TEXT NOT NULL,
    value       TEXT,
    source      TEXT,
    status      TEXT NOT NULL,
    expires_at  TEXT
);
