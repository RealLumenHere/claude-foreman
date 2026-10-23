# wq#4537 — claude_memory.md hand prose wiped at every `--core` boot

Source: `scratch/research/memory-system-audit.md` F1 / QW1.

`claude_context.sh --core` runs `minerva/memory/context_builder.py --out /home/lumen/claude_memory.md`
(wq#3647), and `main()` `os.replace()`s the whole file with text built from Postgres
`fleet_memory`. Prose an agent wrote into the file is gone at the next boot. The 2026-09-08
SESSION blocks HANDOFF.md §3 points at survive only in `/home/lumen/claude_memory.md.bak.1790821395`.

## What this bundle ships

| File | Purpose |
|---|---|
| `context_builder.py` | Live file plus `hand_prose()`: with `--out` it reads the existing file, keeps the `<!-- hand-prose:begin -->` … `<!-- hand-prose:end -->` region byte for byte and appends it after the generated text. No region means a fresh empty one with an `## HAND PROSE` heading, so `tag_sections` indexes it as its own section. A begin marker with no end marker keeps everything to EOF. An unreadable file raises before `os.replace`, so the old file stays and `claude_context.sh` prints its existing WARN. Stdout mode is unchanged. |
| `apply_docs.py` | Exact-string, idempotent edits: ORCHESTRATOR-HANDOFF.md §7 file list and §9 "Rules" line; `fleet_tools.yaml` `memory_state.desc`. If an anchor has drifted, nothing is written. |
| `install.sh` | Backs up and installs the builder, writes `minerva/context/claude_memory_prose_2026-10-01.md` (`sed -n '/SESSION 2026-09-08/,$p'` of the .bak, as in QW1), applies the doc edits, regenerates claude_memory.md once so the region exists, then runs `--verify`. |
| `fleet_decisions_prose.sql` | Proposal only (instinct #7). The `STANDING RULES` row "Prose/procedural detail -> this file." is a fourth source of the bad instruction, rendered from PG. |
| `test_hand_prose.py` + `fixtures/` | 8 tests. Fixtures are frozen excerpts of the 2026-10-22 docs. |

Audit correction: `fleet_tools.yaml` never said "prose goes in claude_memory.md" (verified
2026-10-23, `grep -n claude_memory` → 0 hits). It just didn't say where prose goes. The edit
adds that sentence to `memory_state.desc` and doesn't fix any false claim.

## Why staged, not applied

The worker guard walls `/home/lumen/minerva` (wq#1719/wq#900), and `minerva/memory/` is
untracked in the minerva repo, so there's no PR path. Apply:

```
ssh lumen@100.117.66.102 'bash /home/lumen/foreman/ops/wq4537-claude-memory-hand-prose/install.sh'
ssh lumen@100.117.66.102 'bash /home/lumen/foreman/ops/wq4537-claude-memory-hand-prose/install.sh --verify'
```

## Evidence (2026-10-23)

- `python3 -m pytest -q ops/wq4537-claude-memory-hand-prose/test_hand_prose.py` → 8 passed.
- Live PG probe: the bundle builder plus the live `fm_db.py`, copied to `/tmp/fworker-wq4537`, ran twice with
  `--out` on a copy of `fixtures/claude_memory_with_prose.md` (70,174 bytes each run). The region was
  byte-identical to the fixture after run 1 and after run 2 (316 bytes). The stale generated text was gone.
- `apply_docs.py --check` against the live docs: every anchor was found exactly once (3 of 3 unapplied, 0 drifted).

## For Frank: lumen-session-start skill §4 (Anthropic side, only you can edit)

Replace "Prose/procedural detail goes in /home/lumen/claude_memory.md" with:

> Prose goes in the hand-prose region at the bottom of /home/lumen/claude_memory.md (between `<!-- hand-prose:begin -->` and `<!-- hand-prose:end -->`). Everything above it is regenerated each boot. Facts go via `lumen_state.py ingest`, episodes via `lumen_episode.py commit`.

Replace "~2K tokens" with "~20K tokens" (measured 89,865 bytes, audit F7).

## Not done here

- HANDOFF.md §3 still points at the deleted SESSION blocks. QW2 rewrites §3 completely. Point it at
  `minerva/context/claude_memory_prose_2026-10-01.md` there.
- `fleet_decisions_prose.sql` needs review and an apply (proposal only).
