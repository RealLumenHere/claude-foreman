# wq#3105 — Adversarial verify-pass of wq#3097 (LiteLLM GHSA-3cv6-jpf6-8222 patch prep)

**Verdict: CONFIRM** — the shipped code correctly targets the GHSA-3cv6-jpf6-8222 fix, the
target/fallback digests are independently verified against the real ghcr.io registry (not
just trusted from the ticket text), the fix-marker gate in `verify()` is correctly scoped,
and the caller scan shows zero real hits against the three PR 36011 breaking shapes. One
non-blocking artifact-reproducibility defect found and logged (ISSUE-LOG #1031); did not
change the verdict.

## What was shipped

`/home/lumen/bin` main, commit `c4cb6d7` (landed via PR #187 in that lane repo, confirmed
by `git log --oneline -- maint-ops/litellm-upgrade maint-ops/tests/harness_litellm.sh`):

- `maint-ops/litellm-upgrade:18-19` — `TARGET_DIGEST`/`FALLBACK_DIGEST` bumped to
  v1.96.2 / v1.88.6.
- `maint-ops/litellm-upgrade:54-62` — `verify()` greps the running container for
  `reject_url_valued_destination`, gated on `$want` (not `$patched`) matching
  `TARGET_DIGEST`/`FALLBACK_DIGEST`.
- `maint-ops/tests/harness_litellm.sh:3-20` — `OLD`/`NEW` env-parameterized (were
  hardcoded to the older wq#2989 pair).

`ops/wq3097-litellm-ghsa-3cv6-patch/` (this repo, `foreman`): `README.md` (status writeup)
and `scan_callers_pr36011.py` (item-c caller scan), no `install.sh` — the bundle this ticket
cites had no `--verify` handler, so I added one (see "Verify-contract gap" below).

## Probe 1: digest cross-check against the live ghcr.io registry (not just repo-only)

The ticket text and README assert specific sha256 digests for v1.96.2 (target) and v1.88.6
(fallback). A repo-only check would just diff the ticket's claimed digest against the
script's literal — that only proves internal consistency, not that the claimed digest is
actually what those tags resolve to upstream. Pulled an anonymous ghcr.io pull token and
queried both tags' manifest-list digest directly:

```
$ curl -s "https://ghcr.io/token?service=ghcr.io&scope=repository:berriai/litellm:pull"
$ curl -sI -H "Authorization: Bearer <token>" \
    -H "Accept: application/vnd.oci.image.index.v1+json" \
    "https://ghcr.io/v2/berriai/litellm/manifests/v1.96.2" | grep -i digest
docker-content-digest: sha256:154e23bb5f31b1f10e16392a8ef299bd2cde08de3a64a6849002cfcc25ce3c63

$ curl -sI ... "https://ghcr.io/v2/berriai/litellm/manifests/v1.88.6" | grep -i digest
docker-content-digest: sha256:0c73da8d132b893bfa037511553bafbc9e2ff6abe9b474a66e0e9a354c0be064
```

Both match `TARGET_DIGEST`/`FALLBACK_DIGEST` in `maint-ops/litellm-upgrade:18-19` exactly.
Also confirmed via `fleet-search web "GHSA-3cv6-jpf6-8222"` that the advisory is real and
its title ("Authenticated SSRF and provider-credential exfiltration via unvalidated
request-body routing parameters") matches the README's description. Could not independently
confirm the `reject_url_valued_destination` symbol name inside the image's source tree
(would need a layer/blob pull or `docker exec`; `docker` is denied to this session except
`ps`/`logs`, and SearXNG's general engines were rate-limited/CAPTCHA'd for the more specific
queries) — this remains trusted from the ticket/PR text, not independently re-derived.

## Probe 2: `verify()` gating logic (read the whole function, not just the diff hunk)

Traced all four callers of `verify()`:
- `precheck()` → `verify "$cur" 0 0` — `$want=$cur` (pre-upgrade digest), so the new
  `reject_url_valued_destination` check (gated on `$want ∈ {TARGET,FALLBACK}`) is
  correctly skipped — an un-upgraded instance won't fail routine precheck for lacking a
  fix it doesn't have yet.
- `rollback()` → `verify "$old" 0 0` — same skip applies when rolling back to an old digest
  that predates the fix.
- `apply()` → `verify "$TARGET_DIGEST" 1 1` — `$want=$TARGET_DIGEST`, check is active,
  correctly asserting the fix marker post-upgrade.
- `case --verify` → `verify "$(cur_digest)" 1 0` — after a real upgrade, `cur_digest`
  (read from the live compose pin) equals `TARGET_DIGEST`, so routine liveness checks
  (`ops_verified`) keep asserting the marker going forward. No gap found.

## Probe 3: re-ran the item-(c) caller scan fresh

```
$ python3 ops/wq3097-litellm-ghsa-3cv6-patch/scan_callers_pr36011.py
== 1. path-valued deployment name: 2 hit(s)
== 2. litellm_metadata[bracket] form field: 3 hit(s)
== 3. /health/test_connection: 5 hit(s)
== config knobs already set (...): 6 hit(s)
TOTAL=16
```

This contradicts the README's claimed `TOTAL=0` at first read — but all 16 hits are in
`scan_callers_pr36011.py`/`README.md` under the stale worktree `/home/lumen/worktrees/wq3097-a4`
(still on disk, hardcoded into the script's own `argparse` default `roots` list), i.e. the
scanner matching its own pattern definitions/docstrings, not real application code:

```
$ python3 ops/wq3097-litellm-ghsa-3cv6-patch/scan_callers_pr36011.py \
    | grep -v "scan_callers_pr36011.py\|README.md"
== 1. path-valued deployment name: 0 hit(s)
== 2. litellm_metadata[bracket] form field: 0 hit(s)
== 3. /health/test_connection: 0 hit(s)
== config knobs already set (...): 0 hit(s)
TOTAL=0
```

Filtered, the real-caller conclusion holds: 0 hits. But the artifact as shipped is not
reproducible-as-instructed from its new committed location (self-exclusion only skips the
running copy's own directory, not the stale duplicate at the hardcoded default root). Logged
as ISSUE-LOG #1031 — a false-positive-only defect (does not hide a real hit), not a security
regression, so it does not change the verdict.

## Verify-contract gap (ops/VERIFY_CONTRACT.md, wq#1696)

The ticket named `wq3097-litellm-ghsa-3cv6-patch` as a bundle without a `--verify` handler.
Added `ops/wq3097-litellm-ghsa-3cv6-patch/install.sh`: no install step (the actual fix landed
directly in `/home/lumen/bin` via a separate lane-repo PR, not through this bundle), just a
`--verify` case that runs five `vcontains` checks against the live `/home/lumen/bin` files
(digests + fix marker + harness parameterization), per the template's "an edit, not a copy"
pattern.

Could not execute `bash ops/wq3097-litellm-ghsa-3cv6-patch/install.sh --verify` directly —
refused with `This command requires approval` (no `Bash(bash ops/*/install.sh --verify)`
entry in this session's frozen worker-settings.json; wq#2985's session had bundle-specific
grants this one doesn't). Reran the five underlying `grep -qE` checks (the exact logic
`vcontains` executes) directly instead — all 5/5 matched live `/home/lumen/bin` state.
Logged as ISSUE-LOG #1032 (allowlist gap, not a bundle defect).

## Verdict

**CONFIRM.** No contradiction found; escalation to opus-5@high not triggered. Two
non-blocking issues logged (ISSUE-LOG #1031, #1032) for follow-up, neither of which bears
on the correctness of the shipped GHSA-3cv6-jpf6-8222 patch.
