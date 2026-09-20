-- wq#1775 — the corpus half of the contradiction store.
-- research/PGVECTOR_INVALIDATION_CONTRACTS.md §4.1. Deliberately the same shape
-- as note_contradictions (lumen_contradiction_scan.py, ensure_contradictions_
-- table) so one reporting path covers both stores.
--
-- lumen-contradiction-scan --corpus-apply creates this itself; the file is here
-- so the schema can be reviewed, diffed and applied by hand without running the
-- writer. Applying it writes nothing to research_docs or research_chunks.
--
--   minerva-query sql "$(cat doc_contradictions.sql)"   # needs a writable session

CREATE TABLE IF NOT EXISTS doc_contradictions (
  id            bigserial PRIMARY KEY,
  doc_id        bigint NOT NULL REFERENCES research_docs(id) ON DELETE CASCADE,
  chunk_ix      int,               -- NULL when the hit falls outside every chunk span
  fact_id       text NOT NULL,     -- minerva.db state_facts.id; cross-database, by value
  subject       text NOT NULL,
  predicate     text NOT NULL,
  dead_value    text NOT NULL,
  current_value text,
  valid_to      text,
  severity      text NOT NULL,     -- action | info  (as-of suppressor downgrades to info)
  char_start    int,
  excerpt       text,
  detected_at   timestamptz NOT NULL DEFAULT now(),
  resolved_at   timestamptz,
  UNIQUE (doc_id, fact_id, char_start)
);

-- ON DELETE CASCADE matches research_chunks: when the file is gone, the
-- contradiction is moot.
CREATE INDEX IF NOT EXISTS idx_doc_contra_open
  ON doc_contradictions (doc_id) WHERE resolved_at IS NULL;
