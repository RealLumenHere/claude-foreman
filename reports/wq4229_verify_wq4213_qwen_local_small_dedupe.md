# wq#4229 — verify wq#4213: qwen-local-small dedupe + fallback

**Verdict: CONFIRM**

Archetype 8 adversarial verify-pass. Author: claude-sonnet-5. Verifier: claude-sonnet-5@high
(fresh instance). Target: commit `8487b76b` "fix(litellm): dedupe qwen-local-small
deployments, add a fallback" (`ops/wq4213-litellm-qwen-local-small-group/`), already HEAD
on this worktree's branch.

## What the diff claims

`litellm_config.yaml` has two `qwen-local-small` `model_list` deployments (`api_base`
`.125` and `.135`) and no `router_settings.fallbacks` entry for the group, so once every
deployment is down the raw `ClientConnectorError` reaches the caller instead of degrading.
The config's own inline `description` comments ("hathor judge box" / "ptah") are stale;
the real identity (per `lumen_state.py current`) is `.125`=ptah, `.135`=sekhmet (not
hathor, whose `lan_ip` is `.172`). Decision: drop sekhmet's dead deployment (no live
precedent since 2026-10-11), keep ptah's (flaps, but recovered 2026-10-15), and add
`qwen-local-small: [lumen-local-judge]` to the fallback chain. `dedupe_qwen_local_small.py`
does this as comment-preserving line-level text surgery, independently re-plans the
expected post-edit config via `yaml.safe_load`, and refuses to write on a mismatch. A BUILD
worker cannot reach the real config or the `litellm` container, so the actual apply is
deferred to a fleet-window op (`reload_and_verify.sh`) that precheck-probes the fallback
target, applies, and restarts only if the running gateway doesn't already reflect the edit.

## Verification performed

1. **Read cold**: all six shipped files (`README.md`, `dedupe_qwen_local_small.py`,
   `completes.py`, `reload_and_verify.sh`, the test fixture, the test file).
2. **Ran the shipped probe**: `python3 -I -m pytest ops/wq4213-litellm-qwen-local-small-group/tests/`
   — 8 passed (matches README's claimed count): can-fail gate, fixed-passes, only-dead-host
   dropped, comment/other-entries preserved, existing fallback chains undisturbed,
   idempotent second pass, plan-mismatch refusal, byte-identical backup on write.
3. **Independently checked the host-identity claim** (the factual basis for *which* host to
   drop) rather than trusting the commit message — ran `lumen_state.py current ptah`,
   `current sekhmet`, `current hathor lan_ip` directly:
   - `ptah.lan_ip = 192.168.8.125`, `ptah.availability = OFFLINE since 2026-10-16 18:10 PDT
     ... Returned ~14:14Z 10-15` (conf 0.88) — matches README exactly.
   - `sekhmet.lan_ip = 192.168.8.135`, `sekhmet.status = DOWN since 2026-10-11 ... Needs
     physical/Frank check` (conf 0.53) — matches README exactly.
   - `hathor.lan_ip = 192.168.8.172` (verified 11d ago) — confirms the config's "hathor
     judge box" comment on the `.125` entry is indeed wrong, as claimed.
   No fabricated values per instinct 8 — every identity claim traces to a real fleet-state
   fact, not an invented one.
4. **Checked the cross-referenced `kb/ISSUE-LOG.md` rows** (1194, 1206) exist and are
   consistent with the "BUILD worker has no ssh/fleet-exec access to ptah/sekhmet, cannot
   touch `/home/lumen/agent/litellm_config.yaml`" claim — both present, same
   `grant_missing_env` root cause cited. Confirmed directly: an `ls`/`cat` on
   `/home/lumen/agent/litellm_config.yaml` from this verify worktree was refused by the
   allowlist on the same grounds, reproducing the constraint rather than just trusting it.
5. **Confirmed the `reload_and_verify.sh` dependency on `wq4135-litellm-fast-fallback-retarget`**
   is real: `probe_rung.py` exists at the referenced relative path, its `probe()`/`main()`
   signatures match both call sites (`reload_and_verify.sh`'s direct CLI invocation and
   `completes.py`'s `import probe_rung` + `probe_rung.probe(m, key, timeout)` reuse).
   `completes.py`'s stated behavioral delta (accept a fallback hop, unlike
   `probe_rung.answered_itself`) is real: it keys success on `status == 200` alone, dropping
   the `attempted_fallbacks in (0, None)` requirement.
6. **Adversarial probes outside the shipped fixture population**
   (`/tmp/fworker-wq4229/adversarial_probe.py`, run via `python3 -I`, importing the module
   directly against hand-built YAML the ticket's own test never constructs):
   - A third, unrelated deployment whose `api_base` host is a *superstring* of the drop
     host (`192.168.8.1350` vs. drop target `192.168.8.135`) survives untouched — the
     `ENTRY_RE` group-name match scopes the substring check to the right entries first, so
     this isn't the collision it looks like.
   - A pre-existing multi-hop fallback chain for the same group
     (`qwen-local-small: [some-other-rung, lumen-local-judge]`) is left intact
     (`added=False`), not duplicated or reordered.
   - `router_settings:` present but its `fallbacks:` key entirely absent (no empty list
     either) → `_fallbacks_span()` raises `RuntimeError: router_settings.fallbacks: not
     found` instead of creating the key. **Latent gap, not a live bug**: the shipped
     fixture mirrors production closely enough (it already carries working
     `lumen-lfm2-1b`/`lumen/judge` fallback entries, which the README states are already
     live in production) that the real target almost certainly already has a
     `fallbacks:` key. Worth a follow-up hardening pass if `_fallbacks_span` is ever reused
     against a config that lacks the key, but out of scope for what this ticket needed to
     fix.
   - `do_check()`'s gate (`len(entries) > 1 and not has_fallback`) only flags the
     *duplicate-with-no-fallback* symptom; a single surviving deployment with no fallback
     reports "ok". This is **as-designed, not a hidden defect** — the README explicitly
     scopes the gate to "the can-fail gate (duplicate deployments + no fallback)" and
     separately, honestly flags that a future ptah-goes-permanently-dead scenario is a
     different ticket's call ("that's a `remove_dead_rungs.py`-style full removal for a
     follow-up ticket, not this one's call to make"). The fix as shipped does add the
     fallback unconditionally, so this narrow gate never actually misses the case that
     matters for *this* edit.

No case broke the dedupe, corrupted an unrelated entry, or produced a plan/render
mismatch that should have been caught and wasn't.

## Conclusion

The fix's two moves — drop sekhmet's dead `qwen-local-small` deployment, add a
`lumen-local-judge` fallback — are correctly implemented, independently re-planned before
write, and covered by passing tests plus adversarial probes drawn from outside the shipped
fixture. The host-identity claims that justify *which* host to drop are independently
verified true against live fleet state, not invented. The two design-gap observations above
(no-`fallbacks:`-key crash path; narrow `check` gate) are pre-existing limitations the
author's own README already discloses or that don't apply to the real target config —
neither contradicts what the diff claims to do. **CONFIRM** — no escalation to opus-5@high.
