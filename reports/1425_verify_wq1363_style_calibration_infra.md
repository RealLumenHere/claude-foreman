# wq#1425 — Adversarial verify-pass of wq#1363 (per-contact style calibration)

**Verdict: CONTRADICT** — the doc's analysis of individual/cultural context-conveying
differences (high/low-context, direct/indirect, medium norms, verbosity-as-signal,
idiolect) is sound guidance and the `messages`/`threads` schema citations it makes are
accurate. But its foundational "Existing infrastructure this builds on" section
presents `contact_notes` (D1, wq#1167) and `contacts/*.md` lore injection (wq#1200/
wq#1359, `loadContactLore()`) as infrastructure that **already exists and is live** on
mailbot main. Neither does. Both sit on unmerged branches; the ticket to land them
(wq#1338) is `status: blocked`. A worker implementing this doc's central proposal
("extend `contact_notes` with a `style_profile` JSON column") would hit a table that
does not exist.

## What was shipped

PR http://100.121.17.5:3000/lumen/claude-foreman/pulls/297 (merged, commit `8ff3196`,
1 file): `research/brain/style_calibration.md`, 191 lines, docs-only — no mailbot code
changed, matching its own stated scope ("guidance and a schema proposal, not an
implementation").

## Claims checked against /home/lumen/mailbot (origin/main @ 4b12e6d, fetched fresh)

**Accurate:**
- `loadHistory()` at `worker.js:306`, `loadRecentDodgeStyles()` at `worker.js:317` —
  both exist at the exact cited lines, with the exact cited behavior (bounded recent
  window on the latter, used to avoid repeating a dodge style).
- `messages` table has `direction`, `ts`, `body`, joined to `threads` on
  `contact_email` — confirmed against `migrations/0001_init.sql:1-21`.
- `contacts/jonathan.md`, `_lore_jonathan_only.md`, `_lore_lumenjonathan.md` exist as
  files on main (landed by wq#1200, commit `867f638`).

**False, as stated (present tense, "already exists"):**
- `contact_notes` D1 table: **does not exist on main.** No migration defines it —
  `migrations/` on main has only `0001_init.sql` (threads, messages), `0002_dodge_style.sql`,
  `0003_suppressed.sql`. No `contact_notes` string appears anywhere in
  `mailbot/src/worker.js` (`grep -n contact_notes src/worker.js` → zero hits). The only
  repo hits for `contact_notes` are inside the lore markdown file
  (`contacts/jonathan.md:3,23`), i.e. Frank's own notes describing a feature that was
  never actually wired up.
- `loadContactLore()`: **does not exist on main.** Zero hits anywhere under
  `mailbot/src/*.js`. It exists only on the unmerged branch
  `feat/wq1359-mailbot-contacts-md-lore-has-zero-reader-a1` (commit `d006ae6`,
  2026-09-03 05:41:59), which is not an ancestor of `main` (2026-09-03 06:24:16,
  42 min later — main advanced past it without merging it).
- The "ABOUT THEM" prompt section the doc says the worker "loads... into the prompt":
  no such string exists in `worker.js` on main.
- wq#1359's own ticket text (`wq show 1359`) independently confirms this, in mailbot's
  own words: *"contacts/_lore_jonathan_only.md, ... are ON main ... but NOTHING reads
  them ... wq#1167 ('mailbot: per-contact notes') and wq#1205 (its anti-injection
  bugfix) are both status:done, but their code never reached main; unmerged branches
  feat/wq1167-*, feat/wq1205-*, feat/wq1214-* still exist."* wq#1338, the ticket to
  land the whole series onto main, is `status: blocked` (`wq show 1338`).

## Why this matters, not just pedantry

The doc's central deliverable is a schema and update procedure for a `style_profile`
JSON column "extending" `contact_notes`, and its "using the profile" / "never let it
override `contacts/*.md`" sections assume lore injection is a live code path something
can conflict with. Today, on main, there is no `contact_notes` row to extend and no
lore-injection code path for a profile to conflict with or defer to — the two things
the guidance is calibrated against are themselves unlanded. An implementer following
this doc literally would either silently invent the table from scratch (fine, but not
what "extend" implies) or discover the premise is wrong only after reading the code,
which is exactly the wasted-cycle failure mode a grounding section is supposed to
prevent. The doc should have flagged the wq#1338 dependency; it doesn't mention it or
hedge the tense anywhere.

## What's correct and should survive

The five dimensions-of-variation section and the anti-patterns section (stereotyping
from demographic guesses, overfitting to one message, conflating style with sentiment,
leaking raw characterization into the prompt) are the substantive intellectual content
of the ticket and hold up; nothing in them depends on the infra-existence error. Only
the "existing infrastructure" framing and the parts of "what the profile stores" /
"using the profile" that assert current-tense behavior are wrong.

## Verdict

CONTRADICT. Escalating per archetype-8 protocol: contradiction → opus-5@high.
