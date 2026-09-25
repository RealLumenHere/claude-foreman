# wq#2076 — MacPro6,1 dual-GPU R&D: read-only recon (step 0). BLOCKED on a bench box.

orch-burn-4-cfa06f, 2026-09-24. All findings below are read-only: no runtime installed,
no VBIOS ROM read, nothing changed on any node.

## Why no measurement ran

The ticket is explicit: run step 1 on khepri (staged bench box) when online, or a
non-production Mac — **NOT on a box actively serving inference**. Right now:

- **khepri** (designated bench box): offline, and `khepri.remediation_hold = on` (plug
  mapping unconfirmed) — cannot be brought up without re-verification.
- **maksoor** (step-2 RE bench for VBIOS analysis): offline (~12h).
- **isis, nephthys, hathor** (the 3 online Macs): **all three are running active `ollama`,
  listening on :11434** — every online candidate is a production inference node the ticket
  forbids experimenting on.

So step 1 has no eligible host. Installing a Vulkan/OpenCL compute runtime and pushing GPU
offload onto an old amdgpu card that is also the display device risks wedging the driver,
and on a headless production node with no console that means a power-cycle recovery — the
exact "don't break a working inference node" the ticket warns against.

## Read-only GPU inventory (corrects the ticket)

| box | GPUs | PCI ID | chip | driver | power | compute stack |
|---|---|---|---|---|---|---|
| isis | 2 | `1002:6810` | Curacao XT (R9 270X/370X-class) | amdgpu | D0 | none |
| hathor | 2 | `1002:6810` | Curacao XT | amdgpu | D0 | none |
| **nephthys** | 2 | **`1002:679e`** | **Tahiti LE (Radeon HD 7870 XT)** | amdgpu | D0 | none |

**Ticket correction:** it states all three boxes carry 2× Curacao XT. nephthys does not —
it has 2× **Tahiti LE**, a different (and higher-end) GCN 1.0 part. isis and hathor match
the Curacao description. This matters for the pilot: Tahiti has more shader/VRAM headroom
than Curacao, so if any of these is worth compute-enabling first, nephthys's pair is the
stronger candidate — but nephthys is also serving ollama, so it is not the box to test on.

Confirmed on every one of the 6 GPUs: `amdgpu` driver, power state `D0`, and **no compute
runtime at all** — `clinfo`, `rocminfo`, `vulkaninfo` and `llama.cpp` are all absent. This
matches the ticket's "display-recognized but unused for compute", now verified per-card.

## What the next runner should do (unchanged sequence, once a bench box is up)

1. Bring khepri online (needs the `remediation_hold` cleared first — its own gate), or
   designate a non-production Mac. Do **not** use isis/nephthys/hathor while they serve.
2. Step 1 compute-as-is: install `llama.cpp` Vulkan backend (best bet for GCN 1.0; ROCm is
   too new), offload a small model, measure tok/s vs CPU-only. Reversible, additive.
3. Only if the 2nd GPU is limited: step 2 VBIOS pull (read-only via sysfs `rom`) and
   analysis on maksoor. Both bench hosts are offline today.

## Liveness

Auto-clears when khepri (the designated bench box) is healthy again:
`node_healthy(khepri)`. If a different non-production Mac is chosen instead, unblock by hand.
