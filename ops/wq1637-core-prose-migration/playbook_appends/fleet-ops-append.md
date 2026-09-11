Target: /home/lumen/instructions/playbooks/fleet-ops.md — append at end of file.
Source ticket: wq#1637. Do not apply until instincts_candidates.yaml's matching tier-2 entries
(fleet-state-markers, gpu-b-gating-and-initramfs-ban, reflector-hard-guarantees,
memoryd-identity-trust, aq-escalation-domain) are merged — this content is what those trigger
indices point to.

---

## STATE MARKERS (from claude_memory.md, 2026-05-18)
Fleet entries use markers: [DEPLOYED], [DEPLOYED-BUT-OFFLINE], [PLANNED] (hardware exists,
role documented, NOT in service — treat as future state, never current capability),
[SUPERSEDED]/[HISTORICAL], [RETIRED]. No marker = pre-2026-05-18, unaudited — verify before
relying on it. If an entry has no marker or is [PLANNED], do not write a prompt assuming
the host/service is available: probe with `tailscale status` + SSH first, or add an explicit
precondition step.

## GPU-B GATING BAN — amdgpu-both-bound Macs (2026-06-25 incident)
Do NOT gate GPU-B (setpci D3 / driver_override / unbind) on Isis, Hathor, Nephthys, or
Khepri — all four have amdgpu bound to BOTH D300 GPUs, and gating caused
`pci_channel_io_perm_failure` plus a real overheating incident on Isis+Hathor 2026-06-25.
Leave both GPUs amdgpu-bound, dpm=low. (Wadjet is the one exception: its GPU-B is naturally
absent, so its gate is harmless — do not generalize from Wadjet.)

NEVER edit initramfs on a headless node remotely. It bricked Hathor's boot 2026-06-25
(no local console, unrecoverable over a flaky MCP tunnel; needed Frank physically at the
box). For GPU-B boot determinism, use a udev rule instead
(`/etc/udev/rules.d/01-gpu-b-override.rules`, `ACTION=="add", SUBSYSTEM=="pci", KERNEL=="..."`)
— it can lose the coldplug race against amdgpu without bricking the boot.

## REFLECTOR HARD GUARANTEES (lumen_reflector.py)
Proposal-only LLM reflection over recent episodes. Never mutates claude_memory.md, the state
store, or the episodic store — only writes `dream/reflections/reflection-<date>.{md,json}`.
Fail-loud on any LLM error (ntfy alert + non-zero exit + no report — a silent/empty reflection
must never read as "nothing to improve"). Output is human-gated; promote lessons via
`lumen_episode.py commit`, never auto-applied. Model: deepseek-v4-pro via LiteLLM gateway,
never messenger-chat/messenger-re.

## MEMORYD IDENTITY TRUST
Identity into lumen-memoryd (127.0.0.1:8139) is derived server-side, never read from the
request body — a lane cannot spell its own name. Do not add a client-supplied identity field
to any lumen-memoryd call.

## AQ ESCALATION SUPPRESSION (wq#965)
Once an aq (alert-queue) row escalates — `source='probe' AND priority='high' AND
status='open' AND count>=3` — `aq_escalate.py` fires one P0 ticket + one urgent ntfy page,
then suppresses further escalation on that row while it stays open. Never build a second path
that re-pages an already-escalated open row; `lumen-aq-escalate.timer` runs every 10 minutes
and owns this.

## HARDWARE-RE RESEARCH GATE (wq#991 / wq#1006, 2026-08-27)
Any fworker project with `hardware_class` + `phase_gates:[research]` RED-blocks every device
command (adb/fastboot/heimdall/edl/flashrom/etc.) until `projects/<name>/research/PRIOR_ART.md`
is non-empty — enforced in `foreman/harness/action_gate.py`; hand-writing that file is
tamper-RED. To clear the gate: `python3 /home/lumen/foreman/harness/research_phase.py
--project <name>`, or a single `fleet-search --project <name> ...` also satisfies it.
