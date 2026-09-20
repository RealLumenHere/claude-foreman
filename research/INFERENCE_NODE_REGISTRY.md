# wq#2241 — inference node registry: one file, five generated targets, cold/warm power

Design for Frank's 2026-09-20 ask: add and retire LLM-serving machines simply; record
what each machine and model is *best* at; run several models per box, swapped for the
task; make COLD (powered off) versus WARM (up, model resident) a first-class state;
keep hathor up while the group needs it.

Companion to `research/QWEN36_LOCAL_WORKER_SERVING.md` (per-box serving plan) and
`research/DISPATCH_REBUILD_SPEC.md` §2.2 (the `local-worker` group shape). This doc
owns the *registry and its generators*; those two own the serving unit itself.

Deliverable 2 (the child build tickets) is §9. **Do not build from §1-§8 without
claiming the child ticket that owns that section.**

---

## 1. The problem, measured

Adding one backend today means hand-editing five places that must agree by string. The
five, with the exact sites:

| # | target | site | in git? |
|---|--------|------|---------|
| 1 | gateway model list | `/home/lumen/agent/litellm_config.yaml` `model_list` | **no** |
| 2 | dispatcher slots/ctx/budget/prefill/health | `dispatcher/config.json:6-12` `local_backends` | yes |
| 3 | local pool + routing rows | `research/models.yaml:21,155,168,207,214,237` | yes |
| 4 | scoped key `foreman-local-rung` model allowlist | LiteLLM key record (`research/models.yaml:206` — "gateway-side, not in git") | **no** |
| 5 | name-prefix list, **copied 5×** | `governor/governor_core.py:97`, `orchestrator/judgment.py:41`, `harness/fworker.py:155`, `despag/admission.py:75`, `despag/app.py:61` | yes |

Target 5 is the dangerous one. Every copy is the same literal tuple

```python
LOCAL_POOL_MODELS = ("deepseek-v4-flash", "qwen36-local", "nephthys-qwen14b", "hathor-llama8b")
```

matched with `model.startswith(prefix)` (`governor/governor_core.py:101`). A model id
that matches no prefix is not rejected — it is **silently treated as a cloud model**: it
draws on the Anthropic ledger, takes the governor gate, the burn-rate ceiling and
`cloud_ceiling` instead of a Mac slot. The failure is quiet and expensive.

One copy is already correct: `harness/concurrency.py:54-75` derives the set from
`research/models.yaml` pools via `_pool_models()`, with the hand-kept tuple only as an
import-time fallback. That mechanism is the model for everything below — this design
generalises it rather than inventing a new one.

Facts this design builds on, all measured 2026-09-20 on identical MacPro6,1 CPU-only
hardware (`research/QWEN36_LOCAL_WORKER_SERVING.md:12-16`, `dispatcher/config.json:8-10`):

| node | serving | model | prefill tok/s | decode tok/s | slots | served ctx |
|------|---------|-------|---------------|--------------|-------|------------|
| hathor | ollama :11434 | llama3.1:8b | 13.6 | unmeasured | 2 | 32768 |
| nephthys | ollama :11434 | qwen3:14b | 7.4 | unmeasured | 2 | 32768 |
| isis | llama-server :8095 | qwen3.6-35b-a3b | 60 (bench 63) | 10.9 | 2 | 8192 (last measured; `config.json`'s 32768 is an unverified post-fix target, `research/models.yaml:194-198`) |

Power facts: a MacPro6,1 **auto-powers-on after entering G3**
(`research/ISIS_POWEROFF_RCA.md:70`) — cutting and restoring AC *is* the wake method, no
WoL needed. Idle draw is 86.6 W (hathor) / 103.2 W (isis, with the since-retired
`disable-gpus.service`) — `research/ISIS_POWEROFF_RCA.md:25`. A cold node therefore
costs ~2 kWh/day to leave warm, and one wrong wake costs about a nickel. That asymmetry
sets every threshold in §6.

---

## 2. The registry file

**Path:** `research/inference_nodes.yaml`, beside `research/models.yaml`, read with the
same convention (`FOREMAN_INFERENCE_NODES` env override for tests, mirroring
`harness/concurrency.py:44`).

**Loader:** `harness/inference_registry.py` — pure stdlib + PyYAML, no imports from
`dispatcher/` or `orchestrator/`, because `governor`-adjacent modules import it at
import time and must never fail on a missing dependency (the `_pool_models` rule,
`harness/concurrency.py:64`).

### 2.1 Schema

```yaml
version: 1

# Frozen: the governor's prefix tuple (governor/governor_core.py:97), which this design
# may not edit. See §4. Adding a value here without the governor changing is a LINT FAIL.
governor_prefixes: [deepseek-v4-flash, qwen36-local, nephthys-qwen14b, hathor-llama8b]

families:                 # a model blob + how it is served. NOT a box.
  qwen36-local:
    blob: Qwen3.6-35B-A3B-Q4_K_M.gguf     # sha256 checked at `add`, per QWEN36 §2.1
    serving: llama-server                  # llama-server | ollama
    port: 8095
    health_path: /health
    swap: unit             # one model per systemd unit; swapping = stop/start (§7)
  hathor-llama8b:
    blob: "llama3.1:8b"
    serving: ollama
    port: 11434
    health_path: /api/ps
    swap: implicit         # ollama lazy-loads and unloads on keep_alive; no unit swap
  nephthys-qwen14b:
    blob: "qwen3:14b"
    serving: ollama
    port: 11434
    health_path: /api/ps
    swap: implicit

nodes:
  hathor:
    tailscale_ip: 100.80.71.13     # OBSERVED; fleet_inventory.observed() is the authority
    plug_id: p3                    # MUST equal `fleet-inv plugs` (§2.2). 2026-08-22 Frank: P3 IS HATHOR'S PLUG.
    power_policy: always-on        # cold | warm | always-on
    power_reason: "Frank 2026-09-20: hathor stays up while the group needs it"
    cores: 12
    ram_gb: 64
    idle_watts: 86.6               # research/ISIS_POWEROFF_RCA.md:25
    wake:
      method: plug-ac              # plug off->on; MacPro6,1 auto-powers-on out of G3
      measured_ready_s: null       # filled by `fleet-node bench`; null => never auto-woken
      last_measured: null
    models:
      - family: hathor-llama8b
        resident: true             # unit enabled now
        slots: 2
        served_ctx: 32768          # what the backend REALLY serves, not what we wish
        first_frame_s: 1500
        prefill_tps: 13.6
        decode_tps: null
        measured_at: 2026-09-20
        load_s: null               # cold-load time for this blob on this box (§7)
        capabilities:
          mechanical-edit:      {trusted: true,  source: declared, ref: "harness/fworker.py:181"}
          probe-build:          {trusted: true,  source: declared, ref: "harness/fworker.py:181"}
          standard-engineering: {trusted: true,  source: declared, ref: "harness/fworker.py:179-182"}
```

Per-node fields: `tailscale_ip`, `plug_id`, `power_policy`, `power_reason`, `cores`,
`ram_gb`, `idle_watts`, `wake{method, measured_ready_s, last_measured}`, `models[]`,
and `state` (`live | draining | cold | down | retired`).

Per-model fields: `family`, `resident`, `slots`, `served_ctx`, `first_frame_s`,
`prefill_tps`, `decode_tps`, `measured_at`, `load_s`, `capabilities{}`.

Per-capability fields: `trusted` (bool), `source` (`declared | measured`), plus, when
`measured`, `n`, `success`, `window_days`, `updated`; when `declared`, `ref` (the
file:line the hand-kept decision came from); when `trusted: false`, `reason`.

### 2.2 What the registry does NOT own

- **`plug_id` binding.** `hardware.power_hw.plug_id` in
  `/home/lumen/fleet/inventory/machines/<name>.yaml`, surfaced by `fleet-inv plugs`, is
  the authority. The registry carries a copy so the power logic needs one read, and
  `fleet-node lint` **fails** on disagreement. `plug-ctl`'s own header warns that plug
  labels are dated observations, not assignments — the lint is what keeps a stale copy
  from cutting the wrong machine's AC.
- **`tailscale_ip` / ssh user.** Same deal: `fleet_inventory.observed()` is the authority
  (`/home/lumen/bin/fleet-inv:18-24`), the registry copy is linted against it.
- **Archetype definitions.** `harness/agents.yaml:49-97` owns the nine archetypes and
  their `local:` trust flags. The registry says which *node+model* is trusted for an
  archetype, never what an archetype is.
- **Routing chains.** `research/models.yaml:237` keeps `archetype -> primary -> downgrade`.
  The registry supplies the local candidates; the chain composes them.

### 2.3 Seeding

The three current nodes are seeded from the measured facts in §1 plus, for the two
fields this design could not read, a live command at build time — **do not guess them**:

- `plug_id` for nephthys and isis: `fleet-inv plugs`.
- `decode_tps` for hathor and nephthys: unmeasured today; seed `null` and let
  `fleet-node bench` fill them. A null rate means "not yet measured", never "zero" —
  §7's swap cost and §6's readiness check both refuse to run on a null.

isis seeds as `state: down` with its row intact (it is out of service, wq#2140; its
probe fails so it already contributes 0 slots — `dispatcher/config.json:7`).

---

## 3. Generators: render the five, never hand-edit them again

One command, `fleet-node render`, with `--check` (exit 2 on drift, for CI) and
`--apply`. Each target gets a renderer that writes **only** a sentinel-delimited region,
so hand-written neighbours survive:

```
# BEGIN GENERATED inference_nodes.yaml v1 — edit the registry, not this block
...
# END GENERATED
```

| target | renderer | mechanism |
|--------|----------|-----------|
| 2. `dispatcher/config.json` `local_backends` | `render_dispatcher()` | rewrite that one key; `dispatcher/dispatch_config_lint.py:169` already validates the shape, so the renderer's output is checked by an existing gate |
| 3. `research/models.yaml` local rows + `local` pool | `render_models_yaml()` | rewrite only rows whose id is registry-owned, inside sentinels; `pools:`, `served_ctx`, `context_tokens`, `trust` come from the registry |
| 5. the four editable prefix copies | `render_prefix_lists()` | rewrite the `LOCAL_POOL_MODELS = (...)` literal in `orchestrator/judgment.py`, `harness/fworker.py`, `despag/admission.py`, `despag/app.py` |
| 1. gateway `model_list` | `render_litellm()` | emit `ops/inference-registry/litellm_local_block.yaml`; `--apply` splices it into `/home/lumen/agent/litellm_config.yaml` between sentinels, **after** a timestamped backup (`research/DISPATCH_REBUILD_SPEC.md:110`) |
| 4. scoped key allowlist | `render_key_allowlist()` | emit the desired `models[]`; `--apply` calls LiteLLM `/key/update` for `foreman-local-rung`. Key read from its 0600 file, **never** echoed, never placed on a command line |

Notes that matter:

- **`harness/concurrency.py` needs no renderer.** It already derives from
  `research/models.yaml` (`_pool_models`, line 54), so regenerating target 3 fixes it
  for free. Leave it alone; a sixth copy is the bug this ticket exists to kill.
- The prefix copies stay *literal tuples* rather than becoming
  `from concurrency import LOCAL_POOL_MODELS`. That is deliberate: `despag/app.py:58`
  records that despag must not import fworker, and `governor/governor_core.py:94` is
  import-time-fragile by design. Generating the literal preserves the no-import property
  and makes the copies provably equal (§8 test T3).
- `--apply` on targets 1 and 4 touches the live gateway. Both are idempotent, both take
  a backup first, and both are re-runnable: that is what makes `fleet-node retire`'s
  rollback (§5) a one-liner.

---

## 4. The naming rule (governor-safe, no governor edit)

**Rule.** Every local model id is

```
<governor_prefix>.<node>
```

where `<governor_prefix>` is one of the four frozen strings at
`governor/governor_core.py:97`, and `<node>` is the registry node key.

Examples: `qwen36-local.hathor`, `qwen36-local.nephthys`, `hathor-llama8b.seshat`,
`nephthys-qwen14b.sobek`.

Why this shape and not another:

- `startswith` is the matcher (`governor_core.py:101`), so *any* suffix on a frozen
  string is governor-exempt. The rule needs no governor change.
- The separator is `.` and **must not be `@`**: `dispatcher/local_capacity.py:51`
  defines `base_name()` as `model.split("@", 1)[0]`, so `@` is already the effort
  separator (`hathor-llama8b@low`). A node suffix on `@` would be silently eaten by
  every slot-accounting call in `local_capacity.py`.
- The existing three ids (`hathor-llama8b`, `nephthys-qwen14b`, `qwen36-local`) stay
  valid unchanged — they are `<prefix>` with the node elided, which `startswith` accepts.
  New rows use the dotted form; nothing has to be renamed on day one.

**The cost, stated plainly.** The prefixes are historical accidents: they name a *box*
(`hathor-llama8b`) where the registry means a *family*. So `hathor-llama8b.seshat`
reads as "the llama3.1:8b family, served on seshat" — correct but ugly. Worse, the
frozen list caps the fleet at **three local model families**, however many boxes we add.
A fourth family (say gemma3-27b) has no legal prefix.

The lint enforces this rather than letting it fail silently:

- `fleet-node lint` **fails** if any model id does not start with a `governor_prefixes`
  value, naming the id and pointing at `governor/governor_core.py:97`.
- `fleet-node lint` **fails** if two families claim the same `governor_prefix` with
  different `blob` values — i.e. it refuses to let a new model hide under an old name.
- `fleet-node add --family <new>` refuses with the exact reason and the escape-hatch
  ticket id (§9 C8) rather than half-adding a node that the governor will bill to
  Anthropic.

**The escape hatch** is a three-line diff: replace `governor_core.py:97` with
`from concurrency import LOCAL_POOL_MODELS`, which already reads `research/models.yaml`
(line 54) and would therefore already be registry-driven. This design does not take it,
because the ticket freezes the governor. Ticket C8 (§9) carries it for the day family
#4 is actually wanted; until then the cap is three, on the record.

---

## 5. `fleet-node add` and `fleet-node retire`

Tool: `/home/lumen/bin/fleet-node` (wrapper over `harness/fleet_node.py`), matching the
`fleet-inv` / `fleet-exec` naming convention. Both verbs run the same six phases.

### 5.1 `fleet-node add <node> --family <f> [--slots N] [--power-policy cold|warm|always-on]`

1. **Precheck** (read-only, no writes anywhere). Node exists in `fleet-inv` with a
   resolvable `tailscale_ip` and a `plug_id`; ssh answers; `lscpu` core count and RAM
   match the registry's declared values (`research/QWEN36_LOCAL_WORKER_SERVING.md:84`
   records a real SKU conflict on nephthys — check, don't assume); `<family>.<node>`
   satisfies §4; the blob's sha256 matches the source node's; the port is free.
   Any failure exits 1 having changed nothing.
2. **Dry-run diff.** Render all five targets against the *proposed* registry and print a
   unified diff per target — including the two out-of-git ones. Exits 0. `--yes` is
   required to go past this; without it, `add` stops here. This is the whole point of the
   design: the operator sees five diffs, not five files to edit.
3. **Serve.** Install/enable the unit per `research/QWEN36_LOCAL_WORKER_SERVING.md` §2.2
   (llama-server) or ensure the ollama model is pulled (ollama). Detached
   `systemd-run`, never foreground.
4. **Health probe.** The same probe the dispatcher uses —
   `dispatcher/local_capacity.py:68`, GET `health_url`, 2xx within 2 s — repeated until
   pass or a 300 s deadline. Then one real completion through the gateway.
5. **Bench.** Fills the null fields, and nothing is marked live until it does:
   `prefill_tps` and `decode_tps` from a warm ~4k-token prompt (second run, model
   resident — the exact procedure at `research/QWEN36_LOCAL_WORKER_SERVING.md:109-124`);
   `served_ctx` read from the backend, not declared (`/api/ps` `context_length` for
   ollama, the startup ctx line for llama-server); `load_s` from a cold start;
   `first_frame_s` computed as `served_ctx / prefill_tps * 2`, matching the budget gate
   fworker already enforces (`harness/fworker.py:2450-2456`, wq#2185). Results also go to
   `lumen_state` as `<node>.<family>_bench`, mirroring `isis.qwen36_bench`.
   Go/no-go is §8 T4's gate; a NO-GO leaves the node at `state: down` with the numbers
   recorded, and is not a failure of the command.
6. **Apply + verify.** `fleet-node render --apply`, restart the gateway detached, then
   re-run `fleet-node lint` and one dispatcher `--dry-run` pass showing the new backend
   with free slots.

**Rollback** (`--rollback`, and automatic on any phase-6 failure): restore the
`litellm_config.yaml` backup, revert the key allowlist to its pre-change list,
`git checkout` the three in-git targets, restart the gateway, `systemctl disable --now`
the new unit. Cost of a rolled-back add: the blob's disk, nothing else
(`research/QWEN36_LOCAL_WORKER_SERVING.md:100-107`).

### 5.2 `fleet-node retire <node> [--family <f>] [--power off|leave]`

Same six phases, inverted, and **drain-first**:

1. **Precheck.** Refuse if the node is the last healthy member of `local-worker` (the
   group would render empty and every local ticket would park). Refuse if
   `power_policy: always-on` without `--force` — this is the mechanical form of Frank's
   requirement (5): hathor cannot be retired by accident.
2. **Dry-run diff** across the five targets, as above.
3. **Drain.** Set `state: draining` and render — the node's slots leave the dispatcher's
   view at the next pass, so no new work is admitted. Then wait for running units to
   finish, to a `drain_deadline_s` of 1800. Never kill a running worker to retire a box.
4. **Health probe** confirming zero in-flight.
5. **Bench** is skipped; instead the final capability numbers are frozen into the
   registry row so a later re-add starts from measured data.
6. **Apply + verify**, then `state: retired`. The row is **kept, not deleted** — the isis
   precedent (`dispatcher/config.json:7`: "row left in place") is what lets a node come
   back without re-deriving anything.

`--power off` additionally runs §6's power-down sequence. Rollback is `state: live` plus
a re-render; because step 6 keeps the row, rollback needs no backup of the registry
itself.

---

## 6. Cold / warm lifecycle

### 6.1 The three states

| `power_policy` | meaning | who owns power |
|---|---|---|
| `always-on` | never drained, never powered down by automation | nobody; hathor's row, Frank's requirement (5) |
| `warm` | up, unit enabled, model resident; may be drained if idle and `drain_when_idle: true` | automation, conservatively |
| `cold` | plug off, boots on demand | automation |

A node with `wake.measured_ready_s: null` is **never auto-woken** — the dispatcher has no
honest deadline to wait against. `fleet-node add` measures it; until then a cold node is
`cold-untested` and only a human `fleet-node power <node> on` moves it.

### 6.2 Wake

Signal already exists: `_admit_local` increments `ctx.local_slots_full` every time a
ready local ticket is skipped for want of a slot
(`dispatcher/foreman_dispatcher.py:1446`, `:1462`, `:1471`).

Wake **one** cold node when, for `starve_passes: 2` consecutive dispatch passes:

- `local_slots_full >= starve_min: 2`, **and**
- every warm/always-on member of `local-worker` reports `free_slots == 0`
  (`dispatcher/local_capacity.py:193`), **and**
- the candidate has `measured_ready_s` set, `wakes_today < max_wakes_per_day: 4`, and
  `now - last_drain_ts > cool_s: 900`.

Candidate order: highest `prefill_tps` for the family the starved tickets want, ties
broken by lowest `idle_watts`, then by node name.

Sequence: `plug-ctl <plug_id> on` → poll the health URL every 15 s to a deadline of
`measured_ready_s * 2` → on pass, `state: live` and render (slots appear next pass) → on
timeout, `state: down`, `plug-ctl <plug_id> off`, one ISSUE-LOG line, and the node is
excluded for 24 h. A wake that half-works is worse than one that never started.

**The pass cadence is the primary anti-flap mechanism.** `lumen-foreman-dispatch.timer`
is `OnUnitActiveSec=20min` (`dispatcher/systemd/lumen-foreman-dispatch.timer:10`), so
`starve_passes: 2` means ~40 minutes of sustained starvation before any AC moves. At
86.6 W a wrong wake costs about 0.09 kWh — pennies — while a flapping power cycle costs
hardware. Slow to sleep, cheap to wake.

### 6.3 Drain and power down

Power down a `cold`-policy node when **all** of:

- zero running units on it, **and**
- zero ready local tickets in the queue for any family it serves, **and**
- `now - last_wake_ts >= min_warm_s: 3600` (dwell floor: one wake buys at least an hour),
  **and**
- both conditions have held for `idle_passes: 2` (~40 min).

Sequence, in this order and no other:

1. `state: draining`, render — no new admissions.
2. Confirm zero in-flight (`local_capacity` running counts), else abort and re-arm.
3. `ssh <node> systemctl stop <unit>` — let the model unload cleanly.
4. `ssh <node> poweroff` — graceful OS shutdown.
5. Wait for ssh to stop answering, to a 180 s deadline.
6. **Only then** `plug-ctl <plug_id> off`.

Step 6 last is not style. The MacPro6,1 auto-powers-on out of G3
(`research/ISIS_POWEROFF_RCA.md:70`), so cutting AC under a running OS produces an
immediate unplanned boot — a power-cycle loop wearing the mask of a crash. That is
exactly the shape of the isis incident this fleet already paid for.

### 6.4 Power-action safety interlock

Before **any** `plug-ctl ... off`, all three must hold, and the action is refused with a
logged reason otherwise:

1. `registry.plug_id == fleet-inv plugs` for that node (§2.2).
2. The node answers ssh at its `tailscale_ip` and reports the expected hostname.
3. `plug-ctl <plug_id> watts` is non-zero and within 30% of `idle_watts`.

All three exist because `plug-ctl`'s header records that plug labels are dated
observations and that a machine "sits on whatever plug happened to be free". Cutting the
wrong plug is the one failure in this design that can take down a box nobody asked to
touch. Thor is excluded structurally: it has no registry row and never will.

Every power action is logged to `lumen_state` as `<node>.power_action` (timestamp, verb,
plug, trigger, the starvation counters that justified it) and to the dispatcher's pass
log. Cross-pass counters live in `_runs/power_state.json`
(`{node: {starved_passes, idle_passes, last_wake_ts, last_drain_ts, wakes_today}}`),
beside the other dispatcher state under `FOREMAN_RUNS_ROOT`
(`orchestrator/judgment.py:37`).

### 6.5 Kill switch

`FOREMAN_NODE_POWER=off` (env, read per pass) disables every automatic power action
while leaving wake/drain *decisions* logged. Same shape as
`FOREMAN_LOCAL_CAPACITY=static` (`dispatcher/local_capacity.py:45`), so the rollback
lever is one the operators already know.

---

## 7. Several models per box, chosen per task

### 7.1 How the router names a model

Per archetype, the candidate set is every registry `(node, family)` where
`capabilities[<archetype>].trusted` is true and `served_ctx` clears the estimated prompt
(`prompt_tokens_est`, the figure fworker's budget gate already computes). Ranked by
measured `success`, then `prefill_tps`. The winner is dispatched as `<family>.<node>`,
which §4 makes governor-exempt and which `local_capacity.base_name()` leaves intact.

The `local-worker` group name stays exactly what it is today: a routing name the
dispatcher resolves to a member at admit time (`foreman_dispatcher.py:1453-1467`), so
the group name still never reaches the governor CLI or fworker. The registry supplies
the member list; nothing about that resolution changes.

### 7.2 Capability profiles: where the evidence comes from

Profiles start `source: declared`, seeded from today's hand-kept constants with the
file:line that decided them — `harness/fworker.py:179-182` (nephthys-qwen14b and
hathor-llama8b trusted for standard-engineering because 40k/131k ctx holds a full
fworker prompt), `research/models.yaml:192,201` (qwen36-local mechanical-edit only,
because 8192 ctx crashes on 15-22k prompts).

`fleet-node capabilities refresh` recomputes them from attempt records — the
`attempt-N.json` files under `_runs/wq<id>/`, read with
`dispatcher/self_heal.attempt_records()` (`dispatcher/self_heal.py:339`), grouped by
`(model, archetype)` over a 30-day window, success = the record's classification.

The flip rules are deliberately asymmetric:

- **Demotion is automatic**: `n >= 10 and success < 0.30` sets `trusted: false` with
  `source: measured` and the sample recorded. Failing safe needs no ceremony.
- **Promotion is proposal-only**: `n >= 10 and success >= 0.50` writes a candidate to
  `_runs/capability_proposals.json` and prints it; a human or a review ticket flips the
  bit. Widening what an 8B model is trusted to ship is a judgment call, and this fleet
  already holds that line elsewhere (instinct 7: memory consolidation stays
  proposal-only).
- Below `n = 10`, nothing moves and `source` stays `declared`. A 2-of-3 sample is not
  evidence.

### 7.3 Swap cost and when a swap is refused

Two serving modes, and the registry records which (`families[].swap`):

- **`implicit` (ollama)** — the backend lazy-loads the requested model and unloads the
  idle one on `keep_alive`. "Swapping" is naming a different model in the request. Cost
  is one cold load, paid inside the request; no orchestration needed. This is why the
  two ollama boxes get multi-model for free.
- **`unit` (llama-server)** — one model per systemd unit, one port. Swapping means
  `systemctl stop` + `start`, which **destroys the KV cache of every slot on the box**.
  Ollama 0.32.6 forces a single slot for the qwen3.6 MoE arch, which is why isis runs
  llama-server at all (`research/QWEN36_LOCAL_WORKER_SERVING.md:24-26`) — the `unit`
  mode is not a choice we can optimise away.

`swap_cost_s = unit_stop_s + load_s` (both measured by `fleet-node bench`; a null
`load_s` means no swap is ever attempted).

A `unit` swap is **admitted** only when all four hold:

1. Zero in-flight slots on that node, and zero for `swap_settle_s: 120`. **A swap is
   never performed under load** — that is the whole KV-loss rule, and it is a hard
   refusal, not a cost term.
2. Ready tickets wanting the target family on this node `>= swap_min_queue: 3`.
3. Zero ready tickets for the currently resident family.
4. `now - last_swap_ts >= swap_cool_s: 1800`, capped at `max_swaps_per_day: 6`.

Otherwise the ticket routes to another node, or waits. Refusals are logged with which
of the four conditions failed, so a box that never swaps is diagnosable rather than
mysterious.

Cheaper alternative the design deliberately prefers: **spread families across nodes
rather than swapping on one**. Three ollama-served families cost nothing to co-host; only
the llama-server family is exclusive. With three boxes and three families, the steady
state needs zero swaps. Swapping exists for the case where the fleet is one box short,
not as the normal path.

---

## 8. Acceptance tests

Each maps to exactly one child ticket (§9). All are `pytest` unless noted.

**T1 — registry + lint (C1).** A fixture registry with a known-bad row per rule fails
`fleet-node lint` with the right message: id violating §4; two families sharing a
`governor_prefix` with different blobs; `plug_id` disagreeing with a stubbed
`fleet-inv plugs`; `tailscale_ip` disagreeing with a stubbed inventory; `slots` summing
above `local_ceiling`. The real `research/inference_nodes.yaml` passes, and its three
node rows reproduce the §1 measured table field-for-field.

**T2 — generators are faithful and idempotent (C2, C3).** From the seeded registry,
`fleet-node render --check` against the *current* repo exits 0 — i.e. the generators
reproduce today's `dispatcher/config.json:6-12`, `research/models.yaml` local rows and
all four prefix tuples byte-for-byte. Then `render --apply` twice produces no diff on
the second run. Then delete one node from the fixture registry and assert all five
renders drop it, including `ops/inference-registry/litellm_local_block.yaml` and the
key-allowlist payload.

**T3 — no sixth copy, and the five agree (C2).** A test that imports
`governor.governor_core`, `orchestrator.judgment`, `harness.fworker`,
`despag.admission`, `despag.app` and asserts every `LOCAL_POOL_MODELS` equals the
registry's rendering; plus `harness.concurrency.LOCAL_POOL_MODELS` (derived, not
generated) agrees. Plus a repo grep asserting exactly five sites define that literal —
a sixth copy fails the suite.

**T4 — governor-exemption round trip (C2).** For every registry model id, assert
`governor_core.is_local_model(id)`, `concurrency.is_mac_local_model(id)` and
`fworker.is_local_model(id)` are all true, and that the same holds with an effort suffix
appended (`<id>@low`) — the `base_name` boundary from §4. A fixture id using `@` as the
node separator must fail the lint, with the `local_capacity.py:51` reason quoted.

**T5 — `fleet-node add` (C4).** Against a stub node (HTTP health stub + a fake
`plug-ctl` + a fake ssh): precheck rejects a node with no `plug_id`, a name violating §4,
and a blob sha mismatch. `--dry-run` prints five diffs and writes nothing. A full run
fills `prefill_tps`/`decode_tps`/`served_ctx`/`load_s` from the bench stub and refuses to
mark the node live when the bench misses the go gate
(`research/QWEN36_LOCAL_WORKER_SERVING.md:115-124`). A phase-6 failure restores all five
targets to their pre-run bytes.

**T6 — `fleet-node retire` (C4).** Refuses the last healthy group member; refuses an
`always-on` node without `--force` (the hathor guard, Frank requirement 5); with a
running unit, sets `draining`, admits nothing new, and does not kill the worker; the
retired row survives in the registry with its measured numbers; rollback restores
`state: live` in one render.

**T7 — cold wake (C6).** A simulated dispatcher pass sequence: one starved pass triggers
nothing; two consecutive starved passes with all warm members at zero free slots call
the fake `plug-ctl on` exactly once; a node with `measured_ready_s: null` is never
picked; `wakes_today = 4` blocks; `FOREMAN_NODE_POWER=off` blocks while still logging the
decision; a health-probe timeout powers the node back off and excludes it 24 h.

**T8 — drain, power-down, anti-flap (C6).** With a running unit, no power-off is issued.
Idle but inside `min_warm_s` → no power-off. Idle past both windows → the six-step
sequence fires **in order**, with `plug-ctl off` strictly last (assert call order, since
§6.3's whole point is the ordering). An `always-on` node is never a candidate. The
interlock refuses when the stubbed `fleet-inv plugs` disagrees with the registry, when
ssh reports the wrong hostname, and when watts read zero. A 24-hour simulated trace with
oscillating queue depth produces at most `max_wakes_per_day` power cycles.

**T9 — per-task choice and swap (C5, C7).** The router picks the highest-success trusted
`(node, family)` for an archetype and skips one whose `served_ctx` is below the prompt
estimate. `implicit`-swap families never invoke a unit swap. A `unit` swap is refused
with the failing condition named for each of: in-flight slot, queue below
`swap_min_queue`, a ready ticket for the resident family, inside `swap_cool_s`, and
`load_s: null`. Capability refresh: 10 records at 20% success auto-demotes; 10 at 80%
writes a proposal and leaves `trusted` unchanged; 5 records move nothing.

---

## 9. Child build tickets (Deliverable 2)

Filed in dependency order. **This ticket builds none of them.**

| wq# | | title | deps |
|-----|---|-------|------|
| **2248** | C1 | registry file, schema, loader, `fleet-node lint`; seed the three current nodes (T1) | — |
| **2250** | C2 | generators for the three in-git targets + `render --check` in CI (T2, T3, T4) | 2248 |
| **2251** | C3 | generators for the two gateway-side targets + apply/rollback (T2) | 2248 |
| **2252** | C5 | capability profiles from attempt records: auto-demote, propose-promote (T9) | 2248 |
| **2253** | C4 | `fleet-node add` / `fleet-node retire` (T5, T6) | 2250, 2251 |
| **2254** | C6 | cold/warm power lifecycle in the dispatcher (T7, T8) | 2248, 2253 |
| **2255** | C7 | per-task model choice + swap costing and refusal (T9) | 2248, 2254 |
| **2256** | C8 | retire the frozen-prefix rule: point the governor at the registry (§4). **Parked** at pri 4 until a fourth model family is actually wanted | 2250 |
| **2249** | P1 | **physical** (`frank-hw`): per-Mac auto-boot verify + plug-soak assignment for the waiting cold Macs | — |

wq#2249 is the one genuine physical dependency: §6's wake method assumes each cold Mac
auto-powers-on out of G3 and sits on a known plug. That is verified for the MacPro6,1
class (`research/ISIS_POWEROFF_RCA.md:70`) but not per-box for the machines waiting to
join (the backlog's #337/#354/#352). It blocks *enrolling new cold nodes*, not the build
chain: the three existing nodes have known plugs, and wq#2254 can be built and tested
against them plus the T7/T8 stubs.

## 10. Out of scope

Which archetypes ride `local-worker` in the routing chain stays with
`research/DISPATCH_REBUILD_SPEC.md:109` and `research/models.yaml:237`. GPU serving,
speculative decoding and the hapi/Vulkan path are untouched. Cloud model rows are
untouched — the registry describes Mac inference nodes only, and `deepseek-v4-flash`
appears solely as a frozen `governor_prefix` value, never as a node.
