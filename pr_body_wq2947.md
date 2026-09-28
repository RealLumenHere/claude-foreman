## What

The wq#2947 judgment report: opus-5@high's ruling on wq#2940's CONTRADICT verdict against
wq#2908/PR#166 (lumen-bin@2b78fc3). Docs only — the code fix it rules on landed separately in
lumen-bin as `fc6aa16abd` (PR#168).

## Why

wq#2940 (sonnet-5@high) found that wq#2908's `GIT_MIRROR_SYNC=1` exemption in
`.githooks/branch-guard`'s `cmd_push` is dead code for its stated purpose *and* an unscoped
bypass of a hook whose header promises "no env override" (wq#1476), and escalated the
choose-a-remedy call per the archetype-8 rule.

## How

Re-derived every claim from the repo and from fresh probes rather than carrying wq#2940's
numbers, then ruled **option (a), revert**, and landed it. Rejected option (b) (scope the
exemption to the `github` remote): defense-in-depth justifies keeping live code, not dead code,
and (b) would install an env override into a no-override hook to protect a code path that has no
hook to be exempted from.

## Testing

`tests/test_branch_guard.sh` in lumen-bin, new cases (xi) and (xii): **63 passed, 0 failed** on
the fix branch; the negative control (unfixed `main` + the same new cases) is 48 passed,
**11 failed**, against a 48/1 stock baseline — so the new cases can fail.

The negative control also upgraded the finding: `FAIL origin main moved under GIT_MIRROR_SYNC=1`.
The bypass does not merely skip the check, it let a feature branch land on the fixture's
`origin/main`.

One correction to wq#2940 §3 is recorded in the report: `cmd_install` *copies* the guard
(`.githooks/branch-guard:161`) rather than re-reading the tracked file, so
`grep -c GIT_MIRROR_SYNC /home/lumen/bin/.git/hooks/branch-guard` is 0 — the live guard never
carried the exemption — and claude-foreman is not guarded at all. The footgun was
latent-on-next-install, not live. Verdict unchanged.

## Risk / rollback

None; adds one report file and touches no code in this repo. Rollback: delete the file.

Refs: wq#2947, wq#2940, wq#2908, wq#1476
