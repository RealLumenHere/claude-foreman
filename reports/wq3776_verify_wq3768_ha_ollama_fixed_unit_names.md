# Verify wq#3776 — wq#3768 ha-upgrade + ollama-upgrade fixed systemd-run unit names

Target: PR #246 (http://100.121.17.5:3000/lumen/lumen-bin/pulls/246), merged as `3105270aa9`
(merge commit `8de4527`), lumen-bin repo at `/home/lumen/bin` (fast-forwarded, fix is live —
mtimes on `ha-upgrade`/`ollama-upgrade` are 2026-10-12 14:04, matching the merge). 2-file,
4-line diff. Author: claude-sonnet-5. Verifier: claude-sonnet-5@high, fresh instance,
archetype 8. Gitea's web/API endpoints (`.diff`, `/api/v1/...`) 404'd from this worktree
without auth, so the PR was read cold via the merged commit in the live local checkout, which
is byte-identical to what the PR shipped (confirmed `git diff 3105270 8de4527` is empty).

## What the diff does

```
maint-ops/ha-upgrade:26     --unit=ha-up-$n-\$(date +%s)   -> --unit=ha-up-$n
maint-ops/ollama-upgrade:33 --unit=oll-up-$n-\$(date +%s)  -> --unit=oll-up-$n
```

Drops the per-run epoch suffix from both `RD()` helpers' `systemd-run --unit=` argv, matching
wq#3102's `tailscale-upgrade` precedent (`maint-ops/tailscale-upgrade:18`, static
`--unit=ts-upgrade`). Root cause: sudo-rs exact-argv sudoers matching on an enumerated-rule
node can never match a unit name that varies per invocation.

## Adversarial checks performed

- **Call-site audit** (`grep -n "RD " ha-upgrade ollama-upgrade`): every call passes a fixed
  string literal for `$n` (`stop`, `restore`, `up`, `pull`, `chk`, `tar` in ha-upgrade;
  `stop`, `restore`, ... in ollama-upgrade) — never a variable. Post-fix, the rendered unit
  name is fully deterministic per call site (e.g. always `ha-up-stop`), which is exactly what
  an enumerated sudoers rule needs to match. This was the one way the fix could have been
  incomplete (if `$n` itself were dynamic) — it isn't.
- **`--collect` already present on both lines** (pre-existing, untouched by this diff) — a
  concurrent run on the same node colliding on the now-fixed unit name will fail at
  `systemd-run`, which the ticket calls out as desired (one queued op per node). Confirmed
  this is not a new behavior the diff introduces; `--collect` auto-unloads the unit on exit
  either way, so *sequential* re-runs of the same op are unaffected.
- **Scope sweep**: `grep -rn 'unit=.*\$(date +%s)\|unit=.*-\$n-' maint-ops/` across the whole
  dir returns nothing — no sibling instance of this pattern was missed.
- **Test impact**: `maint-ops/tests/ha_canary.sh` and `tests/ollama_canary.sh` grepped for
  `unit`/`date +%s`/`ha-up-`/`oll-up-` — neither asserts the old per-run unit shape (the
  `ollama_canary.sh` unit references are to a different, unrelated fixed unit
  `ollama-canary`). No test changes were needed, matching the PR's own claim.
- **Liveness probe, run verbatim**: `grep -c "date +%s" maint-ops/ha-upgrade
  maint-ops/ollama-upgrade` → **`ollama-upgrade`: 0. `ha-upgrade`: 3**, not 0 as the ticket's
  stated liveness line literally promises. This is the one place the probe as written could
  fail, and it does — on its face a contradiction candidate.

## Probe mismatch, investigated — not a defect in the fix

The 3 ha-upgrade hits are lines ~35/37/45, all `t0=$(date +%s)` / `now=$(date +%s)` inside the
`VERIFY_TIMEOUT` polling loop (elapsed-time bookkeeping for the HTTP/eplug verify checks) —
unrelated to the `RD()` `--unit=` argv the sudoers bug targets, and present before this diff
too (pre-existing code, outside the 4 changed lines). The ticket's liveness line is an
over-broad grep (matches the substring `date +%s` anywhere in the file, not scoped to the
`--unit=` argument), so it could never have returned 0 for `ha-upgrade` regardless of how
correctly the actual bug was fixed. This was not left for the verifier to discover: the author
already logged it transparently — `/home/lumen/kb/ISSUE-LOG.md` row 1273 states the exact same
finding (imprecise probe, 3 unrelated hits, explains why they're untouched) — and the
cross-repo evidence note (`/home/lumen/foreman/wq3768_cross_repo_evidence.md` lines 38-44)
carries the identical explanation with the specific line numbers. Removing the timeout-loop
`date +%s` calls to satisfy the probe's letter would break `VERIFY_TIMEOUT` and is not what
wq#3768 asked for ("fixed `--unit=ha-up-$n` / `oll-up-$n`" — nothing about the verify loop).

## Verdict

**CONFIRMED.** The diff is minimal, correct, and complete: both `RD()` unit names are now
static and match every call site's actual (always-literal) argv, mirroring the wq#3102
precedent exactly; no sibling instances of the per-run-epoch pattern remain anywhere in
`maint-ops/`; no test assumed the old shape. The ticket's own liveness probe technically fails
for `ha-upgrade` (3, not 0), but that is a pre-existing scoping flaw in the probe text itself
(it greps an unqualified substring that also matches an unrelated, correctly-untouched timeout
loop), not a gap in the shipped fix — and the author disclosed this exact discrepancy with
matching evidence in both ISSUE-LOG.md row 1273 and the cross-repo note before this verify-pass
ever ran. No contradiction; no escalation to opus-5@high needed.
