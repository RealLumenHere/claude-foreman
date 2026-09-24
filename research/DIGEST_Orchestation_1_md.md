# DIGEST — Frank's "Orchestation 1.md" (wq#2465)

Source: `/home/lumen/scratch/TWO/Orchestation 1.md`, 1129 lines, read in full 2026-09-24.
The doc has no stable section numbering (it restarts at "## 1." six separate times), so every
citation is a **line range** plus heading text, e.g. `O1 L858-913 §"Configuring the Reflexion
LangGraph Loop"`.

Third of the wq#2463-2469 digest family; layout follows `DIGEST_Fleet_monitoring_1_md.md` and
`DIGEST_Fleet_searchability_md.md`. Read §8 of the searchability digest before starting a
sibling — its harness traps are real and two of them fired again this run (§8 below).

## 1. What the doc is, and how much of it to trust

A raw multi-turn chat transcript, not a spec — six "here is the blueprint" restarts, each
picking up where the last left off. Five properties matter before acting on any line of it:

- **The doc refutes its own premise at L1068.** Lines 1-1065 are written for "an Ubuntu
  desktop with an RTX 4060" and pin workloads to it (`O1 L170`, `L181`, `L192-208`
  `nodeSelector: gpu: "true"`). Then `O1 L1068` states the opposite: *"You lack heavy CUDA
  acceleration (no RTX 4060, and the AMD D700s in the Mac Pros lack modern ROCm support)."*
  The second statement is the true one — `research/wq2076_macpro_gpu_recon.md` found GCN 1.0
  Curacao XT / Tahiti LE parts with **`compute stack: none`** on all three online Macs. So
  every GPU-scheduling line in `O1 L168-208` is void, retracted by the author 900 lines later.
- **Fourth doc in the family to assume Kubernetes.** `which k3s kubectl helm argocd` returns
  nothing on lumen (verified live this run; only `/usr/local/bin/ollama` answered). Roughly
  600 of 1129 lines — every K3s manifest, Helm install, ArgoCD Application, ServiceMonitor,
  DinD pod and `kubectl label` — is unreachable. Already established three times:
  `DIGEST_Fleet_Memory_3_md.md:40`, `DIGEST_Fleet_monitoring_1_md.md:40`,
  `DIGEST_Fleet_searchability_md.md:222-224`.
- **Escape-mangled code, same as its siblings.** Every block is markdown-escaped (`mcp \=
  FastMCP(...)`, `\-\>`, `\_\_main\_\_`, `result\['StatusCode'\]`, `\# comment`). Nothing runs
  as pasted. Fourth doc, fourth time.
- **A verbatim duplicate block.** `O1 L1123-1127` repeats `L1066-1070` exactly (the
  "asymmetrical cluster / quad-channel DDR3 / here is the toolstack" paragraph). One
  recommendation, two copies, and the file ends mid-promise at `L1128` — the network-allocation
  table it re-introduces is never re-delivered. Expect the remainder in `Orchestation 2.md`
  (wq#2466).
- **Hardware figures are wrong in our favour and against it.** `O1 L1079` claims *"5x Mac Pros
  (2697v2)"* pooling *"320GB"* (`L1094`). Reality: three of five are online
  (`research/wq2076_macpro_gpu_recon.md` — khepri offline with `remediation_hold = on`, maksoor
  offline ~12h) at 64 GB each (`research/inference_nodes.yaml:46,74,102`) = **192 GB**, and
  they are MacPro6,1 single-socket, not dual 2697v2. Any sizing built on 320 GB is off by 40%.

### Two things in this doc must never be built here

1. **`restart_systemd_service(service_name)` as an unrestricted `sudo` MCP tool**
   (`O1 L227-235`), paired with **`power_cycle_ipmi(node_name)`** issuing `ipmitool ... power
   reset` (`O1 L580-597`) and `wake_node` WoL (`L567-578`). Handing an LLM an
   arbitrary-service restart plus a hardware power-reset primitive collides head-on with
   **instinct 2**: Thor (100.112.151.100) must never be restarted, rebooted or interrupted.
   The fleet already solved this the narrow way — `harness/fleet-exec-policy.yaml:89` allows
   `isis: [ollama]` and nothing else, i.e. an allow-list of (node, service) pairs, not a
   free-text parameter. The doc's version is a strictly worse design that also happens to be
   the one shape the house deny-list exists to prevent.
2. **Credentials inline in the code it tells you to deploy.** `"-P", "YOUR_PASSWORD"`
   (`O1 L592`) and `api_key='sk-1234'` twice (`O1 L130`, `L289`). Instinct 4. Even as a
   placeholder this teaches the wrong shape; the fleet keeps tokens in 0600 files behind git
   credential helpers.

## 2. External claims, independently verified

Two claims were worth checking with `fleet-search` rather than reasoning about from inside.

- **`exo` pools RAM across disparate boxes into "a single unified virtual GPU"** (`O1
  L1090-1095`). Partly true, with the binding constraint omitted: distributed inference is
  **network-bound**, and every working example in the wild sits on a fat fabric or a shared
  Apple-Silicon bus — `github.com/ArgentAIOS/dgx-spark-cluster` runs EXO over *200 Gb/s direct
  fabric*; HN's mesh-LLM thread (`news.ycombinator.com/item?id=48876505`, 2026-07) reports
  *"the throughput over a network is incredibly slow"* for exactly this pattern. The doc
  assumes 2.5GbE between the Macs (`O1 L1079`). We do not know that they share a segment:
  `research/FLEET_MAP.md:30` records the Mac Pros' **bridge side as UNKNOWN**, and the fleet
  runs a router-to-router **wifi bridge**. Tensor-parallel traffic crossing a wifi bridge is
  the worst case for this workload. Verdict: measurable, not assumable — T3.
- **DSPy is the way to stop a judge producing prose instead of a verdict** (`O1 L28`,
  `L119-162`, `L996-1029`). The *diagnosis* is right and lands on a real fleet defect (§4). The
  *remedy* is dated: the 2026 field has converged on schema-first validation with automatic
  re-ask — Instructor, PydanticAI, BAML — as the light way to get typed LLM output
  (`snapblock.ai/blog/langchain-alternatives-production-ai-frameworks`, 2026-09;
  `respan.ai/market-map/pydantic-ai/alternatives`). DSPy's distinctive part is the *compiler*
  (`BootstrapFewShot`, `O1 L277-344`), which needs a labelled trainset and a metric we do not
  have. `dspy` is not installed here; `pydantic_ai` is not either; `langgraph` **is** (see §3).
  Verdict: adopt the pattern, skip the framework — T2.

## 3. The fleet is further along than the doc assumes

Verified live this run: `python3 -c "importlib.util.find_spec(...)"` reports **`langgraph`
True**, `mem0` True, and `dspy`/`pydantic_ai`/`nats`/`crawl4ai`/`neo4j`/`temporalio`/`exo`
False. That first result matters, because the doc's central recommendation is "adopt
LangGraph" (`O1 L17`, `L460-514`) and we did, over a month ago:

- `autonomy/lumen_scout_graph.py` is a LangGraph graph on langgraph 1.2.5, and the fleet
  researcher runs a 7-node graph — `load -> web_search -> local_retrieval -> call_llm ->
  quality_gate -> verify_opt -> write` (`research/DSH_EVAL.md:12,55`).
- The stack the doc prescribes as the destination — **LangGraph + LiteLLM + Langfuse** — is
  what the fleet already runs (`research/DSH_EVAL.md:59`; `harness/fworker.py:524` emits one
  Langfuse span per attempt).
- The doc's fault-tolerance claim (`O1 L514`: a crashed pod resumes the graph without data
  loss) and its HITL design (`O1 L602-667`) both require a **checkpointer**. The fleet graph
  "compiles with none today" — already found and written up independently as
  `research/DSH_EVAL.md:76` M4, and pending as **wq#2038** (`[do-not-claim] ORCH DECISION:
  dsh`). The doc *corroborates* an open finding; it does not add one. Do not file a duplicate.

## 4. Every actionable recommendation, with a fleet verdict

Verdicts: **HAVE** = already built and verified; **ADAPT** = right idea, wrong mechanism for a
fleet with no Kubernetes; **REJECT** = do not build; **NEW** = genuine uncovered gap.

| # | doc cite | recommendation | verdict | grounding |
|---|---|---|---|---|
| R1 | `L9` | Tailscale mesh across all nodes | **HAVE** | the fleet is Tailscale-meshed; wq#1510 tracks version upgrades |
| R2 | `L10`, `L168-208` | K3s + node labels/taints to pin workloads | **REJECT** | `which k3s kubectl helm` → nothing. Fourth doc to assume it. The GPU half is void per §1 |
| R3 | `L11`, `L45-74` | LiteLLM at the ingress, System-1/System-2 split, usage-based routing + fallbacks | **HAVE** | LiteLLM gateway is live with per-node served aliases (`research/inference_nodes.yaml:18`); routing tiers are `research/DIFFICULTY_ROUTING.md` + `models.yaml` |
| R4 | `L17`, `L460-514` | LangGraph as the deterministic state machine | **HAVE** | §3 — langgraph 1.2.5, `autonomy/lumen_scout_graph.py`, 7-node researcher graph |
| R5 | `L604-612`, `L655-657` | a persistence checkpointer so a crash resumes mid-graph | **NEW, already filed** | `DSH_EVAL.md:76` M4; wq#2038. Corroboration only |
| R6 | `L18-22`, `L387-454` | NATS JetStream event bus; agents pass UUIDs not payloads | **REJECT** | `nats` not installed; no fan-out problem to solve. wq is the event log and it is a durable SQL table with leases (`bin/wq:111`) |
| R7 | `L28`, `L119-162`, `L996-1029` | replace prompts with DSPy signatures + `dspy.Suggest` assertions so a schema violation forces a retry instead of a bad answer | **ADAPT → T2** | the highest-value line in the doc; §5 T2 |
| R8 | `L29`, `L251-260`, `L741-816` | Crawl4AI/Browser-Use on edge nodes for JS-heavy pages | **ADAPT, already filed** | `crawl4ai` not installed, but the gap is real and narrower: `fleet-search read` dies at `http 403 blocked`. Filed as **wq#2481** by the sibling digest |
| R9 | `L30`, `L76-117` | execution-as-validation: clone the repo, run `make test`, judge the stdout | **ADAPT, mostly HAVE** | the pilot lane (wq#1559, `research/DEFER_PILOT_LANE.md`) is exactly this outlet and it exists. Do **not** build DinD: `harness/guard.sh` denies `docker` outright. wq#2477 is already root-causing why 3/4 pilots SKIP |
| R10 | `L32-37`, `L210-249` | MCP servers instead of per-API Python wrappers | **HAVE** | Commander MCP executes on lumen today; `harness/capability.py` + `fleet-exec-policy.yaml` are the capability layer |
| R11 | `L227-235`, `L548-600` | a `sudo systemctl restart` MCP tool + IPMI power-reset + WoL tools | **REJECT — unsafe** | §1. Instinct 2. `fleet-exec-policy.yaml:89` already does this as an allow-list of (node, service) pairs |
| R12 | `L41` | refactor the orchestrator to FastAPI + WebSockets for streaming | **HAVE** | `despag` already serves `POST /v1/responses` with `stream:true`, and `harness/fworker.py:1466` maps its UHP frames onto the canonical shape (`harness/GATEWAY_CANARY.md`) |
| R13 | `L42`, `L76-117` | never run untrusted code on the host; isolate in ephemeral pods | **HAVE (stronger)** | every worker runs in a dedicated git worktree behind a per-command Bash allow-list (`harness/worker-settings.json`) and `harness/guard.sh`. The doc's own sandbox is weaker: `network_mode="bridge"` despite the comment claiming no network |
| R14 | `L43` | chaos testing: kill workers mid-task, assert the state machine reschedules | **NEW → T4** | wq has lease-expiry reclaim (`bin/wq:27-34`, `WQ_STALE_MIN=45`) and `harness/reaper.py`, but no test kills a worker to prove reclamation fires. §5 T4 |
| R15 | `L262-273`, `L346-385` | Gitea + ArgoCD GitOps so agents deploy their own discoveries | **REJECT** | no K3s to sync to. The auditability goal is already met: workers land through `lumen-pr`, never direct-to-main |
| R16 | `L516-544`, `L669-735` | Prometheus → Alertmanager → webhook → event bus → an SRE agent that remediates | **HAVE (better)** | `dispatcher/self_heal.py` (wq#725) classifies every block as INFRA_CRASH / LIMIT_HIT / WORK_BLOCKED / HARNESS_DEFECT / RUNG_FAULT and releases **only** the two classes where retry is sound. The doc's SRE agent just deletes pods and power-cycles boxes on alert. Monitoring generally: `DIGEST_Fleet_monitoring_1_md.md` |
| R17 | `L858-913` | Reflexion: critique the failure, rewrite, retry, cap at 3 | **NEW → T1** | the retry loop exists (`harness/fworker.py:414` `MAX_ATTEMPTS = 2`) and it feeds attempt 2 a `prior-attempt-notes.md` — but `harness/fworker.py:1466-1489` writes only event **type** names and `git diff --stat`. No analysis of *why*. §5 T1 |
| R18 | `L915-974` | one Grafana pane over Prometheus + ArgoCD + NATS + Neo4j | **REJECT as specified** | three of the four data sources do not exist here. Monitoring is `DIGEST_Fleet_monitoring_1_md.md`'s scope |
| R19 | `L818-856`, `L1115-1117` | GraphRAG / LightRAG relational memory over Neo4j | **REJECT** | `neo4j` not installed; the fleet's knowledge store is already designed and running (`research/BRAIN_KNOWLEDGE_STORE.md`, wq#2442 Obsidian memory verified in `2453_verify_wq2442_obsidian_memory.md`) |
| R20 | `L1119-1121` | Mem0 as an episodic-memory layer between LiteLLM and the agents | **REJECT — re-invention** | `mem0` is importable but unused; the fleet has an episode store, `claude_memory.md`, and the continual-learning pipeline. Also collides with instinct 7: Mem0 auto-injects learned facts, and consolidation here is proposal-only |
| R21 | `L976-994` | cognitive isolation — give each role only the tools its role needs | **HAVE** | that is precisely `harness/worker-settings.json` (per-command Bash allow-list) + `--add-dir` bounding + per-lane settings. The doc's insight is the fleet's existing design |
| R22 | `L1031-1054` | 3-agent debate (Proponent / Skeptic / Moderator) to kill hallucination | **REJECT on cost** | `harness/hallucination_guard.py` already does self-consistency at n=5 with `min_agree`, logprob low-confidence spans (`:105`), and `check_claim`/`claims_from` grounding claims against **cited sources** (`:237-293`). Source-grounding beats a Skeptic's opinion, and the debate is ~7 calls per verdict |
| R23 | `L1056-1064` | match model class to hardware tier; a function-calling-tuned local model for tool calls | **HAVE** | `research/DIFFICULTY_ROUTING.md`, `models.yaml`, `research/QWEN36_LOCAL_WORKER_SERVING.md`, `research/inference_nodes.yaml` per-box `context_tokens`/`first_frame_s` |
| R24 | `L1086-1095` | `exo` to pool 5 Mac Pros' RAM and run a 70B model with no GPU | **ADAPT → T3** | genuinely uncovered (no prior art anywhere in `research/` or `kb/`), plausible on 192 GB of otherwise-idle RAM, but network-bound and gated on an unknown bridge side. Measure first. §5 T3 |
| R25 | `L1101-1103` | PydanticAI for typed agent-node outputs | **ADAPT → folded into T2** | the right mechanism for R7; T2 uses pydantic + re-ask rather than a DSPy compile step |
| R26 | `L1105-1109` | OpenHands in the sandbox for "write code until it compiles" | **REJECT** | that is what an fworker already is, with a stricter harness. Adding a second coding agent inside our coding agent buys nothing and needs the docker path guard.sh denies |
| R27 | `L1066-1084` | per-tier network allocation table (2.5GbE to the DB/inference tier, 1GbE to scrapers) | **ADAPT** | the reasoning is sound and the conclusion — bandwidth matters only for vectors/weights, not JSON — is the argument for T3's measurement. The table itself describes hardware we do not have in that configuration (`FLEET_MAP.md:20-30`) |

Tally: **10 HAVE, 6 ADAPT, 9 REJECT, 2 NEW.** Ten of 27 recommendations describe this fleet's
own running services back to it.

## 5. Which fleet problems this solves

| Fleet problem | What the doc contributes | Cross-ref |
|---|---|---|
| **"Research agents defer instead of dissecting"** — the doc's own framing at `L24-30`, and a measured fleet defect | The real payload. `research/DEFER_PILOT_LANE.md:25-31` records **D2: form failure is scored as a verdict** — `_verdict_from()` returns `rejected_aged` when a note lacks a `RECOMMENDATION:` line, and **19 of 72 researched defers (26%)** died that way. A proposal was never judged; a formatting miss killed it. The doc names the structural fix: a validated output schema whose violation triggers a **re-ask**, never a verdict (`L996-1029`) | T2; wq#1559, wq#2477 |
| **A retry that repeats the first attempt's mistake** | `harness/fworker.py:414` allows exactly one retry, and `:1466-1489` hands it event type-names plus `git diff --stat`. The doc's Reflexion loop (`L858-913`) inserts one critique call between the two attempts, which is the difference between an informed retry and a coin flip | T1; `MAX_ATTEMPTS` at `fworker.py:414` |
| **192 GB of RAM on three boxes that can only serve small models** | `research/inference_nodes.yaml` shows isis pinned to 8192-token contexts, "SMALL prompts only" (`:19`), because a single Mac Pro cannot hold more. `exo`/`llama.cpp` RPC (`L1090-1095`) is the first proposal anyone has made for using the pooled 192 GB, and nothing in `research/` or `kb/` has evaluated it | T3; `wq2076_macpro_gpu_recon.md`, wq#2444 |
| **Reclamation of a hard-killed worker is untested** | `L43` names the discipline: kill the worker, assert the scheduler reschedules. wq has the mechanism (`bin/wq:27-34`, `harness/reaper.py`); nothing proves it fires | T4 |
| **Prior-art searches miss what the world already solved** (wq#991 class) | This doc is itself the evidence for the digest step: 10 of its 27 recommendations are our own services, and one (`exo`) is a month-old miss. Reading the doc raw would have cost a K3s migration; digesting it costs four tickets | wq#991, wq#999 |

Problems the doc claims to solve that we **do not have**: event-bus fan-out (R6), GitOps
auditability (R15), tool/model coupling (R10), per-role tool sprawl (R21), episodic memory
(R20), untrusted-code isolation (R13), streaming orchestration (R12).

## 6. BUILD tickets — filed

Ordered by value. T1 and T2 are the payload; T3 is a cheap measurement that unlocks a large
capability or closes the question for good; T4 is a test.

| | ticket | lane/pri | filed as |
|---|---|---|---|
| T1 | critique step between attempt 1 and attempt 2 | build p2 | **wq#2488** |
| T2 | validated output contract with automatic re-ask, replacing regex-miss-as-verdict | build p2 | **wq#2489** |
| T3 | measure inter-Mac-Pro bandwidth before any pooled-inference adoption | pilot p3 | **wq#2490** |
| T4 | chaos test: SIGKILL a worker, assert its ticket is reclaimed | build p3 | **wq#2491** |

### T1 — Reflexion critique between the two attempts (wq#2488)
Cites `O1 L858-913 §"Configuring the Reflexion LangGraph Loop"` — specifically `critique_node`
then `rewrite_node` before the retry edge, and the `retry_count >= 3` halt.

**Problem.** `harness/fworker.py:414` sets `MAX_ATTEMPTS = 2`, so there is exactly one retry per
ticket. `write_prior_attempt_notes()` (`harness/fworker.py:1466-1489`) is what attempt 2
receives, and it contains: the block classification, up to 20 event **type/subtype** names
(`- assistant`, `- tool_result` — no content), and `git diff --stat`. Nothing states why the
attempt failed or what to do differently. The single retry we are allowed is near-blind.

**Build.** Add a `critique` step that runs once, on attempt-1 failure, before the respawn:
1. Input: the existing `prior-attempt-notes.md` inputs *plus* the final assistant text and the
   last tool error from `transcript_events` (fworker already holds them — `:1469` slices them).
2. One LLM call through the existing LiteLLM gateway on a cheap tier (this is a summarisation
   task — System 1 in the doc's own terms, `O1 L11`), producing at most 15 lines under three
   fixed headings: `WHAT FAILED`, `WHY`, `DO DIFFERENTLY`.
3. Append it to `prior-attempt-notes.md` under `## critique`. The file is already wired into
   the attempt-2 prompt, so no prompt-assembly change is needed — this is the reason to extend
   that file rather than introduce a new artifact.
4. **Fail-open**: if the call errors or times out (10 s), write nothing and keep today's
   mechanical notes. A critique failure must never cost the retry.
5. Do **not** touch `dispatcher/self_heal.py`'s release policy. Its refusal to release
   `WORK_BLOCKED` is deliberate and fail-closed (`dispatcher/self_heal.py:24-26`); T1 improves
   the retry that already happens inside one fworker run, and changing which classes retry is a
   separate decision.

**Acceptance.** A unit test drives `write_prior_attempt_notes` with a synthetic failed
transcript and asserts the three headings appear; a second asserts a raised exception from the
model call leaves the pre-existing mechanical sections intact and the function returning
normally. Cite the `prior-attempt-notes.md` produced by a real attempt-1 failure in the notes.

**Non-goal.** The doc's `rewrite_node` (`O1 L887-891`) — an LLM rewriting the code directly.
That is the worker's job; we are improving its briefing, not replacing it.

### T2 — Validated output contract with automatic re-ask (wq#2489)
Cites `O1 L996-1029 §"Advanced DSPy Signatures (The Laser-Focused Judge)"` — the `dspy.Suggest`
assertion that catches an out-of-range score, rewrites the prompt with the error, and forces a
retry — and `O1 L1101-1103` (PydanticAI) for the mechanism.

**Problem.** The fleet scores *form failures as verdicts*. `research/DEFER_PILOT_LANE.md:25-31`:
`_verdict_from()` in `minerva_defer_review.py` returns `rejected_aged` for a note with no
`RECOMMENDATION:` line, so "the research says no" and "the writer missed the template" are the
same outcome — **26% of researched defers (19/72)** died on the second. wq#1559 designed a
narrow patch (a third `rerun` return state, `DEFER_PILOT_LANE.md:91`). The doc's contribution is
the generalisation: *any* place the fleet parses a contract line out of LLM prose has this bug
latent, and a regex miss should trigger a bounded re-ask, not a verdict. The same
`RECOMMENDATION:` contract already appears in at least `pilots/a7d04e5ebe7d/build_sample.py:90`
and `ops/wq394-netdata-canary/analyze.py:104`.

**Build.** One small shared module, `harness/output_contract.py` (~80 lines), and one caller:
1. `parse_or_reask(text, model, *, max_reasks=1)` where `model` is a pydantic `BaseModel`
   subclass declaring the contract (e.g. `recommendation: Literal["ADOPT","WATCH","SKIP"]`).
2. On a `ValidationError`, issue **one** re-ask carrying the validation error text verbatim and
   nothing else — the doc's mechanism at `O1 L1023-1028`, minus the framework. Second failure
   returns a distinct `rerun` outcome, never a verdict.
3. Wire it into `_verdict_from`'s call site so a missing/invalid `RECOMMENDATION:` reaches the
   `rerun` state wq#1559 specified rather than `rejected_aged`.
4. **Do not install `dspy`.** Its distinctive value is `BootstrapFewShot` (`O1 L277-344`), which
   needs a labelled trainset and an `exact_match_metric` we do not have; adopting a prompt
   compiler also brushes instinct 7, which keeps prompt/memory mutation proposal-only. Use
   pydantic, already a dependency.

**Acceptance.** Unit tests: valid text parses with zero re-asks; malformed text re-asks once
then returns `rerun`; a `rerun` never reaches `rejected_aged`. Mirror `DEFER_PILOT_LANE.md:162`
T3's assertion. Then replay the 19 historically `rejected_aged`-on-form notes through the parser
and report how many now classify as `rerun` — that number is the ticket's whole justification,
so cite it in the done notes.

**Coordinate with wq#2477**, which is root-causing why 3 of 4 queued pilots SKIP. If that
root cause is also a contract-parse miss, T2 is its fix and the two should land together.

### T3 — Measure inter-Mac-Pro bandwidth before adopting pooled inference (wq#2490)
Cites `O1 L1086-1095 §"Distributed Inference (Solving the GPU Deficit)"` and the network
reasoning at `O1 L1074-1084`.

**Problem and the honest framing.** The doc proposes `exo` to pool the Mac Pros' RAM into one
large-model endpoint. The prize is real: three online Macs at 64 GB
(`research/inference_nodes.yaml:46,74,102`) = 192 GB, no usable GPU on any of them
(`research/wq2076_macpro_gpu_recon.md` — `compute stack: none` on all three), and isis is
currently capped at 8192-token contexts, "SMALL prompts only" (`inference_nodes.yaml:19`). No
document in `research/` or `kb/` has ever evaluated pooled inference — a genuine miss. But the
workload is network-bound (§2: 200 Gb/s fabric in the one working public example; HN reporting
network throughput as the killer), and `research/FLEET_MAP.md:30` records the Mac Pros' **bridge
side as UNKNOWN** with a router-to-router **wifi bridge** somewhere in the fleet. If two of the
three Macs are on opposite sides, the answer is no and no install should happen.

**Build — measurement only, no install.**
1. `iperf3` (or `nc` + a timed transfer if iperf3 is absent) pairwise between isis, nephthys and
   hathor. Record throughput and RTT for all three pairs. This *also answers the bridge-side
   question empirically* — a wifi-bridge hop is unmistakable in the numbers — so it removes a
   Frank dependency rather than creating one (instinct 3).
2. Measure single-node baseline first: tokens/s for the model isis serves today, so the
   comparison has a floor.
3. Decision rule, written **before** the measurement: proceed to a scoped `exo` or
   `llama.cpp --rpc` trial only if all three pairs clear ~2 Gbit/s sustained *and* sit on one
   bridge side. Otherwise close the question with the numbers and stop — that is a successful
   outcome for this ticket.
4. Read-only on all three boxes. They are **production inference nodes**; wq#2076 was blocked
   precisely for proposing to experiment on them. `iperf3` is not an experiment, but nothing
   heavier lands here without its own ticket.

**Acceptance.** A results table (3 pairs × throughput/RTT, plus the single-node tokens/s
baseline) in `research/`, a stated bridge-side conclusion per box, and an explicit
PROCEED/CLOSE verdict against the rule above. Cross-ref wq#2444 (D300 resale) — pooling is
CPU/RAM-based and does not depend on keeping those GPUs.

### T4 — Chaos test: SIGKILL a worker, assert its ticket comes back (wq#2491)
Cites `O1 L43 §"Implementation Roadmap"` item 3 — *"Randomly kill pods mid-task to ensure your
state machine accurately reschedules the job."*

**Problem.** `harness/fworker.py:2201` handles SIGTERM; SIGKILL and hard power loss are the
untested paths. The recovery mechanism exists — `bin/wq:27-34` frees a stale lease after
`WQ_STALE_MIN` (45 min) and `bin/wq:608` puts the staleness predicate on the UPDATE itself, plus
`harness/reaper.py` — but no test proves a SIGKILLed worker's ticket is actually reclaimed
rather than stranded `claimed` for 45 minutes. This is the one item in the doc's roadmap that
needs no Kubernetes.

**Build.** A test against a **scratch wq database**, never the live queue:
1. Seed a throwaway ticket, claim it as a fake holder PID, `kill -9` that PID.
2. With `WQ_STALE_MIN` overridden to a few seconds, assert the row returns to claimable and that
   the reclaim is logged loudly (`bin/wq:34` says it should be — assert the log line exists).
3. Assert the negative too: a *live* holder is never reclaimed no matter how quiet
   (`bin/wq:29`). That is the property most likely to regress and the one that would hurt.
4. Bound the blast radius explicitly: `WQ_DB` pointed at a temp file, no `--status` writes
   against the real queue, and no `kill` of any PID the test did not itself spawn.

**Acceptance.** Test passes; paste the reclaim log line. The negative case is the important
assertion — cite it separately.

## 7. What we can STOP doing

1. **Stop evaluating K3s proposals line by line.** Fourth doc in this family to assume
   Kubernetes; ~600 of 1129 lines here are unreachable. Skip on sight. When a doc's
   recommendation begins `kubectl`, `helm install`, or `apiVersion:`, the digest step is to
   name the goal and check whether we already meet it another way — which we did, eight times
   above (R2, R6, R13, R15, R16, R18, R19, R26).
2. **Stop treating "adopt LangGraph" as an open decision.** We adopted it over a month ago:
   `autonomy/lumen_scout_graph.py`, langgraph 1.2.5, plus a 7-node researcher graph
   (`DSH_EVAL.md:12,55`). The only live LangGraph question is the checkpointer, already
   open as wq#2038.
3. **Do not install `dspy`.** The signature/assertion *pattern* is worth taking (T2); the
   framework brings a prompt compiler that needs a labelled trainset we do not have and that
   auto-rewrites prompts — instinct 7 territory. Pydantic plus one re-ask gets the whole benefit
   in ~80 lines.
4. **Do not build a Proponent/Skeptic/Moderator debate** (`O1 L1031-1054`). Seven LLM calls to
   produce an opinion we can beat with source-grounding: `harness/hallucination_guard.py`
   already runs self-consistency at n=5, flags low-confidence spans from logprobs (`:105`), and
   `check_claim`/`claims_from` (`:237-293`) check assertions against **cited sources**. A
   Skeptic argues; a citation check verifies.
5. **Do not build a human-approval gate** (`O1 L602-667`). Instinct 3 retired Frank-gates on
   2026-08-22. Second doc in this family to propose one (`DIGEST_Fleet_searchability_md.md:225`);
   shipping the mechanism would make adding gates cheap, which is backwards.
6. **Never build the unrestricted restart/power-cycle MCP** (`O1 L227-235`, `L548-600`). An
   LLM-callable `systemctl restart <anything>` plus `ipmitool power reset <anything>` is one
   hallucinated argument away from power-cycling Thor — the fleet's only hard rule. The
   allow-list of (node, service) pairs at `harness/fleet-exec-policy.yaml:89` is the shape; the
   doc's free-text parameter is the anti-shape.
7. **Stop re-proposing episodic-memory products.** `mem0` is already importable here and
   deliberately unused: the fleet has an episode store, `claude_memory.md`, the
   continual-learning pipeline and the wq#2442 Obsidian store (verified,
   `2453_verify_wq2442_obsidian_memory.md`). Mem0's auto-injection of learned facts also
   conflicts with instinct 7's proposal-only rule.
8. **Do not reach for DinD or OpenHands** (`O1 L76-117`, `L1105-1109`). `harness/guard.sh` denies
   `docker` except `ps`/`logs`, the worktree + allow-list sandbox is stricter than the doc's
   container (whose own `network_mode="bridge"` contradicts its "NO network access" comment), and
   an fworker already is the "write code, run it, read the error, retry" agent.
9. **Stop copying code out of Frank's research docs.** Every block in this one is
   markdown-escaped and will not run; two contain credentials inline; one contradicts its own
   comment. Fourth doc, fourth time. Retype from upstream.
10. **Stop sizing anything from these docs' hardware claims.** This one asserts 5 Mac Pros and
    320 GB (`L1079`, `L1094`); reality is 3 online and 192 GB. It also assumes an RTX 4060 for
    1065 lines before denying it exists (`L1068`). Check `research/inference_nodes.yaml` and
    `lumen_state.py current <node>` instead.

## 8. The one-line version for Frank

The genuinely new idea in this doc is the small one: your judge agents don't defer because the
prompt is wrong, they defer because **a formatting miss is scored as a rejection** — 26% of our
researched defers died that way (`DEFER_PILOT_LANE.md:25-31`) — and the fix is a validated
schema that re-asks once, which is ~80 lines of pydantic, not the DSPy compiler (T2). Second:
our one retry per ticket currently gets a list of event *type names* and a `diff --stat`, so
adding one critique call between the attempts is nearly free (T1). Third, and the real miss
nobody here had caught: three Mac Pros hold **192 GB of idle RAM with no usable GPU**, and
pooled CPU inference (`exo`, `llama.cpp --rpc`) has never been evaluated — but it lives or dies
on inter-box bandwidth and we don't even know which side of the wifi bridge those boxes are on,
so T3 measures before it installs. Everything else: ~600 lines configure a Kubernetes we don't
run, ten recommendations are services we already operate (LangGraph, LiteLLM, Tailscale, the
self-heal loop, per-role tool allow-lists, the MCP layer, streaming via despag), and two
sections must never be built — an LLM-callable `systemctl restart` + IPMI power-reset that could
hard-reset Thor, and another human-approval gate.

## 9. Harness note (sibling digests wq#2466-2469)

Confirms `DIGEST_Fleet_searchability_md.md:250-267` and adds one:

- `/home/lumen/scratch/` is outside a research worker's `--add-dir` set: `wc`, `grep`, `ls`
  against it are refused. **The `Read` tool reaches it** — read in bounded `Read(offset, limit)`
  chunks. Not retryable by synonym (wq#1107).
- **`guard.sh` matches the string `docker` anywhere in a Bash command** (ISSUE-LOG #653). It
  fired again here on a pure Python import probe whose *module list* contained `'docker'` — the
  word never appeared as a command. Third digest in the family to burn an attempt on this.
  Fix: drop the token, don't rephrase the command.
- `Write` into `/home/lumen/foreman` is denied (primary tree, wq#1719/wq#900). Write to
  `<worktree>/research/` and land with `lumen-pr`; the path in the ticket is where the file ends
  up after the merge.
- **New this run:** the per-command Bash allow-list checks **every segment** of a `;`/`&&` chain,
  so `ls DIR | head; echo ---; wc -l FILE` fails as a whole even when `ls` alone is allowed —
  and `which a b c 2>&1 | head` failed only because it was chained behind a `cd`. Issue one
  shape per call rather than batching cheap commands into a chain; two parallel Bash calls cost
  the same and neither poisons the other.
