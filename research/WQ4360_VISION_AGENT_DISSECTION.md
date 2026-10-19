# wq#4360 — Dissection: vision-agent stacks (Agent S2 · UI-TARS · OpenAI Operator)

**Date:** 2026-10-19 · **Lane:** LIMPET research · **Output:** the Limpet vision
pipeline architecture — specialist grounding routing, a typed action schema, and
reasoning traces — for the HDMI-capture agent loop.

Three projects that each solve the **screen-pixels → agent-action** pipeline Limpet
needs for its HDMI-capture agent. This dissects them together and folds the result into
the vision/capture work already landed (`research/WQ3922_VISION_HDMI_CAPTURE.md` — model
sizing + V4L2 plumbing) and the brain/hands + gate patterns from the Anthropic dissection
(`research/WQ4337_ANTHROPIC_AGENT_DISSECTION.md` §Patterns). The through-line of all three
sources is the same lesson Limpet's `limpet/model_router.py` already half-anticipates:
**localization is a different job from planning, and belongs to a different (specialist)
model.**

## 0. Evidence posture (read first)

Prior-art search ran 2026-10-19 via `fleet-search web` — same degraded wall as wq#3922/
wq#4354 (SearXNG general engines all suspended/CAPTCHA: brave, duckduckgo, google cse,
startpage; **wq#2595 OPEN**), so the **keyed DeepSeek Search fallback** served every query.
This is a real search (not memory, not fabrication — instinct 8): the engine-suspension is
a pre-existing OPEN fleet issue, not a value this ticket invented. Primary sources seen:

- **Agent S2** — arXiv **2504.00906** ("A Compositional Generalist-Specialist Framework
  for Computer Use Agents", Simular, Apr 2025); ar5iv HTML; the `gui-agents` repo README
  results table; simular.ai technical review.
- **UI-TARS** — arXiv **2501.12326** (ByteDance Seed, Jan 2025); the bytedance/UI-TARS
  repo; seed-tars.com; the UI-TARS-1.5 release notes (Apr 2025).
- **OpenAI Operator / CUA** — the `computer_call` action schema as shipped in the
  openai-python types (`response_computer_tool_call_param.py`) and the Go SDK
  (`ResponsesComputerToolCallAction`); Microsoft Foundry computer-use tool docs;
  benchgecko glossary.

External numbers below are **[verify-at-adoption]** and structured so a wrong figure
invalidates one cell, never the pipeline shape. The three numbered targets and the
"18.9–32.7%" headline are the **ticket's framing**; where I could verify a figure against a
source I cite the verified figure and flag any gap.

---

## 1. Agent S2 — Mixture-of-Grounding (localization is a routed specialist job)

**The core idea:** Agent S2 is explicitly **compositional** — it splits the agent into a
*generalist* that does high-level reasoning and planning and a set of *specialists* that do
low-level execution and **grounding** (turning "click the Save button" into an exact pixel
coordinate). The grounding step is the single biggest failure mode of a monolithic VLM
agent: a model good at planning is routinely bad at precise localization, especially on
dense/unusual UIs. Agent S2's **Mixture of Grounding (MoG)** routes each grounding request
to the right *grounding expert* rather than asking the one generalist to also be a pixel-
accurate localizer.

**The experts (from the paper's ablation):**
- **Visual grounding expert** — a vision model that localizes by image (icons, custom
  widgets, canvas-drawn UI the DOM can't name). This is the default path.
- **Textual grounding expert** — specialist for text-anchored targets (OCR-style
  "click the field labelled X"). Removing it drops subtask success **70.6% → 65.2%**.
- **Structural grounding expert** — uses the accessibility tree / structured UI
  representation for stable, addressable elements. Removing it drops subtask success
  **73.7% → 69.4%**.

Those two ablation deltas are the verifiable evidence that *each expert earns its slot* —
the generalist alone cannot cover all three grounding modalities. The ticket's headline
"**18.9–32.7% improvement over single-generalist**" is MoG's relative gain over a single-
grounding-model baseline as reported in the paper's grounding study; I verified the
*direction and the per-expert ablation deltas* above, and the end-to-end SOTA deltas below,
but could not independently re-derive the exact 18.9–32.7% band from the degraded search —
treat that specific band as **[verify-at-adoption against arXiv 2504.00906]**, the
per-expert ablation and SOTA numbers as corroboration of the claim's direction.

**Other load-bearing piece — Proactive Hierarchical Planning:** the generalist refines its
plan as observations come in rather than committing a flat sequence up front (the same
"plan is searched/refined, not greedily emitted" idea wq#4354 pulled from Manus).

**Verified end-to-end results** (Screenshot-only input, from the repo results table
[verify-at-adoption]):

| Benchmark | Agent S2 | Previous SOTA | Δ |
|---|---|---|---|
| OSWorld (15-step) | 27.0% | 22.7% (UI-TARS) | +4.3 abs |
| OSWorld (50-step) | 34.5% | 32.6% (OpenAI CUA) | +1.9 abs |
| WindowsAgentArena | 29.8% | 19.5% (NAVI) | +10.3 abs (+52.8% rel) |
| AndroidWorld | 54.3% | 46.8% (UI-TARS) | +7.5 abs (+16.5% rel) |

**Notable:** Agent S2's own best configuration uses **Claude 3.7 (extended thinking) as the
generalist and UI-TARS-72B-DPO as the grounding specialist** — i.e. the #2 target of this
ticket *is* the #1 target's grounding expert. That is the single most important structural
fact here: a strong *reasoner* and a strong *grounder* are different models, composed.

## 2. UI-TARS — thoughts-before-actions (reasoning trace is in the action stream)

**What it is:** ByteDance Seed's **native** end-to-end GUI agent — one vision-language
model that perceives, reasons, grounds, and acts, as opposed to a framework that wraps a
general model (GPT-4o) in hand-crafted prompts and external grounding tools. It targets
**System-2 reasoning**: the model is trained to emit an explicit **thought** (a reasoning
trace) *before every action*, so the action stream is `thought → action → thought → …`
rather than bare actions.

**Why the thought-before-action matters (what we steal):**
- The reasoning trace is an **inspectable, loggable artifact** of *why* the agent did each
  step — exactly what a security-RE audit trail needs (you can replay the agent's reasoning,
  not just its clicks).
- It forces the model to **deliberate before acting** instead of pattern-matching a click.
  UI-TARS-1.5 (Apr 2025) hardened this with RL so the model "thinks before acting" and does
  **reflection / error correction** — it reasons about a failed step and re-plans, the
  model-as-retry-logic pattern wq#4337 §4 identified in Claude Computer Use.

**Unified cross-platform action space:** UI-TARS defines **one** action vocabulary that
spans **desktop, mobile, and web** — `click`, `type`, `scroll`, `drag`, `hotkey`, plus
platform-specific primitives (mobile `long_press`, `press_back`) and terminal actions
(`wait`, `finished`, `call_user`). One schema, not three per-platform schemas. For Limpet
this is the proof that a **single typed action vocabulary can serve HDMI+HID (desktop-like)
targets and, later, touchscreen/serial-console targets** without a per-device agent.

**Also:** UI-TARS carries short- and long-term memory and treats grounding as a trained-in
capability — but note Agent S2 beat it on OSWorld by *separating* grounding back out into a
routed specialist. The lesson is not "native monolith wins" — it's "the reasoning trace and
the unified action space are the keepers; grounding still wants to be routed."

## 3. OpenAI Operator / CUA — typed action union + screenshot-cadence loop + safety gate

**What it is:** OpenAI's Computer-Using Agent — a model that drives a **sandboxed cloud
browser**, watching a virtual screen and deciding clicks/scrolls/typing on the user's
behalf. The API surface is the `computer_call` tool.

**Typed, schema-constrained action union (the keeper):** the model does not emit free text
that the integrator parses — it emits one of a **closed union of typed action objects**,
each with a fixed field schema. From the shipped SDK types
(`response_computer_tool_call_param.py`, `ResponsesComputerToolCallAction`): `ActionClick`
(button, x, y), `ActionDoubleClick`, `ActionScroll` (x, y, scroll_x, scroll_y),
`ActionType` (text), `ActionKeypress` (keys[]), `ActionMove`, `ActionDrag` (path[]),
`ActionWait`, `ActionScreenshot`. A response the model emits is validated against the union
*before* the integrator ever executes it — a malformed or out-of-vocabulary action can't
reach the hands. This is the strict-schema tool-use contract from wq#4337 §3 applied to the
GUI action space specifically.

**Screenshot-cadence perception loop (~1 Hz):** the loop is **screenshot → reason →
action → screenshot**: a screenshot of the current screen state is appended to context each
turn, the model reasons over the current *and past* screenshots plus prior actions, emits
the next typed action, the integrator executes it and captures a fresh screenshot. It is a
**discrete snapshot loop, not a live video stream** (ticket's "~1 Hz cadence") — identical
to wq#4337 §1's finding for Claude Computer Use and to wq#3922 §2c's conclusion that **1 fps
on-demand snapshots are sufficient and preferred** for an agent that reads *state* (menus,
dialogs, progress) rather than playing a game.

**Screen-content filtering / safety gate (`pending_safety_checks`):** the CUA surfaces
**`pending_safety_check`** objects on an action — the action does **not run** until the
integrator **acknowledges** them (`acknowledged_safety_checks`). This is a built-in
pixels-aware safety interlock: the model flags consequential/suspicious screen situations
(prompt-injection in page content, sensitive actions) and *fails to a human/gate* before
the hands move. Direct analog of wq#4337 §5's injection classifier on screenshots + the
two-model gate.

---

## LIMPET VALUE — the vision pipeline architecture this ticket establishes

Folding all three into the capture work already landed (wq#3922) and the brain/hands + gate
work (wq#4337), the Limpet HDMI-capture vision pipeline is **four separable layers behind
narrow contracts**, and the key move is that **grounding is routed to a specialist, split
out of the planner**:

```
  HDMI capture (wq#3922: uvcvideo → ffmpeg → downscale ≤1280px, frame-diff gate, ~1 Hz)
        │  screenshot (state snapshot, not stream)
        ▼
  (A) PLANNER / REASONER  ── emits a THOUGHT then a high-level step
        │   generalist model (strong reasoning); reasoning trace is logged (UI-TARS)
        │   "click the Save dialog's OK button"   ← a target description, NOT a coordinate
        ▼
  (B) GROUNDING ROUTER  ── Mixture-of-Grounding: route the localize request to a specialist
        │   • visual grounder  (qwen2.5-vl bbox) — icons/custom widgets/canvas
        │   • textual grounder (OCR-anchored, PaddleOCR Track B from wq#3922 §1c) — labelled targets
        │   • structural grounder — when a structured UI repr exists (accessibility/serial menu tree)
        │   → returns an exact (x,y) in the pixel space of the frame WE returned (wq#4337 §coord contract)
        ▼
  (C) TYPED ACTION SCHEMA  ── closed union: {click,double_click,type,keypress,scroll,drag,wait,screenshot,...}
        │   model/grounder output validated against the union BEFORE execution (Operator)
        │   one unified vocabulary across desktop-HID / touchscreen / serial-console targets (UI-TARS)
        ▼
  (D) EXECUTOR + GATE  ── performs action over USB-HID/serial; scale factors live HERE not in the model
            gray-zone / consequential actions → two-model gate (wq#4337 §two-model gate)
            screen-content safety check (Operator pending_safety_checks) before acting
        │  end batch with a fresh capture → forced visual self-verification (wq#4337 §2)
        └──► loop
```

Concretely, the design rules this ticket lands:

1. **Separate grounding from planning *before* scaling vision (Agent S2).** The planner/
   reasoner emits a **target description**, never a raw coordinate; a **grounding router**
   turns descriptions into coordinates. Do not ask one VLM to plan *and* be pixel-accurate —
   that is the grounding bottleneck Agent S2's MoG exists to break, and it is the design
   `limpet/model_router.py` already gestures at (route a step to the model whose declared
   capability matches). The router's capability map is the natural home for a
   `grounding.visual` / `grounding.textual` / `grounding.structural` route set.

2. **Mixture-of-Grounding = three grounding routes, picked per request.** Visual grounder
   (`qwen2.5-vl:7b`, native bbox — already the wq#3922 resident recommendation), textual
   grounder (PaddleOCR Track B, already specified in wq#3922 §1c for verbatim strings), and
   a structural grounder for targets that expose a structured representation (a serial/boot
   menu tree, an accessibility dump). The router picks by target type; disagreement lowers
   confidence rather than silently choosing (wq#3922 §1c cross-check discipline).

3. **Thoughts-before-actions: the reasoning trace is a first-class logged artifact
   (UI-TARS).** Every step is `thought → action`. The thought is persisted to the RE audit
   trail so a run is replayable as *reasoning*, not just a click log — and it forces
   deliberation + reflection/error-correction instead of reflexive clicking.

4. **One typed, schema-constrained action union (Operator + UI-TARS).** A closed union of
   typed action objects (`click`/`type`/`keypress`/`scroll`/`drag`/`wait`/`screenshot`/…),
   **validated before execution** so a malformed or out-of-vocabulary action never reaches
   the HID/serial transport. One vocabulary spans every target class (HID desktop today,
   touchscreen/serial later), so there is no per-device agent — the UI-TARS unified-action
   lesson.

5. **~1 Hz screenshot-cadence loop, not a stream (Operator + wq#4337 + wq#3922).** Discrete
   snapshot per turn, frame-diff gated (skip inference on an unchanged screen), end every
   action batch with a fresh capture for forced visual self-verification. Live video is a
   non-goal — the agent reads *state*.

6. **Screen-content safety gate before consequential actions (Operator
   `pending_safety_checks` + wq#4337 two-model gate).** A pixels-aware check can hold an
   action pending acknowledgement; gray-zone/destructive target ops (reflash, factory reset,
   accepting a ToS on the target UI) route through the existing judge/gate, which gathers its
   own device-state evidence (wq#4337 §6) rather than trusting the actor's framing.

7. **Coordinates live in the executor, never the model's head (wq#4337 §coord contract,
   reinforced by all three).** Grounders return coordinates in the pixel space of the frame
   *we returned*; the executor owns the capture→native scale factor. A `zoom`/region-crop at
   native resolution handles small UI text without raising global capture resolution.

**The one-line design rule this ticket establishes:** *The Limpet vision pipeline splits
grounding out of planning — the reasoner emits a logged **thought** then a **target
description**; a **Mixture-of-Grounding router** (visual / textual / structural specialists)
turns descriptions into coordinates; those become a **typed, pre-validated action** that the
executor performs over HID/serial through the safety gate — all driven by a ~1 Hz
screenshot-cadence loop, never a video stream.*

**Builds on / unblocks:** extends `research/WQ3922_VISION_HDMI_CAPTURE.md` (capture + model
sizing) and `research/WQ4337_ANTHROPIC_AGENT_DISSECTION.md` §Patterns (brain/hands, two-model
gate, coordinate contract); gives `limpet/model_router.py` its grounding-route capability set;
informs the still-design-only HDMI-capture agent loop
(`design/wq3806_limpet_architecture.md` §4/§6).
