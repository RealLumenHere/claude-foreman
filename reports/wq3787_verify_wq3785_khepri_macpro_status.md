# wq#3787 verify-pass: wq#3785 "Verify khepri Mac Pro status and role"

**Verdict: CONFIRM.** PR http://100.121.17.5:3000/lumen/claude-foreman/pulls/1782 (merged
`93408b2510` / cherry-picked as `60d28357` onto main) adds exactly one file,
`reports/wq3785_verify_khepri_status_role.md` (69 insertions, no code). Every live-check
claim in it reproduces independently this session; the one claim it flags as unverified
stays correctly unverified under a broader check.

## Diff shape

`git show --stat 93408b2510` / `60d28357`: 1 file changed, 69 insertions(+), 0 deletions.
Docs-only, no code path touched — the archetype-8 blast radius here is "is the report
true," not "does anything break."

## Re-run of each live-check claim (this session, 2026-10-13 ~07:38 UTC, ~4-8 min after the
original pass)

1. **Reachability** — `curl -m8 http://100.120.16.78:11434/api/tags` → HTTP 200, body lists
   `qwen2.5-coder:7b`, `modified_at: 2026-10-07T07:09:24Z`. Matches report verbatim.
2. **Resident model** — `curl -m8 http://100.120.16.78:11434/api/ps` → `qwen2.5-coder:7b`,
   `size_vram: 4109212055` (exact match to the report's figure), `expires_at:
   2026-10-13T09:31:54Z`. Later than the report's `09:21:37Z` by the expected delta — the
   keep-alive window renewed between the two probes, consistent with an actively-served
   model rather than a stale snapshot.
3. **Idle load/power** — netdata `100.120.16.78:19999`:
   - `system.load` → `load1` 0.015–0.32 across the last 10s window — idle, same order of
     magnitude as the report's `0.015`.
   - `system.cpu` → user+system well under 2% per sample — idle.
   - `cpu.powercap_intel_rapl_zone_package-0` → 17.7–20.7W — matches the report's 18.16W.
   - `sensors.temperature_amdgpu-pci-0200_temp1_edge_input` → 44°C exactly, matching the
     report's figure.
4. **Recorded-fact cross-check** — `lumen_state.py current khepri`: `ollama_version = 0.40.0
   (since 2026-10-07)`, `tailscale_ip = 100.120.16.78` — both match the report's citations.
5. **Hardware cross-check** — `research/fleet_hardware.json:82-93`: Mac Pro 6,1, Xeon E5-2697
   v2 12C/24T, 64GB DDR3 ECC, 2x FirePro D500/Tahiti LE, 256GB blade — matches the report and
   `lumen_state.py` verbatim.

## Checking the one flagged-unverified claim (LiteLLM routing)

The report says grepping `ops/inference-registry/litellm_local_block.yaml` and
`ops/wq3212-litellm-dead-rungs/` turns up no `khepri`/`qwen-local` hit. Reproduced: both
greps return nothing. I widened the search beyond the two files the report checked —
`grep -rl khepri` across every `*.yaml`/`*.yml`/`*.json` in the repo — and the only hits are
`fan_floor_alarm.yaml`, `aer_storm_alarm.yaml`, `wq1613-minerva-verifier-registry/registry.yaml`,
and `research/fleet_hardware.json`, none of them LiteLLM routing config. This *strengthens*
the report's hedge rather than contradicting it: there is no LiteLLM route to khepri
anywhere in the repo, so the "qwen-local rung" role fact (also present verbatim in
`fleet_hardware.json`) is, at best, aspirational or managed outside this repo — correctly
left as an open thread rather than asserted either way.

## Access-note claims (fleet-exec denial precedent, ssh allowlist)

- `harness/fleet-exec-policy.yaml:189` (actual line number in the current tree; report said
  "even today") — `ssh: [lumen, isis, bes, anubis, maat, sobek, hathor, ptah, sekhmet, horus,
  neith, khnum]`. khepri is absent, confirmed.
- `kb/ISSUE-LOG.md` row 670 (wq#2509, 2026-09-24) — confirmed present, matches the cited
  `grant_missing_env` gap and the same ssh-allowlist absence, for the same four scope nodes
  including khepri.
- `wq show 3785` — `needs:` is empty, confirming the report's stated reason it could not use
  `fleet-exec run khepri` and had to go direct-HTTP over tailnet instead.

## Attempt to break it

- Tried widening the LiteLLM grep past the two files the author checked (above) — no
  contradiction, hedge holds.
- Checked the merge commit (`93408b2510`) matches the PR's stated content and that
  `wq show 3785`'s `notes:` field (merge SHA, figures) matches both the commit and the live
  re-probe — no drift.
- No code changed, so there is no second-order behavior (routing, alerting, power
  management) that this report's merge could have broken. The only way this ticket fails is
  if its factual claims don't hold up, and they do.

## Decision

**Confirm.** No contradiction found; every reproducible claim reproduced, and the one
claim the author couldn't verify is still correctly flagged as unverified after a broader
check. No escalation to opus-5@high warranted.
