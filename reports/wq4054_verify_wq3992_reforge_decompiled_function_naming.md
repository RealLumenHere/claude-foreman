# wq#4054 — verify-pass on wq#3992 (REFORGE decompiled-function-naming eval, ONE-SHOT)

PR: lumen/claude-foreman#2002, landed as `984b0840` → `e109da65` on main (squash;
diff between the two hashes is empty, confirmed with `git diff 984b0840 e109da65`).
Touches `scratch/research/reforge-oneshot.md` only (95 insertions, 0 deletions, 1
file) — no timer/unit added, matching the ticket's explicit rejection of that
shape. Author: claude-sonnet-5 · Verifier: claude-sonnet-5@high (fresh instance,
wq#4054).

## Claim under test

wq#3992 asked for a measurement-only REFORGE eval, step 1 being "VERIFY FIRST
that the dataset/substrate is actually released... if not released, stop and
record that." The deliverable claims: (1) REFORGE (arXiv:2607.07738) has no
discoverable released dataset or code across arXiv abstract/HTML/PDF,
HuggingFace, GitHub repo search, and Papers With Code; (2) per the ticket's own
stop clause, steps 2-3 (scoring rungs) never ran; (3) the ticket's candidate rung
`isis-coder-14b` does not exist in any registry, logged as ISSUE-LOG row 1362;
(4) the real local-rung registry is `hathor-llama8b`, `nephthys-qwen14b`,
`qwen36-local` (down), `local-worker`, plus `sonnet-5@high` as cloud ceiling.

## What I checked

1. **Read the diff cold** (`git show e109da65`) and the live ticket text
   (`wq show 3992`): the deliverable's quoted stop-clause and rung-candidate list
   match the ticket verbatim — nothing paraphrased in the author's favor.
2. **Re-fetched the arXiv abstract page directly** (`fleet-search read
   https://arxiv.org/abs/2607.07738`): title, authors, submission dates (v1 7 Jul
   2026, v2 24 Jul 2026) and abstract text match the report's quotes exactly; no
   `Comments:` field, no linked repo, confirming the report didn't drop a
   release signal that was actually present.
3. **Re-ran the HuggingFace datasets search** (`fleet-search read
   "https://huggingface.co/datasets?search=reforge"`): same 4 unrelated hits the
   report cites (Warcraft III Reforged voice pack, two reForge SD-WebUI/portable
   forks, `reforger_m93r`) — no RE/function-naming dataset. Matches.
4. **Adversarial lead (tried to break it)**: re-ran `fleet-search web` for
   REFORGE release/GitHub signals — general web search is no longer degraded
   (google cse answered; brave/duckduckgo/startpage are still down, same as the
   author saw, so wq#2595 is unchanged, not re-logged). One hit looked like a
   genuine contradiction: a Google CSE snippet for the paper author's homepage
   (`andreas.schmidt.novalyst.de`) read *"To appear. Code repository (GitHub).
   Preprint: arXiv:2607.07738. Abstract."* — i.e., a GitHub code repo link next
   to the REFORGE entry. I fetched the live page directly and regex-scanned the
   full 25,170-char extracted text (not just the 2KB preview) for `arxiv|github|
   repository|code`, case-insensitive: zero matches anywhere near the REFORGE
   entry, and the string `2607.07738` does not appear on the page at all. The
   snippet does not correspond to the live page's actual content — a stale/
   cross-contaminated SERP snippet, not a real release. This lead does not
   survive verification; it does not contradict the NO-GO.
5. **Verified the registry correction independently**: `grep -rln
   "isis-coder-14b" .` from the worktree root returns only the deliverable
   itself — the id is genuinely absent from `research/models.yaml`,
   `harness/agents.yaml`, `fleet-config/`. Read `research/models.yaml` lines
   314-341 directly: `hathor-llama8b`/`nephthys-qwen14b`/`local-worker` are all
   `status: LIVE-local`, `qwen36-local` is `status: down` with trust note
   "mechanical-edit only, gated. Offline-survival tier" — matches the report's
   table verbatim. Checked `harness/agents.yaml`'s `verify-pass` block:
   `primary_tuple: sonnet-5@high` — matches the "cloud ceiling" claim.
6. **Checked ISSUE-LOG row 1362** (`kb/ISSUE-LOG.md:1511`): content matches the
   report's citation (nonexistent rung name, correct root cause, correct
   resolution).
7. **Confirmed no eval artifacts exist**: no new files under `scratch/` beyond
   the one deliverable, no downloaded dataset, no LiteLLM scoring output —
   consistent with "nothing was downloaded or treated as ground truth."

## Verdict

**CONFIRM.** The stop-clause was followed to the letter, not just claimed: every
release-channel check in the report reproduces identically on a fresh,
independent fetch, the one promising contradiction I found (the homepage GitHub
snippet) evaporates on direct verification of the live page, the registry
correction is real and correctly logged, and the diff adds no eval code/timer,
matching the ticket's explicit rejection of that shape. Nothing here rises to a
contradiction under the ticket's judgment-trigger bar — no escalation to
opus-5@high.

## Non-blocking observations (not part of the verdict)

- The report's "GitHub repo search ... inconclusive (JS-rendered page, static
  extractor blind)" framing (row in its Step-1 table) remains accurate for
  `fleet-search read` against `github.com/search`; I did not find a cheaper
  fix for that specific channel, so it stays inconclusive rather than negative
  in my check too — consistent with the report's own honesty about the gap.
- Worth a one-line addendum (not required for CONFIRM) that a later re-check of
  REFORGE release status should fetch the author's homepage directly rather
  than trust a search-engine snippet in isolation, given the false-positive
  found in step 4 above.
