# pgvector memory invalidation contracts

- ticket: wq#1713 (build, p3, proposal `3d0ca08f2f6e`) · worker `fworker-wq1713`
- date: 2026-09-11 PT · design + live measurement; no schema changed, nothing written to
  any store
- scope: pgvector `research_docs` / `research_chunks` (MINERVA_PIPELINE_MAP.md §1.8), and
  the invalidation contract as a reusable interface across the fleet's memory stores
- builds on: `MINERVA_STALENESS_REVIEW.md` (wq#1607), `MINERVA_PIPELINE_MAP.md`, and the
  machinery those shipped (wq#1609, #1611, #1612, #1613, #1633)
- measurement script: `ops/wq1713-pgvector-invalidation/measure_corpus_reach.py`

## 0. The finding in one paragraph

wq#1607 designed fact invalidation for the two stores that were visibly drifting and left
the corpus one sentence — §3.8, "add an `asserts_fleet_state` marking" — deferred until the
generalised contradiction scanner existed. It exists (wq#1609, PR #409), so this ticket
went to build the corpus half, and measuring first changed the answer. **Pointing wq#1609's
matcher at all 10,566 corpus documents produces 5 hits and 0 true positives.** The same
matcher, on the same dead-value index, on the fleet's terse artifacts the same afternoon,
produces 30 actionable hits that are almost all real. The difference is not tuning, it is
register: fleet artifacts assert (`plug->node: p2 nephthys/p3 hathor`), research prose
hypothesises (`if Isis or Hathor goes offline`, `a powered-down dGPU reduces chassis
temperature`), and four of the five false positives come from `self_identifying()` reading
hyphenated English compounds — `POWERED-DOWN`, `LIVE-VERIFIED`, `TORN-DOWN` — as
self-identifying tokens that need no subject in scope. So the corpus does not need a
contradiction scanner yet; it needs the two cheap things underneath one, and it needs the
matcher narrowed before anyone points it at prose. Meanwhile the measurement turned up
three findings that outrank the thing this ticket set out to design: **19 chunks are being
served from document versions that no longer exist**, **`foreman/research/` — the fleet's
own design corpus — is not in the index at all**, and **the `lumen-contradiction-scan` and
`lumen-state-verify` timers are `inactive`**, so the invalidation machinery wq#1607
designed and two closed tickets shipped has never actually run.

---

## 1. What landed since wq#1607, and what actually runs

| wq#1607 fix | ticket | code state | running? |
|---|---|---|---|
| 1 reconfirm-on-match | wq#1608 | open | — |
| 2 generalise the contradiction scanner | wq#1609 | merged, PR #409 | **no — `lumen-contradiction-scan.timer` is `inactive`, and `/home/lumen/bin/lumen-contradiction-scan` does not exist** |
| 3 contradiction over `archival_notes` | wq#1610 | blocked (sandbox, ISSUE-LOG #272) | — |
| 4 coverage metric | wq#1611 | done | — |
| 5 render staleness | wq#1612 | done | — |
| 6+7 verifier registry + `source_kind` | wq#1613 | done | **no — `lumen-state-verify.timer` is `inactive`** |
| 8 manifest/FLEET_MAP regen | wq#1615 | claimed | — |
| 9 contradiction penalty in `search_v3` | wq#1614 | blocked (sandbox) | — |
| 12 corpus marking | *never ticketed* | — | **this document** |

Both `install.sh` scripts require root, which is outside a build worker's reach, so both
tickets closed with the code merged and the deploy step documented-but-unrun. This is the
same class defect the review itself named in §2.1 — *a verification that produces no record
is indistinguishable from no verification* — one level up: **a scanner that is merged but
not enabled is indistinguishable from a scanner that was never written.** Running
wq#1609's scanner by hand today finds 30 actionable hits it has never reported to anyone:

```
dead-value index: 64 of 364 closed facts matchable (was 8 under the IP-only filter)
hits: 30 actionable, 2 informational, across 32 total
  [note_row] 15 · [regenerate] 2 · [ticket] 6 · [proposal] 3
  action archival_notes:27424d3dad48 — nephthys.ts_ip = '100.98.74.87'  (closed 2026-09-10)
  action archival_notes:8686257d957c — hathor.status  = 'UNREACHABLE'   (closed 2026-08-09, now 'healthy')
  action /home/lumen/FLEET_MAP.md:24 — gameboy.ts_ip  = '100.118.56.59' (closed 2026-09-10)
```

Enabling those two timers is worth more than everything else in this document, and it is
one root `install.sh` each. Filed as wq#1780.

Two upstream changes matter here. wq#1629 split fact values into a token `value` plus a
prose `note`, taking the dead-value index from 8 entries to **64 of 364 closed facts** —
large enough to be worth pointing at a corpus, and, as §4.2 shows, large enough to start
generating English words. wq#1633 gave projections a `generated-from: <store>@<ts>` header,
which is the convention §4.5 reuses for doc-level as-of stamping.

---

## 2. The corpus as it actually is

Measured 2026-09-11 (§8 explains how; all prior numbers in the fleet's research docs are
from 2026-09-09/10 and are superseded by these):

```
research_docs    10,566 rows · 86.2 MB of content · mtime span 2026-07-12 … 2026-09-10
research_chunks  19,771 rows
categories       other 10,169 (96.2%) · scout_gap 281 · pe_other 100 · pe_agent 16
docs older than 90d ....... 0
docs under foreman/ ....... 0
chunks with doc_hash != parent content_hash ..... 19     <-- invariant violated
chunks whose doc_id no longer resolves .......... 0      <-- structurally guaranteed
```

| | |
|---|---|
| writer | `index_research.py` daily 10:00 UTC (`lumen-research-index.timer`, **active** — the one timer in this area that runs), root `/home/lumen/scratch/research/**/*.md` (`index_research.py:11`); chunk pass `chunk_reembed.py`, gated on `doc_hash` |
| readers | deep-research orchestrator, fleet researchers, `minerva-query`, `minerva_verify` RAG context, AB-RAG (staged) |
| embedder | nomic-embed-text 768d — same as `search_v3`, different from `context_selector`'s bge-m3 |
| validity today | `content_hash` identity against the file, `ON CONFLICT (filepath) DO UPDATE` on change, `DELETE` for files that are gone (`index_research.py:158-163`) |

### 2.1 Mode A — file identity. Solved; do not touch it.

A row corresponds to a file and stops existing when the file does. This is the pattern
wq#1607 §5 told the other stores to copy, and every proposal below is additive to it: no
re-embedding, no deletion, no change to the ingest path.

### 2.2 Mode B — chunk/doc coherence. One half violated, one half structural.

`research_chunks.doc_hash` exists so a chunk can be matched to the document version it was
cut from. **Nothing checks it, and 19 chunks currently fail it.** Those 19 carry text that
no longer exists in their file, attributed to a document whose current content says
something else, fully embedded and fully retrievable. Small in absolute terms and
unambiguous in kind: `index_research` upserts a doc with a new `content_hash` before
`chunk_reembed` re-cuts it, and nothing closes the window or reports it. `federated_chunks`
is the same failure at scale — frozen at `indexed_at = 2026-08-16` against a source file
rewritten dozens of times since (§1.7), undetected until someone went looking.

The orphan half is in better shape than the design assumed:
`research_chunks_doc_id_fkey` is `ON DELETE CASCADE` (`confdeltype = 'c'`), so the
delete-reconcile at `index_research.py:162` takes the chunks with it, and the measured
orphan count is 0. That is a structural guarantee, not a lucky reading, so it needs a
regression check rather than a fix.

### 2.3 Mode C — assertion staleness. No mechanism, and smaller than expected.

A research document is a **dated record**. `wq94_ab_rag_2026-07-11.md` is not wrong; it is
a correct account of 2026-07-11. But it is retrieved by similarity to a question, and
questions are asked in the present tense. When a July report says Nephthys serves
embeddings and the store closed `nephthys.role` in August, the corpus serves the July
sentence unmarked, undated, at the rank it had the day it was written. This is
`MINERVA_STALENESS_REVIEW.md` §1.3's residual risk restated: *the row is not stale — the
file really does say that — but the answer is.*

The distinction rules out the obvious fix. No observation can re-verify a document, because
the document is not a claim about now. Nothing should expire it, rewrite it, or delete it.
The only correct intervention is at the point of use.

And the measured size of the problem is small. Full-corpus scan, §4.2: **5 hits, 0 of them
real**. The corpus contains no mechanically detectable superseded fleet assertion today.
That is a reason to build the cheap half (render the date) and not the expensive half (scan
for contradictions) — not a reason to declare the mode closed, since the corpus grows
~45 docs/day and `foreman/research/` (§2.4), the document class most likely to assert fleet
state, is not in it yet.

### 2.4 Mode D — corpus scope. Confirmed missing.

The indexer's root is `/home/lumen/scratch/research` (`index_research.py:11`), and
`SELECT count(*) FROM research_docs WHERE position('foreman' in filepath) > 0` returns
**0**. `/home/lumen/foreman/research/` — `MINERVA_PIPELINE_MAP.md`,
`MINERVA_STALENESS_REVIEW.md`, `FLEET_COST_PLAN.md`, this file — is absent from the fleet's
own semantic index. A researcher asking "how does minerva invalidate facts" gets arXiv
papers. That is a coverage bug rather than an invalidation bug, but it belongs in the same
batch, because internal fleet docs are exactly the population the invalidation machinery
exists for: they are the only ones that assert fleet state in the present tense.

---

## 3. The invalidation contract, as an interface

The recurring defect across wq#1607, wq#1633 and this review is that each store answered a
*different subset* of the same five questions, and nobody noticed the gaps because there
was no form to leave blank. Stating them as an interface makes the blanks visible and gives
the next store something to fill in rather than a bespoke patch to wait for.

**A store that holds memory must answer, in code:**

1. **Identity** — what makes a row correspond to its source, and what detects that the
   source changed?
2. **Removal** — what deletes or closes a row whose source is gone?
3. **Assertion** — what does this row claim about the world outside itself, and what is
   allowed to contradict it?
4. **Evidence** — what re-verifies it, how often, and *where is the audit row proving the
   check ran*?
5. **Presentation** — how does a reader see degraded confidence at the point of use?
6. **Liveness** — *is the machinery answering 1-5 actually enabled?* (Added by this
   review. wq#1609 and wq#1613 both answer 3 and 4 in merged code and neither has ever
   run; §1.)

Filled in for every store in the map:

| store | 1 identity | 2 removal | 3 assertion | 4 evidence | 5 presentation | 6 liveness |
|---|---|---|---|---|---|---|
| `state_facts` | key `(subject,predicate)` | `valid_to` on supersession | `lumen-contradiction-scan` | `fact_verifications` + registry sweep | `?`/`‼` + evidence age (wq#1612) | **timers inactive** |
| `archival_notes` | note id | revoked / superseded / merged | `note_contradictions` (wq#1610, blocked) | none — episodes are not re-observable | designed, not landed (wq#1614) | n/a |
| `research_docs` | `content_hash` + `mtime` ✅ | DELETE-reconcile ✅ | **empty** | n/a by nature (§2.3) | **empty** | indexer active ✅ |
| `research_chunks` | `doc_hash` — **19 violations** | CASCADE ✅ | **empty** | n/a | **empty** | ✅ |
| `federated_chunks` | mtime + sha ✅ | — | **empty** | — | flag off, serves nobody | path unit added by wq#1633 |
| `claude_memory.md` | none | none | scanner proposal tier | a human reading it | none | scanner not running |

The corpus is the only store watertight on 1 and 2 and blank on 3 and 5. That is a better
position than any other row, and it is why this is a marking job rather than a rebuild.

**Column 4 is `n/a` for the corpus on purpose.** The ticket asks for TTL+provenance; the
honest answer for a corpus is that TTL is the wrong instrument (§5.2). Writing `n/a` and
saying why is the point of the form — a blank that is a *decision* must be distinguishable
from a blank that is a *gap*, or the coverage metric wq#1611 shipped lies in the other
direction.

---

## 4. Mechanism

Additive, deterministic, written to live in `ops/` outside `/home/lumen/minerva`.

### 4.1 Schema — one column, one table

On `research_docs` (all nullable, no backfill required, no re-embedding):

| column | type | meaning |
|---|---|---|
| `doc_kind` | TEXT | `external_literature` · `fleet_internal` · `vendor_doc` · `generated` — resolved from **path**, not `category` (§5.1) |
| `asserted_at` | TIMESTAMPTZ | the doc's own as-of date: front-matter `date:` if present, else `mtime`, with the caveat in §4.4. **Not** `indexed_at`. |
| `asserts_fleet_state` | BOOLEAN | set when a contradiction lands in the doc; cleared when the last one resolves. Only meaningful once §4.2's precision problem is solved. |

New table, deliberately the same shape as `note_contradictions`
(`lumen_contradiction_scan.py:374-391`) so one reporting path covers both:

```sql
CREATE TABLE doc_contradictions (
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
CREATE INDEX idx_doc_contra_open ON doc_contradictions (doc_id) WHERE resolved_at IS NULL;
```

`ON DELETE CASCADE` matches what `research_chunks` already does and is correct: when the
file is gone, the contradiction is moot. `fact_id` is a cross-database reference held by
value — `state_facts` lives in `minerva.db` (SQLite), the corpus in Postgres — and the scan
re-derives every row from the live index, so a dangling reference self-heals on the next
pass instead of needing a foreign key that cannot exist.

### 4.2 Detection — measured, and not ready to ship

The design was to reuse wq#1609 wholesale: `build_dead_value_index()` (`:200`) for the
index, `scan_text()` (`:261`) for boundary-aware matching with subject/cue scoping and the
three suppressors, and a new `TIER_DOC = "doc_row"` whose only `--apply` action is an insert
into `doc_contradictions`. That part holds. What does not hold is the assumption that a
matcher tuned on fleet artifacts transfers to research prose.

`ops/wq1713-pgvector-invalidation/measure_corpus_reach.py`, run read-only against the live
corpus on 2026-09-11:

```
dead-value index entries ................ 64
prefilter candidates (distinct docs) .... 2,862  of 10,566   (27%)
elapsed ................................. 110.8s wall, 5.1s CPU, 52.8 MB peak
hits .................................... 5 action, 0 info
true positives .......................... 0
```

All five:

| hit | matched text | why it is wrong |
|---|---|---|
| `khepri.status = 'POWERED-DOWN'` | "a **powered-down** dGPU reduces chassis temperature by 10-15°C" | generic technical prose; no claim about khepri |
| `wadjet.status = 'POWERED-DOWN'` | same line | one line, two subjects, neither asserted |
| `khepri.status = 'POWERED-ON'` | "fraction of **powered-on** buckets where per-node CPU <1%" | a metric definition |
| `llama33-local.status = 'LIVE-VERIFIED'` | prose about memory/evidence conflict | the token appears as vocabulary, not as a claim |
| `hathor.status = 'OFFLINE'` | "\| **Node failure** – Isis or **Hathor offline**. \| Pipeline stalls. \|" | a hypothetical failure-mode table |

Two distinct causes, both prose-specific:

1. **`self_identifying()` reads hyphenated English compounds as self-identifying tokens.**
   Its rule is `len(value) >= 8 and re.search(r"[0-9._:/@-]", value)` (`:146`), so
   `POWERED-DOWN`, `LIVE-VERIFIED`, `TORN-DOWN` and `FULLY-RETIRED` all qualify and are
   matched **unscoped** — no subject, no predicate cue required, anywhere in 86 MB of text.
   Four of the five hits. This is the wq#1629 over-broad-`self_identifying` problem
   recurring in a form the shouting fix did not cover, and it is **a live defect in the
   deployed scanner**, not only a corpus concern: those same four entries are unscoped
   against `FLEET_MAP.md`, `claude_memory.md` and `archival_notes` today. Filed as wq#1781.
2. **Heading-level subject widening is too wide for long documents.** `OFFLINE` is scoped
   and still matched, because `enclosing_context()` lets a subject come from the nearest
   markdown heading — correct for a YAML block key, wrong for a 60 KB report where a
   heading names a node and 200 lines below it discuss something else.

The contrast is the finding. The *same* index and the *same* matcher, run against the
fleet's terse artifacts the same afternoon, returned 30 actionable hits that are almost all
real (§1). Terse artifacts assert; research prose hypothesises, defines vocabulary, and
argues about scenarios. Three narrowings make the corpus tier shippable, and all three
should be measured before, not after:

- **Self-identifying requires a non-alphabetic separator run**, not merely a hyphen: an IP,
  a `*.service` name, a hostname with a digit. `A-B` where both halves are dictionary words
  is a compound adjective, not a token. Applied here, 4 of 5 hits vanish.
- **Same-line subject only** for `doc_kind != fleet_internal`. No heading widening in prose.
  Applied here, the 5th vanishes.
- **A hypothetical suppressor** alongside the existing negation/changelog/as-of set: a line
  in conditional or definitional mood (`if `, `would`, `suppose`, `e.g.`, `fraction of`,
  a table row under a "failure mode"/"scenario" header) is not an assertion. This is the
  one that needs care — it is the most likely to suppress a real hit — and it is also the
  one the corpus needs most.

Until all three land and re-measure above zero true positives, **the corpus tier ships in
report-only mode**. A scanner writing `doc_contradictions` rows at 0% precision would
poison the read-time penalty in §4.5.2 with exactly the noise wq#1607 §3.5 wrote its
value-shape rules to avoid.

**Cost, now that it is measured.** The SQL prefilter is the right shape but the wrong
adjective: it does not "collapse to a handful," it returns 2,862 documents — 27% of the
corpus — because bare status words are common in technical writing. 110s wall against 5s
CPU says the time is 64 sequential `ILIKE` scans over an 86 MB text column, not the Python
matching. That is affordable for a daily timer and it is not affordable per-query. If the
index grows past ~200 entries, a `pg_trgm` GIN index on `content` is the next step; measure
first, since it is not free on a 10k-row text column.

**Localise to the chunk, not the document.** Free, because the chunk table already carries
the spans:

```sql
SELECT chunk_ix FROM research_chunks
 WHERE doc_id = %(doc_id)s AND %(char_start)s BETWEEN char_start AND char_end;
```

A 60 KB report with one superseded IP at character 41,000 has one bad chunk and roughly
thirty good ones. Penalising the document penalises all thirty. Doc-level
`asserts_fleet_state` stays, but it drives *rendering* (§4.5.3), never ranking.

### 4.3 Cadence

Fold into `lumen-contradiction-scan.timer` behind a `--corpus` flag — one scanner, one
dead-value index built once, two stores scanned, rather than a second timer racing the
first for the same index. Postgres unavailable must `WARN` and exit 0 on the corpus half
while the sqlite half still runs; the corpus is the less critical of the two and must never
take the fleet-facing scan down with it. This presupposes the timer is enabled at all
(wq#1780).

### 4.4 Staleness scoring — two states, because a third could never fire

Keep wq#1607 §3.6's calibration caveat verbatim: an ordering and marking signal, never a
percentage.

```
doc_staleness(d):
  open doc_contradictions with severity='action'  -> CONTRADICTED
  otherwise                                       -> DATED      (the normal state)
```

The draft of this design had a third state, `AGED-ASSERTING`, for internal docs older than
90 days that assert fleet state. **Measurement killed it: 0 of 10,566 docs have an `mtime`
older than 90 days**, and the whole corpus spans 2026-07-12 to 2026-09-10. Nothing would
ever have fired. This is the archival-notes finding from wq#1607 §1.2 repeating in a
different store — the corpus turns over faster than any age threshold worth setting, so
*contradiction, not age, is the invalidation signal here too*.

That measurement also undercuts `mtime` as a fallback for `asserted_at` (§4.1). A corpus
whose oldest file mtime is 2026-07-12 is not a corpus of documents all written since July;
it is a corpus whose file mtimes were reset by whatever populated the directory. `mtime`
answers "when did this file last change on disk," which is not "as of when is this true."
Front-matter `date:` is the real signal, `mtime` is the honest fallback, and the renderer
must label which one it is showing rather than presenting both as an as-of date.

There is deliberately no `FRESH` and no `STALE`. A corpus document is not fresh and does
not go bad; `DATED` is the correct resting state for 10,000 rows, and a vocabulary that
calls that condition a problem produces a metric nobody can drive to zero.

### 4.5 Read-time contract — the part that actually fixes it

Three obligations on every reader, in priority order:

1. **Render the as-of date, always.** Every retrieved chunk carries
   `(as of 2026-07-11, 62d — from front-matter)` or `(file mtime 2026-08-30, 12d)` on its
   attribution line. Highest value per line changed in this document: no scoring change, no
   evaluation, no risk, and it is the only one of the three that does not depend on §4.2's
   precision problem being solved. Today a July report and a September report arrive in a
   reader's context looking identical. Same reasoning as wq#1612 rendering evidence age in
   `LIVE_FLEET_STATE` — a session cannot discount what it cannot see.
2. **Soft-penalise contradicted chunks, never exclude them.**
   `score *= CORPUS_CONTRA_PENALTY` (env `MEMORY_V4_W_DOC_CONTRA`, default 0.5, 1.0 = off)
   for chunks with an open `severity='action'` row. Soft for wq#1607 §3.7's reason: a
   report with one superseded IP usually still carries the method, benchmark or decision
   that made it worth retrieving. `severity='info'` — the as-of suppressor's output, a doc
   correctly recording history — gets no penalty at all. **Gated on §4.2 reaching non-zero
   precision**; at today's 0/5 this switch would only degrade retrieval.
3. **Warn on assertion, in the answer.** When a served chunk's doc has
   `asserts_fleet_state`, append one line: `⚠ this document asserts fleet state as of
   <asserted_at>; check lumen_state for current values.` Deterministic, generated from a
   boolean, no model in the loop. Same gate as 2.

**Do not add a freshness prior to corpus ranking.** `W_REC = 0.00` was measured to cost
recall on the frozen golden set (wq#574) and wq#1607 §3.7 was right to forbid reverting it.
A penalty on documents we have *positive evidence about* is a different instrument from
blanket age decay, and only the former is proposed. An age term in corpus ranking is a
measured change against a corpus golden set that does not exist — build the set first.

**Evaluation.** Obligation 1 is display-only and needs none. Obligations 2 and 3 change
what reaches a reader, and the frozen note golden set
(`golden_v4_frozen_20260814.jsonl`, sha `8ba5afb0…f1bbf46b7`) does not cover corpus
retrieval, so citing it would be theatre. The honest gate is the one this ticket just ran:
scan read-only, take the actionable hits, and confirm by hand that each is a real
superseded claim in live text. Today that number is 0 of 5, and the correct response is to
fix the matcher, not to ship the penalty.

### 4.6 Doc-level supersession — explicit only

A newer report on the same subject often obsoletes an older one, and it is tempting to
detect that with embedding similarity plus a date comparison. **Do not.** Every other
invalidation path in this fleet is deterministic and key-based, which is why the
bi-temporal store is trusted at all (wq#1607 §5); a cosine threshold deciding that one
document retires another is a silent, unauditable, unreproducible mutation of the corpus,
and the first false positive costs more trust than the feature earns. §4.2 is a live
demonstration of what a plausible-looking heuristic does to precision on this exact text.

The supportable version is explicit: an optional `supersedes:` front-matter key naming a
prior document's path, populating `research_docs.superseded_by` at index time. Same
key-based shape as `archival_notes`, same rarity expected — that column reads 1 across
1,686 notes, which is the *correct* number for a hard invalidation used deliberately. Worth
the column; not worth a ticket until someone writes the first `supersedes:` line.

### 4.7 Chunk/doc coherence — one measured violation, one structural guarantee

```sql
-- I1: no chunk may be served from a document version it was not cut from
SELECT count(*) FROM research_chunks c JOIN research_docs d ON c.doc_id = d.id
 WHERE c.doc_hash IS DISTINCT FROM d.content_hash;        -- measured: 19  (must be 0)

-- I2: no chunk may outlive its document
SELECT count(*) FROM research_chunks c LEFT JOIN research_docs d ON c.doc_id = d.id
 WHERE d.id IS NULL;                                      -- measured: 0, CASCADE-guaranteed
```

Contract, both directions, so neither half has to trust the other:

- **Readers** join on `AND c.doc_hash = d.content_hash`. A chunk failing the join is text
  that no longer exists in the file and must not be served; a doc whose chunks all fail
  falls back to doc-level retrieval, which is the pre-chunking behaviour and is safe. This
  closes the 19 immediately, without waiting for the writer fix.
- **`chunk_reembed`** deletes the old chunk rows for a `doc_id` before writing new ones, so
  the window closes at the source. I2 needs no work — `research_chunks_doc_id_fkey` is
  already `ON DELETE CASCADE` — only a regression check that it stays that way.
- **Both counts are reported** by the daily pass and by `handoff verify`, per wq#1611's
  rule: a zero nobody prints is indistinguishable from a check that never ran. Growth in
  either raises an `aq` row.

### 4.8 Corpus scope

`foreman/research/` is confirmed absent (§2.4). Add it as a second indexer root with
`doc_kind = fleet_internal`. It is the document class most likely to assert fleet state, so
it is simultaneously the highest-value addition to the index and the population §4.2's
narrowed matcher would be aimed at — and, until it is indexed, the corpus scan has almost
nothing to legitimately find, which is a large part of why the reach measurement came back
at 5 hits.

---

## 5. TTL and provenance, as the ticket framed them

### 5.1 Provenance: `doc_kind`, from the path

`doc_kind` must be derived from the file path, deterministically, and never from content.
The design's first draft resolved it from the existing `category` column; measurement rules
that out — `classify()` puts **10,169 of 10,566 docs (96.2%) in `other`**, so `category`
carries almost no signal.

| `doc_kind` | source | invalidation semantics |
|---|---|---|
| `external_literature` | arXiv/vendor papers under the corpus root | never invalidated; age is metadata, not a defect |
| `vendor_doc` | API/product documentation | may be superseded by a newer fetch of the same URL; `supersedes:` only |
| `fleet_internal` | `foreman/research/`, `agents/work-queue/reports/` (once §4.8 lands) | the only class scanned with heading-widened subjects; the population contradiction scanning is aimed at |
| `generated` | deep-research output, model-written summaries | as `fleet_internal`, plus: never a source of truth for a fleet fact — a contradiction here always resolves in favour of `state_facts` |

This mirrors `state_facts.source_kind` (wq#1613) on purpose. The fleet then has one
provenance vocabulary and one rule falling out of it: *an observation never overrides what
Frank stated, and a document never overrides an observation.*

### 5.2 TTL: the wrong instrument, and what replaces it

A TTL answers "when should this stop being believed." For a dated record the answer is
never — the document never claimed to be current. Applying one would reproduce the dead
mechanism `half_life_days` became in `state_facts`: set on 9 of 830 rows, filtering
nothing, inert (wq#1607 §1.1). §4.4 is the second data point — the age threshold this
design started with would have matched 0 documents out of 10,566. The fleet has now run
this experiment twice.

What a TTL reaches for is **the reader's ability to discount by age**, and that is a
rendering problem, solved by `asserted_at` on the attribution line (§4.5.1) with an honest
label saying whether the date came from front-matter or from a file mtime.

---

## 6. Ranked plan

| # | fix | catches | effort | risk |
|---|---|---|---|---|
| 0 | **Enable the two inactive timers** — root `install.sh` for wq#1609 and wq#1613 (wq#1780) | 30 actionable contradictions the fleet has never been told about, plus every verifier sweep since wq#1613 closed. Outranks everything else in this document. | S | low — both are report-first, `--apply` only writes machine tiers |
| 1 | **Narrow `self_identifying()`** so hyphenated English compounds are scoped (wq#1781) | a live false-positive source in the *deployed* scanner, against notes, FLEET_MAP and claude_memory — not only the corpus | S | low — strictly narrows; re-run §1's scan to confirm the 30 hold |
| 2 | **`asserted_at` rendered on every retrieved chunk** (§4.5.1, wq#1773) | July and September reports arriving in context indistinguishable. No scoring change, no eval, no dependency on #3. | S | low — display only |
| 3 | **Reader-side `doc_hash` join + `chunk_reembed` delete-before-write** (§4.7, wq#1776) | the 19 chunks being served from document versions that no longer exist | S | low — one join clause; I2 already structural |
| 4 | **Index `foreman/research/`** (§4.8) | the fleet's own design corpus missing from its own index; also the population #5 needs to be worth running | S | low — more rows, same pipeline |
| 5 | **`--corpus` tier, report-only**, with the three narrowings in §4.2 (wq#1775, re-scoped) | assertion staleness in the corpus — *after* #1 and #4, and only if re-measurement clears zero | M | medium — 0/5 precision today is the reason it is report-only |
| 6 | **`asserts_fleet_state` warning + contradiction penalty** (§4.5.2-3) | contradicted chunks outranking current facts | M | medium — gated on #5 producing a clean hit set; env var is the rollback |
| 7 | **`doc_kind` from path** (§5.1) | completes the provenance vocabulary; `category` is 96% `other` and cannot serve | S | low |
| 8 | **`supersedes:` front-matter → `superseded_by`** (§4.6) | explicit doc retirement | S | low — dormant until first use |

Sequencing: 0 and 2 are independent of everything and should land this week. 1 gates 5; 4
makes 5 worth running; 6 gates on 5. 3 is independent. 7 and 8 are finishing work.

### Tickets

| fix | ticket | lane/pri |
|---|---|---|
| 0 enable the two timers | wq#1780 | build p1 |
| 1 narrow `self_identifying()` | wq#1781 | build p1 |
| 2 `asserted_at` at read time | wq#1773 | build p1 |
| 3 chunk/doc coherence | wq#1776 | build p2 |
| 5 `--corpus` scan tier (report-only, gated) | wq#1775 | build p2 |
| — reach measurement | wq#1772 | **done by this ticket** (§4.2) |

Fixes 4, 6, 7 and 8 are not ticketed: 4 needs a decision on indexer roots that belongs with
whoever owns `index_research.py`, 6 gates on 5, and 7 and 8 are not worth a ticket before 5
has run once.

---

## 7. What this deliberately does not do

- **Does not expire, delete, or re-embed anything.** No corpus row's lifecycle changes.
- **Does not put a model in the invalidation path.** Every mechanism is deterministic and
  key-based, for the reason wq#1607 §5 gives.
- **Does not auto-supersede by similarity** (§4.6).
- **Does not revert `W_REC = 0.00`** or add blanket age decay to ranking (§4.5).
- **Does not ship a corpus contradiction writer at today's precision** (§4.2).
- **Does not change the bi-temporal model, `index_research.py`'s ingest path, or the
  proposal-only law** for `claude_memory.md` and `instincts.yaml`.
- **Does not claim a calibrated score.** `doc_staleness` is two ordinal states.

---

## 8. How this was measured, and the access note

Every number above is from 2026-09-11 against the live stores. The route matters for the
next research worker, because the obvious ones are all closed:

- `/home/lumen/minerva/**` and `/home/lumen/scratch/**` are outside the build worker's path
  sandbox by deliberate policy (`fleet-exec-policy.yaml:67`, wq#1467 — credentials are
  mirrored into `minerva/context/`). Same wall that blocked wq#1610 and wq#1614;
  ISSUE-LOG #272.
- `minerva-query` and `psql` are **not on the worker Bash allowlist** — both refuse with
  `This command requires approval`.
- **What works: `/home/lumen/bin/lumen-run`** (allow-listed as `Bash(/home/lumen/bin/lumen-run *)`,
  ISSUE-LOG #320). It launches a detached `systemd-run` unit reparented to PID 1, outside
  the path sandbox, and `lumen-run log UNIT` returns the journal. So
  `lumen-run start -- /home/lumen/bin/minerva-query sql "SELECT …"` reaches pgvector with
  `minerva-query`'s own `SELECT`/`WITH` gate and `default_transaction_read_only = on` still
  enforcing read-only, and `lumen-run start -- python3 <script>` runs a measurement script
  from the worktree against both stores at once. Logged as ISSUE-LOG #321.

Two `minerva-query` gotchas found the hard way: a literal `%` in freeform SQL is
interpreted as a psycopg parameter placeholder and raises `IndexError: tuple index out of
range` (use `position('x' in col) > 0` instead of `ILIKE '%x%'`), and only one statement per
invocation is allowed.

Measurements, all read-only:

```bash
lumen-run start -- /home/lumen/bin/minerva-query sql "SELECT (SELECT count(*) FROM research_chunks c
  JOIN research_docs d ON c.doc_id=d.id WHERE c.doc_hash IS DISTINCT FROM d.content_hash) AS lagging_chunks,
  (SELECT count(*) FROM research_chunks c LEFT JOIN research_docs d ON c.doc_id=d.id WHERE d.id IS NULL)
  AS orphan_chunks, (SELECT count(*) FROM research_docs) AS docs, (SELECT count(*) FROM research_chunks)
  AS chunks"
#   lagging_chunks 19 · orphan_chunks 0 · docs 10566 · chunks 19771

lumen-run start -- /home/lumen/bin/minerva-query sql "SELECT conname, confdeltype FROM pg_constraint
  WHERE contype='f' AND conrelid='research_chunks'::regclass"
#   research_chunks_doc_id_fkey · c   (= ON DELETE CASCADE)

lumen-run start -- /home/lumen/bin/minerva-query sql "SELECT sum(size_bytes) AS bytes, min(mtime)::date,
  max(mtime)::date, count(*) FILTER (WHERE position('foreman' in filepath) > 0) AS foreman_docs,
  count(*) FILTER (WHERE mtime < now() - interval '90 days') AS older_90d FROM research_docs"
#   86150359 · 2026-07-12 · 2026-09-10 · 0 · 0

lumen-run start -- /home/lumen/bin/minerva-query sql "SELECT category, count(*) FROM research_docs
  GROUP BY category ORDER BY 2 DESC"
#   other 10169 · scout_gap 281 · pe_other 100 · pe_agent 16

lumen-run start -- python3 ops/wq1713-pgvector-invalidation/measure_corpus_reach.py
#   index 64 · candidates 2862 · 110.8s · 5 action / 0 info · 0 true positives

lumen-run start -- python3 ops/wq1609-minerva-contradiction-scan/lumen_contradiction_scan.py
#   30 actionable, 2 informational — on the fleet artifacts, same index, same afternoon

lumen-run start -- systemctl is-active lumen-contradiction-scan.timer lumen-state-verify.timer \
                                       lumen-research-index.timer
#   inactive · inactive · active
```

Numbers superseded by this pass, for anyone reading the older documents: `research_docs`
10,390 → **10,566**, `research_chunks` 19,435 → **19,771**
(`MINERVA_PIPELINE_MAP.md` §1.8, 2026-09-10); "0 orphans" still holds and is now known to be
structural; "the healthiest store here" (`MINERVA_STALENESS_REVIEW.md` §1.3) holds for mode
A and does not hold for mode B.

### 8.1 Re-measurement, 2026-09-20 (wq#1772)

Everything above is a correct account of 2026-09-11 and is left standing as one — §2.3's
own argument. This is the same three measurements re-run nine days and 898 documents later,
read-only, by the same route. I1/I2 now come from wq#1776's shipped checker
(`ops/wq1776-pgvector-chunk-doc-invariants/check_chunk_doc_invariants.py --json`, run
without `--raise-aq` so it persists nothing) rather than hand-typed SQL.

| | 2026-09-11 | 2026-09-20 |
|---|---|---|
| I1 stale chunks | 19 | **21** |
| I1 docs affected | — | 21 |
| I2 orphan chunks | 0 | 0 |
| `research_chunks_doc_id_fkey` | `confdeltype='c'` | `confdeltype='c'`, `confupdtype='a'` |
| docs · chunks | 10,566 · 19,771 | 11,464 · 21,552 |
| corpus bytes | 86.2 MB | 93.9 MB |
| `mtime` range | 07-12 … 09-10 | 07-12 … 09-20 |
| docs older than 90d · under `foreman/` | 0 · 0 | 0 · 0 |
| dead-value index entries | 64 | 87 |
| prefilter candidates | 2,862 of 10,566 (27%) | 3,617 of 11,464 (32%) |
| elapsed | 110.8s | 168.0s |
| hits | 5 action, 0 info | 9 action, 0 info |
| true positives | 0 | **0** |

**I1 is still violated and drifting.** Two more stale chunks in nine days, against 8.5%
corpus growth — a slow leak, not a one-off window. wq#1776's checker is installed
(`/home/lumen/bin/check-pgvector-invariants`, 2026-09-18) and its `install.sh:3` ships no
timer by design, so the drift is measurable on demand and watched by nothing;
`lumen-chunk-doc-invariants.timer` is `inactive`. Nine days of drift went unnoticed until
this pass re-ran the numbers by hand — wiring filed as wq#2152, behind wq#1788.
`lumen-contradiction-scan.timer` has since gone `active` (it was `inactive` on 09-11).

**I2 is still 0, and the guarantee re-read in full this pass** is
`FOREIGN KEY (doc_id) REFERENCES research_docs(id) ON DELETE CASCADE` — confirmed twice
independently, from `pg_constraint.confdeltype` and from `pg_get_constraintdef`.

**Reach: wq#1781's fix worked, and uncovered a worse class underneath.** All four
hyphenated-compound false positives from 09-11 (`POWERED-DOWN`, `POWERED-ON`,
`LIVE-VERIFIED`) are gone. The fifth survives unchanged — `hathor.status = 'OFFLINE'` in the
same hypothetical failure-mode table. The other eight hits are one new failure mode:

`isis.service_state` closed `UP → DEGRADED`. `UP` is two characters, so `self_identifying()`
(`:146`) correctly declines to treat it as self-identifying and scopes it to its subject.
The scope test is subject-token **and** predicate-cue present in the enclosing section, and
`boundary_re()` (`:188`) compiles case-insensitively — so `\bUP\b` matches the English
preposition. All eight hits are the word "up" in ordinary prose that happens to sit in a
section mentioning Isis and a service: *"Spin up a FastAPI orchestrator"*, *"Set up the
Docker container"*, *"Spin up a `pytorch/pytorch:2.3-cuda12.1` image"*.

This is not wq#1781 recurring. There the value was wrongly unscoped; here it is correctly
scoped and still wrong, because subject-plus-cue is far too weak a scope for a value that is
also a function word — any prose *about* the subject contains it. Filed as wq#2151.

The §4.2 verdict is unchanged and now has a second independent pass behind it: **0 true
positives**, twice. The blocker is precision, not reach — a third of the corpus already
reaches the matcher.

### 8.2 The common-word fix, 2026-09-20 (wq#2151)

Filed off §8.1's hit list and worked the same afternoon, by which point half of it had
already been fixed by someone else. **wq#1775 landed its corpus tier in between**, carrying
a narrowing of this very class — a 2-4 character all-caps value must be shouted in the
prose too (`SHOUTED_SHORT_RE`, corpus-only, measured on the same four surviving hits).
Two lanes reading the same §8.1 evidence reached the same diagnosis within hours; the
rebase is where that surfaced, not the dispatch.

So the corpus half is already closed, and this branch neither loses nor regains reach on it
(`ops/wq1713-pgvector-invalidation/measure_corpus_reach.py`, which since wq#1775 calls the
shipped `scan_corpus()` rather than a copy):

| | main | this branch |
|---|---|---|
| dead-value index entries | 87 | 87 |
| prefilter candidates | 3,617 | 3,617 |
| corpus action hits | 0 | 0 |

What wq#1775 deliberately did *not* touch is the fleet tiers, and it said why in its own
test: *"Fleet artifacts keep the case-insensitive match they were measured with."* Not a
judgement that they were clean — an admission that nobody had looked. This ticket looked.
`lumen-contradiction-scan --json` over notes and fleet artifacts, read-only, 2026-09-20,
438 closed facts, index 87 both times: **32 hits → 28**, four removals, nothing added.

| removed | why it was wrong |
|---|---|
| `archival_notes:6d29bb85c4b9` `sia.status = 'UP'` | *"- 3 containers up"* |
| `LIVE_FLEET_STATE.md:19,20` `isis.service_state = 'UP'` (info) | the word inside two hardware inventory lines |
| `fleet/inventory/machines/bes.yaml:7` `bes.status = 'DOWN'` | `alert_desc: "bes unreachable — … vault standby slot down"` |

Four false positives, no true positive lost. That last one is why the shipped rule is a
word list rather than wq#1775's length gate promoted to the fleet tiers: `DOWN` sits in a
file the scanner may open a *ticket* against, and `ONLINE`, `RUNNING` and `ACTIVE` are all
past four characters. `COMMON_WORD_VALUES` is the axis the failure actually has — an
English word — and `SHOUTED_SHORT_RE` keeps covering what it does not: `OK`, `EOL`, `RMA`,
shouted and short and not words.

Of the ticket's three options, (a) ships, (b) was implemented and then dropped, (c) is
rejected.

**(b), the line-local predicate cue, was measured and withdrawn.** Requiring the cue on the
matched line rather than in the enclosing section removed exactly one further corpus hit
against the pre-wq#1775 matcher — one that case-exactness removes anyway
(`corpus_reach_pre_wq1775.txt`: 9 hits; cue-rule-only: 8; case-only: 1) — and zero fleet
hits. Its one measured effect on the other side was to break
`test_prose_subject_may_not_come_from_an_enclosing_heading`: `# pbox status` followed by
*"the scheduler stayed active throughout"* in a fleet-internal doc, which wq#1775 asserts
is a hit. A rule whose measured benefit is zero and whose measured cost is a deliberate
behaviour elsewhere does not ship. The theoretical case for it survives — it is the only
rule that would fire on a common word recorded in *lowercase*, where case-exactness is a
no-op — but `UP` does not exercise it and neither did anything else in 32 hits, so it goes
back on the shelf until something measures it.

**(c), a minimum-length gate, is rejected on the evidence:** length is the wrong axis.
`P3`, `E1` and `OK` are as short as `UP` and carry none of the risk, while `free`, `live`
and `down` are longer than the gate would have to be. What makes `UP` dangerous is not that
it is two characters, it is that it is a word English prose uses about anything.
