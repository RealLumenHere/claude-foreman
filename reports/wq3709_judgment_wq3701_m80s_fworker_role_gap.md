# wq#3709 — judgment: wq#3701's CONTRADICT on wq#3658 (M80s fworker role gap)

Judge: claude-opus-5 (fresh instance). Subject: wq#3701's verify-pass
(claude-sonnet-5) of wq#3658/PR#1728, reported in
`reports/wq3701_verify_wq3658_m80s_fworker_role_gap.md` (landed `c09bdccd`).

## Verdict: UPHELD on the facts, DOWNGRADED on severity — and fixed here instead of filed

The defect is real and I re-derived it independently. It is not a "gap in the
deliverable's core job"; it is an accuracy bug in a source-of-truth doc, low severity,
with a cheap fix. Since the fix is ~40 lines of markdown and I had the evidence in
hand, filing a follow-up ticket to amend `ROLE_ASSIGNMENT.md` would have cost more
dispatch overhead than the edit. The amendment is in this branch
(`ops/wq3658-hardware-role-assignment-limpet-secondar/ROLE_ASSIGNMENT.md`), so no
follow-up ticket is needed and none was filed.

## What I verified myself (not inherited from wq#3701)

- Role table line 15 gives M80s `Heavy compute, primary x86 inference, OpenHands/Forge
  host` with `NOT Frank's workstation`; line 16 gives M70s#1 `Isolated fworker
  execution host`. Deploy-order line 52 cites wq#3652 as `vPro/AMT, fworker sandbox,
  NVMe isolation`. Both readings confirmed against the pre-amendment file. The verifier's
  line numbers are correct.
- `wq show 3652` detail: steps 4–7 are a `fworker` nologin user, a systemd sandbox
  (`ProtectSystem=strict`, `ProtectHome=yes`, `PrivateTmp`, `NoNewPrivileges`, eBPF
  `IPAddressDeny=100.64.0.0/10 + 192.168.8.0/24`, `IPAddressAllow=Lumen-IP/32`),
  `~/.claude/` auth migration, and env pointed at Lumen LiteLLM+wq. The verifier
  characterized this accurately; nothing invented.
- `ops/wq941-sentinel-provision/RUNBOOK.md:23-24` reads "UPS/battery-backed, on a
  DIFFERENT circuit from Lumen. Independence is the whole point." The two-flavors
  distinction the verifier drew is real, and it is *stated in the sources* — only the
  role doc was silent on it.

## What wq#3701 could not know, and what it strengthens

wq#3701 saw wq#3652 as `status: claimed` ("in flight"). It is now `done`: the sandbox
unit, provisioning scripts and a passing 5/5 hardening test landed as `7297b825`
(`ops/wq3652-m80s-provisioning/fworker-sandbox@.service`, `provision-fworker.sh`,
`test_fworker_sandbox_hardening.py`), with the privileged remainder as wq#3703/3704/3705.
That removes the strongest counter-argument available at verify time — that M80s's
fworker role was speculative scope on a ticket still in flight. It is shipped code.
`ops/wq3652-m80s-provisioning/README.md:56-58` ties the unit to `harness/fworker.py`'s
worker-slot subcommand, i.e. the same BUILD-worker execution path Lumen runs. M80s is a
fworker host by the same definition M70s#1 is.

## Why I downgraded severity

Three arguments cut against calling this a core-job failure, and the verifier did not
weigh them:

1. **No in-repo consumer exists yet.** I grepped the repo for `ROLE_ASSIGNMENT`:
   the only hit outside the doc itself is wq#3701's own report. The doc's consumers are
   prospective future tickets, by its own framing. Nobody has been misled yet.
2. **The provisioning authority was never wrong.** wq#3652 and its README describe the
   M80s sandbox correctly and in detail. Anyone actually provisioning M80s reads that,
   not the role table. The defect is a navigational one.
3. **"Explicitly NOT" is an ownership column, not an exhaustive negative-role list.**
   `NOT Frank's workstation` does not assert non-fworker-ness; the misreading the
   verifier describes requires the reader to treat column silence as denial.

What survives all three: a doc whose stated purpose is "the source of truth for what
each box is for" was wrong about what one of four boxes is for, while naming the
contradicting fact 37 lines later. That is worth fixing on accuracy grounds alone,
cheaply, before the prospective consumers arrive. "Worth a follow-up ticket" — yes in
substance, no in process: worth the edit, not worth the dispatch.

## A sharper defect the verifier missed

The deploy order's *rationale* conflicts, not just its role table. Line 45 justifies
M70s#1 at step 2 because it "reduces Lumen's load by moving fworker execution off it."
M80s at step 5 does exactly that too — and as of today M80s is the node with a tested
sandbox unit, while wq#941 (M70s#1's enabler) is still `blocked` on physical disks with
no fworker implementation anywhere. So the one node the order ranks *last* for fworker
purposes is the nearer-term lever for the benefit the order uses to rank the *second*
node. Read as a priority claim about fworker offload, the ordering is backwards. The
amendment says so explicitly rather than silently reordering: outage-independence and
load-relief are different goals with different winners, and only Frank reorders a
deploy sequence.

## The amendment

In `ops/wq3658-hardware-role-assignment-limpet-secondar/ROLE_ASSIGNMENT.md`:

- M80s row gains the sandboxed-fworker role and an explicit "NOT an *independent*
  fworker host" negative; M70s#1 row gains "NOT the only fworker execution host."
- New `## Cross-reference: M80s and wq#3652 (fworker sandbox)` subsection, parallel to
  the existing wq#941/M70s#1 one, with a 4-row table contrasting the two isolations
  (independence-from-Lumen vs OS sandbox around an untrusted agent; survives a Lumen
  outage vs dies with Lumen; threat model; what each is picked for) and three
  consequences: count both nodes for throughput and only M70s#1 for resilience; the
  deploy-order rationale caveat above; scope `allow.restart: fworker-sandbox@*` in
  `fleet-exec-policy.yaml` to M80s, the node that runs the unit.
- Two stale wq#3652 status references corrected ("claimed and in flight" → `done` for
  the authorable part, with wq#3703/3704/3705 named).

## Disposition

wq#3701's CONTRADICT: correct, upheld, well-evidenced, with one real argument-weighing
omission (it did not consider that the doc has no consumers yet, which is what makes
this low-severity rather than a core-job failure). No rework on wq#3658 — the doc is
amended in place here. No follow-up ticket filed; the fix is in this branch.
