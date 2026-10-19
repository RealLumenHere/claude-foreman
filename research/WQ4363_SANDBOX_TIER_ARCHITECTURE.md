# wq#4363 — Sandbox tier architecture for Limpet: risk-class → isolation mapping

**Date:** 2026-10-19 · **Lane:** LIMPET research · **Sources:** vendor docs and dated
third-party writeups retrieved 2026-10-19 via `fleet-search web` (SearXNG general engines
all suspended/CAPTCHA'd, keyed DeepSeek Search fallback used per the tool's own `note`
field — same outage pattern logged for wq#4347/wq#4356/wq#4358). Per-provider citations
inline below. **Scope honesty:** this is a documentation/market dissection producing a
*design mapping*, not a deployment. No sandbox provider was stood up or benchmarked on the
fleet; every latency/scale number is the vendor's or a third party's published figure and
is labelled as such. The Limpet-side facts (D3 egress posture, untrusted-exploit rule) are
read from `limpet/DESIGN-DECISIONS.md` and cited by line. Companion to
`research/WQ4370_SECURITY_VERIFICATION_DISSECTION.md` (per-tool confinement *policy*) — that
doc covered *what* to confine; this one covers *which isolation primitive* each RE task
class is confined *with*.

---

## 1. Why this ticket exists

Isolation is commoditized — Firecracker, gVisor, Kata, V8 isolates, and plain containers are
all off-the-shelf in 2026. The live question for Limpet is **not** "which sandbox is best"
but "which primitive fits which RE execution class," because Limpet's workload is not one
workload. Running `binwalk` over a firmware image, detonating that firmware under QEMU, and
running an ML decompiler-triage model have wildly different threat models, latency budgets,
and statefulness needs. A single sandbox tier either over-isolates the cheap read-only steps
(latency tax on recon) or under-isolates detonation (a parser/VM escape gets fleet egress).

Limpet already has the seed of a tiered posture: **D3 — the build sandbox has no egress**
(`limpet/DESIGN-DECISIONS.md:106`), FETCH is fail-closed, and **downloaded exploit bodies
stay untrusted and are never auto-run** (`limpet/DESIGN-DECISIONS.md:110-111`). The mapping
below extends that single fail-closed tier into a graded ladder.

---

## 2. The six primitives (as published)

| Provider | Primitive | Published cold start | Isolation boundary | Network/state shape | License / mode |
|---|---|---|---|---|---|
| **E2B** | Firecracker microVM | ~150–200ms (pre-warmed snapshot pools) | Hardware-virt (KVM) microVM — full kernel per sandbox | ephemeral; egress controllable | Apache-2.0; self-host via Terraform, but hosted SaaS is the default story |
| **Modal** | gVisor (`runsc`) | strong but higher than microVM; 100k+ concurrent sandboxes | syscall-interception user-kernel; weaker than HW-virt, stronger than namespaces | GPU passthrough *inside* sandbox (T4…H200/B200) | hosted platform; code-first |
| **Daytona** | Docker container | ~27ms spin-up, <90ms end-to-end | namespace/cgroup — container only | **persistent by default** (runs until stopped), shared Volumes | hosted; Docker-native, Docker-in-Docker |
| **Fly.io Sprites** | persistent per-agent VM | boots in seconds; **~1s checkpoint/restore** | full Linux VM, dedicated per agent | persistent 100GB, auto-idles billing, snapshot/rollback | hosted |
| **Cloudflare Workers** | V8 isolate | sub-ms (no cold VM) | V8 context only — *no kernel, no processes, no arbitrary binaries* | edge, global; JS/WASM runtime only; CPU-time capped | hosted edge |
| **K8s Agent Sandbox CRD** | pluggable gVisor/Kata | GKE Agent Sandbox **GA May 2026**; **300 sandboxes/sec**, 90% <200ms | pluggable — gVisor (syscall) or Kata (microVM) per policy | self-managed cluster; quotas/netpolicy/audit as first-class | `agents.x-k8s.io/v1beta1`, SIG Apps; open |

**Sources (retrieved 2026-10-19):** E2B & CubeSandbox comparison —
`wavespeed.ai/blog/posts/cubesandbox-vs-e2b` (E2B Apache-2.0, Firecracker, ~150–200ms,
self-host via Terraform but hosted-default). Modal — `modal.com/resources/best-code-execution-sandbox-llamaindex`
(broad GPU inc. H100/H200/B200 inside Sandboxes, 100k+ concurrent). Daytona —
`daytona.io/dotfiles/fastest-growing-infra-company-in-history` (~27ms spin-up, <90ms e2e,
persistent-by-default, Docker-native, Volumes). Fly.io Sprites —
`sdxcentral.com/news/flyio-debuts-sprites-persistent-vms-...` (persistent VMs, boot in
seconds, 100GB, auto-idle billing, ~1s checkpoint/restore). Cloudflare —
`developers.cloudflare.com/workers/reference/how-workers-works` (V8 isolates, Workers
runtime, global network; JS/WASM, not a general OS). K8s — `tech.yahoo.com/.../kubernetes-agent-sandbox-becoming-unit-...`
(Sandbox CRD under SIG Apps, `agents.x-k8s.io/v1beta1`; GKE Agent Sandbox GA May 2026, 300
sandboxes/sec, 90% <200ms, gVisor/Kata). **Caveat:** vendor/analyst copy is marketing-framed;
treat the numbers as order-of-magnitude, not SLA. All six provider claims in the ticket
(E2B Apache-2.0/Firecracker/~200ms, Modal gVisor+GPU/100k, Daytona 27ms Docker, Fly.io
persistent/~1s checkpoint, Cloudflare V8/JS-only, K8s CRD GA May-2026/300-per-sec) were
independently corroborated by these sources.

---

## 3. The isolation axis that actually matters for RE

Three properties, not one, separate these primitives — and RE tasks stress different ones:

1. **Escape resistance.** Is the boundary hardware-virt (Firecracker, Kata, Fly VMs — a
   guest-kernel exploit still faces the hypervisor), syscall-interception (gVisor/`runsc` —
   a narrowed but shared kernel surface), or namespaces only (Docker — a kernel bug is a
   host bug)? V8 isolates are a *fourth* kind: no OS to escape *to*, but also no OS to run
   native RE tooling *in*.
2. **Statefulness.** Ephemeral teardown (E2B/Modal default) vs persistent-by-default
   (Daytona, Fly Sprites, with checkpoint/restore). RE recon *accumulates* artifacts
   (extracted filesystems, string dumps, Ghidra projects); throwing that away between steps
   is the wrong default for a multi-hour engagement.
3. **In-sandbox compute.** Can the sandbox itself run a GPU model (Modal) — needed for
   ML-assisted decompilation triage — or must inference live outside the isolation boundary?

RE's dominant threat is **the target running inside the sandbox is actively hostile** —
malicious firmware under emulation, a malformed image that exploits the *unpacker*, a fetched
exploit body. That pushes the high-risk tier toward hardware-virt, egress-denied, ephemeral.
The dominant *cost* is latency × step-count on the many cheap read-only steps, which pushes
the low-risk tier toward sub-100ms containers.

---

## 4. OUTPUT — Risk-class → sandbox-tier mapping for Limpet

Four tiers. Each Limpet RE task class is assigned by its **worst-case behaviour of code
running inside the sandbox**, not by its average case.

### Tier 0 — Read-only static recon (latency-critical, low risk)
**RE tasks:** `file`/`readelf`/`strings`/`objdump` metadata passes, catalog/KB lookups,
plugin manifest validation, string-level triage — the firmware-static-analysis skill's recon
pass. Input is untrusted *data* but no attacker code executes; the only risk is a parser bug
in the tool itself.
**Tier:** **Daytona-class Docker container (~27ms), or a Cloudflare V8 isolate for the
pure-JS/WASM subset** (manifest validation, text transforms, KB query shaping). Latency
budget dominates; container isolation is adequate because nothing hostile runs. Egress still
denied by default (D3). Persistent Volume holds the accumulating artifact set so Tier-0 steps
chain without re-extraction.

### Tier 1 — Untrusted-input parsing / extraction (medium risk)
**RE tasks:** `unblob`/`binwalk` firmware extraction, archive/filesystem unpacking, decoding
unknown blobs — the firmware-extraction skill. **Here the *unpacker* is the attack surface:**
a malformed image aims to exploit the parser. Attacker-influenced, but not yet
attacker-chosen-code.
**Tier:** **gVisor** — Modal-class or a K8s Sandbox CRD with `runsc`. Syscall interception
contains a parser RCE far better than a namespace; full HW-virt is overkill for a parser that
isn't *supposed* to execute anything. Ephemeral per extraction, no egress. K8s CRD is the
self-hosted form (quotas + netpolicy + audit as first-class), which matters because Limpet is
a fleet-resident tool, not a SaaS tenant.

### Tier 2 — Active detonation / emulation (high risk — hostile code executes)
**RE tasks:** QEMU system/user-mode emulation of extracted firmware, Firmadyne/FirmAE runs,
running a fetched exploit body, dynamic analysis where **the target's own code executes** —
the firmware-emulation skill. This is the tier Limpet's "exploit bodies stay untrusted, never
auto-run" rule (`DESIGN-DECISIONS.md:110-111`) is guarding; when they *are* run, they run
here.
**Tier:** **Firecracker microVM — E2B (self-hosted) or Kata via the K8s CRD.** Hardware-virt
is non-negotiable: a guest-kernel or QEMU escape still faces the hypervisor. Strictly
ephemeral, egress hard-denied (MITM/capture happens via an explicit in-sandbox tap, never
general egress). ~150–200ms cold start is irrelevant next to a multi-minute emulation run.
Self-hosted E2B (Apache-2.0, Terraform) keeps hostile firmware off a shared SaaS and on fleet
hardware Limpet controls.

### Tier 3 — Long-lived recon workspace (stateful, medium risk, spans the engagement)
**RE tasks:** the multi-hour/multi-day engagement workspace that accumulates Ghidra projects,
extracted trees, notes, and intermediate artifacts across many Tier-0/1 steps — the "recon
phase that accumulates artifacts" the ticket calls out.
**Tier:** **Fly.io Sprites-class persistent per-agent VM** (or Daytona persistent sandbox +
Volume for the lighter case). Boot-once, install-tooling-once, **checkpoint/restore (~1s)**
between risky sub-steps so a Tier-2 detonation that corrupts the workspace rolls back instead
of restarting the engagement. Full VM (not container) because the workspace hosts the Tier-1
extraction and shells out to Tier-2 detonation sandboxes; it is the *orchestration* home, not
the detonation chamber.

### Cross-cutting — In-sandbox GPU inference
**RE tasks:** ML-assisted decompilation triage, binary-similarity embeddings, local model
inference *inside* the RE loop (the only reason to co-locate inference with execution is to
avoid shipping multi-GB extracted binaries out of the isolation boundary).
**Tier:** **Modal** is the *only* one of the six with GPU passthrough inside the sandbox. If
Limpet wants in-loop inference under isolation, Modal is the primitive; otherwise inference
stays an external service and this row is N/A. Flagged, not mandated — most RE triage models
can run outside the execution sandbox.

### Summary table

| Tier | Risk driver | RE task class | Primitive | Boundary | State | Egress |
|---|---|---|---|---|---|---|
| 0 | parser bug only | static recon, KB/manifest | Daytona container / CF V8 (JS subset) | namespace / isolate | shared Volume | denied |
| 1 | hostile *input* to unpacker | `unblob`/`binwalk` extraction | gVisor (Modal / K8s `runsc`) | syscall intercept | ephemeral | denied |
| 2 | hostile *code* executes | QEMU detonation, exploit run | Firecracker (E2B self-host / Kata CRD) | HW-virt | ephemeral | hard-denied |
| 3 | long-lived workspace | engagement recon accumulation | Fly Sprites / Daytona persistent | full VM | persistent + ~1s checkpoint | denied |
| × | in-loop GPU inference | ML decompile triage | Modal | gVisor + GPU | ephemeral | per-policy |

---

## 5. Recommendation for the Limpet build

- **Don't adopt one provider; adopt the tier ladder.** The decision Limpet actually needs is
  a **risk classifier at task-dispatch** (which tier does this RE step land in?) plus a thin
  driver interface so each tier's primitive is swappable. This mirrors the K8s CRD's own
  pluggable-runtime model (gVisor *or* Kata per policy) and Limpet's existing manifest-gated
  plugin posture.
- **Self-host the high-risk tier.** Tier 2 must not run hostile firmware on a shared SaaS.
  E2B (Apache-2.0 + Terraform) or Kata-via-CRD on fleet hardware are the only options that
  keep detonation on boxes Limpet controls. The ticket's "E2B self-hostable Apache-2.0" line
  is the load-bearing property, not the 200ms number.
- **Make persistence a Tier-3 feature, not a global default.** Daytona/Fly persistence is
  right for the engagement workspace and *wrong* for Tiers 1–2, where ephemeral teardown is a
  security property. Pair persistent Tier-3 with checkpoint/restore so Tier-2 risk is
  reversible.
- **Egress stays fail-closed at every tier** (extends `DESIGN-DECISIONS.md:106`). Capture is
  an explicit in-sandbox tap, never general egress; fetched exploit bodies remain untrusted
  and only ever execute in Tier 2.
- **Cloudflare V8 is a corner case, not a tier of its own.** JS/WASM-only with no processes
  means it cannot run any native RE tool; its slot is the pure-JS/WASM subset of Tier 0
  (manifest validation, text/KB transforms at the edge). Don't force RE tooling into it.

---

## 6. Caveats

1. **No fleet benchmark.** Every latency/scale figure is vendor/analyst-published and
   marketing-framed; before committing a tier, benchmark the candidate on real Limpet
   firmware under the fleet's egress-denied network, because cold-start numbers assume warm
   pools and default networking.
2. **gVisor is a syscall surface, not a hypervisor.** The Tier-1↔Tier-2 line (gVisor vs
   Firecracker) is the single most consequential call here. If a Tier-1 extraction class is
   ever found to execute attacker code (some unpackers invoke helper binaries), it must be
   reclassified to Tier 2 — classify by worst case, re-audit the unpacker set.
3. **Self-hosting cost is real.** E2B/K8s self-host is Terraform + cluster ops the fleet must
   own; the Apache-2.0 license removes the legal blocker, not the operational one.
4. **Dated market, Jan-2026 cutoff + Oct-2026 search.** GKE Agent Sandbox GA (May 2026) and
   CubeSandbox (Apr 2026) postdate the model cutoff and are known only via this search;
   re-verify before build.
