# wq#4293 — EVALUATE: Percona-Lab/PACK directory-of-small-files memory

**VERDICT: ALREADY IMPLEMENTED, as a superset. The pattern PACK describes —
split a monolithic memory blob into many small tagged Markdown files, load a
cheap index instead of the whole file, retrieve scoped-in — is live on two
independent paths: the read side (`tag_sections.py` → `context/sections/`,
wq#1633/§1.9d) and the write side (`obsidian_bridge.py`, wq#2442). Both
predate this ticket. Nothing here needs building.**

## The ticket's premise is stale

The ticket describes `claude_memory.md` as "one 497-line blob." It is not,
and hasn't been for a while: `/home/lumen/claude_memory.md` is 554 KB,
5,662 lines, **279 H2 sections**, 137.6K tokens total
(`research/MINERVA_PIPELINE_MAP.md:118`). The 497-line figure is old.

## Read side: this already IS PACK's directory-of-small-files

`research/MINERVA_PIPELINE_MAP.md:119`:

> `minerva-tag-sections.path` (watches the file) → `tag_sections.py` →
> `context/sections.json` (2.2 MB, bge-m3 embeddings inline) +
> `context/sections/NNN_<slug>.md` (**279 files**) +
> `context/claude_memory_index.md`. Regenerated 2026-09-10 05:39, i.e. 6 min
> after the last file write — this path IS working.

That is PACK's whole pitch, already shipped: one small file per subject
(`sections/NNN_<slug>.md`, 279 of them) plus a cheap index
(`claude_memory_index.md`), instead of loading the 137.6K-token monolith.
The boot-time reader doesn't even load the index-then-everything — it's
scoped further still: `research/MINERVA_PIPELINE_MAP.md:122` —
`context_selector.select()` loads **core sections + hybrid-ranked non-core
under a 20K-token budget**, using bge-m3 embeddings with BM25+RRF, not a
flat tag match. Core alone is 29 sections / 11.06K tokens
(`research/MINERVA_PIPELINE_MAP.md:118`) — an ~92% reduction off the 137.6K
full file for the common boot path, in the same range as PACK's claimed
94%, achieved with semantic ranking PACK's tag scheme doesn't have.

## Write side: directory-of-files-with-YAML-frontmatter, already named that

`memory/OBSIDIAN.md:19-23` (wq#2442) states PACK's exact mechanism, written
before this ticket existed:

> an Obsidian vault is just a directory of markdown files with YAML
> frontmatter. Writing a `.md` file into the vault *is* writing a note.

`memory/obsidian_bridge.py:102` (`write_note`) renders each episode's
archival digest as its own note with YAML frontmatter
(`memory/obsidian_bridge.py:47-70`) — a new small file per subject, not an
append into one growing document. This closes PACK's "writes go to a
specific file, no merge corruption" claim for the episodic/archival side:
each ticket's note is its own file, so two concurrent writers can't
collide on one blob the way two hand-edits of `claude_memory.md` could.

## The one real gap vs. PACK, and why it's not worth a new build

`claude_memory.md` itself is still a single authoritative prose file for
*writes* — hand edits plus `minerva_brief.py`'s daily prepend
(`research/MINERVA_PIPELINE_MAP.md:121`) — even though *reads* are fully
chunked via `tag_sections.py`. A PACK-style "one file per subject,
authoritative" write model would remove that collision surface entirely.
But `research/MINERVA_PIPELINE_MAP.md:121` already flags this as standing
law ("proposal-only... the brief writer is the one standing exception") and
`:323` tracks demotion out of the monolith as a separate, already-open
thread (wq#577), not a gap this ticket discovered. Re-architecting the
authoritative store is a bigger, already-tracked project; bolting a second
competing small-files scheme onto an already-working chunked-read +
per-note-write pair would add a third index to reconcile for no measured
gain — `research/MINERVA_PIPELINE_MAP.md:225` already notes the fleet is
carrying two embedders/two vector formats (bge-m3 for `sections.json`,
nomic-embed-text for `federated_chunks`) over overlapping text; a PACK
clone would be a third.

## Recommendation

No new build. Close as already-implemented: `tag_sections.py`/
`context_selector.py` (read-side chunking + budget-gated retrieval,
wq#1633) and `obsidian_bridge.py` (write-side per-note files, wq#2442)
jointly deliver what PACK proposes, with semantic ranking PACK lacks. If
`claude_memory.md`'s single-file write path is ever worth revisiting, that
is wq#577's demotion thread, not a new PACK-pattern build.
