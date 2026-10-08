# wq#3658 — Hardware role assignment (Limpet, M80s, M70s#1, M70s#2)

This is the decision record other provisioning tickets for these four nodes should
cite as the source of truth for *what each box is for*, so role decisions don't drift
or get re-litigated per-ticket. It does not provision anything itself — wq#3652
(M80s) carries that work and M70s#1/M70s#2/Limpet provisioning are
not yet filed as their own tickets as of this writing (`wq list` has no ticket titled
for them individually).

## Role table

| Node | CPU | RAM target | Role | Explicitly NOT |
|---|---|---|---|---|
| Limpet | i5-14500 | — | Secondary control/worker. Runs Ollama qwen3.5-9b + Docker workers. | NOT the primary control plane yet. |
| M80s | i7-12700 | 64GB | Heavy compute, primary x86 inference, OpenHands/Forge host (migration target for wq#3686/wq#3687, currently on Lumen). Also a sandboxed fworker execution host (wq#3652) — see the reconciliation below. | NOT Frank's workstation. NOT an *independent* fworker host: its sandbox cannot run without Lumen. |
| M70s#1 | i5-12500 | 32GB | Isolated fworker execution host, in the *independent-of-Lumen* sense (own circuit/UPS). | NOT the only fworker execution host — M80s gets one too, with different isolation. |
| M70s#2 | — | — | Docker offload from Lumen + Gitea mirror + embeddings. | — |

## Cross-reference: M70s#1 and wq#941 (sentinel)

wq#941 ("[SENTINEL-PRIME] P0: provision M70s Gen 3 as the dedicated fleet-response
node") already assigned a role to an M70s Gen 3 box: hostname `sentinel`, CPU SKU to
be recorded in the i5-12400/12500/12600 range, role `fleet-response-node`, on its own
circuit/UPS so it survives a Lumen outage, running Qwen3-30B-A3B resident
(`ops/wq941-sentinel-provision/RUNBOOK.md:4,16,42,80-83`). That SKU range overlaps
this ticket's M70s#1 (i5-12500) exactly, and "independent fleet-response node" and
"isolated fworker execution host" describe the same underlying property — isolation
from Lumen so the fleet keeps functioning if Lumen goes down.

Treat `sentinel` (wq#941) as the same physical box as M70s#1, not a duplicate: wq#941
is the detailed provisioning runbook (disks, power, tok/s benchmarking, registration
in `lumen_state.py`), and this ticket's M70s#1 entry is that box's role inside the
wider four-node assignment. wq#941 is currently `blocked` (physical work — no OS/disks
yet), which matches this ticket's deploy order below (M70s#1 is step 2, before M70s#2,
Limpet, and M80s).

No prior ticket or doc in this repo names an M70s#2 box, Limpet, or assigns the Gitea
mirror / embeddings role — that pairing is new as of this ticket.

## Cross-reference: M80s and wq#3652 (fworker sandbox)

Two of these four nodes are fworker execution hosts, not one. wq#3652 ("M80s
provisioning: vPro AMT + fworker sandbox + NVMe isolation") puts a second BUILD-worker
execution environment on M80s: a `fworker` nologin user owning `/var/fworker` only
(step 4), wrapped in a systemd sandbox with `ProtectSystem=strict`, `ProtectHome=yes`,
`PrivateTmp`, `NoNewPrivileges` and an eBPF address filter that denies the tailnet and
LAN while allowing Lumen's /32 (step 5), with `~/.claude/` auth migrated from Lumen
(step 6) and the worker env pointed at Lumen's LiteLLM + wq (step 7). The unit and
provisioning scripts landed as `7297b825`
(`ops/wq3652-m80s-provisioning/fworker-sandbox@.service`,
`provision-fworker.sh`, hardening test `test_fworker_sandbox_hardening.py`); the
privileged remainder is wq#3703/wq#3704/wq#3705.

The two isolations are opposites, which is why both roles can exist without
duplicating each other:

| | M70s#1 / wq#941 | M80s / wq#3652 |
|---|---|---|
| Isolation means | independence *from* Lumen | OS security sandbox *around* the worker |
| Coupling to Lumen | minimal by design — own circuit/UPS, survives a Lumen outage (`ops/wq941-sentinel-provision/RUNBOOK.md:23-24`) | maximal by design — egress restricted to Lumen's /32, jobs dispatched by Lumen's wq, so it dies with Lumen |
| Threat/failure model | Lumen is down | the agent inside the sandbox is untrusted |
| Picked for | fleet response that must outlive Lumen | throughput offload under confinement |

Consequences for anyone using this doc:

- Do not read M70s#1 as "the fworker box." Capacity planning for BUILD-worker
  throughput must count both nodes; resilience planning that needs a worker to survive
  a Lumen outage must count only M70s#1.
- The deploy-order rationale below gives M70s#1 step 2 because it "reduces Lumen's
  load by moving fworker execution off it." M80s (step 5) does that too, and as of
  2026-10-08 it is the node with the sandbox unit already written and tested, while
  wq#941 is still blocked on physical disks. If the goal is load relief rather than
  outage independence, M80s is the nearer-term lever and the ordering below should not
  be read as a priority claim about fworker offload specifically.
- When onboarding either node to `fleet-exec-policy.yaml`, scope
  `allow.restart: fworker-sandbox@*` to the node that actually runs the unit
  (M80s per `ops/wq3652-m80s-provisioning/README.md:63,83`); M70s#1's fworker role
  is not yet specified at unit level anywhere.

## Deploy order (highest impact first)

1. **BC-250** (hapi-1) — highest impact. Already has a corrected bring-up addendum at
   `ops/wq3651-bc250-bios-flash-governor/RUNBOOK-ADDENDUM.md`, blocked on the physical
   BIOS flash (non-blocking physical task per standing autonomy policy).
2. **M70s#1** — reduces Lumen's load by moving fworker execution off it. Tracked by
   wq#941 (blocked on physical disk/OS install).
3. **M70s#2** — Docker offload from Lumen, Gitea mirror, embeddings. Not yet filed as
   its own ticket.
4. **Limpet** — inference (Ollama qwen3.5-9b) + Docker workers, secondary control.
   Not yet filed as its own ticket.
5. **M80s** — heavy compute, OpenHands/Forge migration target, 64GB RAM, *and* a
   sandboxed fworker execution host (see the wq#3652 reconciliation above). wq#3652
   (vPro/AMT, fworker sandbox, NVMe isolation) is `done` as of 2026-10-08 for the
   authorable part (`7297b825`); the physical and privileged steps are wq#3703
   (MEBx/AMT + Ubuntu), wq#3704 (MeshCommander on Thor) and wq#3705 (user/NVMe/unit
   install, Claude auth migration, LiteLLM key).
6. **Mac Pro retirement** — last, and gated on measurement, not on schedule: wq#3657
   says explicitly "Do NOT retire before measuring" and keeps isis (35B MoE) + hathor
   (judge) until replacement throughput on the nodes above is verified.

## Open items for whoever files the M70s#2 and Limpet provisioning tickets

- M70s#2 needs a hostname decision (sentinel's non-Egyptian, function-named convention
  per `ops/wq941-sentinel-provision/RUNBOOK.md:16-17`, vs. the existing Mac Pros'
  Egyptian names) and a Gitea-mirror topology (pull mirror of which repos, refresh
  cadence).
- Limpet's "secondary control" framing implies it should be able to take over
  control-plane duties from Lumen if needed, but is explicitly not promoted to that
  role in this ticket — a future ticket, not this one, should define the promotion
  trigger/criteria.
