# wq#4176 — verify wq#4166 EVALUATE: Cline agentic coding platform

Archetype 8 adversarial verify pass. Author: claude-sonnet-5. Verifier: claude-sonnet-5@high
(fresh instance, same model as author).

PR: #2114 (merged to `main` as b2b48934). Deliverable is docs-only —
`research/WQ4166_CLINE_EVAL.md` (+94), no code/config touched.

## Claim under test

Doc verdicts **NO-GO** on Cline as an fworker alternative (the ticket's own framing: local
models only, zero API spend). Reasoning: that exact experiment already ran under Claude Code
as the harness (wq#2576/wq#2583, 0/166 local sessions SUCCESS) and the measured bottleneck is
box/model capacity (prefill latency, small-model FALSE_COMPLETE, Isis CPU-only inference), not
the orchestrating CLI — so swapping the harness would reproduce the same failure, not a new
data point. Also finds an infra-access gate (no Node.js/npm, no install verb) identical to the
prior wq#4158 Scrutiny eval, and checks Cline's claimed MCP/audit-trail/Plan-Act features
against what fworker already has.

## What I checked

Re-derived every citation independently rather than trusting the doc's transcription of its
own sources:

- **`research/LOCAL_RUNG_AUDIT_2576.md:9`** — line 9 reads "0 of 166 local fworker sessions
  were classified SUCCESS" verbatim.
- **`:53-56`** — per-model failure counts (hathor-llama8b 32 FALSE_COMPLETE/16 HANG;
  nephthys-qwen14b 24 HANG/11 KILLED; qwen36-local 45 HANG/14 CRASH) and the 13.6 tok/s
  prefill / ~390s-to-first-token / `first_frame_s` medians (1500/2400/900) all match the
  source doc exactly.
- **`research/models.yaml:369,373`** — `mechanical-edit: [sonnet-5@low]` and
  `probe-build: [sonnet-5@medium, haiku-5-5]`; `local-worker` is absent from both, matching
  the "pulled from both chains" claim (wq#2583).
- **`research/fleet_hardware.json:62`** — Isis GPU field reads "2x AMD FirePro D300 (Curacao
  XT), dpm=low, NOT inference devices — Qwen3-32B runs on CPU" verbatim. Cross-checked the
  doc's `qwen3.6:35b-a3b` model-name claim against `models.yaml:275` (wq#777 re-probe: "Isis
  /api/tags answers and lists qwen3.6:35b-a3b") — consistent with the `qwen3:32b class`
  hardware note, not a mismatch.
- **`research/models.yaml:284-287` / `:330`** — 4,910-token prompt / 277s prompt-eval / load
  average 20.06 (wq#1623) and `qwen36-local`'s `status: down` both match at the cited lines.
- **`docs/limpet_readme.md:68`** — "The energize-confirm trust gate" bullet, with the
  JTAG/SWD/flash-writes/GPIO-reset-lines scope, is exactly where cited. The doc's rebuttal
  that this is Limpet's hardware-RE doctrine term, not a coding-agent plan/approval split,
  holds — Limpet's own doc makes no mention of a coding CLI.
- **`harness/fleet-exec-policy.yaml:42`** — "sudo is v2 (fleet-exec has no `sudo` verb yet)"
  verbatim at the cited line.
- **`research/WQ4158_SCRUTINY_SMART_MONITORING_EVAL.md:74-92`** — the cited range states the
  identical install-access gate (no `sudo`/install verb, read-only `ssh:` allowlist) and the
  `wq2844-netdata-macs/install.sh` precedent the Cline doc leans on.
- **No Node.js/npm footprint** — `grep -rl "node_modules\|package.json\|npm install\|npm i "`
  across `fleet-config/` and `docs/` returns zero hits, matching the doc's claim.
- **External Cline facts, re-searched from scratch rather than trusting the doc's own
  citation**: `fleet-search web` confirms `npm install -g cline` is the real install command
  (docs.cline.bot/getting-started/installing-cline and npmjs.com/package/cline, both
  independent of the doc's citation); confirms community guidance naming Qwen3-Coder 30B /
  Devstral Small 24B as the reliable-tool-calling bar for local models (matterai.so,
  localaimaster.com); confirms SSO/SCIM/audit-logging is gated to Cline's Enterprise tier
  per a third-party comparison (qodo.ai), consistent with the doc's "enterprise-tier" framing
  of audit trails.
- **Acceptance probe re-run**: `python3 harness/fworker.py verify-evidence --ticket 4166
  --worktree .` → `{"ok": true, "failed": [], "checked": [{"id": "AC1", "ok": true, ...}]}`,
  independently reproduced, not just read from the PR snapshot.
- **Ticket-framing fidelity** — `wq show 4166`'s `detail` field (Cline's feature list, "zero
  API spend" framing, github.com/cline/cline) matches what the doc actually addresses; the
  doc isn't answering a different question than the one asked.

### Gap found, not a contradiction

`_runs/wq4166/usage-1.json` shows `server_tool_use: {web_search_requests: 0,
web_fetch_requests: 0}` for the authoring session — Anthropic's native web-search/fetch tool
was never invoked. `episode.jsonl` only logs an `attempt_end` summary (no per-turn tool-call
trace), so I can't confirm from local evidence that the authoring session's `fleet-search`
Bash call actually ran with the exact query string the doc cites (`fleet-search` runs over
Bash, which wouldn't show up in `server_tool_use` either way, so the zero count doesn't prove
absence). This means the specific historical search invocation is unauditable from this
worktree. It doesn't change the verdict: I independently re-ran equivalent `fleet-search`
queries myself (above) and every external factual claim the doc makes from that search —
install command, local-model size bar, enterprise-tier audit gating — came back true on fresh
evidence, not just on faith in the original transcript.

## Falsifiability note

The probe here is independent re-derivation: every `file:line` citation was opened and read
rather than cross-checked against the doc's own quoting of itself, the external Cline claims
were re-searched from scratch, and the acceptance script was re-run rather than trusted from
its logged output. Nothing came back contradicted; one gap (unauditable original search
transcript) was surfaced and did not survive as a contradiction once independently
re-verified.

## Verdict

**CONFIRMED.** Every checkable citation in `research/WQ4166_CLINE_EVAL.md` — the local-rung
audit numbers, the routing-chain yaml, the hardware/model-registry facts, the limpet
energize-confirm definition, the fleet-exec-policy and WQ4158 precedent citations, and the
external Cline facts — matches independent re-verification. The NO-GO verdict's core argument
(the measured bottleneck is box/model capacity, not the orchestrating CLI, so Cline would
reproduce the same failure) is sound and not undermined by anything found here. No evidence
the verdict or its supporting citations are wrong.

No contradiction found; not escalating to judgment.
