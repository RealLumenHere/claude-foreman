# CLAUDE EXTENDED MEMORY — synthetic fixture (wq#4588)

Synthetic stand-in for the context_builder.py render: same H1 + H2 shape, no live data.
Every section mentions session/context so the BM25 path selects it for the --core query.

## PERMANENT DECISIONS (never re-ask)

- session context decision one: keep the selector deterministic.
- session context decision two: section files are rewritten in place, never unlinked first.
- session context decision three: the index is replaced atomically.

## STANDING RULES

- session rule: research first, verify every path before acting on it.
- context rule: an acceptance probe must be able to fail.
- session rule: land through lumen-pr, never direct to main.
- context rule: mask before print.
- session rule: never restart Thor.

## FRANK CORRECTIONS

- context correction: the 74 KB figure was the truncated one, not the baseline.
- session correction: measure twice, compare bytes, not headers.

## FLEET DEVICES

- **alpha** — session context host, TS 192.0.2.10
- **beta** — session context host, TS 192.0.2.11
- **gamma** — session context host, TS 192.0.2.12
- **delta** — session context host, TS 192.0.2.13

## FLEET FACTS

### architecture:selector
- reads: sections.json then sections/<id>.md per selected id, for every session context
- budget: 20000 tok, gates non-core sections only

### architecture:tagger
- trigger: minerva-tag-sections.path on claude_memory.md PathModified, each session context build
- duration: 10-40 s per run, dominated by one embed call per section

### architecture:builder
- writes: claude_memory.md via tempfile + os.replace on every --core session context call

## RECENT EPISODES (newest first)

- wq#4588 session context selector race RCA — 2026-10-23
- wq#4585 session context instincts repoint measure — 2026-10-23
- wq#4576 session context boot budget deploy — 2026-10-23

## HAND PROSE (preserved across regen, wq#4537)

- session context hand-written note kept verbatim.
