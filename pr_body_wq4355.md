## wq#4355 — LIMPET dissect: Perplexity Computer, task-aware multi-model routing

Dissects Perplexity Computer (launched 2026-02-25) across the five axes the ticket
names, grounded by live `fleet-search` fetch (model memory postdates the product),
then lands the ticket's routing upgrade.

### Research (`design/wq4355_perplexity_computer_dissection.md`)
- **19-model backend, role-specialized:** Claude Opus 4.6 core reasoning, Gemini deep
  research + subagent creation, Grok speed, GPT-5.2 long-context, Nano Banana images,
  Veo 3.1 video. The model is picked by subtask *need*, not by the user.
- **Automatic subtask→model routing**, **user override + per-subtask spend controls**,
  **SPACE** per-task disposable Firecracker microVMs (pause/resume/branch, hours–days),
  **background parallel execution**. Each axis mapped to a Limpet read.

### Routing upgrade (`limpet/model_router.py`)
Implements the ticket's "task-classifier → model-policy layer, not a config file",
kept pure/offline (caller still places the LiteLLM call):
- `REQUIREMENT_PROFILES` — vision-heavy / long-context / speed-critical / code-gen.
- `resolve_profile()` — profile → operator policy, resident-model fallback, fail-closed
  on unknown profile.
- **User-override escape hatch** on `resolve_profile` and `resolve_step` — a pinned
  model wins over the classifier outright (Perplexity axis 3).

### Verification
`cd limpet && python3 -m pytest test_model_router.py -q` → **14 passed** (7 pre-existing
+ 7 new, incl. a falsifiable override-bypass probe).

🤖 Generated with [Claude Code](https://claude.com/claude-code)
