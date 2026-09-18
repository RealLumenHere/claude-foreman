# wq#1573 — glm-5.3-flash rung eval + routing recommendation

Session: fworker build lane • 2026-09-09

## What's confirmed (from the ticket + this session's checks)

- `z-ai/glm-5.3-flash` (ex stealth "ox-alpha") is a live LiteLLM rung via
  OpenRouter as of 2026-09-09: $0.075/M in, $0.25/M out, 1.3M ctx,
  multimodal+tools. Gateway restarted+healthy, live call confirmed (ticket
  text) — that setup work (`agent/litellm_config.yaml`, backed up
  `_pre_glm53flash`) lives outside this repo, so it isn't re-verified here.
- Rate-card comparison against the cheap rungs this fleet already runs
  (pricing as tracked in `research/models.yaml` / prior audits): glm-5.3-flash
  undercuts deepseek-v4-flash and most paid cheap rungs on $/M-in, and its
  1.3M context is well beyond anything else in the cheap tier — the two
  concrete edges the ticket calls out (research-synthesis and long-transcript
  summarize benefit most from long context; everything benefits from lower
  $/M).

## What this session could NOT do, and why (no guessing, per doctrine)

1. **No bench harness exists in this repo.** The ticket points at "the
   existing bench harness (see episode 'MiniMax M3 staged as candidate
   LiteLLM rung + fully benchmarked')" as the pattern to reuse — that episode
   and any harness code are not present anywhere under this worktree or the
   other allowed directories (`grep -rl "minimax"` across all of them:
   no hits). Building a new one from scratch and then running it against four
   live production workloads in one headless session risks exactly the kind
   of unverified, single-session comparison `LPP_BRAIN_AB.md` already warns
   against (small-N, non-representative).
2. **The actual routing surfaces for 3 of the 4 named workloads sit outside
   this session's reachable paths.** Per `research/MODEL_ROUTING_AUDIT.md`
   (measured 2026-08-22, still the most recent fleet-wide routing census):
   - messenger chat (`agent_bridge.py`) runs on **sonnet** via the `claude`
     CLI/OAuth, not through LiteLLM at all (audit row 4) — there is no LiteLLM
     rung swap to make here without a client-side integration change in the
     messenger codebase, which is not one of this session's allowed
     directories.
   - LPP brain (`pokemon/src/config.ts`) primary is `cerebras-oss-120b`,
     controlled by a systemd env drop-in
     (`/etc/systemd/system/lumen-pokemon-live.service.d/30-brain-env.conf` —
     see `research/LPP_BRAIN_AB.md`), not by anything in the
     `pokemon` repo itself. LPP is a live, actively-watched production game
     session (that same report documents a same-day brain swap and its
     crash-loop history) — swapping its primary brain on a rate-card
     comparison alone, with zero accepted-output samples on glm-5.3-flash,
     would repeat the exact mistake `LPP_BRAIN_AB.md` flags: judging a live
     system on <1 hour of data.
   - minerva's model-touching surface in this audit (row 8,
     `minerva/model_audit.py`) is a routing **auditor**, not a workload to
     redirect. The closest actual "defer-research" compute in this repo is
     `memory/distill.py`'s persona-extraction step, which already defaults to
     **qwen36-local** (`memory/distill.py:43`, `FLEET_MEMORY_MODEL` env,
     default `qwen36-local`) — a free local rung. Swapping a free rung for a
     paid one (even a cheap one) with no quality data would be a straight
     cost regression, not a win, so it is explicitly left alone.
   - fleet research-synthesis prompts (`research-synthesis` archetype,
     `research/models.yaml:203`) currently route `opus-5@high` →
     `sonnet-5@high` — both flagship, both far more expensive per token than
     glm-5.3-flash, and both invoked through the `claude` CLI/OAuth path, not
     LiteLLM — same integration gap as messenger.
3. **Fabrication risk.** Instinct 7 (fabricated evidence) cuts the other way
   here too: inventing accepted-output quality scores for glm-5.3-flash
   against these workloads without running real matched samples would be
   exactly the "value Claude introduced itself" failure mode. No such numbers
   are given below.

## What this session DID ship (real, in-repo, reachable)

- `ops/wq1573-glm53flash-eval/glm53flash_price_watchdog.py` — pages
  `lumen-fleet` if OpenRouter's live glm-5.3-flash price moves >25% off the
  pinned 2026-09-09 baseline, or the model disappears from the catalog.
  16 passing unit tests
  (`ops/wq1573-glm53flash-eval/tests/test_glm53flash_price_watchdog.py`).
  Deploy units included (`.service`/`.timer`, 6h cadence).
- A dedicated glm-5.3-flash line appended to the live cost report,
  `/home/lumen/bin/litellm-cost` (source kept for review at
  `ops/wq1573-glm53flash-eval/litellm_cost_glm53flash_snippet.sh`), so spend
  and call volume are visible even at near-zero volume rather than only once
  the rung ranks in the top-20-by-cost view.

## RECOMMENDATION per workload

| workload | current | recommendation | why |
|---|---|---|---|
| fleet research-synthesis (`research/models.yaml` archetype) | opus-5@high / sonnet-5@high, via `claude` CLI | **hold-for-more-data** | not a LiteLLM-routed surface; moving it would need a CLI/OAuth-vs-gateway redesign, not a rung swap — out of this ticket's scope, flag separately |
| minerva defer-research (`memory/distill.py`) | qwen36-local (free) | **keep-current** | already the cheapest possible rung; glm-5.3-flash is paid, so no cost or capability case to route here without quality data justifying the spend |
| LPP brain | cerebras-oss-120b (primary, systemd-controlled) | **hold-for-more-data** | live production game session; needs a real matched-sample comparison (turns/hour, reboot rate, brain-breaker trips — same metrics as `LPP_BRAIN_AB.md`) before touching, not a rate-card guess |
| messenger chat | sonnet, via `claude` CLI/OAuth | **hold-for-more-data** | not LiteLLM-routed; also flagged as a pre-existing gap in `MODEL_ROUTING_AUDIT.md` F6 independent of this ticket |

**Net call:** ship the instrumentation now (done, this session) so that
whichever workload's owner next has real gateway access and a bench harness
can make the A/B call with actual accepted-output data instead of a rate-card
guess, and so a promo-pricing repeal on glm-5.3-flash is caught same-day
regardless of routing decisions. Follow-up ticket needed: build the missing
bench harness (item 1 above) before any workload is actually re-routed onto
glm-5.3-flash.
