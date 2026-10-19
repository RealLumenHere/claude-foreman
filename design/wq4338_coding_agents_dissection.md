# wq#4338 — LIMPET DISSECT: OpenHands + Devin + Cursor Agent — coding-agent patterns for the RE loop

Status: research/design. Author: build worker, 2026-10-19. Dissects three coding agents that
solve the **same orchestration problems Limpet solves for RE** — task planning, tool chaining,
error recovery, workspace isolation, approval gates — and maps each transferable pattern onto
the Limpet code that already exists (or the gap where it does not). Companion to the
tool-dissection line: wq#4100 (Rev-Deck, `design/wq4100_revdeck_evidence_first_console.md`),
wq#4019 (moria/mithril), wq#4005 (EMBArk, `design/wq4005_emba_dissection.md`). OpenHands is
already on the fleet (wq#4007 deployed/smoke-passed; hardening wq#4149 —
`research/OPENHANDS_1_8_READINESS.md`), so its patterns are the best-grounded of the three.

## 0. Grounding — and the verify-gap

The three targets could **not** be read from source live this session. `fleet-search web`'s
searxng general engines are all suspended/CAPTCHA'd (brave/ddg/google-cse/startpage — wq#2595
still open); only the keyed DeepSeek-Search fallback answered. OpenHands returned its own
`openhands/README.md` architecture section (LLM / Agent / AgentController / State / EventStream /
Event=Action|Observation / Runtime) — primary and trustworthy. Devin and Cursor returned
**docs/third-party-profile** snippets (Devin enterprise-deployment docs + the 2024 AI-Agent-Index
entry; Cursor tool-profiles and a skills-marketplace page), not source. So:

- OpenHands internals below are `[web-verified]` against its README, and `[F]` fleet-confirmed
  where `research/OPENHANDS_1_8_READINESS.md` already recorded them.
- Devin and Cursor internals are `[web-stated]` (vendor docs / third-party profiles) and should
  be re-confirmed by a verify-pass once general web search is restored (ISSUE-LOG #1387 tracks
  the same restore dependency wq#4100 hit). Neither product is open-source, so "read the source"
  will never be available for them — docs are the ceiling, and the patterns are the point, not
  the line numbers.

Nothing in the Limpet recommendations (§3, §4) depends on an unverified Devin/Cursor detail:
each lands on a Limpet fact cited to real code on this branch.

## 1. The five shared problems, one sentence each

All three coding agents and Limpet are the same machine pointed at different targets. A coding
agent drives {editor, shell, browser} over a **repo**; Limpet drives {Ghidra, JTAG, logic
analyzer, SDR, …} over a **device under test**. The orchestration spine is identical:

1. **Task planning** — turn a goal into an ordered, revisable sequence of steps.
2. **Tool chaining** — feed one tool's output into the next tool's input, as a loop.
3. **Error recovery** — notice a step failed and do something other than barrel on.
4. **Workspace isolation** — keep one task's mutations (and its untrusted inputs) off the host
   and off every other task.
5. **Approval gates** — let the risky subset of actions require a human yes.

## 2. Pattern catalog — how each agent solves it, and where Limpet already stands

### 2.1 OpenHands (`[web-verified]` / `[F]`)

- **Planning + state:** the `State` object carries `current step`, a `history of recent events`,
  and the agent's `long-term plan`; `AgentController` runs the step loop, asking `Agent` for the
  next `Action` given `State`. Plan is **data in State**, not a static upfront script — it is
  re-derived each step. `[web-verified: openhands README]`
- **Tool chaining:** the **EventStream** is a single central hub; every component publishes/
  subscribes `Event`s, and an `Event` is either an **Action** (edit file / run command / message)
  or an **Observation** (file contents / command output). Chaining = Action → Runtime executes →
  Observation back onto the stream → next Action. One append-only log *is* the chain.
- **Error recovery:** a failed command returns an **Observation** carrying the error (not an
  exception that unwinds the loop); the agent sees it as the next input and re-plans. Recovery is
  "the error is just another observation," which is why the loop is robust to tool failure.
- **Workspace isolation:** the **Runtime** executes all Actions in a **Docker sandbox**
  (per-conversation container, `/workspace` mount) — the agent never runs commands on the host.
  `[F] research/OPENHANDS_1_8_READINESS.md` records the container/sandbox hardening (egress
  allowlist, limits, reaper) the fleet added on top.
- **Approval gates:** **confirmation mode** — the agent proposes an Action and waits for operator
  confirm before the Runtime executes it; paired with a **security analyzer** that risk-scores
  each Action so the gate can be conditioned on risk rather than firing on every step.
  `[web-stated]`

### 2.2 Devin (`[web-stated]`, vendor docs / AI-Agent-Index)

- **Planning:** plans a complex task into steps up front and reports the plan; uses Cognition's
  own models plus frontier models. Progress + questions surface **Slack-native** — Devin narrates
  what it is doing and *asks when blocked* rather than guessing.
- **Simultaneous tools:** operates a **browser + editor + shell together** inside one task
  session — e.g. read docs in the browser, edit in the editor, run in the shell, in a single loop.
- **Workspace:** a dedicated per-task cloud workspace/VM (Enterprise Cloud or customer-dedicated
  deployment) — one task, one isolated machine.
- **Budget as a first-class signal:** **ACU** (Agent Compute Unit) — complex tasks consume more
  ACUs, with transparent per-task cost tracking. The agent runs against a *budget*, which bounds
  runaway loops and makes "this is getting expensive, ask the human" a measurable trigger.

### 2.3 Cursor Agent mode (`[web-stated]`, tool profiles)

- **Context/planning:** agent mode runs inside the IDE with the **full file tree, terminal, and
  LSP diagnostics**; **repo-map context injection** gives it a cheap whole-repo prior so it makes
  fewer exploratory tool calls before acting.
- **Approval — the sharpest of the three:** proposed file changes are presented as a **diff the
  operator accepts or rejects, per hunk**, with **checkpoints/restore** so any applied change can
  be rolled back. Approval is scoped to a *reviewable, reversible* unit, not an all-or-nothing
  "proceed?"
- **Multi-file coordination:** Composer/agent mode plans multi-step edits across many files,
  **identifies dependencies and potential side-effects**, and proposes validation steps; supports
  **parallel agents** on independent slices.

## 3. The Limpet map — what already exists (do not rebuild)

Limpet already implements the RE-flavoured version of four of the five, on this branch:

- **Tool chaining = the evidence stream.** The MCP meta-server writes one JSON block per
  `tools/call` to an **append-only** `evidence/tool-calls.jsonl`
  (`ops/wq3808-limpet-mcp-meta-server-a1/evidence.py:20,114,119-124`) — this is OpenHands'
  EventStream-of-Observations, already append-only, already per-call. The read-only console tails
  it (`limpet/workspace_console.py:1-20,52,79,98`).
- **Error recovery / claim contract = the critic.** `limpet/critic.py:81` (`deterministic_check`)
  and `:120` (`review`) are the verify-then-accept-or-reject loop: a finding is checked against
  evidence and can be rejected back, Cursor's accept/reject applied to *RE findings* instead of
  code diffs.
- **Workspace isolation = per-workspace evidence + untrusted-input boundary.** Evidence refs are
  validated to be **workspace-relative, real, and non-escaping** (`limpet/obligations.py:141-158`
  — absolute paths and `..` rejected), and target bytes are treated as attacker-controlled: the
  injection classifier screens tool output before it re-enters the agent
  (`limpet/injection.py:108,124`). This is Devin's per-task VM boundary expressed as "evidence and
  inputs stay inside the one target's workspace."
- **Approval gate = energize-confirm.** `needs_energize` rides each tool's registry record
  (`limpet/registry_index.py:328`) with a **per-tool override table** so a read/scan tool under an
  energizing plugin stays autonomous while a write/flash tool gates (`:287-305,329-331`); plugin
  trust is a separate signing gate (`limpet/registry_trust.py:291,299`). The "done" boundary is
  the obligation ledger: `resolve` **refuses to close a claim without real evidence**
  (`limpet/obligations.py:224-226`) and `require_done` blocks done while any obligation is open
  (`limpet/obligations.py:285`, `:57`).

The **one axis Limpet has no code for is planning** (§2's problem 1). The vision calls Limpet
"agent-driven, not tool-driven — the agent suggests the next workflow step from what's connected
and what's been found" (`docs/limpet_vision.md:56-58`), but there is no plan artifact and no
step-loop controller in `limpet/`; the implicit plan today is "the set of open obligations." That
is the gap §4 targets.

## 4. What to steal — ranked value ÷ cost, each tied to real code

1. **Make the evidence log a two-way EventStream, not a one-way log (OpenHands).** Highest value.
   Today the meta-server *writes* `evidence/tool-calls.jsonl`
   (`ops/wq3808-limpet-mcp-meta-server-a1/evidence.py:119-124`) and the console/agent only *read*
   it (`limpet/workspace_console.py:98`). OpenHands' insight is that the **same** append-only
   stream the human watches is the stream the agent re-plans from — so **error recovery is replay
   from the last good Observation**, free, with no separate retry machinery. Steal: let the agent
   consume `tool-calls.jsonl` as its history-of-observations (it already carries `ok`/`coverage`
   per block), so a failed call is "the next observation," not a thrown exception. Near-zero new
   surface: one reader, reusing the block contract already pinned at
   `limpet/workspace_console.py:52`.
2. **Risk-score the energize gate instead of flag-gating it (OpenHands security analyzer +
   Cursor per-hunk diff).** Today the gate is a static boolean per tool
   (`limpet/registry_index.py:328`, override `:329-331`). OpenHands conditions its confirmation on
   a per-Action risk score; Cursor scopes approval to a *reviewable, reversible* unit. Steal both:
   before an energizing Action fires, render a **"what this will do to the device" preview**
   (which lines/pins/flash regions) and gate on that, so the operator confirms a *specific
   action*, not a tool class — and the SDR-TX open question (`docs/limpet_vision.md:49-53`,
   plugin-spec §6) becomes "a higher risk class," which risk-scoring expresses naturally rather
   than needing a second gate type.
3. **A plan artifact + "ask when stuck" threshold (Devin).** Fills the §3 planning gap. Devin
   plans up front, reports the plan, and *asks when blocked* instead of guessing — the same
   discipline Limpet's instinct 3 wants (act autonomously, queue the genuinely-blocked step). The
   open-obligation set (`limpet/obligations.py:57,285`) is already a latent plan; surface it as a
   first-class ordered plan the console renders, and add a confidence/stuck threshold that opens a
   *blocking obligation* (operator question) rather than looping. Design-only until the agent loop
   exists.
4. **A budget signal (Devin ACU).** Lowest urgency. Limpet runs on a local LLM (offline-first,
   `docs/limpet_vision.md:54-55`) so token cost is not the pressure it is for Devin — but
   *wall-clock / tool-call count per target* is a real runaway signal on an appliance. A simple
   per-workspace call-count budget, read off the evidence log that already exists, bounds a stuck
   loop without any new accounting store. Defer behind item 1.
5. **Repo-map → target-map context injection (Cursor).** Cursor's repo map cuts exploratory tool
   calls; Limpet's analogue is a **target map** (what the HAL detected + what's been found so far)
   injected as the agent's cheap prior, so it does not re-enumerate a device it already fingered.
   Partially latent in the workspace index (`limpet/workspace_index.py`); pointer only, gated
   behind the planning loop (item 3).

## 5. Verdict

The three coding agents confirm Limpet's shape and surface one real gap. The evidence log is
already OpenHands' append-only Action/Observation stream (`ops/wq3808-…/evidence.py:119-124`), the
critic is Cursor's accept/reject applied to findings (`limpet/critic.py:81,120`), the per-workspace
evidence + injection boundary is Devin's per-task isolation (`limpet/obligations.py:141-158`,
`limpet/injection.py:108,124`), and energize-confirm is confirmation mode
(`limpet/registry_index.py:328`). Four patterns already exist. The transferable **steals** are:
(1) read the evidence log *back* into the agent so recovery is replay, not retry — the single
highest-value, lowest-cost move; (2) risk-score the energize gate and show a device-change preview
(OpenHands analyzer + Cursor diff), which also dissolves the SDR-TX open question; and (3) the
missing axis — a first-class plan artifact with a Devin-style "ask when stuck" threshold, built on
the obligation set Limpet already tracks. Open item: verify-pass on Devin/Cursor internals once
general web search is restored (ISSUE-LOG #1387); both are closed-source, so vendor docs are the
evidence ceiling.
