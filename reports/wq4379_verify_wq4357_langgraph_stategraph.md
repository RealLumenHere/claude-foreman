# wq#4379 — Adversarial verify-pass of wq#4357 (PR#2257: dissect LangGraph StateGraph)

**Verdict: CONFIRM** — `research/WQ4357_LANGGRAPH_DISSECTION.md` (249 lines, PR#2257, branch
tip `619200cc`) holds up under independent re-sourcing of every external claim and direct
re-check of every local-repo citation. One third-party citation (a GitHub PR number) is
slightly imprecise about which commit inside a release PR carries the described content —
detailed below as a caveat, not a contradiction.

## What was shipped

PR#2257 added `research/WQ4357_LANGGRAPH_DISSECTION.md`, a desk-research dissection of
LangGraph's `StateGraph` runtime (state/node/edge model, compile-time validation gaps,
checkpointing/durable execution, human-in-the-loop `interrupt()`, state-driven routing via
`Command.goto`/`Send`, production pedigree) plus a "LIMPET VALUE" section mapping it onto the
wq#3901 Target Device Wizard spec and the wq#3999 REx recon/hunters/reconcile topology.

## Probe

Research doc, not code — same probe shape as precedent (wq#4366/wq#4374/wq#4378): (1)
re-derive every external factual claim from fresh, independent `fleet-search web` queries
rather than trust the doc's own citations, (2) re-check every local file:line/section
citation against the actual files in this worktree, (3) confirm the three cited local files
are unchanged between the PR's branch point and current `main` so a direct check is valid,
(4) adversarially chase the one citation shaped like it could be a fabrication (an obscure
third-party repo PR number).

**Local citations — checked directly** (confirmed `git diff 619200cc~1 HEAD -- docs/target-wizard-spec.md design/wq3999_rex_skill_dissection.md research/WQ4356_A2A_PROTOCOL_DISSECTION.md`
is empty, so current-`main` line numbers equal PR-branch-point line numbers):

- `target-wizard-spec.md:54-58` (one-sentence flow `detect → identify → suggest workflow →
  create workspace → begin analysis`) — confirmed verbatim.
- `target-wizard-spec.md:70-71` ("Each stage is a committed transition in the session
  record, so an onboarding is itself replayable and auditable") — confirmed verbatim.
- `target-wizard-spec.md:102-107` (`WizardSession` shape: `session_id, stage,
  target_profile, suggested_workflow, workspace_path, log[]`, `/var/limpet/sessions/<id>.json`
  persistence) — confirmed verbatim, including the field names doc maps onto
  thread_id/checkpoint.
- `target-wizard-spec.md:112-124` (ASCII state diagram: DETECT→IDENTIFY, known→SUGGEST,
  unknown/probable→ASSIST) — confirmed exact fork and all six named states
  (DETECT/IDENTIFY/ASSIST/SUGGEST/WORKSPACE CREATE/ANALYZE) present at those lines.
- `target-wizard-spec.md:127-128`, `138-169` (ASSIST loop, IDENTIFY known/unknown fork,
  VID:PID deterministic steps 1-3 vs. vision-assisted wq#3900 call) — confirmed verbatim,
  including the specific claim that the deterministic and vision paths write into the same
  `TargetProfile.identification` fields with no architectural seam.
- `target-wizard-spec.md:171-184` (§4.3 confidence gate, thresholds ≥0.85/0.4-0.85/<0.4) —
  confirmed verbatim, including that read-only steps run freely and only energize-steps gate.
- `target-wizard-spec.md:194-199` (§4.4 profile→arc table) — confirmed table occupies exactly
  that 6-line range (header+separator+4 rows).
- `target-wizard-spec.md:245-247` (`--confirm` flag; "prints the blocking reason and exits
  non-zero") — confirmed verbatim.
- `target-wizard-spec.md:258-277` (§6 Testability: known/unknown/parity/hygiene fixture
  probes) — confirmed section boundary and all four numbered probes match.
- `design/wq3999_rex_skill_dissection.md` §4 — confirmed the five named stance agents
  (`re-bughunt`, `re-safety`, `re-arithmetic`, `re-lifecycle`, `re-logic`), `re-recon` as a
  solo first phase, `re-reconcile` as the solo join phase — matches the doc's claim that
  recon "must complete alone before anything else runs" and reconcile is "the join node
  after the `Send` fan-out completes."
- `research/WQ4356_A2A_PROTOCOL_DISSECTION.md` §7 item 4 ("Operations": `GetTask`/`ListTasks`
  for reconcile to poll status before adjudicating) — confirmed item 4 is exactly this, not
  a different item number. §6 ("CAVEAT — A2A deliberately omits authorization",
  `securitySchemes` boundary) — confirmed the "wire format, never the policy" framing is
  verbatim-equivalent to §6's own text.

**External citations — re-sourced fresh via `fleet-search web`, not trusted from the doc:**

- GitHub issue #6735 ("request: Compile-time validation of nodes that aren't statically
  linked") — confirmed real, with the doc's quoted repro output ("Graph compiled
  successfully (no warning about orphan node)") matching the issue's own code sample
  verbatim.
- GitHub PR #6751 ("fix(langgraph): add compile-time detection of detached nodes") —
  confirmed real; PR adds `on_detached_nodes` param with modes `warn`(default)/`raise`/
  `ignore` — confirms the doc's claim that the orphan gap "was still open at time of
  writing, not yet a default guarantee" (default mode only warns, doesn't raise).
- `MULTIPLE_SUBGRAPHS` error page — confirmed the doc's quote ("This error occurs when you
  call a subgraph inside a node multiple times, and the subgraph is compiled with
  `checkpointer=True`") is verbatim from `docs.langchain.com/oss/python/langgraph/errors/MULTIPLE_SUBGRAPHS`.
  The three-mode comparison (`checkpointer=False`/stateless, `None`/per-invocation default,
  `True`/per-thread) matches the official feature table exactly, including that only the
  per-invocation (`None`) mode supports parallel calls to the same compiled subgraph.
- `interrupt()` / `Command(resume=...)` — the doc's quoted phrase "pause execution and wait
  for external input" is verbatim from the LangGraph human-in-the-loop docs; confirmed
  `interrupt_before`/`interrupt_after` as the static (compile-time) alternative to dynamic
  `interrupt()`.
- `Command.goto` / `Send` — confirmed `Send(graph, state)` as the documented fan-out
  primitive ("N parallel children, N depends on runtime state") and `Command.goto` as the
  node-decided dynamic-routing primitive, matching the doc's distinction from
  `add_conditional_edges`.
- Case-studies table — confirmed verbatim: `docs.langchain.com/oss/python/langgraph/case-studies`
  is "compiled from public sources" and lists exactly J.P. Morgan (Financial Services,
  Copilot, "Interrupt talk, 2025") and Klarna (Fintech, Copilot, "Case study, 2025") — no
  more, no fewer rows, matching the doc's two-row table exactly.
- Uber secondary-source claim — confirmed the agentmarketcap.ai phrase the doc quotes
  ("each sub-agent operates statelessly from its own perspective but contributes to a shared
  parent graph state") verbatim, and confirmed the doc's own framing is correct: Uber is
  *not* on LangChain's official case-studies table (checked above), so flagging it as
  secondary-source rather than giving it the same confidence as J.P. Morgan/Klarna is the
  right call, not a hedge to avoid scrutiny.
- MIT license / Copyright (c) 2024 LangChain, Inc. — confirmed verbatim via the unpkg
  `@langchain/langgraph` LICENSE text and a third-party-notices mirror listing
  `langgraph-prebuilt`/`langgraph-sdk` etc. all under the same MIT grant.

## Adversarial checks that did not break it

- Chased the one citation shaped like a plausible fabrication: `nexus-substrate/nexus-agents`
  PR #6006, cited as "third-party confirmation of `Command.goto` semantics against upstream
  LangGraph." PR #6006 is real but is a bundled Changesets release PR
  ("chore(release): version packages"); the substantive content ("`GraphBuilder.addNode`
  accepts `gotoTargets`, so `Command.goto` can reach a node that has no static edge",
  explicitly described as "mirroring LangGraph's" semantics) is attributed in the release
  notes to the underlying change (#5727), folded into #6006's changelog. The doc's citation
  points at the PR that actually carries and merges that changelog text, so it isn't wrong,
  just one hop coarser than citing #5727 directly — a precision nit, not a fabricated
  reference. The semantic claim itself (goto redirects the next runnable set independent of
  static edges) is confirmed accurate.
- Tried to catch the PR #6751 orphan-detection claim as overclaiming ("compile() does catch
  some structural errors today" vs. "does not catch every orphan" — could these contradict
  each other?). They don't: the doc correctly separates the pre-existing "unreachable END"
  compile-time check (independently confirmed by a third-party troubleshooting hit) from the
  still-partial orphan-node check PR #6751 was proposing — two different checks, correctly
  not conflated.
- Checked whether the Target Device Wizard spec is still accurately described as
  "design-only" (a framing that could go stale). `docs/target-wizard-spec.md:8` states this
  verbatim ("This is a **design-only** document"), so the framing is current, not stale.
- Checked the `checkpointer` mode table for the specific risk of getting "which mode allows
  parallel same-subgraph calls" backward (an easy detail to invert). The official feature
  table's "Multiple calls (same subgraph)" column reads ✅ (`None`) / ❌ (`True`) / ✅
  (`False`) — the doc states exactly this, not inverted.

## Non-issues

- The "LIMPET VALUE" §8 mapping is explicit about scope boundaries: it calls out the
  `MULTIPLE_SUBGRAPHS` collision risk for the five-hunter `Send` fan-out *before* it becomes
  a build-time surprise, and separately flags that per-hunter full-checkpoint-history audit
  would need distinct `thread_id`s — a forward-looking design note, not an invented
  requirement.
- §8's closing paragraph explicitly declines to let `interrupt()`'s resume value stand in for
  authorization, deferring to the fleet's own broker/allowlist/gate plane — consistent with
  the same posture wq#4356 §6 took for A2A's `securitySchemes`, not scope creep.
- The doc's own sourcing note (`fleet-search web` fallback to DeepSeek Search because SearXNG
  general engines were suspended/CAPTCHA'd on 2026-10-19) is disclosed up front, not buried —
  matches what this verify pass independently hit using the same tool.

## Bottom line

Nine local file:line/section citations across three repo documents and roughly a dozen
external technical specifics (two GitHub issue/PR numbers, one error-page quote, three
checkpointer modes, two routing primitives, a two-row official case-studies table, a
secondary-source Uber claim, and the MIT license) all reproduced correctly against
independent re-sourcing — several of them word-for-word. The one third-party PR citation
that's one hop coarser than ideal still points at a real PR carrying the described content,
not a fabricated one. No escalation.
