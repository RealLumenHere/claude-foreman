# wq#3181 — verify wq#3168 [litellm] dead local rungs time out 60s

Archetype 8 adversarial verify pass. Author: claude-sonnet-5. Verifier: claude-sonnet-5@high
(fresh instance, same model as author).

PR: http://100.121.17.5:3000/lumen/claude-foreman/pulls/1440, merged (local main sha
`22808fd5`, note also merged as `c1668776d0` per wq#3168's own done-notes). Files:
`tools/council/council.py` (+5/-1), `tools/council/council.yaml` (+15/-9),
`tools/council/tests/test_council.py` (+14), `ops/wq3168-dead-local-rungs/README.md`
(net new, 113 lines).

## Claim under test

wq#3168 (audit parent wq#3162) found 4 dead LiteLLM rungs eating a 60s+ timeout before
fallback: `claude-max-opus`/`claude-max-sonnet` (claude-shim on :8091, 0 listeners) and
`kimik3-0.4b-local`/`lumen-lfm2.5-2.6b` (:8000 bound to 127.0.0.1, unreachable from the
litellm container). The PR claims: (1) retired `claude-max-opus` as a council seat
(`enabled: false` in `council.yaml`), (2) fixed a general bug in
`council.py::load_config` where a chairman-list entry for a disabled seat still reached
`synthesize()` for a real timeout even though fan-out skipped it, covered by a new test,
(3) the other 3 dead rungs need an edit to `/home/lumen/agent/litellm_config.yaml` on the
gateway node, outside this (and this verify session's) sandbox write access, so only
documented retire/restore recommendations shipped for those, not a fix.

## What I checked

- Read `council.py`, `council.yaml`, `test_council.py` diffs cold from the merge commit
  (`git show 22808fd5`), plus the pre-fix versions (`git show e1a89d67:...`) to see the
  exact before/after.
- Ran the real test suite in place: `python3 -m pytest tools/council/tests/test_council.py
  -v` → **13 passed**, including the new
  `test_load_config_drops_disabled_seat_from_chairman_list`.
- Loaded the actual shipped `council.yaml` through `council.load_config` (not a synthetic
  fixture) and printed the resulting seats/chairman:
  `seats=[gemini,deepseek,gpt]` (claude dropped), `chairman=['deepseek-v4-pro',
  'gemini-flash']` — `claude-max-opus` is gone from both fan-out and chairman, so a real
  council run now never spends a timeout on it. This also confirms `kimi-k3` (independently
  disabled before this PR, `enabled: false` pre-existing) is *also* now correctly dropped
  from chairman — the fix generalizes as claimed, not scoped narrowly to claude-max-opus.
- Checked design intent for the chairman-must-be-a-seat invariant this fix assumes
  (`tools/council/README.md:24-27`: "the first model in `chairman:` ... A chairman whose
  seat already failed is skipped") — the pre-existing docs already assume chairman entries
  are seats, so filtering chairman to enabled seat models is consistent with the original
  design, not a new restriction that could break an intentional "chairman-only,
  non-fan-out" pattern; no such pattern exists anywhere in this repo (grepped
  `tools/council/`).
- Falsifiability check on the fix itself: confirmed `council.chairman == ['ma']` (not
  `['ma','md']`) with `md` disabled, both via the new unit test and by hand-tracing
  `load_config`'s `seat_models`/`chairman` lines (`council.py:143-148`).
- Checked the "can't touch litellm_config.yaml" claim isn't a fabricated excuse: `ls -la
  /home/lumen/agent` from this verify session refuses identically ("requires approval" /
  outside allowed dirs), same sandbox boundary the PR documents.
- Cross-checked ISSUE-LOG row 1069 (wq#3168) against the PR's own account — root cause,
  fix location (`council.py:143-148`), and disposition ("SOLVED (council fallback chain) /
  DOCUMENTED-PENDING-GATEWAY-ACCESS") match exactly; no discrepancy between what was logged
  and what shipped.
- Confirmed `lumen_model_watchdog.py`'s CLOUD/LOCAL rosters already exclude all 4 dead rung
  names (`grep -n` on `ops/wq2903-model-ref-drift/lumen_model_watchdog.py`), so the claim
  that no watchdog-roster edit was needed holds.

## Verify handler (wq#1696/ops/VERIFY_CONTRACT.md)

`ops/wq3168-dead-local-rungs/` shipped as a bundle (README.md only) but had **no
`install.sh --verify` handler** — a repo-only check does not count per the ticket. Added
`ops/wq3168-dead-local-rungs/install.sh`, modeled on the `wq2740-account-prefix-adjacency`
precedent (in-repo fix, "live" = merged to primary main, no artifact to copy). It runs
`vcontains` against the three markers of the applied fix:
- `tools/council/council.yaml`: `claude-max-opus,...enabled: false`
- `tools/council/council.py`: the `wq#3168:` chairman-filter comment
- `tools/council/tests/test_council.py`: the new test's name

Falsifiability proven directly (doctrine requires a probe that *can* fail): all three
markers **fail to match** against the pre-fix blobs (`git show e1a89d67:<path> | grep -E
...`) and **match** against the live post-fix files — the probe distinguishes the two
states, it isn't vacuously true. `bash ops/wq3168-dead-local-rungs/install.sh --verify`
itself could not be executed in this sandbox — only `Bash(bash scripts/*)` is
allowlisted, not `bash ops/*/install.sh`; this is the same allowlist-gap class as other
`ops/*` install scripts, named here per the "say so plainly" rule rather than retried.
The regex logic was verified directly with `grep -qE` against the live and pre-fix file
content instead (see above), which is what `vcontains` does internally.

The bundle deliberately does **not** claim liveness for the other 3 dead rungs
(`kimik3-0.4b-local`, `lumen-lfm2.5-2.6b`, `claude-max-sonnet`) — those need a
`litellm_config.yaml` edit on the gateway node that was never shipped, so the handler
would be wrong to assert they're fixed. This matches the PR's own honest scope claim.

## Verdict: CONFIRM

wq#3168's diff does what it claims: `claude-max-opus` is fully removed from both the
council's fan-out and chairman request paths (verified by loading the real shipped
config, not just reading the diff), the underlying chairman-list bug is fixed generally
(verified it also now correctly excludes the independently-disabled `kimi-k3`), 13/13
tests pass, and the acknowledged gap (3 rungs needing gateway-node access this sandbox
doesn't have) is genuine and consistently documented across the PR, the bundle README,
and ISSUE-LOG row 1069 — not an invented excuse. Added the missing `install.sh --verify`
handler with a falsifiable probe (fails pre-fix, passes post-fix). No contradiction found;
no escalation to opus-5@high.
