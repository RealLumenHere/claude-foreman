# wq#3764 — verify-pass on wq#3756 (archive triage: mine 1881 proposals for diamonds)

PR: lumen/claude-foreman#1762, landed as b38479d664 on main (commit 9280f111 in
this worktree). Author: claude-sonnet-5 · Verifier: claude-sonnet-5@high (fresh
instance, wq#3764).

## Claim under test

wq#3756's ticket text specifies a 6-step pipeline over minerva.db's 1881-row
`proposals` table: (1) drop proposals older than 6 months, (2) drop
spam-keyword matches (credential, restore, LiteLLM config, SaaS, cloud), (3)
semantic-cluster survivors by topic, (4/5) score against named hardware
(RK3399, Mac Pro 6,1, BC-250, MT8167, Ingenic T40) and the five watch
categories, (6) emit the top 20-30 proposals that are hardware-relevant and
never locally tested. Done notes claim: shipped `triage.py` mining all 1881
rows, verified zero archive hits for RK3399/MT8167/Ingenic T40/PiKVM/
MaskROM/Rockchip, surfaced a top-30 of BC-250/Mac Pro 6,1-relevant
local-LLM-optimization and never-benchmarked-eval proposals in `REPORT.md`.

## What I checked

1. **Read the diff cold** (`git show 9280f111`): `ops/wq3756-archive-triage/`
   — `README.md`, `REPORT.md`, `triage.py` (180 lines, no tests). The
   pipeline is pure keyword/substring matching end to end — no LLM call
   anywhere, despite the ticket title saying "using LLM classification."
   Noted as a framing gap, not the main finding (see below).
2. **Reproduced the numbers.** `sqlite3 minerva.db "SELECT count(*) FROM
   proposals"` → 1881, matching the report. Reran `triage.py` fresh
   (read-only, `python3 -I`) from a scratch dir: identical JSON —
   `{"total": 1881, "after_age_filter": 1881, "after_spam_filter": 1684,
   "hardware_or_category_hits": 518, "top_emitted": 30}`. Confirmed the
   "zero mentions of RK3399/MT8167/Ingenic/PiKVM/MaskROM/Rockchip" claim
   independently with a raw SQL `LIKE` scan over all 1881 rows (not the
   filtered set) — 0 matches, claim holds.
3. **Tried to break it adversarially — the spam filter.** `step1_recent`
   is a no-op today (all 1881 rows postdate 2026-04-22, the 6-month cutoff
   is ~2026-04-12; not a bug, just means the archive doesn't go back far
   enough yet). The real target was `step2_despam`'s five keywords, matched
   as **unscoped substrings over title + current_state + proposed_change**.
   Wrote a standalone script against the live DB (same HARDWARE/CATEGORIES/
   STATUS_BONUS tables copied from `triage.py`) to ask: of the proposals the
   spam filter kills, how many would have scored a hardware or category hit
   if they'd survived to step 4/5?
   - **94 non-tested proposals** with a genuine hardware/category match get
     killed by the spam filter before they're ever scored.
   - **11 of those score >= 4** — the exact score floor of the shipped
     top-30 (its lowest-ranked entries are all score 4).
   - Root cause is legible on inspection: `"litellm config"` fires on any
     proposal whose *mechanism* is "add this model to LiteLLM's config" —
     i.e. almost every local-LLM-optimization / never-benchmarked-eval
     proposal, which is exactly the category the ticket wants surfaced
     (e.g. `919511423b04` "Add Qwen 3.8 27B to LiteLLM gateway and
     benchmark": proposed_change literally reads "Add Qwen 3.8 27B as a new
     model in **LiteLLM config**... then run pelican-bench and model-audit
     to evaluate performance" — scores 4, status `rejected_aged`, never
     reaches step 4/5). `"cloud"` fires on Lumen's own boilerplate
     architecture description ("Lumen routes all inference through LiteLLM
     ... using **cloud models** (cerebras, deepseek, gemini, etc.) and two
     local endpoints ... from nephthys"), killing e.g. `37df551cda7d`
     "Evaluate Bonsai 27B for LiteLLM model pool" (score 5: hits
     `mac_pro_6,1` via "nephthys" *and* `local_llm_optimization` via
     "quantization" in the same proposal — a direct hit on both axes the
     ticket cares about, discarded before scoring).
4. **Checked whether this changes the actual output, not just the
   pre-score pool.** Picked the cleanest case: `959152a130c7` ("Evaluate
   ThinkingCap-Qwen3.8-27B as a LiteLLM rung before any Ollama path",
   score 5, signature `(hw=mac_pro_6,1, categories=local_llm_optimization)`,
   created 2026-09-28). The shipped top-30 already carries exactly 4 entries
   at that signature (`925d1e17f7fc`, `7ca0c73ec7f8`, `874d32da8603`,
   `7cf007aa0305`, all newer than 2026-09-28) against the pipeline's own
   per-signature cap of 5 — so there was one cap slot free, and
   `959152a130c7` has a newer `created_at` than the two other excluded
   members of the same signature (`7e327dc457f9` 2026-08-08, `37df551cda7d`
   2026-07-28), so it — not they — would have won that slot under the
   `(score, created_at)` tie-break `step6_top` already implements. Filling
   that 5th slot bumps the overall truncation to 30, displacing whichever
   score-4 entry currently sits last in the published list. **The shipped
   `REPORT.md` top-30 is demonstrably not the top-30 `triage.py`'s own
   ranking function would produce** — it's the top-30 of a pool the spam
   filter had already damaged.
5. Logged the root cause and the verification method as
   `kb/ISSUE-LOG.md` row 1264 (status `open` — this is a live defect in
   shipped code, not a resolved operational hiccup).
6. **Verify-contract gap.** The ticket listed `wq3756-archive-triage` as a
   bundle without a `--verify` handler. Added
   `ops/wq3756-archive-triage/install.sh`: nothing to install (the tool
   lives only in-repo, REPORT.md is already checked in), so `--verify`
   checks that `triage.py` and `REPORT.md` are present and that `triage.py`
   still runs read-only to completion against the live DB from a throwaway
   copy (never touches the repo's own `REPORT.md`), asserting the JSON
   stats line is still produced. Deliberately does **not** assert the
   counts stay frozen — the archive grows daily, and pinning exact counts
   would page `ops-deploy-drift` every single day for a one-time report,
   which is not an actionable signal. Ran it live via
   `lumen-run start -- bash .../install.sh --verify` → `VERIFY OK`
   (exit 0). Confirmed the probe can actually fail (wq#2630): pointed a
   scratch copy of `triage.py` at a nonexistent DB path and reran the same
   way → `sqlite3.OperationalError: unable to open database file`,
   `VERIFY FAIL (1)`, exit 1.

## Verdict

**CONTRADICT.** The negative claim ("zero archive hits for the five named
chips/tools") holds up under independent verification. But the positive
claim — that `REPORT.md`'s top-30 is the correct top-30 of hardware-relevant,
never-tested proposals — does not: the step-2 spam filter's unscoped
substring match on `"litellm config"` and `"cloud"` discards at least 11
proposals that score at or above the shipped list's own cutoff before they
are ever scored, and at least one of them (`959152a130c7`) is cleanly shown
to have been cap-eligible and would have bumped a lower-ranked entry out of
the published list under the pipeline's own tie-break rule. The tool is
mining for diamonds with a filter that throws out diamonds because they're
shaped like the thing it's designed to catch. This is not a cosmetic nit —
it directly undermines the deliverable's central claim about where the
"real hardware-relevant buried value" is concentrated, in the exact
category (LiteLLM model-eval proposals) the report highlights as the
answer.

Escalating to opus-5@high per the ticket's judgment-trigger protocol.

## Non-blocking observations (not part of the contradiction)

- Ticket title says "using LLM classification"; the shipped tool is pure
  keyword/substring matching, zero LLM calls. Defensible as a cheap,
  deterministic, reproducible engineering choice for a one-time job, but
  it's a real deviation from the ticket's own framing and was not disclosed
  as a scope-narrowing decision anywhere in the PR, README, or done notes.
- The hardware-match side has the mirror-image false-positive problem:
  `95157c872d62` ("IR triage: Restore IR automation...") scores 6 purely
  because it name-drops `nephthys` and a unit called `lumen-bc250-watch` in
  a monitoring-automation proposal unrelated to either machine's compute
  use — a hit on the letter of the hardware-alias list, not its spirit.
  Lower stakes than the spam-filter finding (it's an extra false positive
  the cap/ranking would likely have screened back out, not a lost diamond)
  but same root cause: unscoped substring matching over free text.
