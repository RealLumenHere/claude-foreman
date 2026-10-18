# wq#4258 — Fleet consolidation: Mac retirement + RTX 3090 + 13900HK

Status check performed before writing anything here (doctrine: verify, don't guess).

## Phase 1 — Benchmark all Macs: already done, not "running"

The ticket says "wq running." It isn't — `wq show 2246` (`[macbench] unattended
model-after-model bench for the Macs`) is `status: done`, and `wq show 2651`/`2815`/
`3794` carried the follow-on judgment and bake-off work, also done. No benchmark gap
remains for this ticket to carry.

## Phase 2 — Add Claude Sonnet + Haiku to LiteLLM: done here, blocked only on the key

`litellm_config_snippet.yaml` (this directory) adds `claude-sonnet` / `claude-haiku`
model_list entries routed through `ANTHROPIC_API_KEY`, which already has its slot in
`fleet-config/docker/litellm/.env.example:5` — only the value ("workspace key
pending," per the ticket) is missing. Minting that key is a credential act on Frank's
own account (instinct 3 physical dependency) — queued, not a gate. Once it lands,
deploy per `fleet-config/docs/RECOVERY.md` §4: hand-merge the snippet into
`/home/lumen/agent/litellm_config.yaml`, restart the `litellm` container, verify
`curl -s http://127.0.0.1:4000/health/liveliness` → 200, then one real completion
against each new alias.

## Phase 3 (reroute aliases) / Phase 4 (power off Macs) — already in flight under wq#3657

`ops/wq3657-mac-pro-retirement-plan/PLAN.md` is an existing, more carefully
fleet-reconciled plan for this exact transition (same Mac Pro 6,1 boxes: hathor,
isis, nephthys, wadjet, khepri). Current measured state
(`research/inference_nodes.yaml:52-53,90-91,118-119`): `hathor` live (pinned to the
judge, kept until a 4-week-stable replacement gate per wq#3657 Phase 4), `nephthys`
live (wq#3657 wave-1 retirement candidate), `isis` already `state: down`. `wadjet` and
`khepri` are already offline (`research/INVENTORY_RECONCILIATION.md:45,58`). This
ticket does not re-litigate that plan or its `verify_replacement.py` gate — it is the
right instrument already and duplicating it here would fork two retirement
authorities over the same hardware.

## Phase 5 — "Part out Macs + sell M70s/M80s": correction, not an action

This is the one place the ticket text is wrong in a way that matters before anyone
acts on it. "M70s" and "M80s" are not spare Mac hardware — they are the two
fworker/inference host roles assigned in
`ops/wq3658-hardware-role-assignment-limpet-secondar/ROLE_ASSIGNMENT.md:15-16`:
M80s is the primary x86 inference + OpenHands/Forge + sandboxed fworker execution
host (wq#3652); M70s#1 is the independent-of-Lumen fworker execution host (own
circuit/UPS) and is the same physical box as `sentinel` (wq#941, SENTINEL-PRIME P0
watchdog). Selling them would remove the fleet's only Lumen-independent fworker
capacity and the sentinel watchdog node, not recover idle Mac-Pro wattage. Treating
the ticket's "M70s/M80s" as license to sell those boxes would be winging it past a
fabricated premise (instinct 8 in spirit: the ticket's naming doesn't match anything
sellable in the current fleet) — flagged here rather than executed. If the intent was
instead "sell the physical Mac Pro 6,1 trashcans once wq#3657 retires them," that is
already wq#3657's hardware, not a distinct M70s/M80s sale, and has no separate action
item.

## Phases 6-8 — RTX 3090 x2 + 13900HK: no software action possible yet

`research/fleet_hardware.json` and `research/INVENTORY_RECONCILIATION.md` have no
13900HK entry and no RTX 3090 GPU entry (the existing "3090" hit in the inventory is
an unrelated Dell OptiPlex 3090 desktop model, not a GPU). There is no hostname, no
IP, no SSH target — buying and installing the GPUs is a physical/financial act only
Frank can do (instinct 3), and there is nothing to pre-stage in this repo for a host
that doesn't exist yet (no inference-server config, no `tools/macbench/nodes.yaml`
entry can be written against an unknown IP). Queued as a physical task, not a gate on
Phase 2's deliverable above.

## What this ticket delivers

- Phase 2's ready-to-merge LiteLLM snippet (`litellm_config_snippet.yaml`).
- The Phase 5 correction, so nobody executes a sale against the wrong hardware.
- Phases 1/3/4 pointed at the tickets that already own them, instead of forking a
  second retirement plan.
- Phases 6-8 explicitly queued as physical/financial, with the reason nothing earlier
  can be staged for them.
