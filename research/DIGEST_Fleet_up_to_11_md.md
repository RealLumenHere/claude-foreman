# DIGEST — Frank's "Fleet up to 11.md" (wq#2469)

Source: `/home/lumen/scratch/TWO/Fleet up to 11.md`, **107 lines**, read in full 2026-09-24.
Citations are `F11 L<range>` plus heading text, e.g. `F11 L90-105 §"3. Tuning mbpfan for AI
Workloads"`. The file has no duplicated chapters; all 107 lines are content.

Sixth and last of the wq#2463-2469 digest family. `DIGEST_Fleet_Monitoring_2_md.md:23` predicted
this file would continue FMon2's FastAPI/JWT thread. **That prediction is wrong** — this is a
different thread entirely: it is a hardware doc about the MacPro6,1 trashcans, and it does not
mention monitoring, MCP or auth once. The FMon2 continuation does not exist in `scratch/TWO/`.

## 0. TL;DR — one recommendation in this doc is worth the whole file

The doc's own thesis (Vulkan on the FirePros) is **already adjudicated**: `wq#2501` was filed off
`DIGEST_Orchestation_2_md.md` R20 two days ago with strictly better evidence, and the doc's
step 1 (force the `amdgpu` driver) is **already true on our hardware** — we have been running
amdgpu-bound cards on these boxes since June (`ISIS_POWEROFF_RCA.md:140-142`).

What is new, un-filed, and directly fixes a defect the fleet has already *measured and walked
past* is `F11 L90-105`: **`mbpfan`**. The string `mbpfan` appears **zero times** in
`/home/lumen/foreman`, `/home/lumen/kb`, `/home/lumen/bin` and `/home/lumen/agents`.

The defect it fixes is in our own RCA. `ISIS_POWEROFF_RCA.md:84-105` measured isis's single
chassis fan across two days:

| Bucket | fan rpm | Te0T °C | cpu % |
|--------|---------|---------|-------|
| 09-18T23:58 | 789.5 | **81.4** | **84.4** |
| 09-19T07:44 | 796.6 | **84.1** | 68.1 |
| 09-19T22:09 | 789.2 | 84.2 | 0.3 |

`fan1_min` reads 790, `fan1_max` 1947, `fan1_manual=0`. The RCA's own words: *"the SMC is in auto
mode and simply keeps this chassis at its floor"*, and *"All three MacPro6,1 units idle their
single fan regardless of load"* (`ISIS_POWEROFF_RCA.md:100-103`).

The RCA used that measurement to **falsify a hypothesis** and stopped. It never asked why. The
doc answers (`F11 L92-94`): *"Standard Linux ACPI cannot interface with Apple's System Management
Controller"* — nothing in a stock Linux install drives the MacPro6,1 fan, so it sits at the SMC
floor forever. `mbpfan` reads `coretemp` and writes `applesmc` directly.

**We are running 60-84% CPU at 84 °C on a fan that has never once left 790 rpm, and we have
1157 rpm of unused headroom sitting in `fan1_max`.** That is the finding.

It also makes `wq#2501` safe. wq#2501 proposes lighting up both D700s for Vulkan inference on a
chassis whose CPU and both GPUs share one triangular thermal core (`F11 L92`). The standing rule
recorded at `ISIS_POWEROFF_RCA.md:139-142` exists precisely because *gating* a GPU on these boxes
**overheated that core** once already. Running two GCN 1.0 cards flat out behind a fan pinned at
its floor is the same mistake with more watts. **wq#2501 should not run until the fan does.**

---

## 1. What the doc is, and how much of it to trust

A short, clean research note — not a chat transcript like the rest of the family. Four properties
matter before acting on any line:

- **Same escape-mangling** as the rest of `scratch/TWO/`: `read\_file`, `radeon.si\_support=0`,
  `\[Service\]`, `lspci \-k`, `**min_fan1_speed = 1100**` with the markdown bold *inside* the code
  span. Nothing here is pasteable. Anything we implement gets retyped from upstream docs.
- **It contradicts itself on the GPU model.** `F11 L7` says *"dual AMD FirePro D500s"*; `F11 L10`
  says *"D300/D500/D700"*; `F11 L32` says *"dual FirePro D-series"*. Our fleet is mixed — isis
  runs **D300s** (`ISIS_POWEROFF_RCA.md:140`), nephthys and hathor run **D700s** (`wq show 2501`).
  Every VRAM number in the doc is therefore unusable for us; use the per-node figures instead.
- **It contradicts itself on a repo owner.** `F11 L12` calls macpronix *"tarantula-org/macpronix"*
  in the link text and `F11 L13` links `github.com/yutila-org/macpronix`. One of the two is wrong;
  neither was verifiable from here (§6).
- **`F11 L15-17` is a verbatim restatement of `F11 L10-11`.** The `amdgpu` recommendation is made
  twice. It is one recommendation, not two.

Two claims in the framing section are worth checking separately:

- `F11 L3`: *"The fleet has been regularly running at a 3 [/10]. … We have a dozen machines."*
  Consistent with what we know — `INVENTORY_RECONCILIATION.md:19-20` enumerates ~25 machine files
  of which roughly a dozen are live. The premise — **idle capacity, not missing capacity** — is
  already the premise behind `wq#2076`, `wq#2444` and `wq#2501`. The doc adds urgency, not news.
- `F11 L5`: *"We have almost entirely resolved the local LLM dispatch issue."* Not adjudicated
  here; `wq#2448` (Wadjet joins the inference pool) is still **blocked**, and prefill on the two
  serving Macs is 13.6 and 7.4 tok/s (`research/inference_nodes.yaml:58,86`). Read it as Frank's
  framing, not as a closed ticket.

---

## 2. Every actionable recommendation, with a verdict

R-numbers are this digest's. "Verdict" is one of **NEW** (file it), **ALREADY DONE** (stop),
**SUPERSEDED** (an open ticket covers it better), **UNVERIFIED** (do not act on the doc's word),
**REJECT** (blast radius exceeds value).

| # | Cite | Recommendation | Verdict |
|---|------|----------------|---------|
| R1 | `L36-57 §"1. Enabling the amdgpu Driver"` | Boot with `radeon.si_support=0 amdgpu.si_support=1` to move the FirePros off the legacy `radeon` driver | **ALREADY DONE.** `ISIS_POWEROFF_RCA.md:140-142` records the standing rule since 2026-06-25: *"leave both GPUs amdgpu-bound with dpm=low on isis/hathor/nephthys/khepri"*, and `:163` reads `amdgpu` sysfs on isis. amdgpu binds today. Keep `lspci -k` as a *check*, not a change |
| R2 | `L53-57` | Verify with `lspci -k \| grep -EA3 'VGA\|3D'`, expect `Kernel driver in use: amdgpu` | **NEW, but tiny** — fold into wq#2501 step 1 as a precondition assert. Note isis will currently fail it: its cards are **PCI-removed**, not merely unbound (`ISIS_POWEROFF_RCA.md:160-164`) |
| R3 | `L61-77 §"2. Configuring Ollama for Dual GPUs"` | `Environment="OLLAMA_VULKAN=1"` in a `systemctl edit ollama.service` override, *"bypasses the default ROCm check"* | **UNVERIFIED — probably false for stock Ollama.** §6. Do not put this in a bundle. wq#2501 already picked the documented path (llama.cpp `GGML_VULKAN=1`) |
| R4 | `L77` | `OLLAMA_SCHED_SPREAD=1` so a large model splits VRAM evenly across both cards | **SUPERSEDED / no-op today.** This is a real Ollama variable, but it only does anything once Ollama can *see* a GPU, which is R3. And the split it buys is 2×6 GB on nephthys — `wq show 2501` already states 6 GB per GPU cannot hold anything we serve, so the ceiling is prefill offload, not residency |
| R5 | `L79-81` | `OLLAMA_HOST=0.0.0.0` to accept the LiteLLM proxy | **ALREADY DONE.** The gateway reaches both serving Macs over the tailnet today (`research/inference_nodes.yaml:41,73` give their tailscale IPs and resident models) |
| R6 | `L88 §"Container Orchestration"` | If serving from Docker/K3s, pass `/dev/dri` render nodes in and repeat the env | **Conditional on R3.** Also moot in the near term: the Macs serve from systemd, not containers |
| R7 | **`L90-105 §"3. Tuning mbpfan for AI Workloads"`** | Install `mbpfan`; set `min_fan1_speed=1100`, `max_fan1_speed=1900`, `low_temp=50`, `high_temp=70`, `max_temp=80`, `polling_interval=1` | **NEW — the whole value of the file.** §0. Zero prior mentions fleet-wide. Directly addresses the measured defect at `ISIS_POWEROFF_RCA.md:84-105`. → **T1** |
| R8 | `L105` | *"this daemon only polls the CPU core temperatures natively, so set lower thresholds to cool the GPUs sharing the same block"* | **NEW, and it is the important caveat, not a footnote.** mbpfan is blind to GPU temperature. On a shared thermal core that makes CPU temp a *proxy* for GPU temp — acceptable for CPU inference, unproven for the Vulkan workload wq#2501 wants. → folded into **T1** acceptance |
| R9 | `L21-22 §"System Tuning & Kernels"` | `wolffcatskyy/linux-mac`: custom kernel patchset — maps reboots to full power-offs (Apple EFI cold-boot bug: warm reboot leaves GPUs uninitialised, black screen), embeds AMD GPU firmware, BORE scheduler, BBR3 | **REJECT the patchset, harvest two pieces.** A bespoke kernel on four nodes is the highest-blast-radius change in the file and we would own it forever. BBR3 needs no patchset (`net.ipv4.tcp_congestion_control`, mainline). The reboot→power-off mapping is a kernel cmdline, not a patchset. → **T3**, question-first |
| R10 | `L24-25` | `gaul/mbpfan` as the specific implementation | **Use the recommendation, verify the source.** `gaul/mbpfan` is a fork; the long-standing upstream is `dgraziotin/mbpfan` and Debian/Ubuntu package it. **T1 resolves provenance before anything is installed** — it does not take the doc's link |
| R11 | `L12-13` | `macpronix`: declarative NixOS config turning the Trashcan into an immutable headless CI node (Tailscale mesh, patched wpa_supplicant, tuned mbpfan) | **REJECT as a deployment; read as a reference.** Re-imaging four serving nodes onto NixOS to get a fan curve is absurd. Its `mbpfan.conf` is a second opinion on T1's numbers and that is all we want from it. Owner name is also unresolved (§1) |
| R12 | `L7-8` | `Scottcjn/trashclaw`: local LLM agent for vintage Macs, 14 built-in tools (`read_file`, `git_commit`, shell), no cloud or API keys | **REJECT.** We have fworker, despag, governor and a scoped-key gateway. A 14-tool unsandboxed local agent is a step *down* from `harness/worker-settings.json`. Its only interest is as an existence proof that someone else runs agents on this exact hardware |
| R13 | `L29-30` | `linux-on-mac` org: audio routing fixes, `bcwc_pcie` (Broadcom PCIe / FaceTime camera) | **STOP — irrelevant.** Internal speakers and a webcam on four headless inference nodes. The doc itself says most of the org is upstreamed |
| R14 | `L32-34` | *"the dual FirePro D-series are too old for modern ROCm; Vulkan is the only path"* | **SUPERSEDED — and we already had better evidence.** `research/wq2076_macpro_gpu_recon.md` tested ROCm/OpenCL and recorded `compute stack: none`; `DIGEST_Orchestation_2_md.md:89-95` then found `github.com/ggml-org/llama.cpp/issues/20889` — a Vulkan crash report from *MacPro6,1 + FirePro D700 + Ubuntu 26.04 + RADV*, i.e. our exact board. That is a stronger citation than anything here |

---

## 3. Which fleet problems this solves

| Fleet problem | Where it lives | What `F11` contributes |
|---|---|---|
| **The MacPro6,1 fan never ramps under load** — 790 rpm at 84 °C / 84% CPU, 1157 rpm of unused headroom, on all three units | `ISIS_POWEROFF_RCA.md:84-105` — measured, then used only to falsify a hypothesis; no follow-up ticket exists | **Solves it.** `F11 L92-94` supplies the root cause (no ACPI↔SMC path) and `L96-103` supplies a complete config. This is the one place the doc beats us outright |
| **wq#2501 (Vulkan probe on the D700s) has no thermal precondition** | `wq show 2501` — three steps, none thermal, on a chassis with one fan for CPU + 2 GPUs | **Adds the missing gate.** `F11 L92` names the shared triangular thermal core; `ISIS_POWEROFF_RCA.md:139-142` records that we have already cooked this core once. wq#2501 must depend on T1 |
| **wq#2484 "Thermal backpressure: hold a subsystem when a node runs hot"** (open, p3, gated) | `wq list` | **Supplies its missing actuator.** Backpressure alone throttles *work*; mbpfan adds *cooling*. Ordering matters: cool first, throttle second, or we shed load we did not need to shed |
| **wq#2153 (retire `disable-gpus.service` on isis) and wq#2178 (gpu-lowpower bundle on hathor)** — both **blocked** | `wq list` | **Adjacent.** `F11` argues the cards are an *asset* (Vulkan), which is the opposite premise to "disable them for idle draw". `ISIS_POWEROFF_RCA.md` §5c already showed the disable path *raises* idle draw (isis 103.2 W with cards removed vs hathor 86.6 W with them bound). The doc corroborates; it does not unblock |
| **wq#2076 "MacPro6,1 dual-GPU R&D"** — blocked | `wq list` | **Corroborates, supersedes nothing.** R14 |
| **wq#2444 "Test D300 GPUs for resale, fund D700s"** — claimed | `wq list` | **One input.** If the D300s are worth keeping only when Vulkan works, wq#2501's result is a resale input. `F11` does not help decide; note the dependency and move on |
| **Fleet idle at 3/10 with a dozen boxes** | `F11 L3`; `INVENTORY_RECONCILIATION.md:19-20` | **Frames it, does not solve it.** The doc's own answer (Vulkan) is capped at prefill offload on 6 GB cards |
| **Three unexplained abrupt power losses on isis** | `ISIS_POWEROFF_RCA.md` §0 — *"not yet explained and point at hardware"* | **A weak lead, honestly labelled.** `F11 L21` describes an Apple EFI *cold-boot* bug — warm reboot → uninitialised GPUs → black screen. That is a display symptom, not a power cut, and does not match our trace. → **T3** checks for the symptom before anyone touches a kernel |

---

## 4. BUILD tickets to file

**All four are filed.** T1 = `wq#2508`, T3 = `wq#2509`, T4 = `wq#2510` (deps 2508); T2 was a
`wq note` + `wq edit --deps 2508` on the existing `wq#2501`. The specs below are what went in.

### T1 — `mbpfan` thermal control on the MacPro6,1 nodes (`wq#2508`, lane `fleet`, p1)

**The one ticket that must be filed from this doc.**

Problem, measured: `ISIS_POWEROFF_RCA.md:84-105`. All three online MacPro6,1 units hold 789-797
rpm regardless of load, `fan1_manual=0`, `fan1_min=790`, `fan1_max=1947`, with `Te0T` at 81-84 °C
under 60-84% CPU. Root cause per `F11 L92-94`: stock Linux has no ACPI path to the Apple SMC, so
the fan sits at the SMC floor. Nothing in `/home/lumen/foreman|kb|bin|agents` mentions `mbpfan`.

Scope, in order:

1. **Resolve provenance before installing anything.** Check `apt-cache policy mbpfan` on the
   target first — a distro package is the preferred source. If packaging is needed, the upstream
   is `dgraziotin/mbpfan`, **not** the `gaul/mbpfan` fork the doc links (`F11 L24`). Record which
   source was used and its version in the ticket notes.
2. **Stage on nephthys** (`100.98.74.87`, `power_policy: warm`, `research/inference_nodes.yaml:71`)
   — *not* hathor, which is `always-on` serving (`:43`, Frank 2026-09-20). Same node wq#2501 picked,
   same reason.
3. Write `/etc/mbpfan.conf` from `F11 L98-103`, with one correction: the doc gives
   `max_fan1_speed = 1900` as *"the physical maximum"*; our own measurement says **1947**
   (`ISIS_POWEROFF_RCA.md:100`). Use 1900 anyway — a 47 rpm margin below the SMC ceiling is
   deliberate, not an error. Full set: `min_fan1_speed=1100`, `max_fan1_speed=1900`,
   `low_temp=50`, `high_temp=70`, `max_temp=80`, `polling_interval=1`.
4. **Confirm mbpfan actually takes the fan.** It must set `fan1_manual=1` and write `fan1_output`;
   if `fan1_manual` stays `0` the daemon is running and doing nothing, which reads as success in
   `systemctl status`. Assert the flag explicitly.
5. Soak under real load — a `llama-bench` or an actual served request against `nephthys-qwen14b` —
   and capture, from netdata's retained `sensors.*` series (the RCA used exactly these and they
   survive reboots): `fan_applesmc-isa-0300_fan1_Main_input`, `Te0T`, `TC0P`, `TM1a`, `system.cpu`.
6. Record **idle** watts from `/home/lumen/glance/assets/plugs.json` before and after. A higher
   fan floor costs power; the RCA's power table (§5c) is the comparison baseline. If the delta is
   large, say so — it is an input to wq#1981 (idle-shutdown guard) and wq#2484.

Acceptance: a before/after trace where fan rpm moves off 790 as `Te0T` crosses 50 °C, peak `Te0T`
under sustained load is **lower** than the 84.1 °C at `ISIS_POWEROFF_RCA.md:87`, `fan1_manual=1`
is asserted, and idle watt delta is recorded with a number. Cite netdata series names and
timestamps. Then, and only then, propose rollout to hathor and isis as a separate ticket.

Explicitly out of scope: GPU temperature. `F11 L105` concedes mbpfan polls CPU cores only. On a
shared thermal core CPU temp is a *proxy*; T1 must state in its verdict whether that proxy holds
under a GPU-heavy load, because **T2 depends on the answer**.

### T2 — gate wq#2501 on T1 (ticket edit, not a new build) — **done**

`wq note 2501` recording: the Vulkan probe lights both GCN 1.0 cards on a chassis that shares one
triangular thermal core between CPU and both GPUs (`F11 L92`), whose fan has never left its floor
(`ISIS_POWEROFF_RCA.md:84-105`), and which we have already overheated once by changing GPU state
(`:139-142`). Add `deps=<T1>`. Add to wq#2501 step 1 the `lspci -k` precondition assert from
`F11 L53-57` — and note that **isis would fail it today** because its cards are PCI-removed
(`:160-164`), which is one more reason the probe belongs on nephthys.

Also fold in the R3 correction: if anyone reaches for `OLLAMA_VULKAN=1` from this doc, it is
unverified (§6) — wq#2501's llama.cpp `GGML_VULKAN=1` path is the documented one and stays.

### T3 — MacPro6,1 warm-restart black screen: does the symptom exist here? (`wq#2509`, lane `fleet`, p3)

Question-first; **do not build a kernel**. `F11 L21` claims an Apple EFI bug where warm reboots
leave the FirePros uninitialised (black screen), which `wolffcatskyy/linux-mac` fixes by mapping
reboots to full power-offs.

1. Search the Macs' journals for the symptom: reboots after which amdgpu fails to initialise a
   card. If it has never happened, **close the ticket** — we would be treating a bug we do not have.
2. If it has: the fix is a kernel *cmdline* (`reboot=` mode), not a patchset. Test that. The
   custom kernel stays rejected (R9).
3. Separately and independently of 1-2, check `net.ipv4.tcp_congestion_control` on the fleet. BBR3
   is mainline; if we are on `cubic` that is a one-line sysctl worth its own small ticket, and it
   has nothing to do with Macs.

Do **not** attach this to the three unexplained isis power losses. A black screen is not a power
cut, and `ISIS_POWEROFF_RCA.md` ruled out every software cause it tested.

**Status (wq#2509 attempt a2, 2026-09-24): blocked on dispatch, not answered.** The ticket was
filed with `needs:` empty, so `fworker.resolve_exec_grant()` never wrote a `grant.json` for the
attempt (`fworker.py:1820-1832`) and every `fleet-exec run <node>` call on isis/hathor/nephthys/
khepri denied `grant_missing_env` before any argv was even evaluated — none of steps 1-3 above
could be run, including the local-only half of step 3 (`/proc/sys/net/ipv4/tcp_congestion_control`
sits outside the build worker's allowed dirs). Fixed the dispatch gap
(`wq edit 2509 --needs "ssh(isis),ssh(hathor)"`) and released the ticket for redispatch rather than
closing it: closing as "symptom never happened" without ever reading a journal would be a guess,
not a verification (`kb/ISSUE-LOG.md` #670 has the full root cause). nephthys and khepri are not
in `fleet-exec-policy.yaml`'s `ssh:` allowlist at all — that file lands only by PR, so reaching
them needs a separate, reviewed policy change (mirroring the existing hathor-pilot entry) before
a future attempt can cover all four named nodes; until then a redispatch can only check isis and
hathor.

### T4 — fan rpm is measured, alarmed on nothing (`wq#2510`, lane `fleet`, p2, deps 2508)

`ISIS_POWEROFF_RCA.md:84` proves netdata already retains
`sensors.fan_applesmc-isa-0300_fan1_Main_input` and the `Te0T`/`TC0P`/`TM1a` series on these
nodes. A human had to hand-query them during an RCA to discover a two-day-old thermal condition.

Add an alarm: MacPro6,1 node sustains `Te0T > 80 °C` for N minutes **while** fan1 stays under
1000 rpm → that is the exact "SMC floor under load" signature, and after T1 it also becomes the
"mbpfan died" detector. Feed it to wq#2484's thermal-backpressure hold rather than inventing a
second path. Cross-ref `wq#1016` (Thor dead-fan thermal emergency, blocked, physical): a fan alarm
that fires on Thor would have caught that one too, but **Thor is never to be restarted or
interrupted** — the alarm notifies, it does not act, on Thor.

---

## 5. What we can STOP doing

1. **Stop planning to set `radeon.si_support=0 amdgpu.si_support=1`.** amdgpu already binds both
   cards on isis/hathor/nephthys/khepri and has since June (`ISIS_POWEROFF_RCA.md:140-142`). Any
   future Mac ticket that proposes this GRUB edit as *work* is redoing a solved thing. Demote it to
   a one-line precondition check (R2). **This also means the doc's headline recommendation, the
   thing it repeats twice (`F11 L10-11`, `L15-17`), is a no-op for us.**
2. **Stop treating "the Macs have no compute stack" as final.** `DIGEST_Orchestation_1_md.md:20`
   generalised `wq2076_macpro_gpu_recon.md`'s `compute stack: none` into *"the AMD D700s lack
   modern ROCm support"* and therefore every GPU line is void. Correct for CUDA/ROCm/OpenCL, and
   `DIGEST_Orchestation_2_md.md:89-95` already flagged it as too broad for RADV. This doc is the
   second independent source saying so. wq#2501 owns the correction; stop re-deriving it.
3. **Stop reading `ISIS_POWEROFF_RCA.md`'s fan table as a closed question.** It was collected to
   kill a hypothesis and it did. The data also contains an unfixed defect that has been sitting in
   the file since 2026-09-20. T1 closes it.
4. **Stop considering NixOS/macpronix, TrashClaw, or the `linux-on-mac` drivers.** R11-R13. Webcam
   and speaker drivers on headless inference nodes; a re-image to obtain a fan curve; a 14-tool
   local agent competing with fworker. Logged so nobody re-evaluates them in three weeks.
5. **Stop writing `OLLAMA_VULKAN=1` into any bundle or runbook** until §6 is resolved. If it is not
   a real upstream variable, it is a systemd override that silently does nothing — the worst
   failure shape we have, because `systemctl status` reports green.

---

## 6. Unresolved — and why

`fleet-search web` returned `{"ok": true, ... "results": []}` for **four** distinct queries this
session (`ollama vulkan support`, `Ollama OLLAMA_VULKAN environment variable ...`,
`ollama OLLAMA_SCHED_SPREAD ...`, `mbpfan macbook fan daemon linux coretemp`) while
`fleet-search github` on the same terms returned results normally. Logged as `kb/ISSUE-LOG.md` #667
— the failure shape matters beyond this ticket: `ok:true` with an empty array is indistinguishable
from *"the world has no answer"*, so it discharges the mandatory prior-art step without searching.
So the web verifications below are **incomplete by tooling, not by effort**:

- **`OLLAMA_VULKAN=1` (R3) — the important one.** Not confirmed as an upstream Ollama variable.
  The strongest signal available: `fleet-search github "ollama vulkan"` surfaces
  **`wilgnne/ollama-vulkan`** — *"Dockerized Ollama with Vulkan support"* (10 stars, updated
  2026-02-14). A third-party fork existing to add Vulkan is evidence that stock Ollama **lacks**
  it. Treat the doc's `L73` as wrong until someone greps the Ollama release notes. Note this
  intersects `wq#1511` (open: update Ollama to v0.33.1 fleet-wide) — if Vulkan landed upstream, it
  landed in some version, and wq#1511 is where that gets established.
- **`gaul/mbpfan` vs `dgraziotin/mbpfan` (R10).** Neither surfaced in a github search that did
  return four lesser `mbpfan*` repos. T1 step 1 resolves this on the box with `apt-cache policy`.
- **macpronix owner (§1).** `tarantula-org` vs `yutila-org`. Moot given R11 rejects it.
- **Live driver/fan state was not probed.** `ssh` is not on this BUILD worker's allowlist —
  already logged as `ISSUE-LOG` #662, not re-filed. Every hardware claim in this digest is
  therefore sourced from `ISIS_POWEROFF_RCA.md`, `inference_nodes.yaml` and `wq show`, all of which
  cite live instruments from 2026-09-20. T1 re-measures on the box.
- **Filing T3 itself hit a wall**, logged as `kb/ISSUE-LOG.md` #668: `guard.sh` blocked the
  `wq add` because the *ticket prose* described an Apple EFI bug involving a warm restart — the
  denylist substring-matches verbs anywhere on the command line, including inside a quoted
  `--detail`. Reworded and filed as `wq#2509`. Worth knowing before writing any future digest
  ticket about power, restarts or thermal shutdown.

### Adjacent prior art surfaced while verifying (not from the doc)

`fleet-search github "ollama vulkan"` also returned two repos worth a note, flagged as **mine, not
the doc's**, so nobody mis-attributes them:

- **`akandr/bc250`** (378 stars, updated 2026-09-24) — *"AMD BC-250 (PS5 APU) setup guide — Ollama
  + Vulkan inference"*. We own BC-250 blades. Corroborates rather than adds:
  `reports/bc250-llamacpp-vulkan-rocm-20260709.md:10-14` already established Vulkan-only /
  ROCm-dead on gfx1013, and `wq#120` is blocked on *"no hardware yet connected"* — a physical
  task, not a research gap.
- **`ferrumox/fox`** (188 stars, updated 2026-09-24) — *"Drop-in replacement for Ollama … Requests
  that share a prompt reuse each other's KV cache instead of each prefilling it."* Worth a look on
  its own merits: **prefill is our measured bottleneck** (13.6 and 7.4 tok/s,
  `research/inference_nodes.yaml:58,86`) and fleet workers share a large identical system prompt,
  which is the exact shape this targets. Out of scope for wq#2469 — raising it, not filing it.

---

## 7. Provenance

- Doc read in full: 107/107 lines, 2026-09-24.
- Cross-referenced: `ISIS_POWEROFF_RCA.md`, `DIGEST_Orchestation_2_md.md`,
  `DIGEST_Orchestation_1_md.md`, `DIGEST_Fleet_Monitoring_2_md.md`, `LOCAL_MODEL_STRATEGY.md`,
  `INFERENCE_NODE_REGISTRY.md`, `INVENTORY_RECONCILIATION.md`, `research/inference_nodes.yaml`,
  `kb/ISSUE-LOG.md`, `wq show 2501`, `wq list`.
- Negative result, stated because it is the load-bearing claim of §0: `grep -rn "mbpfan"` over
  `/home/lumen/foreman` returns **nothing**; the same term across `/home/lumen/kb`, `/home/lumen/bin`
  and `/home/lumen/agents` likewise. The recommendation is genuinely new to this fleet.
