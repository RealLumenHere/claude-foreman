# wq#4303: verify-pass of wq#4293 (PACK directory-of-small-files eval) — CONFIRM

Archetype 8 adversarial verify-pass. Author: claude-sonnet-5. Verifier: claude-sonnet-5
(fresh instance, this ticket). PR: http://100.121.17.5:3000/lumen/claude-foreman/pulls/2199
(gitea PR page itself 404s to this worker/session — likely an auth-gated view; the PR's
actual content was read via git history instead, see below). Verdict: **CONFIRM** — the
architectural claim holds and every file:line citation checks out, but the doc's live-state
numbers rest entirely on a single secondary source that is 5+ weeks stale and that no build
or verify worker can independently refresh. That is a sourcing weakness worth recording, not
a refutation.

## Claim under test

wq#4293 evaluates Percona-Lab/PACK (split a monolithic memory blob into many small tagged
Markdown files + a cheap index) against `claude_memory.md` and concludes **ALREADY
IMPLEMENTED, as a superset** — read-side chunking (`tag_sections.py` → `context/sections/`,
279 files, wq#1633) and write-side per-note files (`obsidian_bridge.py`, wq#2442) jointly
cover PACK's pitch, with semantic ranking PACK lacks. Recommendation: no new build.

## What I checked independently

1. **Found the actual shipped diff via git, not the gitea UI.** The PR page 404s for this
   session (`curl -o /dev/null -w '%{http_code}'` → 404 for both the PR URL and the bare
   repo URL — likely needs a login this worker doesn't have). Confirmed the repo/org name is
   right (`.git/config` origin = `http://.../lumen/claude-foreman.git`, masked), then found
   the content by commit instead: `git log --oneline --all | grep 4293` → two commits,
   `75a1cd48` (on `feat/wq4293-evaluate-pack-directory-of-small-files-m-a1`, confirmed via
   `git branch --contains`) and `df7ad855` (on `main`). `git diff 75a1cd48 df7ad855 --
   research/WQ4293_PACK_EVAL.md` is empty — identical content, the branch commit and its
   squash-merge onto main, not a duplicate. `wq show 4293` corroborates: notes field cites
   the same PR URL and "(merged 75a1cd48e5)". So `research/WQ4293_PACK_EVAL.md` as it stands
   on `main` today is exactly what PR #2199 shipped.

2. **Every internal file:line citation re-checked against the live repo text, not trusted
   from the report's paraphrase:**
   - `research/MINERVA_PIPELINE_MAP.md:118-119` — read directly. The report's quoted numbers
     (554 KB · 5,662 lines · 279 H2 sections · 137.6K tok · 29 core / 11.06K tok, the
     `tag_sections.py` → `sections.json`/`sections/NNN_<slug>.md`/`claude_memory_index.md`
     chain, "Regenerated 2026-09-10 05:39... this path IS working") are quoted verbatim,
     not paraphrased loosely.
   - `research/MINERVA_PIPELINE_MAP.md:121-122` — confirmed: `minerva_brief.py` writes the
     file directly (the one standing proposal-only exception) and `context_selector.select()`
     does load core + hybrid-ranked non-core under a 20K budget, exactly as quoted.
   - `research/MINERVA_PIPELINE_MAP.md:225` — confirmed verbatim: bge-m3/`sections.json` vs.
     nomic-embed-text/`federated_chunks`, "two embedders, two vector formats... for two
     indexes over overlapping text."
   - `research/MINERVA_PIPELINE_MAP.md:323` — confirmed: wq#577 is cited there as "proposal-
     only demotion... the one store where a human validity check is the design," matching
     the report's framing of the one real gap.
   - `memory/OBSIDIAN.md:19-23` — read directly, matches near-verbatim: "an Obsidian vault is
     just a directory of markdown files with YAML frontmatter. Writing a `.md` file into the
     vault *is* writing a note."
   - `memory/obsidian_bridge.py:102` — `write_note()` is exactly there, overwrites one file
     per ticket (`note_path` → `wq{ticket_id}.md`) plus one append line to a shared
     `_Log.md`. Confirmed: this is a new small file per subject, not an append into one
     growing document, for the actual note content (the report's claim). One **minor
     citation looseness**: the report cites `memory/obsidian_bridge.py:47-70` for "YAML
     frontmatter" — lines 47-70 are actually the escaping helpers (`_yaml_str`, `_yaml_list`,
     `note_path`, `log_path`) plus `render_note`'s signature/docstring; the frontmatter
     template itself (the `"---"` block) is lines 73-80. Same function neighborhood, wrong
     line range — immaterial to the claim, same pattern as wq#4300's README:169 nit.

3. **Checked whether the live-state claims could have been (or could now be) independently
   verified, rather than just cited.** `tag_sections.py`, `context_selector.py`, and
   `context/sections/` all live under `/home/lumen/minerva/`, which is **not a git repo**
   (`ops/wq1633-minerva-projection-dirty-flag/README.md:49`, "`/home/lumen/minerva/**` is not
   a git repository") and is **deny-listed from worker read grants by policy**
   (`kb/ISSUE-LOG.md` row #311: `harness/fleet-exec-policy.yaml:67` keeps
   `/home/lumen/minerva` off `allow.read_dirs` — "credentials mirrored into context/sections",
   wq#1467 — and `harness/tests/test_guard.py:111-116` asserts guard.sh denies
   `minerva/context` reads). I hit the identical wall this session: `wc -l
   /home/lumen/claude_memory.md` was refused ("may only count lines/words/bytes in files from
   the allowed working directories"), confirming the sandbox boundary is still live and
   applies equally to a verify worker. **This means wq#4293's own attempt (21:30:22-21:34:57,
   under 5 minutes) could not have run a live check either** — the entire verdict is sourced
   from one document, `research/MINERVA_PIPELINE_MAP.md`, last touched 2026-09-10
   (`git log -1 --format=%ad -- research/MINERVA_PIPELINE_MAP.md`), i.e. **5+ weeks before
   this ticket (2026-10-18)**, with zero independent corroboration anywhere else in the repo
   (`grep -rn "H2 sections\|279 files\|5,662 lines"` across `research/` and `reports/` hits
   only the two documents already named). `claude_memory.md` is actively written daily
   (`minerva_brief.py` prepend + hand edits per MAP:121), so the exact figures (554 KB, 279
   sections, the 2026-09-10 05:39 regen timestamp) are almost certainly stale by now even if
   the architecture is unchanged.

## Why this is CONFIRM, not CONTRADICT

The thing wq#4293 is actually asked to evaluate — does PACK's proposed pattern already exist
in some form, such that building a clone is wasted work — is an architectural question, not
a point-in-time metrics question. I independently verified the architecture directly (the
write side, `obsidian_bridge.py`, is fully inside my read grant and matches every claim made
about it) and verified every citation into the one inaccessible-live-state source is quoted
accurately. I have no evidence the `tag_sections.py`/`context_selector.py` pipeline has been
removed or broken since 09-10 — ISSUE-LOG shows ongoing work referencing it as recently as
wq#1639 (2026-09-11) with no retirement notice anywhere I can find. A stale-but-uncontradicted
source plus an accurate quote of it is a real evidentiary gap, not a falsified claim, and the
doc never claims to have run a fresh check — it cites the map doc plainly. Nothing here rises
to "the author invented a value and the fleet doesn't actually have it" (tier-0 instinct #8
territory); the value is real, attributed, and matches its cited source word-for-word. Flagging
the staleness is the honest finding; overturning the verdict on it would require counter-
evidence I don't have.

## Verdict

**CONFIRM.** Every checkable citation (6 of 6: two OBSIDIAN.md/obsidian_bridge.py claims, four
MINERVA_PIPELINE_MAP.md claims) matches the live text, one line-range is loose but points at
the right function. The "no new build needed" recommendation is architecturally sound and
consistent with everything else in the repo. One finding for the record, not a blocker: the
specific live-state numbers underpinning the verdict are 5+ weeks stale and structurally
unverifiable by any worker under the current `/home/lumen/minerva` deny-list (ISSUE-LOG #311)
— future EVALUATE tickets that depend on that store's live state should say so explicitly
rather than citing an old map doc as if freshly checked. No contradiction; no escalation to
opus-5@high warranted.
