# wq#3658 — Hardware role assignment (Limpet, M80s, M70s#1, M70s#2)

This is the decision record other provisioning tickets for these four nodes should
cite as the source of truth for *what each box is for*, so role decisions don't drift
or get re-litigated per-ticket. It does not provision anything itself — wq#3652
(M80s) is already claimed and in flight, and M70s#1/M70s#2/Limpet provisioning are
not yet filed as their own tickets as of this writing (`wq list` has no ticket titled
for them individually).

## Role table

| Node | CPU | RAM target | Role | Explicitly NOT |
|---|---|---|---|---|
| Limpet | i5-14500 | — | Secondary control/worker. Runs Ollama qwen3.5-9b + Docker workers. | NOT the primary control plane yet. |
| M80s | i7-12700 | 64GB | Heavy compute, primary x86 inference, OpenHands/Forge host (migration target for wq#3686/wq#3687, currently on Lumen). | NOT Frank's workstation. |
| M70s#1 | i5-12500 | 32GB | Isolated fworker execution host. | — |
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
5. **M80s** — heavy compute, OpenHands/Forge migration target, 64GB RAM. In flight as
   wq#3652 (vPro/AMT, fworker sandbox, NVMe isolation).
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
