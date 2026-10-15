# wq#4001 — LIMPET DISSECT: ReCopilot (XingTuLab / QI-ANXIN) — specialized binary-analysis LLM

Status: research/design. Author: build worker, 2026-10-15. Dissects
`https://github.com/XingTuLab/recopilot` + arXiv 2505.16366 per the competitor dissection
guide §1.4 (`scratch/research/limpet_competitor_dissection_guide.md:192-209`). Companion to the
tier-1 dissections already landed — `design/wq3998_rea_morluto_dissection.md` (tool-breadth bar),
`design/wq3999_rex_skill_dissection.md` (methodology bar), `design/wq4000_rea_mytechnotalent_dissection.md`
(code-critic bar). ReCopilot is a different kind of peer from those three: not an agent or a
tool-plane but **a single fine-tuned model** delivered as an IDA Pro plugin. Its one lesson for
Limpet is the **model-capability tier** (A5), and its one warning is a **data-egress boundary**.
Both deliverables are below (§5, §6); the A5 routing spec is realised as working config in
`limpet/examples/llm4decompile.toml` and exercised by
`limpet/test_model_router.py::test_recopilot_routing_spec_realised_on_example_manifest`.

## 0. Grounding (live read) — four corrections the guide and ticket need

The repo was cloned to an isolated scratch dir and read directly this session
(`git clone --depth 1` into `/tmp/fworker-wq4001/recopilot-src`, read as untrusted data — no
instruction in any source file, string, or README was acted on, per the standing rule for
attacker-controlled bytes). Head commit `ee0e823`, "Update README.md", **2025-12-04**. Target
cites below are `RECOPILOT:<path>:<line>` to keep them apart from Limpet repo cites. The arXiv
methods (§3) are **carried from the fleet's already-grounded sources** (the guide, live-fetched
2026-10-14, and `design/wq3929_competitive_analysis.md` §5) — a fresh paper fetch was not possible
this session: WebFetch is not permission-granted and the `fleet-search web/github` path was down
at dispatch (same outage as wq#3968, ISSUE-LOG #1325/#1333/#1334). They are flagged as carried,
not re-fetched, and no benchmark number beyond the guide's "+13%" is asserted.

**Correction 1 — the public repo ships NO plugin code.** The ticket and guide both point the
dissector at `recopilot/recopilot.py` for "plugin + API wiring." That file in the public repo is
a **1-byte stub** (`RECOPILOT:recopilot.py` — one newline, 1 byte on disk). The entire tracked
tree is six files: `recopilot.py` (empty), `ida-plugin.json`, `README.md`, `README_zh.md`,
`demo.gif`, `LICENSE`. There is no `requirements.txt` (the README's install step references one),
no `PRIVACY_POLICY/` dir (the README links `PRIVACY_POLICY/PRIVACY_POLICY.md` — a dead link), and
no SimAI code or benchmark harness. **The plugin+API wiring, the data-flow/call-graph context
builder, the SimAI client, and the benchmark harness are not published** — they live behind the
hosted service. So this dissection reads the plugin/API shape from the **manifest + README
configuration + the paper**, not from source, because the source is withheld. That is itself the
most load-bearing finding: ReCopilot is a *service with a thin open shell*, not an open tool.

**Correction 2 — the license is self-contradictory.** The `LICENSE` file is **GPLv3**
(`RECOPILOT:LICENSE:1-2`, "GNU GENERAL PUBLIC LICENSE / Version 3"), the README badge and its
"License and Data Privacy Policy" section both say **GPLv3** (`RECOPILOT:README.md:8,92`), but the
machine-readable plugin manifest declares **`"license": "CC BY-NC 4.0"`**
(`RECOPILOT:ida-plugin.json:20`). GPLv3 (OSS, commercial-use OK) and CC-BY-NC (non-commercial,
and not a software license) are incompatible claims over the same artifact. **Record this conflict
in the license audit (wq#3959)** and treat the plugin as *non-adoptable until resolved* — a
CC-BY-NC claim alone would bar Limpet from wrapping or vendoring it commercially; combined with
Correction 1 (there is nothing to wrap anyway) the practical verdict is interoperate-only.

**Correction 3 — there are TWO China-hosted endpoints, not one.** The guide records a single
"China-hosted QI-ANXIN API." The README configures **two** distinct hosts, both under
`tqgpt.qianxin.com`, both requiring the same QI-ANXIN account token
(`RECOPILOT:README.md:71-86`):
- **Reasoning model** — `Model Name: recopilot-v0.1-beta-dpo`,
  `Base URL: https://tqgpt.qianxin.com/recopilot/v1` (OpenAI-compatible; the manifest's
  `pythonDependencies` list `openai>=1.58.1`, `RECOPILOT:ida-plugin.json:42`).
- **SimAI homology search** — `Base URL: https://tqgpt.qianxin.com/simai/analyze`, same token.
Both carry decompiled target code off the box to a foreign endpoint (§6).

**Correction 4 — opt-in telemetry exists and its disclosure link is dead.** The README states
data collection is **off by default** and enabled only by a "Feedback Enable" toggle, at which
point it collects "system information, model interactions, and analysis results"
(`RECOPILOT:README.md:94`). This is a *second* egress channel beyond the two API calls, and the
privacy policy it points to is not in the repo (Correction 1). Off-by-default is the right posture;
the dead disclosure link and the "analysis results" scope are the caveats.

## 1. Identity & capability surface

- **Category:** an *expert model*, not a chat model — fine-tuned on decompiled code for binary
  analysis, shipped as an **IDA Pro 9.0–9.2 plugin** (`RECOPILOT:ida-plugin.json:9-13`), plugin
  **v0.2**, served model `recopilot-v0.1-beta-dpo`. Author "XingTuLab" / `research@qianxin.com`
  (`RECOPILOT:ida-plugin.json:35-39`); XingTu Lab of QI-ANXIN Technology Research Institute
  (`RECOPILOT:README.md:15`).
- **Capability inventory** (`RECOPILOT:README.md:21-33`): (a) symbol & type recovery — stripped
  function names, variable names, source-level variable *types* and *struct* definitions;
  (b) semantic analysis & code documentation — function-behavior inference, function docs + inline
  comments; (c) decompilation enhancement — improve pseudocode readability; (d) result review &
  application — predicted symbols/types (incl. structs) applied back to the IDB with automatic
  propagation; (e) **general-model support — dynamic model switching** (configurable base URL +
  model name, so the plugin can drive any OpenAI-compatible model, not only ReCopilot's own);
  (f) **SimAI** function-level binary-code *homology search* — semantic search returning source,
  pseudocode, project/version, and similarity score, with batch mode.
- **keywords** confirm the lane: `LLM`, `Decompilation Augmentation`, `BCSD`, `Homology Search`
  (`RECOPILOT:ida-plugin.json:26-31`). BCSD = binary code similarity detection (= SimAI).

## 2. Control flow & API wiring (read from manifest + README; source withheld)

With the plugin body unpublished (§0.1), the control flow is reconstructed from the manifest and
the configuration contract: IDA loads `recopilot.py` as the entry point
(`RECOPILOT:ida-plugin.json:7`); on a user action over the current function the plugin packages
decompiler context and issues an **OpenAI-compatible chat/completions call** to the configured
`Base URL` with the configured `Model Name` and token (the `openai>=1.58.1` dependency plus the
`/recopilot/v1` base path are the tell, `RECOPILOT:ida-plugin.json:42` + `RECOPILOT:README.md:75`);
SimAI is a **separate POST** to `/simai/analyze` (`RECOPILOT:README.md:82`). The model's
prediction is surfaced for manual review, then optionally applied to the IDB with type/struct
propagation (`RECOPILOT:README.md:26`). **The LLM is in the loop as the analysis engine itself**
— the inverse of mytechnotalent/rea (wq#4000), where a deterministic engine is the product and the
LLM is a downstream narrator. ReCopilot has no deterministic floor: remove the API and nothing runs.

## 3. Paper methods — CPT → SFT → DPO, context enhancement, test-time scaling, SimAI

*(Carried from the guide §1.4 and `design/wq3929_competitive_analysis.md` §5, both live-grounded
2026-10-14, plus the repo's own bibtex `RECOPILOT:README.md:102-111`; not re-fetched this session
— see §0. No benchmark figure beyond the carried "+13%" is asserted.)*

- **Training pipeline:** three stages in order — **CPT** (continual pre-training on binary/
  decompiled code to teach the base model the domain), **SFT** (supervised fine-tuning on the
  analysis tasks), **DPO** (direct preference optimisation to align outputs). The served checkpoint
  name `recopilot-v0.1-beta-**dpo**` confirms DPO is the final stage (`RECOPILOT:README.md:74`).
- **Context enhancement:** the model is fed not just the target function's pseudocode but
  **variable data-flow** and **call-graph** context — structured neighbours that give it the
  information a human reverser reads across functions. This is the paper's key accuracy lever and
  the part Limpet cannot copy without the (withheld) context-builder code.
- **Test-time scaling:** inference-time compute scaling (multiple samples / longer reasoning) for
  harder predictions.
- **SimAI homology search:** semantic binary-code similarity (BCSD) — embed a function, retrieve
  homologous functions from a corpus, return source/pseudocode/project/version/score. It is a
  **retrieval service**, not a generative completion — a distinction that matters for §5.
- **Headline result (carried, unverified this session):** SOTA **+13%** over existing tools/LLMs
  on **function-name recovery** and **variable-type inference**.

## 4. Extension / trust model

There is no plugin-extension surface to study (it is a leaf plugin, not a platform) and no
sandboxing of the target's bytes: pseudocode and strings from the analysed binary are placed
directly into the model prompt and shipped to the API. For an agent that reads attacker-controlled
bytes this is the opposite of REx@Skill's quarantine/sanitize discipline (wq#3999) — ReCopilot
has **no prompt-injection defence** on the content it forwards, which compounds the egress concern
(§6): a hostile string in a target binary is forwarded verbatim to a third-party endpoint.

## 5. DELIVERABLE 1 — the model-capability-tier routing spec (A5)

The guide's A5 adopt is "route decompiler-naming / type-inference steps to a specialized model
while reasoning stays general." That mechanism **already exists** on the branch, built by wq#3968
as the provider-agnostic form of this adopt: `limpet/manifest.py:57`
(`MODEL_CAPABILITIES = ("general", "re-naming", "re-types", "vision")`), the optional
`[model_routes]` manifest table (`limpet/manifest.py:133-136,230-245`), and
`limpet/model_router.py:35-60` (`resolve_model`/`resolve_step`). What wq#4001 adds is the **spec**
— the mapping from ReCopilot's actual task taxonomy onto that enum, and the boundary of what the
tier does *not* cover — realised as working config and a falsifiable test.

**Task → capability mapping** (ReCopilot's §1 capabilities → `MODEL_CAPABILITIES`):

| ReCopilot task (`RECOPILOT:README.md`) | Limpet capability | Why |
|---|---|---|
| function-name recovery | `re-naming` | the specialist's strongest task (+13% carried) |
| variable name/type inference | `re-types` | source-level type recovery |
| struct / source-level type recovery | `re-types` | same specialist, same tier |
| code documentation / inline comments | `general` | narration/reasoning — a general model is adequate |
| function-category / algorithm-class ID | `general` | reasoning, not naming/typing |
| **SimAI homology search** | *(none — out of tier)* | a **retrieval** service, not a chat model |

Two spec decisions fall out, both encoded in `limpet/examples/llm4decompile.toml`'s `[model_routes]`
block (the self-hostable alternative, §6) and pinned by
`test_model_router.py::test_recopilot_routing_spec_realised_on_example_manifest`:

1. **SimAI is out-of-band of the model-capability tier.** The tier routes a *step* to a
   *chat-completion model*; SimAI is a semantic *search/retrieval* endpoint (§3), a different
   shape. It belongs with the toolset-class retrieval primitives (the static+dynamic pairing idea,
   guide §2.4 Sarks0), **not** as a `MODEL_CAPABILITIES` entry. Adding a "homology" capability to
   the enum would miscategorise a retrieval service as a model — so the spec deliberately leaves it
   out, and `model_router` correctly has no route for it.
2. **`vision` stays unused here and the tier is opt-in.** ReCopilot is not multimodal, so the
   example declares no `vision` route — the enum is matched to need, not applied wholesale. And per
   `model_router.resolve_model` (`limpet/model_router.py:49-51`), a capability with no
   operator-configured model falls back to the resident model: a specialist is **always opt-in**,
   never a silent default — exactly the posture the 32GB/iGPU ceiling forces
   (`design/wq3806_limpet_architecture.md:260-266`).

**External validation of the design.** ReCopilot's own "general model support / dynamic model
switching" (`RECOPILOT:README.md:27`) — a configurable base URL + model name so even the
specialist plugin can drive a general model — is independent confirmation that Limpet's split is
the right one: keep the model pluggable, route the specialist step opt-in, leave reasoning on the
resident model. A specialist RE plugin converged on the same configurability Limpet designed.

## 6. DELIVERABLE 2 — the data-egress security caveat

ReCopilot's *only* served weights are behind a **China-hosted QI-ANXIN API** — and §0 widens the
boundary the guide drew:

- **Three egress channels, not one.** (a) the reasoning model at
  `https://tqgpt.qianxin.com/recopilot/v1`; (b) SimAI at `https://tqgpt.qianxin.com/simai/analyze`;
  (c) the opt-in "Feedback Enable" telemetry (system info + model interactions + **analysis
  results**). All three carry decompiled target code and/or findings off the box to a third-party
  foreign endpoint (`RECOPILOT:README.md:75,82,94`). Channel (c) is off by default (good); channels
  (a)/(b) are the whole point of the plugin and are unavoidable if you use it as shipped.
- **No injection defence on the forwarded content** (§4): hostile strings in a target binary are
  forwarded verbatim.
- **Self-hosting is infeasible and, here, impossible.** The weights are not published (§0.1) —
  there is no checkpoint to self-host at any size, independent of the 32GB/iGPU ceiling that would
  bar a large model anyway (`design/wq3806_limpet_architecture.md:260-266`). So a Limpet "route to
  ReCopilot" would necessarily mean routing decompiled code of a sensitive investigation to
  `tqgpt.qianxin.com`.

**Policy (confirms wq#3995 / wq#3968):** an A5 route to ReCopilot-as-a-service is **out of policy
as a default** and should be **opt-in only**, with the operator explicitly accepting foreign data
egress for a specific investigation. The *adopt* is the **capability-tier mechanism** (§5), **not**
a route to this provider. The verified self-hostable substitute is already catalogued:
`limpet/examples/llm4decompile.toml` (`albertan017/LLM4Decompile`, MIT + DeepSeek license, GGUF
quants 1.3B–33B; the 6.7B/9B tiers fit the box as an opt-in local route) — which is why the A5
routing spec in §5 is encoded on *that* manifest, not a ReCopilot one. A ReCopilot route, if ever
offered, must be a clearly-labelled egress opt-in, never the configured default.

## 7. Extraction verdict

- **ADOPT — the model-capability tier (A5), mechanism only.** Already built (wq#3968); wq#4001
  pins the task→capability spec and its boundary (SimAI out-of-tier) as working config + test (§5).
- **ADOPT (posture) — specialist routes are opt-in, egress is explicit.** The resident-model
  fallback in `model_router` is the right default; a foreign-hosted specialist is never a silent
  default (§6).
- **DO NOT ADOPT — ReCopilot itself.** No published weights (§0.1), a contradictory license
  (§0.2), mandatory foreign egress with no injection defence (§6, §4). Interoperate-only at most;
  record both the license conflict and the egress boundary in the audit (wq#3959).
- **Net read.** ReCopilot proves the *value* of a decompilation-specialist model and the *shape* of
  pluggable routing, but is itself a closed service, not adoptable tech. Limpet lifts the idea
  (capability tier, realised on an open, self-hostable model) and explicitly refuses the provider.
