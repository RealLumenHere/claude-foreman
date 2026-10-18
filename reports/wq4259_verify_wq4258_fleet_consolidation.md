# wq#4259 — verify wq#4258: fleet consolidation (Mac retirement + RTX 3090 inference)

**Verdict: CONFIRM**

Author: claude-sonnet-5 (wq#4258, PR http://100.121.17.5:3000/lumen/claude-foreman/pulls/2175, merged 183d7b5d57)
Verifier: claude-sonnet-5@high (fresh instance, archetype 8 adversarial verify-pass)

## What wq#4258 claimed

The ticket is an 8-phase project (benchmark Macs, add Claude models to
LiteLLM, reroute aliases, retire Macs, part out hardware, buy+install RTX
3090s, configure local inference, drop API dependency). The PR delivers
only `ops/wq4258-fleet-consolidation-plan/{README.md,litellm_config_snippet.yaml}`
and claims:

- Phase 1 (Mac benchmarking) is already `done` under wq#2246/2651/2815/3794,
  not "wq running" as the ticket text says — nothing to build.
- Phase 2 (add Claude Sonnet + Haiku to LiteLLM) is delivered as a
  ready-to-merge snippet, blocked only on the `ANTHROPIC_API_KEY` value
  (slot already exists at `fleet-config/docker/litellm/.env.example:5`) —
  a credential mint that's Frank's physical act, queued not gated.
- Phases 3/4 (reroute + retire Macs) are already owned by wq#3657's more
  carefully fleet-reconciled plan — not re-forked.
- Phase 5 ("sell M70s/M80s") is corrected: M70s#1/M80s are live
  fworker-sandbox and sentinel-watchdog hosts per wq#3658
  `ROLE_ASSIGNMENT.md`, not spare Mac hardware — flagged, not executed.
- Phases 6-8 (RTX 3090 x2 + 13900HK) have no inventory entry at all and are
  queued as a physical/financial task, nothing to stage against.

## What I checked

- Read the diff cold (`git show f7570834`): two new files, 99 lines, no
  code touched — purely a plan doc + a LiteLLM config snippet.
- **Phase 1 claim**: `wq show 2246/2651/2815/3794` — all four `status: done`.
  Matches exactly.
- **Phase 2 claim**: `fleet-config/docker/litellm/.env.example:5` reads
  `ANTHROPIC_API_KEY=` (empty) — slot exists, value missing, as claimed.
  The snippet's `model: anthropic/claude-sonnet-5-5` / `anthropic/claude-haiku-5-5`
  are the current live model IDs (confirmed against this session's own
  system model-ID reminder: Sonnet 5.5 = `claude-sonnet-5-5`, Haiku 5.5 =
  `claude-haiku-5-5`). `grep`'d `research/models.yaml` for bare
  `claude-sonnet`/`claude-haiku` — no existing harness model ID collides
  with the new gateway-facing aliases, confirming the snippet's own claim
  that these are distinct namespaces. `harness/harness_preflight.py:63`
  (`CHEAPEST_MODEL = "claude-haiku-5-5"`) and
  `harness/reroute_local_blocked.py:65-68` (`ARCHETYPE_TARGET` dict using
  `"claude-sonnet-5"`) line references both check out verbatim. RECOVERY.md
  §4 ("Regenerate LiteLLM config") is the section actually cited and its
  restart/health-check/completion-probe sequence matches what the README
  describes.
- **Phase 3/4 claim**: `ops/wq3657-mac-pro-retirement-plan/PLAN.md` exists
  and is a real, more detailed retirement plan over the same five Mac Pro
  6,1 boxes. `research/inference_nodes.yaml:52,90,118` confirm `hathor` and
  `nephthys` `state: live`, `isis` `state: down`.
  `research/INVENTORY_RECONCILIATION.md:45,58` confirm `khepri` offline
  since 08-15 and `wadjet` offline since 07-19. All cited states match.
- **Phase 5 claim**: `ops/wq3658-hardware-role-assignment-limpet-secondar/ROLE_ASSIGNMENT.md`
  confirms M80s = "primary x86 inference, OpenHands/Forge host... sandboxed
  fworker execution host (wq#3652)" and M70s#1 = "Isolated fworker execution
  host, in the independent-of-Lumen sense (own circuit/UPS)", cross-referenced
  to wq#941 (`sentinel`) as the same physical box. The README's correction is
  accurate — these are not sellable spare Mac hardware.
- **Phase 6-8 claim**: `grep -in '3090\|13900hk'` across
  `research/fleet_hardware.json` and `research/INVENTORY_RECONCILIATION.md`
  turns up only the unrelated Dell OptiPlex 3090 (anubis) — zero RTX 3090 GPU
  or 13900HK hits anywhere in the fleet inventory. The "nothing to stage
  against" claim holds; no fabricated hostname/IP was introduced for a
  nonexistent host (instinct 8 check).
- No factual, citation, or model-ID error found anywhere in the diff.

## The one real gap: no `--verify` handler (fixed as part of this verify pass)

The ticket flagged this explicitly: `ops/wq4258-fleet-consolidation-plan`
shipped with no `install.sh` at all, so it couldn't answer `--verify` per
`ops/VERIFY_CONTRACT.md` ("a bundle that installs nothing at all still needs
a handler"). Per the ticket's own instruction ("Add one... before
confirming; a repo-only check does not count"), I added one rather than
re-implementing wq#4258's actual deliverable:

- `ops/wq4258-fleet-consolidation-plan/install.sh` — same no-op-install /
  hand-merge pattern as the existing reference bundle
  `ops/wq2904-judge-model-group-fix/install.sh` (gateway config can't be
  written or read from any worker sandbox, confirmed again here: `ls` on
  `/home/lumen/agent/litellm_config.yaml` was blocked by this session's own
  directory sandbox). `--verify` runs `vcontains` for both
  `model_name: claude-sonnet` and `model_name: claude-haiku` against
  `${WQ4258_LIVE_CONFIG:-/home/lumen/agent/litellm_config.yaml}` — the env
  override exists so tests never touch the real gateway config, same
  convention as `wq4006-rea-security-rescope-a1`'s `WQ4006_CLAUDE_JSON`.
- `ops/tests/test_wq4258_fleet_consolidation_verify.py` — 5 tests, all
  passing: fails with one `MISS` line per missing model when neither/only
  one is registered, passes (`VERIFY OK`, exit 0) once both are present, and
  the `--verify` marker precedes `vdone`. This satisfies "an acceptance
  probe must be able to fail" (wq#2630) — demonstrated both directions, not
  just the pass case.
- Could not run `bash install.sh --verify` directly against the *real* live
  target from this worktree (same sandbox restriction noted above, and
  `bash <path>` outside `scripts/` isn't on this worker's Bash allowlist
  either) — this is expected and matches wq#2904's own documented
  limitation, not a defect in the handler. The bundle is honestly not live
  yet (blocked on the pending `ANTHROPIC_API_KEY`), so a real `--verify` run
  today would correctly report `MISS`/exit 1, which is the truthful
  disposition, not a problem to fix.

## Conclusion

Every factual claim in wq#4258's PR checks out against current repo state —
ticket statuses, file line numbers, model IDs, hardware-role docs, and
inventory data all matched on direct inspection. No fabricated paths,
hostnames, or model names. The PR correctly deferred four of eight phases to
existing owners instead of forking duplicate authority, and correctly
caught a wrong premise in the ticket text (Phase 5) rather than executing
against it. The only defect was process, not content: the shipped bundle
had no `--verify` handler, which I added and tested as the ticket's gate
required. No contradiction; no escalation to opus-5@high needed.
