# Research triage — 2026-11-05

Session: `triage-research-77d2c0` (claude.ai, Fable 5.1). Triage only — nothing implemented, no tickets re-dispatched.
Scope: the 7 research tickets Frank listed. Four landed artifacts; two were killed on a capability gate and have no artifact; one is unclaimed and has no artifact. Every claim below is from the ticket, its artifact, or a URL fetched today (marked).

Verdict scale: **adopt** (do it, file build tickets) · **pilot** (bounded trial first) · **park** (keep the finding, no action now) · **reject** (close, do not revisit without a new trigger).
Relevance: **DIRECT** (touches a live fleet component) · **ADJACENT** (informs design of something planned) · **PARK** (interesting, no owner).

## Status table

| Ticket | Subject | wq status | Artifact | Relevance | Verdict |
|---|---|---|---|---|---|
| wq#4852 | Kimi K3.1 Pro routing | done (PR #2605) | `foreman/research/kimi_k31_pro_routing.md` | DIRECT (LiteLLM routing) | **reject** (route) / **park** (DARK rung recipe) |
| wq#4853 | Grok Bot architecture | done (PR #2606) | `foreman/research/grok_bot_architecture.md` | ADJACENT (CCC, foreman) | **adopt** 2 patterns / **reject** purchase |
| wq#4856 | Octopus-SI-OS for Limpet | done (PR #2607) | `foreman/research/wq4856_octopus_si_os_limpet_dissection.md` | DIRECT (Limpet MCP gate, registry) | **adopt** items 1–3 / **park** 4–8 |
| wq#4859 | SysKnife for agent authority | done (PR #2608) | `foreman/research/sysknife_fleet_evaluation.md` | DIRECT (ops-apply DESIGN.md §3) | **adopt** design, **reject** product |
| wq#4860 | Clawboo + Pinchy for Limpet | **blocked** (CAPABILITY_DENIED ×2) | none | ADJACENT (Limpet sandbox model) | **park** — re-run only after wq#4716 route works; premises partly wrong |
| wq#4861 | Windmill for foreman | **blocked** (CAPABILITY_DENIED ×2) | none | ADJACENT (foreman durable orchestration) | **park** — already decided: LangGraph pilot (wq#4630), Trigger.dev NO |
| wq#4865 | OpenGrokBot deploy on Lumen | **open**, unclaimed | none | ADJACENT (CCC agent-teammate pattern) | **pilot** — bounded, stub-model first; do NOT treat as supersession of #4853 |

Frank's prompt called all seven "completed". Three are not (`wq show` 4860/4861 = blocked, 4865 = open). Triaged on what exists; see per-ticket notes.

---

## wq#4852 — Kimi K3.1 Pro routing evaluation

**Summary.** The worker fetched live pricing (2026-11-05): Kimi K3.1 Pro is served by Moonshot direct, OpenRouter and Together at $2.00 in / $10.00 out per Mtok (cache read $0.20); it is not on SambaNova. Against the fleet brain `deepseek-flash` at peak ($0.30 / $1.20) that is ~6.7× input, ~8.3× output and ~33× on cache hits, and it is still 1.5–2.5× dearer than the `deepseek-v4-pro` fallback. LiteLLM upstream already maps `moonshot/`, `together_ai/` and `openrouter/moonshotai/kimi-k3.1-pro`, so adding it is config-only. License is MIT-plus-two-clauses (>$20M MaaS revenue, >100M MAU attribution), §4 exempts internal use — compatible. The worker correctly did not add a route or run the 3–5 ticket trial because the ticket conditioned both on cost-competitiveness, and it recorded a DARK-rung recipe (OpenRouter route reusing the existing `openrouter-deepseek-pro` key, pin `provider.order` to Moonshot/Together to avoid fp4 long-tail hosts). One caveat: whether the gateway's pinned LiteLLM image carries the price-map entries was not verified (sandbox boundary, same as ISSUE-LOG #1069).

**Relevance:** DIRECT. **Verdict: reject** as a brain route now; **park** the DARK-rung recipe. Re-open only if Kimi drops to ~$1/$4 (V4 Pro parity) or a hard-ticket escalation tier is actually wanted. No action.

## wq#4853 — Grok Bot architecture

**Summary.** The architecture is already public: a published image-level reconstruction (gist wtfsayo/b80f74f4) shows one container per *account* (not per bot), a Node "SandHost" gateway (HTTP in, SSE out), per-agent SQLite, an exec-daemon for shell/PTY/MCP/X11, Xvfb + Chrome + noVNC desktops, and Cursor-backed inference behind a short-lived token. The worker's component-by-component mapping says every piece is self-hostable on fleet infra via LiteLLM/DeepSeek except pixel-level `computerUse`, which needs a UI-grounding VLM the fleet does not route. The captured box has real security defects (unauthenticated Docker API :2375, passwordless VNC, one trust domain per account) — copy patterns, never the binaries. Cost: Grok Bot ships with X Premium at $30/mo; the worker recommends **not buying** — the gist already exceeds what a month of poking would reveal, and the only unanswerable part (demo-to-routine UX) is optional. Five CCC patterns extracted; the two that matter: (1) accept-fast/stream-SSE with an idempotency nonce, (2) step checkpoints so an approval *pauses and resumes* a turn instead of kill-and-redispatch. Also a use-without-see credential broker, and "automations are just turn producers".

**Relevance:** ADJACENT (CCC gateway, foreman approval flow). **Verdict: adopt** patterns 1–2 as CCC/foreman design inputs (they coincide with the hold-state fix already identified in agent-authority SYNTHESIS.md); **reject** the $30 subscription; **park** the UI-grounding VLM question until a `computerUse` need exists. Suggested follow-up (not filed): one BUILD ticket "CCC gateway spike: durable prompt accept + nonce + SSE + step-checkpoint table".

## wq#4856 — Octopus-SI-OS for Limpet OS primitives

**Summary.** Fable-grade dissection of a ~59k-line Rust agent kernel (MIT, 1 commit, macOS-only product). The product is useless to the fleet; the architecture is the best Limpet design input this batch. Eight primitives: Interface (identity = folder, classifiers never route), Artifact (manifest + content hash, permissions ride the envelope never the payload — a clean prompt-injection firewall), Run (durable JSON that parks on checkpoints, no thread held while waiting), Execution Environment (nested, only narrows), Worker (two JSON files = pin + ceiling), Policy (**never stored, formed fresh at every arrival** = ceiling ∩ EE chain ∩ bindings), Mark (recorded only after a verifier passes; nothing can emit a mark), Ledger (append-only JSONL with a closed refusal vocabulary). The worker mapped all eight to Limpet's existing HAL / MCP meta-server / Registry / Agent-core and ranked adoption: (1) per-call grant formation at the MCP trust gate, (2) declarative worker pins under `agents/<name>/{agent.json,policy.json}`, (3) typed-refusal ledger, (4) EE narrowing for untrusted-firmware tasks, then run-as-data for long RE campaigns, verifier packages, and self-extension. It also flags what *not* to copy: descriptor maximalism, the no-watchdog purism (contradicted by wq#4629), and no-migration state handling. Linux port needs a landlock/bwrap sandbox backend (preview is macOS `sandbox-exec`).

**Relevance:** DIRECT (Limpet trust gate and registry exist today). **Verdict: adopt** items 1–3 as Limpet design work; **park** 4–8 behind them. Suggested follow-ups (not filed): DESIGN ticket "Limpet MCP gate: per-call grant formation from on-disk descriptors" (owner: Limpet lane, after wq#4084 installer decision); the verifier-package idea (item 7) belongs to foreman acceptance.json as much as Limpet.

## wq#4859 — SysKnife for fleet agent authority

**Summary.** v0.30.0, MIT, 191 typed Linux actions with risk tiers, Ed25519 hash-chain audit, SO_PEERCRED identity, single-use TTL receipts — a well-built version of the ops-apply daemon the fleet already designed (`foreman/docs/agent-authority-research/DESIGN.md` §3). Four disqualifiers, all source-cited: (1) the daemon requires approver == previewer uid (`dispatcher.rs:819-878`), the exact opposite of `wq grant` F8 — an fworker with a shell as uid `lumen` can approve itself, so the human gate does not hold; (2) containers are Podman-only, no Docker/compose; (3) no apt rollback (rpm-ostree only, stated roadmap); (4) one daemon per host, fleet management is Phase 10 roadmap. Pace is pre-1.0 (6 releases in 12 days, wire protocol and audit encoding still moving). `sysknife doctor` was not run: the worker allowlist refused an arbitrary binary (ISSUE-LOG 1655) — the worker did not work around the gate, which is correct. Four mechanisms to borrow into ops-apply: signed hash chain as the log format with tip anchoring, SO_PEERCRED principal in the signed row, execute-time revalidation of approved state (generalises the §3.4 TOCTOU check), and the apt argv allowlist pattern (shows why a sudoers trailing `*` is unsafe — feeds `never.sudo_argv_deny` V9).

**Relevance:** DIRECT (ops-apply is the live agent-authority design). **Verdict: adopt the design, reject the product.** Do not install the system daemon anywhere. Re-check only when a Docker/compose action family and a cross-account approver (or externally minted receipt) ship. Suggested follow-up (not filed): fold the four borrow items into DESIGN.md §3 as a dated addendum.

## wq#4860 — Clawboo + Pinchy for Limpet agent orchestration

**Status.** Two attempts, both KILLED → CAPABILITY_DENIED: the `novel-design` archetype routed to an fworker with no WebFetch/WebSearch grant; the failure note says to route via the fleet-search / searxng recipe (wq#4716). No artifact. `foreman/_runs/wq4860/` holds only loop-handoff and failure records.

**Triage check (fetched today, not from the ticket):** Clawboo is real and active (clawboo@0.4.0, PRs #268–#319 this quarter): SQLite-backed durable board, per-task git worktrees with a verify command and a fresh read-only review on a different model, budgets with cap-mode auto-pause and per-run spend holds, "coordination plane vs runtime" split with MCP as spine — the ticket's premise holds. Pinchy is **AGPL-3.0, not MIT** as the ticket states, is a layer on top of OpenClaw (v0.5.5 tracks OpenClaw 2026.5.20), and the three-zone workspace (read-only `SOUL.md`/`AGENTS.md`/`IDENTITY.md`, `uploads/`, writable `workbench/`, enforced by the write tool's allow-list) exists as described. Both are TypeScript products, not libraries; what Limpet would take is the pattern, and three of the six Clawboo patterns (worktree + verify-before-merge, delegation caps in code, spend governance) are things the foreman already does or has designed.

**Relevance:** ADJACENT. **Verdict: park.** Do not re-dispatch as written: fix the ticket first (AGPL correction; drop the patterns the fleet already has; narrow to Pinchy's three-zone write-allow-list and Clawboo's fresh-model review step, both of which overlap wq#4856 items 1–2 and wq#4859's borrow list). Re-run only through the web-capable research recipe. The class defect (novel-design research with external sources dispatched to a no-web worker) is already named by wq#4716.

## wq#4861 — Windmill workflow engine for foreman

**Status.** Same failure as wq#4860: two attempts KILLED on the WebFetch gate, no artifact.

**Triage check (fetched today):** Windmill is **AGPLv3 for the source build, with a mixed-license "Community Edition" binary** — not Apache 2.0 as the ticket states. Internal use needs no commercial license; re-exposing it in a product would. The engine is Postgres-backed, worker groups pull from the DB queue (`WORKER_GROUP`, `NUM_WORKERS`, zombie-job timeout, nsjail sandbox optional), and approval steps are first-class flow nodes. The prior fleet note on this (`scratch/research/pe_automation_n8n_vs_windmill_vs_temporal…`, Jul 2026) is stale (cites 2024 versions) and should not be relied on. More importantly, the orchestration-engine question was already decided on 2026-10-25 (HANDOFF orchestrator note): Trigger.dev NO, LangGraph pilot YES (wq#4630, db `limpet_graph`), plus fworker watchdog+resume tickets. Windmill answers the same question with a heavier footprint (Postgres + server + workers + UI) and would be a second durable-execution substrate beside the LangGraph pilot.

**Relevance:** ADJACENT. **Verdict: park.** Do not re-dispatch. If the LangGraph pilot fails its exit criteria, reopen as "Windmill vs LangGraph for foreman durable flows" with the license fixed. The one pattern worth lifting without running Windmill: approval nodes as *data in the flow definition* with retry/backoff per step — same lesson as Grok Bot pattern 2 and Octopus checkpoints.

## wq#4865 — OpenGrokBot self-hosted deploy

**Status.** Open, unclaimed, P1, no artifact. Ticket text says "SUPERSEDES wq#4853" — it does not; #4853 answered the architecture question and this is a deployment candidate.

**Triage check (fetched today, github.com/wolfqing/OpenGrokBot):** MIT, 46 commits, **3 stars, 2 forks, 0 watchers, no releases, 1 open issue**. v0.2 is its own core (v0.1 was an OpenClaw distribution). Stack: one Node gateway (`pnpm dev`, port 5173), one Docker container per bot with headed Chromium + noVNC + shell/file shim, SQLite under `gateway/data`, any OpenAI-compatible endpoint via `OPENGROKBOT_API_BASE`, `OPENGROKBOT_MODEL=stub` runs the whole UI with no model. Draft-and-hold approvals, `MEMORY.md` diff chips, cron routines, group dispatch, allowlisted handoffs (`OPENGROKBOT_A2A_ALLOW`, 2-hop cap) are claimed implemented and tested. Screens bind to 127.0.0.1 only. Teach-mode is roadmap, not shipped. The ticket's eight architecture bullets match the README.

**Fit against the fleet:** it is a single-author, days-old project with no users — a demo of the shape, not a platform. The pattern overlap with CCC is real (SOUL.md ≈ worker definition, draft-and-hold ≈ pause-for-approval, group dispatch ≈ foreman fan-out) and that is exactly what wq#4853 already extracted. Deploying it as a service on Lumen would add a Node gateway, a per-bot Chromium container fleet (RAM: Lumen root is 335G/466G and wq#2094 is still moving data off it; container RAM per headed Chromium is unmeasured — estimate 0.5–1 GiB, verify in the pilot), and a second agent runtime beside fworkers with no wq integration. The ticket's liveness probe (`docker images | grep -q opengrokbot`) measures that an image was built, not that anything works.

**Relevance:** ADJACENT. **Verdict: pilot, bounded.** Reframe the ticket from "RESEARCH+DEPLOY … NOW" to a 2-hour evaluation: clone into `/scratch`, build the bot image, run with `OPENGROKBOT_MODEL=stub` and then against LiteLLM `lumen/best`, exercise one approval + one memory chip + one routine, record RAM per bot container, write `research/wq4865_opengrokbot_eval.md`, tear down. Exit criteria to go further: it survives a gateway restart with routines intact, approval flow is idempotent (409 on double-approve as claimed), and a bot container stays under 1 GiB idle. If it passes, the follow-up is still "steal the approval/memory-chip UX for CCC", not "run OpenGrokBot as a fleet service". Downgrade P1 → P3; it is not blocking anything.

---

## Corrections to ticket premises (record so the next dispatch does not re-inherit them)

- wq#4860: Pinchy is AGPL-3.0, not MIT. Clawboo is MIT.
- wq#4861: Windmill is AGPLv3 (source) / mixed CE binary, not Apache 2.0.
- wq#4865: OpenGrokBot is 3 stars / 46 commits / no releases, and does not supersede wq#4853.
- wq#4853 ticket left Grok 4.6 pricing blank: $2 in / $6 out per Mtok (OpenRouter, per the artifact).

## Cross-cutting findings

1. **Three tickets converge on one design primitive:** approval as a *persisted checkpoint that pauses and resumes a run* (Grok Bot pattern 2, Octopus Run/checkpoint, Windmill approval node, agent-authority SYNTHESIS hold-state). This is the thing to build; it is already partly ticketed via the agent-authority research. Nothing here argues for a new product.
2. **Two tickets converge on policy-formed-per-call from on-disk descriptors** (Octopus Policy, SysKnife's typed catalogue + SO_PEERCRED). The fleet's ops-apply DESIGN.md §3 is the right home.
3. **Dispatch defect:** `novel-design`/`research-synthesis` tickets with external GitHub sources went to a no-web worker twice (wq#4860, #4861; #4854 and #4855 in the same batch also died on permission loops, different gate). wq#4716 names the route fix; until it lands, research tickets citing URLs should be lane-tagged for the web-capable recipe at creation.

## Follow-ups (NOT filed — triage-only session; Frank to say go)

- BUILD: CCC gateway spike (durable accept + nonce + SSE + step-checkpoint table) — from #4853.
- DESIGN: Limpet MCP gate per-call grant formation + declarative worker pins — from #4856.
- DOCS: DESIGN.md §3 addendum with SysKnife's four borrow items — from #4859.
- EDIT: wq#4860 (fix license, narrow scope, web recipe), wq#4861 (close as park, pointer to wq#4630), wq#4865 (reframe to bounded eval, P3).
- VERIFY: gateway's pinned LiteLLM image has the `moonshot/kimi-k3.1-pro` price-map entries (only matters if the DARK rung is ever added).
