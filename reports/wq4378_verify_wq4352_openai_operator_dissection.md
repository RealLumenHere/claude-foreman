# wq#4378 — verify wq#4352 (PR#2256, `research/WQ4352_OPENAI_OPERATOR_DISSECTION.md`)

**Verdict: CONFIRM.** Documentation-only research ticket (no code, no executable
probe shipped or implied) — 220 lines added, one file, merged to `main` as `baf5b5e8`.
The verify pass targets the two ways a research doc can be wrong: (1) its quotes/code
excerpts from the two primary sources it cites are fabricated or distorted, and (2) its
cross-references into this repo (other WQ docs, design doc, `limpet/model_router.py`)
misquote or mislocate what they point at. Neither held up as a break; one minor
internal framing inconsistency is noted below but doesn't affect the doc's factual
content or its design recommendations.

## What shipped (re-read cold)

`git show baf5b5e8` on `feat/wq4352-limpet-research-dissect-openai-operator--a2`
(merged to `main` as PR#2256): one new file, `research/WQ4352_OPENAI_OPERATOR_DISSECTION.md`,
220 lines, covering the ticket's four numbered points (screen-reading loop, action
verification, error recovery, multi-step planning) plus a 6-item LIMPET VALUE section.
Matches the ticket's `notes` field exactly (forced-screenshot verification, split
error-recovery policy, no upfront planner, 6 recommendations).

## Primary sources re-fetched independently and diffed against the doc's quotes

Re-ran the same `fleet-search read` tool the doc claims to have used, against both
cited URLs, fresh (not relying on the author's extracted text):

- `https://learn.microsoft.com/en-us/azure/ai-foundry/openai/how-to/computer-use` — full
  reference script retrieved (94.6KB HTML extraction). Checked line-by-line against every
  code claim in §1-§3:
  - `actions = computer_call.actions  # actions is now a batched array` — confirmed verbatim,
    matches doc's "BATCHED" claim.
  - Screenshot taken once after the whole `for action in actions:` loop, not per action —
    confirmed in `process_model_response`.
  - `DISPLAY_WIDTH=1440, DISPLAY_HEIGHT=900` and `validate_coordinates()` clamping
    `max(0, min(x, DISPLAY_WIDTH))` — confirmed verbatim.
  - "We'll constantly do this in a loop after each step allowing the model to see if the
    command it tried to execute was successful or not... but for simplicity we will force
    a screenshot to be taken for each iteration." — confirmed verbatim, exact quote.
  - `take_screenshot()`'s `last_successful_screenshot` cache-on-failure fallback — confirmed
    verbatim.
  - `handle_action()`'s `except TimeoutError: pass` after `wait_for_load_state`, and the
    outer `except Exception as e: ...traceback.print_exc()` around the action-execution
    block — confirmed verbatim, matches the doc's three-layer error-recovery claim exactly.
  - API-call failure (`client.responses.create`) caught and `break`s the loop, vs.
    action failure which only logs and continues — confirmed verbatim; the doc's claim that
    these are two distinct policies is accurate.
  - New-tab auto-switch (`len(all_pages) > 1`, newest non-blank page) — confirmed verbatim.
  - `max_iterations` cap / "Reached maximum number of iterations. Stopping." — confirmed
    verbatim.
  - The `reasoning` → `computer_call` sample ("Exploring 'File' menu option.") the doc cites
    for §4's planning claim — confirmed verbatim, found under the safety-checks example in
    the source.
- `https://openai.com/index/operator-system-card/` — full system card retrieved (699KB).
  Checked every quote/number in §0-§3 and the LIMPET VALUE section:
  - "[Operator] interprets screenshots and interacts with graphical user interfaces
    (GUIs)... just as people do." — confirmed verbatim (doc's elision only drops a
    parenthetical, doesn't change meaning).
  - "Operator is a research preview of our Computer-Using Agent (CUA) model, which
    combines GPT-4o's vision capabilities with advanced reasoning through reinforcement
    learning" — confirmed, backs the doc's "GPT-4o's vision backbone plus RL" framing.
  - "reasoning, error correction, and the ability to adapt to unexpected events" —
    confirmed verbatim, exact RL-training quote used in §3.
  - 90% risk reduction from confirmations, 607-task eval set, 92% confirmation recall —
    all three numbers confirmed verbatim in the system card's model-mistakes section.
  - Watch-mode description ("automatically pausing execution when the user becomes
    inactive or navigates away from the page... resume... once they return") — confirmed,
    matches the doc's LIMPET VALUE item 6 paraphrase.

No fabricated quote, no distorted code excerpt, no invented number found in either
source.

## In-repo cross-references — checked against the files they point at

- `research/WQ4360_VISION_AGENT_DISSECTION.md` §3 — doc claims WQ4360 "cited this
  action-schema family from the SDK types" but didn't cover loop/recovery mechanics.
  Confirmed: WQ4360 §3 covers the typed action union and the `pending_safety_checks`
  gate, not the exception-handling/retry policy wq4352 adds. No overlap, no gap claimed
  falsely.
- `research/WQ3922_VISION_HDMI_CAPTURE.md` — "frame-diff gate" cited in LIMPET VALUE
  item 1 — confirmed present (lines 157/166/258).
- `research/WQ4337_ANTHROPIC_AGENT_DISSECTION.md` — "forced visual-self-verification"
  and "two-model gate" cited in LIMPET VALUE items 1 and 6 — both terms found verbatim
  (lines 206, 219).
- `design/wq3806_limpet_architecture.md` §6 "Agent core" — cited in LIMPET VALUE item 4 —
  confirmed §6 is titled exactly "Agent core".
- `limpet/model_router.py` — cited in the closing "Builds on/unblocks" line — file exists.

All five cross-referenced files/sections exist and say what the doc claims they say.

## One framing inconsistency found (not a break)

§1's header reads "Loop shape, verbatim from the Azure docs (**identical mechanics to
what shipped as Operator**)," but the paragraph immediately below it says actions being
batched per turn is "a recent change from one-action-per-turn" in the Azure docs. Those
two statements are in tension: if batching is a *recent* change to the loop, the
mechanics are not strictly identical to what shipped under the original Operator
product (Jan 2025, pre-batching). This is a header overclaim, not a wrong fact — the
body's own caveat already corrects it, and none of the six LIMPET VALUE recommendations
depend on whether batching existed at Operator's original launch. Not escalation-worthy.

## Scope check

Ticket (wq#4352) asked for exactly four numbered points plus LIMPET VALUE; the shipped
doc has sections 1-4 covering screen-reading, verification, error recovery, and planning,
plus a 6-item LIMPET VALUE section. Nothing in scope skipped, nothing out-of-scope added
(diff is the one new file, no code touched).

## Verification performed

1. Read the full 220-line doc cold (`git show baf5b5e8:research/WQ4352_OPENAI_OPERATOR_DISSECTION.md`).
2. Re-fetched both primary sources independently via `fleet-search read` (not reusing the
   author's cached extraction) and diffed every quote, code excerpt, and number against
   the fresh fetch — zero mismatches.
3. Checked all five in-repo cross-references against actual file content — all accurate.
4. Confirmed PR#2256's commit (`baf5b5e8`) is on `main` and matches the ticket's done notes.

No contradiction found — no escalation to opus-5@high required.
