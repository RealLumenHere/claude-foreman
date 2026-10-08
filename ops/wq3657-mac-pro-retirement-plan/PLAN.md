# wq#3657 — Mac Pro retirement: consolidate to M70s + BC-250

## Correction to the ticket text first (doctrine: verify, don't guess)

The ticket names `maat`+`sobek` as part of "4 Mac Pros" to retire in phase 3. They are
not Mac Pros: `maat` is a WeiBu ADL-N mini-PC and `sobek` is a Trigkey Green G4
(`research/INVENTORY_RECONCILIATION.md:134,159`), both already low-power (sobek is a
dedicated efficiency canary, `research/wq415_sobek_n100_efficiency_canary.md`). The
actual MacPro6,1 fleet is `hathor`, `isis`, `khepri`, `wadjet`
(`research/INVENTORY_RECONCILIATION.md:111,121,130,184`); `khepri` is already offline
and `wadjet` has been off since 2026-07-19 (`:188,225`). `nephthys` is also a
decommissioned MacPro6,1 (`:188`) but is currently re-provisioned as a live inference
node (`research/inference_nodes.yaml:77-103`) — it is in scope for this consolidation
too, since it is the same idle-power class the ticket is trying to shed.

Reading the ticket's intent rather than its literal node names (full-autonomy instinct
3: decide and act, record reasoning): "retire the 4 Mac Pros, in two waves" maps to the
real fleet as:

- **Wave 1 (ticket's "maat+sobek")** → `nephthys` + `wadjet` (wadjet already down;
  nephthys is the live, currently-loaded one and the safer first cutover since isis is
  already degraded and hathor is pinned to the judge).
- **Wave 2 (ticket's "isis+hathor")** → `isis` + `hathor`, unchanged — the ticket's own
  names are correct here, and the ticket's own caveat ("keep isis and hathor until
  replacement throughput is verified") is honored below.

## Current hardware state (read before acting, not assumed)

- **BC-250 (`hapi-1`)**: on the bench, **not networked**. Phase 1 BIOS flash is a
  hands-on physical act (programmer clips, 3.3V rail) a headless worker cannot perform —
  tracked as a non-blocking physical task, not a gate
  (`ops/wq3651-bc250-bios-flash-governor/RUNBOOK-ADDENDUM.md:8-15`). Serving path once
  up: Vulkan/RADV via `llama-server`, never ROCm/Ollama on this silicon
  (`RUNBOOK-ADDENDUM.md:100-125`).
- **M70s (`sentinel`)**: never joined the tailnet; blocked on disk/OS install, a
  physical act (`ops/wq1492-liveness-backlog-sweep/no_tag_reasons.yaml:55`,
  `ops/wq941-sentinel-provision/RUNBOOK.md:5-9`). Design is Sentinel P0 (watchdog), not
  an inference role — repurposing it as an inference node per this ticket is a scope
  change from `decisions/SENTINEL_PRIME_DESIGN.md` and needs its own node entry, not an
  assumption that the existing bring-up covers it.

Phase 1 ("deploy BC-250 + M70s as inference nodes") is therefore **blocked on physical
provisioning of both replacement boxes**. Per instinct 3, that physical work is queued
as a non-blocking physical task (hands on hapi-1 and on the M70s units), not a gate on
the rest of this ticket — this plan and its measurement gate ship now so execution is
mechanical the day either box is reachable over SSH.

## Phase 2 — measurement gate (built now, runs once hardware is up)

`verify_replacement.py` (this directory) is the phase-2/3 gate: it reads the same
macbench results JSONL the fleet already produces
(`tools/macbench/mb_measure.py`, `tools/macbench/mb_render.py`) for a baseline Mac Pro
row and a candidate replacement row, and verdicts PASS only if the candidate's decode
tok/s is within `--min-decode-ratio` (default 0.9) of the Mac Pro's AND its mean watts
is at most `--max-watts-ratio` (default 1.0) of the Mac Pro's. Either axis can fail
independently (`tests/test_verify_replacement.py::test_fail_when_watts_not_improved`
is the proof: a faster-but-still-Mac-Pro-wattage candidate fails on watts alone).

Run, once a candidate node has a macbench results file:

```
python3 ops/wq3657-mac-pro-retirement-plan/verify_replacement.py \
  --baseline <nephthys-or-isis-or-hathor results.jsonl> --candidate <hapi-1-or-sentinel results.jsonl> \
  --row qwen36-local
```

Add `hapi-1` and `sentinel` to `tools/macbench/nodes.yaml` once they have a tailscale
IP (today they have none — adding placeholder entries now would make the bench tool
try to SSH to a box that doesn't exist on the tailnet).

## Phase 3 — retire wave 1 (nephthys + wadjet)

Gate: `verify_replacement.py` PASS against both `nephthys`'s and (if re-measured)
`wadjet`'s Mac-Pro baseline rows, for every model family the replacement needs to carry
(`qwen36-local`, `nephthys-qwen14b` per `research/inference_nodes.yaml:16-40`). Do not
flip `research/inference_nodes.yaml` nodes.nephthys.state to `retired` until that PASS
is on record — the registry lint (`harness/inference_registry.py:25` `STATES`) already
has a `retired` state for exactly this transition.

## Phase 4 — retire wave 2 (isis + hathor), 4-weeks-stable gate

Ticket's own constraint, kept as written: do not retire isis (35B MoE) or hathor
(judge) until wave-1 replacement throughput has been stable for 4 weeks AND
`verify_replacement.py` PASSes for isis's `qwen36-local` row and hathor's
`hathor-llama8b` row. Hathor is additionally pinned `local_worker_group: false`
(`research/inference_nodes.yaml:71`, wq#2796) because it serves the judge path
synchronously — its replacement needs the same isolation before cutover, not just the
same throughput number.

## Power accounting (ticket's numbers, kept as the target to verify against)

Ticket claims idle: 4 Mac Pros ≈ 192W (~$0.74/mo marginal) vs 2 M70s+BC-250 ≈ 55W
(~$0.94... as written this is inverted — the replacement's *idle* draw should cost
less, not more, than the current fleet's. This looks like a units/scope slip in the
ticket text (monthly-energy-cost math depends on $/kWh and hours, not stated), not a
number this worker can re-derive without the missing rate. Flagging rather than
re-deriving a guessed electricity rate (doctrine: never guess a value) — `verify_replacement.py`'s
`--max-watts-ratio` gate uses the measured watts directly and does not depend on
resolving this ambiguity.
