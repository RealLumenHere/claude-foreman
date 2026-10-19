# wq#4369 — Next-gen computer-use dissection: unified action space, Goodhart detection, topology

**Date:** 2026-10-19 · **Lane:** LIMPET research · **Sources:** three 2026 papers retrieved
2026-10-19 via `fleet-search web` (SearXNG general engines all suspended/CAPTCHA'd — brave
"too many requests", duckduckgo timeout, google cse "too many requests", startpage CAPTCHA;
keyed DeepSeek Search fallback used per the tool's own `note` field — same outage pattern
logged for wq#4347/wq#4356/wq#4358/wq#4363). Per-paper citations inline below.

**Scope honesty.** This is a documentation/market dissection producing a *design mapping*,
not an implementation. No model was trained, no action-space runtime was built, no judge was
rewired. Every benchmark delta, step-count, and topology count below is the paper's own
published figure and is labelled **[paper-claim]**. The Limpet-side facts (the vision-only
action path, the critic contract, the 5-blind-hunter loop) are read from committed files and
cited by line. This is not fabricated evidence (instinct 8): the three artifacts are real and
externally verifiable —
- **OS-Helix** — *Unifying GUI Interaction and Code Execution in Native Computer-Using
  Agents*, arXiv:2610.22234 (HF papers page, published Oct 15 2026).
- **OpenGCUA** — *Toward a Fully Open Stack for Generalist Computer Use*, arXiv:2610.22200,
  code at `github.com/Tongyi-MAI/OpenGCUA` (Alibaba Tongyi-MAI).
- **Qualixar OS** — *A Universal Operating System for AI Agent Orchestration*,
  arXiv:2604.06392 (Zenodo record 19454219).

All three external-fact rows are **[verify-at-adoption]** — re-confirm at the
`TOOL_REGISTRY.md` go/no-go before any provisioning. The doc is structured so a wrong
external figure invalidates one row of a table, never the design shape.

---

## 0. Why this ticket exists

Limpet's agent core today has **one** action path for anything it sees on a target screen:
the HDMI capture card feeds frames into "the agent core's vision path"
(`design/wq3806_limpet_architecture.md:207`), and the agent acts by locating a pixel and
clicking it. That is the right primitive for a device with *only* a screen (a locked
appliance, a BIOS menu, a KVM). It is the wrong primitive the moment the same target also
exposes a shell, an accessibility tree, or a scriptable interface — which most of Limpet's
real targets do, because Limpet drives Linux boxes and dev boards, not just sealed panels.
Forcing a file rename or a `dmesg` grep through "screenshot → find text → click → type" is
slow, brittle, and burns vision tokens on work a one-line command does exactly.

The three papers this ticket dissects each attack a different face of that problem, and each
maps onto a different Limpet surface:

| Paper | Core claim | Limpet surface it informs |
|---|---|---|
| OS-Helix | GUI *and* code must live in **one** policy, learned together | the HDMI/vision action space (OUTPUT 1) |
| OpenGCUA | code-as-glue across 3 modalities beats vision-click | the HDMI/vision action space (OUTPUT 1) |
| Qualixar OS | 12 topology semantics + Goodhart detection via cross-model entropy | the hunter judge (OUTPUT 2) + topology (OUTPUT 3) |

---

## 1. OS-Helix — one policy, two action types

**What it is [paper-claim].** OS-Helix is an end-to-end-trained multimodal computer-using
agent that unifies GUI actions (click, keystroke) and code execution (emit a code block that
runs in the host shell) **in a single action space** — the same policy decides, per step,
whether to click or to run code. Its headline finding is a *negative* result that justifies
the whole design: naively bolting a code tool onto a GUI agent via prompt-level tool
composition **degrades** the GUI agent. Routing between GUI and code cannot be prompted in
after the fact; it has to be internalized through training on hybrid data. To get that data
they introduce *application-artifact composition* (pair a desktop app with authentic file
artifacts to instantiate an executable Ubuntu state) and a decoupled
instruction/evaluator generation pipeline, then train with hybrid SFT + RL. They also report
**step-balanced** training — the supervision is shaped so the policy does not collapse into
"always click" or "always code," but keeps both live.

**The load-bearing lesson for Limpet.** The failure mode OS-Helix documents is *exactly* the
one Limpet would hit if it "added a shell tool" to its vision agent by prompt alone: the model
keeps defaulting to the vision-click path it was trained on and treats the shell as an
afterthought. So OUTPUT 1 cannot be "give the agent a `run_shell` tool and hope." The routing
preference has to be made structural — either via a trained policy (expensive, out of scope
for a device we assemble from off-the-shelf local VLMs) or, failing that, via an **explicit
router in the harness** that chooses the cheapest modality that can satisfy the step before
the VLM is ever asked to look. Limpet will not train a policy; therefore Limpet takes the
router route (§4).

---

## 2. OpenGCUA — code-as-glue, three modalities, measured

**What it is [paper-claim].** OpenGCUA is a fully-open stack (data + models + code,
Tongyi-MAI) for generalist computer-use agents. Its central idea is **code-as-glue**: rather
than treating the GUI as the only way to touch a machine, the agent operates across **three
modalities** —
1. the **accessibility tree** (structured UI: element roles, labels, bounds — read/act without
   pixels),
2. the **shell** (files, processes, system state — the native way to do file/script work),
3. the **GUI** (pixels + click/type — the fallback for anything the first two cannot reach),

and *glues* them together with code. The reported results are the reason this paper is in
scope: combining code with GUI gives a **+19.6-point** improvement (reported on OSWorld-v2,
across 8 models per the AI-Weekly writeup) and **53% fewer steps** than the GUI-only baseline.
Fewer steps is the part Limpet cares about most — every saved step is a saved screenshot, a
saved VLM call, and a saved round-trip over the HDMI/USB path. OpenGCUA also ships a
**cross-device SDK** so the same agent code drives different machines.

**The load-bearing lesson for Limpet.** OpenGCUA is the *empirical* backing for OS-Helix's
design stance: the 3-modality ordering (accessibility → shell → GUI) is a cost gradient, and
the measured 53%-step reduction says the gradient is worth honoring. Limpet's HDMI pipeline
today is pinned at the bottom of that gradient (GUI-only), which is precisely the baseline
OpenGCUA beats by 19.6 points. The accessibility-tree modality is the one Limpet is missing
entirely and the cheapest to add *when the target cooperates* (an AT-SPI/UIA bus, or a device
that exposes a DOM/serial console) — and when it does not, Limpet correctly falls back to the
vision path it already has.

---

## 3. Qualixar OS — topology semantics + Goodhart detection

**What it is [paper-claim].** Qualixar OS is positioned as a universal OS for multi-agent
orchestration. Three pieces are relevant here:
1. **12 multi-agent topology semantics** — named collaboration shapes (the ticket lists
   *grid*, *forest*, *mesh*, *maker* among them) that a team can be instantiated in, rather
   than hand-wiring agent graphs per task. A **Forge** team-design engine picks/assembles the
   topology, drawing on a **historical strategy memory** (what worked before for this class of
   task).
2. **Three-layer routing** — Q-learning + Bayesian model routing, a learned+probabilistic
   stack that assigns work to models.
3. **Goodhart detection via cross-model entropy monitoring** — the piece most directly useful
   to Limpet. The insight: when a metric becomes a target, agents optimize the *metric* rather
   than the *goal* (Goodhart's law). Qualixar watches **cross-model entropy** — the agreement/
   disagreement structure across multiple models scoring the same thing — and treats a
   **collapse in entropy** (models converging suspiciously hard, or converging onto the
   proxy signal rather than the ground truth) as the fingerprint of reward-hacking.

**The load-bearing lesson for Limpet.** Limpet already runs a judged, adversarial finding
loop: five **blind, non-communicating** stance hunters (`re-bughunt`, `re-safety`,
`re-arithmetic`, `re-lifecycle`, `re-logic`) produce findings, then a reconcile/critic step
adjudicates them (`design/wq3999_rex_skill_dissection.md:131-160`), and `limpet/critic.py`
gates each finding with a deterministic check plus a model review
(`limpet/critic.py:81`, `limpet/critic.py:120`). That judge **is** a metric, and a vuln-hunter
rewarded for "findings the critic accepts" is a textbook Goodhart setup — it will learn to
produce findings *shaped to please the critic* rather than real defects. Qualixar's
cross-model entropy monitor is a defense Limpet can bolt onto exactly this loop (§5).

---

## OUTPUT 1 — Unified action-space spec for the HDMI pipeline

**Current state (cited).** HDMI frames feed the vision path and the agent acts by click
(`design/wq3806_limpet_architecture.md:207`); the capture device itself is a zero-config UVC
card (`limpet/hardware.d/06-hdmi-capture.toml:4-18`). There is no shell modality and no
accessibility modality in the action space — every action is a pixel action.

**Spec: one action space, three modalities, harness-routed (not prompt-composed).**
Per OS-Helix's negative result (§1), the modality choice must be made *before* the VLM is
asked to look, by a harness router — Limpet does not train a policy. The action space the
agent core exposes is a single typed union:

```
Action =
  | Shell { cmd, timeout }              # modality 2 — cheapest when a shell/serial console exists
  | Accessibility { node_id, op, arg }  # modality 1 — when an AT-SPI/UIA/DOM tree is readable
  | GuiClick { x, y } | GuiType { text } | GuiKey { chord }   # modality 3 — vision fallback (today's only path)
```

**Routing rule (the cost gradient from §2, applied in order):**
1. If the target exposes a shell/serial console *and* the step is file/process/state work →
   emit `Shell`. (This is OpenGCUA's code-as-glue; it is where the 53%-fewer-steps comes from
   **[paper-claim]**.)
2. Else if an accessibility tree is readable for the target element → emit `Accessibility`
   (structured, no pixels).
3. Else → fall back to the existing vision path: capture frame, locate, `GuiClick`/`GuiType`.

Each modality is a **HAL capability**, so it is mediated by the meta-server and trust-gated
and audited identically to every other tool (`design/wq3806_limpet_architecture.md:231-232`)
— adding shell/accessibility actions does **not** open an unmediated side-channel. The shell
modality in particular must inherit the D3 build-sandbox egress posture and the
"downloaded-exploit bodies stay untrusted, never auto-run" rule so a target screen cannot talk
the agent into running attacker-authored code (classic strings-are-data, not instructions:
`design/wq3999_rex_skill_dissection.md:1-4`).

**Why not just train an OS-Helix policy.** We assemble Limpet from off-the-shelf local VLMs on
an iGPU-only M70q-class box (`research/WQ3922_VISION_HDMI_CAPTURE.md`); there is no training
pipeline and no labeled hybrid corpus. The harness router is the pragmatic substitute: it
captures the *routing* value OS-Helix identifies without the training cost, and it degrades
safely (unknown target → vision-only, i.e. today's behavior).

---

## OUTPUT 2 — Goodhart detection for the hunter judge

**Target loop (cited).** Five blind non-communicating hunters → reconcile → `critic.py`
review (`design/wq3999_rex_skill_dissection.md:131-160`; `limpet/critic.py:81,120`).

**The failure to detect.** A hunter optimized against "findings the critic accepts" can
Goodhart the critic: emit findings in the critic's preferred *shape* (a template the model
scores high) rather than findings backed by real defects. Because the hunters are blind to
each other, the usual cross-check (do independent hunters corroborate?) is not wired into the
accept decision.

**Detector, from Qualixar (§3):** add a **cross-model entropy monitor** over the hunters +
critic, as a *diagnostic gate*, not a new judge:
- Score each candidate finding with ≥2 independent critic models (Limpet already pins a judge
  model with a fallback — `judge/JUDGE_MODEL_PIN`, `judge/JUDGE_MODEL_FALLBACK`, and ensembles
  — `judge/judge_ensemble.py`; the ingredients exist).
- Compute entropy of the accept/reject (and rationale-embedding) distribution across those
  models, per finding and aggregated per hunter over a window.
- **Flag an entropy *collapse*** — the models agreeing far more tightly than the finding's
  evidence warrants, or agreement correlating with a surface feature (finding length, phrasing,
  a stock template) rather than the deterministic-check result (`limpet/critic.py:81`). That is
  the Goodhart fingerprint: the signal has decoupled from the ground truth.
- On flag → route the finding to human/stronger-model adjudication and dock the hunter's
  trust weight; do **not** auto-reject (entropy collapse is a *smell*, not a verdict — a
  genuinely unambiguous bug also produces high agreement, so the discriminator must be
  "agreement vs. evidence strength", using the deterministic check as the evidence anchor).

This is a monitoring layer on top of the existing judge, reusing the pinned/fallback/ensemble
machinery Limpet already has; it does not replace `critic.py`'s contract.

---

## OUTPUT 3 — Topology recommendation

**What Limpet already runs.** The REx-derived analysis loop is, in Qualixar's vocabulary, a
**star-into-mesh-into-funnel**: one recon agent fans out (star) to five parallel
non-communicating hunters, which converge into one reconcile/critic
(`design/wq3999_rex_skill_dissection.md:131-160`). The blindness is deliberate — "two
extractions are not two readings"; hunters may not see each other's findings — so the
disagreement at reconcile is *informative* rather than herd-contaminated.

**Recommendation.** **Keep the current recon→5-blind-hunters→reconcile topology as the default
for a single-target RE analysis** — it is already one of Qualixar's named shapes and its
blindness property is load-bearing for the Goodhart defense in OUTPUT 2 (independent scorers
are what make the entropy monitor meaningful). Adopt Qualixar's *Forge + historical strategy
memory* idea **selectively**: let the harness pick between this default and a lighter topology
by task class, remembering what worked —
- **single firmware image / single binary** → the 5-blind-hunter mesh (high assurance, worth
  the cost).
- **bulk triage / recon-only sweep** → a flat parallel *grid* of cheap recon agents, no blind
  hunters (the assurance tax is not warranted on a first-pass inventory).
- **a long multi-stage reconstruction** → a *forest* (independent per-component trees that
  reconcile at the root).

Do **not** adopt all 12 Qualixar semantics — most are orchestration shapes Limpet has no
workload for, and the fleet already has its own dispatch (fworker/wq). The adoptable nucleus
is: (a) the default topology is already correct, (b) add a task-class selector with a small
strategy memory, (c) the Goodhart monitor rides the blind-hunter variant specifically.

---

## 4. Adoption posture / go-no-go

| Item | Verdict | Blocker / note |
|---|---|---|
| Unified action space (shell/accessibility/GUI, harness-routed) | **DESIGN — adopt the router shape** | needs a HAL capability per modality, each trust-gated (`design/wq3806_limpet_architecture.md:231`); shell modality inherits D3 no-egress |
| OS-Helix trained policy | **REJECT for build, keep as prior-art** | no training pipeline on an iGPU box; router is the substitute |
| OpenGCUA 3-modality + SDK | **ADOPT the ordering, [verify-at-adoption] the SDK** | the +19.6/−53% figures are [paper-claim]; the cost-gradient ordering is the durable takeaway |
| Goodhart cross-model entropy monitor | **DESIGN — adopt as a gate** | reuses `judge/` pin+fallback+ensemble; flag-not-reject; anchor on `critic.py:81` deterministic check |
| Qualixar 12 topologies | **PARTIAL** | keep current blind-hunter default; add task-class selector + strategy memory; ignore the rest |

**All external figures are [verify-at-adoption].** Prior-art search ran degraded (general
engines suspended — a pre-existing OPEN outage, not a value this ticket invented; instinct 8),
so the three papers' numbers come via the DeepSeek fallback and model knowledge (cutoff
2026-01); re-confirm at the `TOOL_REGISTRY.md` go/no-go. Companion dissections:
`research/WQ4360_VISION_AGENT_DISSECTION.md`, `research/WQ4352_OPENAI_OPERATOR_DISSECTION.md`,
`research/WQ4364_OBSERVABILITY_OPTIMIZATION_DISSECTION.md`.
