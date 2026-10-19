# wq#4352 — Dissection: OpenAI Operator / Computer-Using Agent (CUA)

**Date:** 2026-10-19 · **Lane:** LIMPET research · **Output:** error-recovery and
verification design for the Limpet HDMI-capture agent loop.

## 0. Evidence posture (read first)

Prior attempt (classification: KILLED) burned turns trying to extract the Operator
system card as a **PDF** via curl/WebFetch — that path has no text-extraction tool wired
up and returns binary. This run used `/home/lumen/bin/fleet-search read <url>` against
the **HTML** sources instead (trafilatura-based extraction, same tool WQ4347/WQ4360 used
successfully), which worked on every source below with no re-reads. `fleet-search web`
again hit the degraded SearXNG wall (brave/duckduckgo/google-cse/startpage all
suspended/CAPTCHA — wq#2595 OPEN, not something this ticket invented) and fell back to
keyed DeepSeek Search, same as WQ4360/WQ4347.

Primary sources actually fetched and read this run:
- **OpenAI's own Operator System Card** — `openai.com/index/operator-system-card/`
  (HTML, fetched in full, ~32KB extracted text). This is OpenAI's primary safety/behavior
  writeup for the **product** ("Operator"), Jan 2025.
- **Microsoft Azure AI Foundry "Computer Use" how-to docs**
  (`learn.microsoft.com/en-us/azure/ai-foundry/openai/how-to/computer-use`) — this
  documents the underlying **API** (`tools=[{"type": "computer"}]` on the Responses API,
  model `gpt-5.4`, the direct descendant of what shipped as `computer-use-preview` under
  Operator) with a full reference Playwright integration script. WQ4360 §3 already cited
  this action-schema family from the SDK types; this run pulled the **narrative loop code**
  behind it — the actual request/response cycle, safety-check wire format, and the
  reference error handling, which is the part this ticket needs and WQ4360 didn't dig into.

Everything below is sourced from these two documents; anything synthesized across them
into a design recommendation is marked as such.

---

## 1. Screen reading loop — capture, interpret, act

**No OCR, no DOM parsing in the base loop — pure screenshot + vision.** The system card:
"[Operator] interprets screenshots and interacts with graphical user interfaces
(GUIs)... just as people do." The model is GPT-4o's vision backbone plus RL on top; there
is no separate OCR pass or accessibility-tree read in the documented loop — the model
reads the rendered pixels directly, the same monolithic-vision approach WQ4360 contrasted
UI-TARS against Agent S2's split-out grounding specialist. Operator does not route
grounding to a specialist model; it is trained end-to-end to localize UI elements itself.

**Loop shape, verbatim from the Azure docs (identical mechanics to what shipped as
Operator):**
```
1. client.responses.create(model=..., tools=[{"type": "computer"}], input=[...])
2. response.output contains, per turn:
     - a "reasoning" item (chain-of-thought summary, logged/inspectable)
     - a "computer_call" item with an actions[] array (BATCHED — one turn can
       emit multiple actions, not strictly one-action-per-screenshot)
3. Application code executes actions[] in order against the real browser/OS.
4. Application takes ONE screenshot after the whole batch (not one per action).
5. Screenshot is POSTed back as a computer_call_output with
   previous_response_id chaining (not a replayed full history each turn).
6. Repeat until the model's output has no computer_call item, or a caller-set
   max_iterations is hit.
```
This is a **discrete snapshot loop, not a stream** — matches WQ4360/WQ4337/WQ3922's
shared finding. The new detail this source adds: **actions can be batched per turn**
(the Azure docs explicitly flag `actions` as "now a batched array" — a recent change from
one-action-per-turn), and the screenshot is taken **once per batch**, not once per
individual action — the model trades a slightly staler mid-batch view for fewer
round-trips. Screenshot resolution is fixed and caller-declared
(`DISPLAY_WIDTH=1440, DISPLAY_HEIGHT=900` in the reference script, matching the actual
browser viewport) — the model's coordinates are native to that declared space, and the
reference `validate_coordinates()` clamps `(x, y)` into `[0, DISPLAY_WIDTH] x [0,
DISPLAY_HEIGHT]` before any click executes.

## 2. Action verification — how it checks a click worked

**There is no separate verification step, before/after diff, or DOM-state check in the
documented loop.** Verification is **delegated entirely to the model, via the mandatory
post-action screenshot**: "We'll constantly do this in a loop after each step allowing
the model to see if the command it tried to execute was successful or not, which then
allows it to adjust based on the contents of the screenshot" (Azure docs, verbatim). The
docs explicitly call out the alternative they rejected — letting the model *decide* when
to take a screenshot (there is a `screenshot` action type in the vocabulary for exactly
that) — "but for simplicity we will force a screenshot to be taken for each iteration."
So Operator's reference integration **forces** a screenshot every turn rather than
trusting the model to ask for one; the model-initiated `screenshot` action exists as a
fallback/explicit-recheck primitive, not the primary verification path.

**Screenshot-capture failure has its own fallback, independent of action verification**:
`take_screenshot()` caches `last_successful_screenshot` globally and returns the cached
frame if the live capture throws — so a transient capture glitch degrades to a stale
frame rather than crashing the turn or blocking on retry.

## 3. Error recovery — click fails / page changes unexpectedly

Three distinct layers, each handling a different failure class:

- **Model-level (trained-in, not code):** per the system card, RL training specifically
  targets "reasoning, error correction, and the ability to adapt to unexpected events" —
  i.e. recovering from a failed click or unexpected page state is a **learned behavior**,
  driven by the model seeing the post-action screenshot and re-planning from there. There
  is no hardcoded "if click didn't land, retry at offset" logic; the forced screenshot
  (§2) is what makes this possible — the model literally cannot know its action failed
  without it.
- **Transport/action-execution level (code, in the reference script):**
  `handle_action()` wraps a `page.wait_for_load_state(...)` after a click in
  `except TimeoutError: pass` — a load-state timeout is swallowed, not retried, and the
  loop proceeds to screenshot + re-ask-the-model regardless. The outer
  `process_model_response` loop wraps the **entire action-execution block** in a bare
  `except Exception as e: print(...); traceback.print_exc()` — any action-handling
  exception is logged and swallowed, and the loop still takes a screenshot and continues.
  Net effect: **the code never retries an action itself; every failure is converted into
  "take a screenshot of whatever state resulted and hand it back to the model."**
- **Loop/session level (hard caps, no retry on these):**
  - `max_iterations` (passed into `process_model_response`, default `ITERATIONS`) is a
    hard cap — "Reached maximum number of iterations. Stopping." No backoff, no retry
    budget distinct from the iteration budget itself.
  - A failure in the **API call** (`client.responses.create(...)`) itself — as opposed to
    a failure executing an action — is caught, logged with a traceback, and `break`s the
    loop outright. Action failures degrade gracefully (continue); API failures terminate
    the session. This is the one place the reference code treats "the model is
    unreachable" as categorically worse than "the model's last action didn't work."
  - A framework-level (not model-driven) recovery: if an action opens a new browser tab,
    the code detects `len(all_pages) > 1` and auto-switches `page` to the newest
    non-blank tab — this is infrastructure self-healing for a known side effect (links
    opening `target=_blank`), not something the model has to notice and fix itself.

**Product-level error containment (system card, distinct from the loop code above):**
confirmation gating and "watch mode" exist specifically to cap the *blast radius* of a
recovery failure, not to recover from it: human-reviewed confirmation before
state-changing actions (purchases, emails, deletions) catches ~90% of the impact of
model mistakes per OpenAI's own eval (607-task set, 92% confirmation recall); "watch
mode" auto-pauses execution on high-impact sites (e.g. email) when the user goes
inactive or navigates away, resuming only when the user returns. Both are "stop and wait
for a human" fallbacks for action classes where "let the model notice and self-correct"
(§3 above) is judged too risky.

## 4. Multi-step task planning — decomposing a task into screen interactions

**There is no separate upfront planner producing a plan artifact.** Planning is
**interleaved, per-turn, inside the same reasoning item that precedes each action** — the
sample trace in the Azure docs shows a `reasoning` item ("Exploring 'File' menu option")
immediately before the `computer_call` it justifies. This confirms WQ4360 §2's UI-TARS
finding (thought-before-action) is also how OpenAI's own reference loop structures
planning: the "plan" is the running sequence of per-turn reasoning summaries, not a
document decomposed before execution starts. The system card's framing reinforces this at
the product level: a user **task** ("buy a pair of shoes") is risk-classified up front by
policy (search → checkout → purchase, as an enumerated **action** sequence used for
*safety* categorization, not execution planning), but the actual turn-by-turn navigation
is emergent — the model re-plans from the latest screenshot every iteration, consistent
with §3's "no hardcoded retry, model replans from what it sees."

---

## LIMPET VALUE — error-recovery and verification design for the HDMI-capture loop

This folds into the vision pipeline WQ4360 already established (capture → planner →
grounding-router → typed-action → executor+gate) and sharpens the **verification and
error-recovery layer** specifically, which is this ticket's unique contribution — WQ4360
covered Operator's action schema and safety gate but not its recovery mechanics.

1. **Verification is "take a screenshot and hand it to the model" — not a diff
   algorithm.** Operator's own reference code does not diff before/after screenshots or
   check DOM state; it forces a fresh capture every turn and lets the model's vision
   judge success. For Limpet this validates the design already implied by wq#3922's
   frame-diff gate (skip *inference* on an unchanged frame) plus WQ4337's forced
   visual-self-verification: **the gate decides whether to re-infer, the model decides
   whether the action succeeded** — don't build a separate pixel-diff "did it work"
   classifier when the planner's next-turn read of the capture already does that job,
   provided (per §2) the capture after every HID action is **forced**, never
   model-optional, exactly as the Azure reference rejected letting the model opt out of
   its own screenshot.

2. **Two different exception-handling policies for two different failure classes —
   action failures degrade, transport failures terminate.** Operator's loop: an action
   that throws (click didn't land, element moved) is logged and swallowed, and the loop
   *continues* with a fresh capture; a failure to reach the model API at all `break`s the
   session. Limpet's executor should mirror this split: a HID/serial action that errors
   (stuck key, dropped USB write) logs and continues to the next capture+reasoning turn
   — the planner gets to see the resulting device state and react, same as Operator's
   model does — but a capture-pipeline failure (no HDMI signal, V4L2 device gone) should
   terminate the run rather than feed the planner a stale or synthetic frame as if it
   were real device state.

3. **Cache the last-good frame on a capture glitch; never block the loop on a retry.**
   Direct lift of `take_screenshot()`'s `last_successful_screenshot` fallback: a
   transient HDMI/V4L2 frame-grab failure should return the last successfully captured
   frame rather than stall the agent loop waiting for the capture device to recover —
   matches wq#3922's capture-pipeline layering (capture is a service the agent loop
   consumes, not something the agent loop blocks inside).

4. **Hard iteration cap is a session-level circuit breaker, independent of per-action
   retry logic.** Operator's `max_iterations` stops a runaway session regardless of
   whether individual actions are "succeeding" — directly reusable for a Limpet run that
   gets stuck relanding on the same boot-menu screen. Should live in the agent daemon's
   outer loop (`design/wq3806_limpet_architecture.md` §6 Agent core), not inside the
   executor.

5. **No hardcoded action retry — error correction is the planner's job, enabled by
   forcing a capture, not a code-level retry loop.** This is the one place to resist the
   instinct to add "if click missed, retry at nudged coordinates" logic to Limpet's
   executor: Operator's own reference implementation deliberately doesn't do this either
   — it relies on the RL-trained model noticing a mis-click in the next screenshot and
   re-planning. For Limpet, that means the grounding router (WQ4360 §LIMPET VALUE item 1)
   and planner need the *failed* action's resulting frame fed straight back in, not
   suppressed behind an executor-level retry — a code-level blind retry would hide the
   exact signal (the post-click frame) the planner needs to replan correctly.

6. **Risk-tiered human gate is a complement to, not a substitute for, the above —
   already covered by WQ4337's two-model gate**, but this source adds the concrete
   mechanism worth copying: "watch mode" pauses execution on **inactivity/navigation
   away** from a high-risk target, not on a fixed per-action cadence. For a hardware
   target this maps to pausing before a destructive op (reflash, factory reset) if no
   operator has acknowledged the session within a timeout — reuse the judge/gate's
   existing device-state evidence gathering (WQ4337 §6) as the resume condition, not a
   bare timer.

**Builds on / unblocks:** extends `research/WQ4360_VISION_AGENT_DISSECTION.md` §3's
Operator action-schema findings with the loop mechanics and recovery policy it didn't
cover; reuses `research/WQ3922_VISION_HDMI_CAPTURE.md`'s frame-diff gate and
`research/WQ4337_ANTHROPIC_AGENT_DISSECTION.md`'s forced-verification and two-model-gate
patterns rather than duplicating them; informs the still-design-only agent core in
`design/wq3806_limpet_architecture.md` §6 and the capability routing in
`limpet/model_router.py`.
