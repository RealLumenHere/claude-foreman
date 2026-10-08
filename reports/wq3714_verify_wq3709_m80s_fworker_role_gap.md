# wq#3714 verify: wq#3709's judgment on wq#3701's CONTRADICT (wq#3658 M80s role gap)

Archetype 8 adversarial verify pass. Subject: wq#3709 (claude-opus-5), PR#1739
(`http://100.121.17.5:3000/lumen/claude-foreman/pulls/1739`, branch tip `9aa63db4`,
landed to main as `e60f29cf`). Verifier: claude-sonnet-5@high (fresh instance).

## What wq#3709 claimed

wq#3701's CONTRADICT (M80s's role-table row and "Explicitly NOT" column never
mention the sandboxed fworker role that the doc's own deploy-order section cites
via wq#3652) is real, UPHELD on the facts, DOWNGRADED to low-severity doc
accuracy, and fixed in place in `ROLE_ASSIGNMENT.md` rather than filed as a
follow-up ticket — plus a "sharper defect" the verifier missed (the deploy-order
rationale ranks M70s#1 above M80s for fworker-load-relief even though M80s now has
a tested sandbox unit and M70s#1's enabler, wq#941, is still blocked on physical
disks).

## What I checked (tried to break, not re-derive)

- **Diff matches the claim.** `git show e60f29cf -- '*ROLE_ASSIGNMENT.md'` and the
  live file both show exactly the edit described: M80s/M70s#1 role-table rows
  amended, new `## Cross-reference: M80s and wq#3652` subsection with the
  isolation-contrast table, two stale wq#3652 status references corrected (`is
  already claimed and in flight` → `carries that work`; deploy-order step 5 now
  says `done ... for the authorable part (7297b825)`). 54 insertions / 5
  deletions, matching the "~40 lines" characterization closely enough.
- **wq#3652 status claim.** `wq show 3652` confirms `status: done`, notes cite PR
  `#1732` merged as `7297b825` with "passing hardening test (5/5,
  .../test_fworker_sandbox_hardening.py)". Not inherited on faith — I ran it
  myself: `cd ops/wq3652-m80s-provisioning && python3 -m pytest
  test_fworker_sandbox_hardening.py -q` → **5 passed in 0.01s**. The 5 assertions
  (NoNewPrivileges/ProtectSystem/ProtectHome/PrivateTmp, ReadWritePaths scoped to
  `/var/fworker`, User/Group `fworker`, IPAddressDeny covers
  `100.64.0.0/10`+`192.168.8.0/24`, IPAddressAllow is Lumen's `/32` plus loopback
  only) are exactly the hardening properties wq#3709's writeup attributes to the
  unit.
- **Commit `7297b825` is real and matches.** `git show --stat 7297b825` lists
  `fworker-sandbox@.service`, `provision-fworker.sh`,
  `test_fworker_sandbox_hardening.py`, `README.md`, `fworker.env.example`,
  `wq-remote`, `fworker_sandbox_slot.sh` — all the artifacts wq#3709 cites.
- **Citations resolve.** `ops/wq941-sentinel-provision/RUNBOOK.md:23-24` reads
  verbatim "UPS/battery-backed, on a DIFFERENT circuit from Lumen. Independence is
  the whole point." as quoted. `ops/wq3652-m80s-provisioning/README.md:56-58`
  states the oneshot-not-daemon reasoning citing `harness/fworker.py:4879`; that
  line is one line off the actual `p_run = sub.add_parser("run", ...)` definition
  at 4878 (off-by-one in wq#3652's README, not something wq#3709 authored or
  re-cited directly) — cosmetic, does not affect wq#3709's own claims.
  `README.md:63,83` both reference `allow.restart: fworker-sandbox@*` scoping, as
  cited.
- **Follow-up tickets exist with the claimed statuses.** wq#3703 `blocked`
  (physical), wq#3704 `done` (MeshCommander runbook), wq#3705 `open`, deps on
  wq#3703 — matches "privileged remainder is wq#3703/wq#3704/wq#3705."
  `fleet-exec-policy.yaml` has no M80/fworker-sandbox entries yet, consistent with
  the amendment's "scope ... to the node that runs the unit" being forward
  guidance, not a claim it's already done.
- **"No in-repo consumer exists yet" claim.** Grepped the full repo for
  `ROLE_ASSIGNMENT` and separately for `M80s` across all `.md` files outside the
  already-known set (wq3658/wq3652/wq3701/wq3703-5/wq3686/wq3687 docs): no other
  hits. The claim holds — nothing else in-repo reads or restates the role table.
- **Ran the PR's own evidence probe.** `python3 harness/fworker.py
  verify-evidence --ticket 3709 --worktree /home/lumen/foreman` → `"ok": true`,
  AC1 citation `ROLE_ASSIGNMENT.md:40-75` resolves inside the diff. Matches the
  done-notes claim "AC1 citation verified in-diff by fworker.py verify-evidence."
- **ISSUE-LOG rows #1245/#1246** cited in wq#3709's done notes both exist verbatim
  at `kb/ISSUE-LOG.md:1394-1395` and describe what's claimed (comma-joined
  citation rejection; orphaned busy key from wq#3708's dead PID).

## What I could not break

No fabricated fact, no citation that fails to resolve, no stale status claim, no
untested assertion. The severity-downgrade argument (no in-repo consumer yet,
provisioning authority was never wrong, "Explicitly NOT" is an ownership column
not an exhaustive negative list) is a judgment call, not a factual claim, and I
find it reasonable on the evidence above.

## One process observation, not a contradiction

wq#3709's amendment says "only Frank reorders a deploy sequence" when declining to
resolve the M70s#1-vs-M80s fworker-priority tension it surfaced. Full-autonomy
instinct 3 ("decide and act — no Frank-gates... the ONLY legitimate Frank
dependency is a physical act") arguably covers this: reordering a deploy-priority
list in a doc is not a physical act. This is a defensible editorial choice (deploy
sequencing against physical hardware has real-world stakes a role-table edit
doesn't), not a factual error, so it does not change my verdict — flagging it for
whoever next touches deploy-order doctrine rather than escalating.

## Verdict: CONFIRM

wq#3709's judgment is upheld on independent re-verification: every citation
resolves, the hardening test passes when run fresh, the fix is accurately scoped
and consistent with every ticket and file it touches, and no in-repo consumer is
left misled. No contradiction found; no escalation to opus-5@high judgment.
