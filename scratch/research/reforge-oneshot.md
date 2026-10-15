# wq#3992 — REFORGE decompiled-function-naming eval, one-shot (measurement only)

Rework of archive proposal `3078fc82a3cc` ("Integrate REFORGE dataset into Lumen's
model evaluation pipeline", status `planned`, tag `model_eval_never_benchmarked`,
`ops/wq3756-archive-triage/REPORT.md:35`). That proposal's shape (new timer + unit)
is rejected per the ticket; this is measurement-only, no timer/unit added anywhere.

## Step 1 — is the REFORGE dataset/substrate actually released?

Paper: *REFORGE: A Method for Benchmarking LLMs' Reverse Engineering Capabilities in
Decompiled Binary Function Naming*, Andreas U. Schmidt, arXiv:2607.07738
(v1 2026-07-07, v2 2026-07-24), cs.SE. Confirmed real — abstract fetched directly
from `arxiv.org/abs/2607.07738` (not guessed; the ID came from the ticket text and
was verified, not assumed, per instinct 8/doctrine).

Abstract (verbatim, relevant clause): REFORGE is "a provenance-tracked pipeline
that constructs function-level ground truth from C source through compilation,
DWARF and syntactic extraction, alignment, and decompilation" with "a proof-of-concept
evaluation of seven contemporary LLMs on function naming" that the authors themselves
call validity-demonstrating, not a release-grade benchmark artifact.

**Release check (what I could reach):**

| Channel | Method | Result |
|---|---|---|
| arXiv abstract page | `fleet-search read https://arxiv.org/abs/2607.07738` | No code/data-availability statement, no `Comments:` field, no linked repo |
| arXiv HTML render | `fleet-search read https://arxiv.org/html/2607.07738[v2]` | HTTP 404 — no HTML version published |
| arXiv PDF | `fleet-search read https://arxiv.org/pdf/2607.07738` | Fetched (438KB) but the extractor pulls no text from PDF binaries — could not grep an appendix/footnote for a data-availability line |
| HuggingFace datasets | `fleet-search read "https://huggingface.co/datasets?search=reforge"` | 4 hits, all unrelated (Warcraft III Reforged voice pack, SD-WebUI reForge fork, etc.) — no RE/function-naming dataset |
| GitHub repo search | `fleet-search read "https://github.com/search?q=REFORGE+decompiled+function+naming&type=repositories"` | Page is JS-rendered; static extractor returned only chrome/boilerplate, no result list — inconclusive, not a confirmed negative |
| Papers With Code | `fleet-search read "https://paperswithcode.com/search?q=REFORGE+decompiled"` | No matching paper/dataset entry |
| General web search (`fleet-search web`) | 2 attempts, different queries | `ok:false` — brave/google-cse suspended (rate-limited), duckduckgo/startpage CAPTCHA'd, no `BRAVE_API_KEY` fallback. Known, already-tracked infra gap, wq#2595 (see ISSUE-LOG rows referencing wq#2595, e.g. row 715). Not re-logged here. |
| GitHub API search (`fleet-search github`) | 1 attempt | `401` — no usable token, same class as ISSUE-LOG row 1313 |

**Verdict: NOT VERIFIED RELEASED.** Every channel that actually returned content
found no dataset, no code repo, and no license. The two negative results with real
signal (HuggingFace, Papers With Code) are first-party catalogs for exactly this kind
of artifact and both came back empty by name match. The GitHub repo-search result is
inconclusive (JS-rendered page, static extractor blind) rather than a clean negative,
and the general web-search path was unavailable end-to-end due to the pre-existing
wq#2595 outage, so this is not a fully exhaustive negative — but nothing surfaced a
release, and the paper's own framing ("proof-of-concept... demonstrates the validity
of the concept") reads as a methodology paper, not a dataset-release paper.

Per the ticket's own instruction — **if not released, stop and record that** — steps
2 and 3 (scoring function-name recovery on a REFORGE subset through LiteLLM across
rungs) do not run. No local copy of REFORGE's dataset exists or was fetched; nothing
was downloaded or treated as ground truth.

## Rung list correction (caught before any run, not after)

The ticket's candidate rung list names `isis-coder-14b`. That id does not exist
anywhere in this repo: `grep -rln "isis-coder-14b" .` from the worktree root returns
zero files, against `research/models.yaml`, `harness/agents.yaml`, and
`fleet-config/` as the registries that would contain it. Logged as ISSUE-LOG row 1362
rather than silently substituted and left unexplained.

The actual local-rung registry (`research/models.yaml:314-341`, GENERATED from
`research/inference_nodes.yaml`) is:

| id | status | trust note |
|---|---|---|
| `hathor-llama8b` | LIVE-local | standard-engineering + mechanical-edit/probe-build local set |
| `nephthys-qwen14b` | LIVE-local | standard-engineering + mechanical-edit/probe-build local set |
| `qwen36-local` | **down** (Isis-hosted, flapping/gated, offline-survival tier) | mechanical-edit only, gated |
| `local-worker` | LIVE-local | alias/group over the three local boxes; mechanical-edit + probe-build primary rung |

Had this ticket run the scoring step, the 3-4 rungs would have been `local-worker`,
`nephthys-qwen14b`, `qwen36-local` (if its flapping Ollama backend answered — ticket's
own "if routed" hedge was correct to include), and one cloud ceiling rung
(`sonnet-5@high`, the fleet's standing verify-pass/ceiling model per
`harness/agents.yaml:69-72`).

## GO/NO-GO

**NO-GO** on running a REFORGE-dataset eval now. The substrate is not verifiably
released under any license we could check, so there is nothing to score rungs
against without fabricating ground truth — which would defeat the paper's own
point (it exists specifically because naive ground-truth construction for this task
is unreliable).

This does not block wq#3786 (REA/Ghidra first-pass renaming rung choice for MT8167
miko / RK3568 gocheckin firmware blobs). That choice should be made from REA's own
task-specific trial runs against the actual target binaries, not a REFORGE proxy
score — ground truth for *our* blobs is whatever Ghidra/REA converges on after
manual review, which is a better fit for picking a first-pass rung than an unreleased
third-party benchmark would have been anyway. Revisit REFORGE if/when a release
shows up (re-run the Step 1 checks above; HuggingFace + Papers With Code name-search
are the cheapest re-checks).

## Issue-log references

- ISSUE-LOG row 1362 (this ticket): `isis-coder-14b` does not exist in the model registry.
- ISSUE-LOG row 1313 / wq#2595 (pre-existing, not re-logged): `fleet-search github`/`web`
  have no usable credentials/search-engine access this session.
