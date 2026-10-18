# wq#4166: Cline agentic coding platform — evaluation

## Verdict: NO-GO as an fworker alternative. The ticket's premise was already tested on this fleet, under a different harness, and failed.

The ticket frames Cline as "a potential fworker alternative using local models only (zero API
spend)." That exact experiment — point an agentic coding CLI at the fleet's local Ollama
models through the LiteLLM gateway and have it close real tickets — already ran, for 30+
days, across 166 sessions, under Claude Code as the harness
(`research/LOCAL_RUNG_AUDIT_2576.md:9`, wq#2576):

> "Across all of `_runs`, 0 of 166 local fworker sessions were classified SUCCESS."

The failure modes are per-box, not per-harness: `hathor-llama8b` (32 FALSE_COMPLETE, 16 HANG —
the 8B model declares "done" after ~2 calls), `nephthys-qwen14b` (24 HANG, 11 KILLED), and
`qwen36-local` (45 HANG, 14 CRASH) (`research/LOCAL_RUNG_AUDIT_2576.md:53-56`). The audit traced
HANG to prefill latency — a 5.3k-token median worker prompt takes ~390s to first token at
hathor's measured 13.6 tok/s prefill, with `first_frame_s` medians of 1500s (hathor)/2400s
(nephthys)/900s (qwen36) — and the fleet already acted on this: `local-worker` was pulled from
both the `mechanical-edit` and `probe-build` routing chains (`research/models.yaml:369,373`,
wq#2583), with re-entry gated on passing the macbench agentic suite at >=60% success.

None of this is specific to Claude Code as the orchestrating CLI. Swapping the harness for
Cline changes which process sends the HTTP request to the same Ollama backend over the same
LiteLLM gateway; it does not change the box's prefill throughput, the 8B/14B models' tendency
to self-report completion after two turns, or qwen36-local's context ceiling. Re-running this
exact test with Cline as the driver would reproduce the same measured bottleneck, not a new
data point.

## Hardware check: the fleet's local pool doesn't meet Cline's own stated bar

Prior-art search (`fleet-search web "Cline local model Ollama performance poor tool calling
reliability small models"`, 2026-10-18) surfaces Cline's own community guidance that reliable
agentic tool-calling needs roughly 24B-30B-class models (Qwen3-Coder 30B, Devstral Small 24B)
— smaller models are the ones reported as unreliable at tool calls, which matches the fleet's
own measured FALSE_COMPLETE/HANG pattern above.

Checked against `research/fleet_hardware.json` and `research/models.yaml`:
- `hathor-llama8b` (8B) and `nephthys-qwen14b` (14B) are both well under that bar.
- The one model large enough, `qwen3.6:35b-a3b` on Isis, runs **CPU-only** —
  `research/fleet_hardware.json:62`: "2x AMD FirePro D300 (Curacao XT), dpm=low, NOT inference
  devices — Qwen3-32B runs on CPU" — and is already measured queue-starved under any
  concurrent load: a 4,910-token prompt spent 277s in prompt eval alone at load average 20.06
  (`research/models.yaml:284-287`, wq#1623). Its registry entry also carries `status: down`
  (`research/models.yaml:330`).

Pointing Cline at this same rung would not be a fresh test of a bigger/better-suited model; it
would be the identical, already-down rung in the registry.

## Claimed features checked against what fworker already has

- **MCP native**: not a gap. Claude Code sessions on this fleet already drive MCP servers
  (Commander MCP is the live example this very session runs under); there is no missing
  capability here for Cline to fill.
- **Full audit trails**: not a gap either. Every fworker session already writes a stronger
  trail than Cline's open-source task-history/checkpoints feature (confirmed via search as the
  OSS-tier offering, with SSO/compliance-grade audit logging reported as an enterprise-tier
  feature on top): per-ticket `_runs/<id>/attempt-N.json` for every turn, a pinned
  `evidence.json` with citations checked against the actual diff, the append-only
  `/home/lumen/kb/ISSUE-LOG.md`, and ordinary PR history. Adopting Cline would not add
  auditability the fleet lacks.
- **Plan/Act "like energize-confirm"**: the ticket's analogy doesn't hold up. "energize-confirm"
  is Limpet's hardware-RE doctrine term for gating actions that physically energize hardware —
  JTAG/SWD, flash writes, GPIO/reset lines (`docs/limpet_readme.md:68`) — not a coding-agent
  plan/approval split. The actual fworker analogue to Cline's Act-mode approval gate is the
  PreToolUse deny-list hook plus the done-evidence gate that already runs on every ticket in
  this harness. Plan/Act is a reasonable UX pattern on its own merits, just not the same
  mechanism the ticket compares it to.

## Install-access gate, same shape as prior eval tickets

Cline's CLI ships as `npm i -g cline` (prior-art search result, cline/cline README). No
Node.js/npm footprint exists anywhere in `fleet-config/` today (checked by grep across
`fleet-config/` and `docs/`), and `harness/fleet-exec-policy.yaml`'s `run_readonly_cmds`
allowlist has no install verb at all ("sudo is v2 (fleet-exec has no `sudo` verb yet)" —
`harness/fleet-exec-policy.yaml:42`). That's the identical infrastructure-access gate the
Scrutiny eval hit (wq#4158, `research/WQ4158_SCRUTINY_SMART_MONITORING_EVAL.md:74-92`): a
headless build worker cannot install anything new on a fleet node; it would need an
interactive session with real ssh access, following the `wq2844-netdata-macs/install.sh`
precedent, not something this ticket's execution context can do itself.

## What would actually be worth testing (not this ticket, not this execution context)

If someone wants to re-open the local-rung question, the lever that matters is model capacity
and inference backend, not the orchestrating CLI: Isis needs either a real GPU/CUDA path or to
stay off the critical path, and any candidate model needs to clear the macbench agentic-suite
bar wq#2583 already set (>=60% success, <=10% FALSE_COMPLETE) before it's worth pointing any
CLI — Cline, Claude Code, or otherwise — at it for ticket work. That's a hardware/model
procurement question, not a harness-choice question, and it isn't gated on Frank's input; it's
simply unfunded/undone work, tracked by the existing macbench re-entry bar.

Separately, Cline-as-a-human-driven-VS-Code-extension against a cloud model key (Claude, GPT)
is a legitimate, different product (developer IDE assistant, not an autonomous fleet worker).
That's an individual tool preference, not a fleet-automation decision, and out of scope for a
build-worker ticket to adopt unilaterally.
