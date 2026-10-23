# wq#4588 — RCA: `claude_context.sh --core` payload flaked 74 KB ↔ 115 KB

ISSUE-LOG row 1559. wq#4585 measured `--core` three times: 115197 / 74089 / 74089 B, same
selector header (`mode=hybrid, core=0, selected=8, budget=20000`). The extra ~41 KB was the
claude_memory.md sections (FLEET FACTS, STANDING RULES, PERMANENT DECISIONS, …) after LIVE
FLEET STATE.

## Root cause

`--core` races itself:

1. `claude_context.sh:79` runs `context_builder.py --out /home/lumen/claude_memory.md`. It
   always rewrites the file (tempfile + `os.replace`, `context_builder.py:350-356`), even
   when nothing changed.
2. That rename fires `minerva-tag-sections.path` (`PathModified=/home/lumen/claude_memory.md`),
   which starts `minerva-tag-sections.service` → `tag_sections.py`.
3. `tag_sections.run()` began by **unlinking every `sections/*.md`**, then rewrote them one
   at a time, each after an Ollama embed call. `sections.json` was rewritten last. The
   window with files missing lasts 7–126 s (median 12 s over the last 98 runs in
   `minerva/logs/context.log`).
4. `claude_context.sh:81` runs `context_selector.py` right away. It loads the old
   `sections.json` (8 ids, header still says `selected=8`), but `read_section()`
   (`context_selector.py:55-57`) returns `''` for every file that is still unlinked.
   The header and the instincts block look the same, and the section bodies are gone.

So **74 KB was the truncated figure and 115 KB was the correct one.** The spike was the one
run where the selector won the race. At rest the selector alone emits 64326 B today
(`python3 context_selector.py 'session start core context' | wc -c`). Of that, 16297 B is
LIVE_FLEET_STATE.md and ~47.8 KB is the 8 section files, which is the same size as the
missing ~41 KB. That also means the wq#4540/wq#4576 boot-budget figures (75,020 B "post
wq#4576", 74089 B in wq#4585) were measured with the sections dropped.

Not the cause: `sections.json` being re-tagged *with different content* mid-read. The ids
are `{i:03d}_{slug}` and stay stable across regens, and the index is not read again after
the first load.

## Fix

`tag_sections.py` (full replacement of `/home/lumen/minerva/context/tag_sections.py`):

- no up-front unlink. Each section file, `sections.json` and `claude_memory_index.md` is
  written to a tmp file and `os.replace`d, so a reader never sees a missing or torn file.
- after the new index is in place, only `*.md` files whose id is not in it get pruned. The
  `*.md.bak.*` copies are left alone, as before.
- `sec_path` uses `SECTIONS_DIR` instead of a second hardcoded live path (that hardcoded
  path is why a test could not redirect the original).

What a reader can still see during a re-tag: the old index plus a mix of old and new
section bodies with the same ids. All the content is there. Token estimates can lag by one
regen. Residual window: if a regen drops or renames a section, a reader that loaded the
old index just before the prune reads `''` for that one id. That window is microseconds,
not the 7–126 s it was.

`reindex_surgical.py` (manual tool, nothing invokes it) has the same unlink-first pattern.
It is not changed here.

## Verify (fixtures only, no live access)

    python3 -m pytest -q ops/wq4588-core-selector-race/

`fixtures/tag_sections.orig.py` and `fixtures/context_selector.py` are pinned copies of the
live files (2026-10-23). `fixtures/claude_memory.md` is synthetic. The race test re-tags an
unchanged memory file in a thread with a 50 ms/section embed while sampling `select()`:

- `test_orig_tagger_truncates_selector_output`: the original tagger yields outputs smaller
  than at rest (the probe fails on the bug).
- `test_fixed_tagger_keeps_selector_output_complete`: every sample equals the at-rest size,
  with no torn-JSON errors.
- `test_fixed_tagger_prunes_only_dropped_ids`: a dropped section's file is removed, `.bak`
  files are kept, and no tmp files are left.

## Deploy

`install.sh` (minerva is write-walled from BUILD workers by guard.sh `WRITE_PRIMARY_TREES`):
it refuses if the live tagger drifted from the pinned original, backs up, installs, runs the
live secrets test and this suite, runs `--verify`, then prints two back-to-back `--core`
sizes. After deploy, expect **~115 KB every run**, not 74 KB. The boot budget needs
re-baselining against that number.
