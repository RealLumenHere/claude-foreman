# wq#3701 verify: wq#3658 hardware role assignment (Limpet/M80s/M70s#1/M70s#2)

Archetype 8 adversarial verify pass. Author: claude-sonnet-5. Verifier: claude-sonnet-5
(fresh instance, same model as author).

PR: #1728, merged to `main` as `9f31df31494393fa3ba8897852b1d194b302f7a2` (merge commit
`ac119e6a`). File: `ops/wq3658-hardware-role-assignment-limpet-secondar/ROLE_ASSIGNMENT.md`
(+66). No other files touched — pure docs/decision-record ticket, no code, no executable
acceptance probe. "Run its probe" here means: verify every citation the doc makes against
the live cited sources, and check the role table for internal consistency across the four
nodes and the other tickets it cross-references.

## What wq#3658/PR#1728 claimed

A decision record assigning roles to four nodes (Limpet, M80s, M70s#1, M70s#2) so later
provisioning tickets don't re-litigate roles per-ticket. It explicitly reconciles one
overlap: wq#941 ("sentinel") and this ticket's M70s#1 are the same physical box, with
M70s#1's "isolated fworker execution host" and wq#941's "fleet-response-node" both
describing the independence-from-Lumen property (separate circuit/UPS).

## What I checked

- Read `ROLE_ASSIGNMENT.md` cold against the original wq#3658 ticket detail text — role
  table entries (Limpet i5-14500 secondary control/NOT primary control plane; M80s
  i7-12700/64GB heavy compute+inference/NOT Frank's workstation; M70s#1 i5-12500/32GB;
  M70s#2 Docker offload+Gitea mirror+embeddings) match the ticket's own wording verbatim —
  no invented facts.
- Verified every `grep -n`-style citation against the live file:
  `ops/wq941-sentinel-provision/RUNBOOK.md:4,16,42,80-83` — hostname `sentinel` (line 16),
  hardware/no-GPU (line 4), SKU range i5-12400/12500/12600 (line 42), role
  `fleet-response-node` + inventory-only registration (lines 80-83). All five line numbers
  resolve to exactly the claimed content.
- Verified the "Qwen3-30B-A3B resident" and "UPS/different circuit" claims against
  `RUNBOOK.md:23-24,61,69-73` — both present and accurately characterized.
- Cross-checked ticket statuses cited in the deploy order: wq#3651 (`status: done`, notes
  confirm physical BIOS flash is tracked separately as a non-blocking physical task —
  matches the doc's "blocked on the physical BIOS flash" framing), wq#941 (`status:
  blocked` — matches), wq#3652 (`status: claimed` — matches "in flight"), wq#3657
  (`status: done`, notes/detail match the doc's "Do NOT retire before measuring" /
  isis+hathor quotes verbatim).
- Grepped the repo (`foreman/*.md`, `kb/ISSUE-LOG.md`) for any other M80s reference: none
  exists, so "NOT Frank's workstation" isn't a fabricated embellishment — it's lifted
  directly from the wq#3658 ticket's own detail text.

## Contradiction found (CONFIRMED)

**The role table gives M80s an incomplete role and the document fails to reconcile an
overlap it had the evidence for, in contrast to the diligence it shows for the one overlap
it did catch.** The doc's cross-reference section carefully works out that wq#941
("sentinel") and M70s#1 are the same box and that their two role descriptions
("fleet-response-node" / "isolated fworker execution host") describe one underlying
property. But the document's own deploy-order section (line 52) cites wq#3652 — M80s's own
provisioning ticket — by its full scope: "vPro/AMT, **fworker sandbox**, NVMe isolation."
`wq show 3652`'s detail confirms this is not loose phrasing: step 4 creates a `fworker`
user (nologin), step 5 wraps it in a systemd sandbox (`ProtectSystem=strict`,
`ProtectHome=yes`, `NoNewPrivileges`, an eBPF network filter denying tailnet/LAN and
allowing only Lumen's IP), step 6 migrates `~/.claude/` auth from Lumen, and step 7 points
the fworker env at Lumen's LiteLLM+wq — i.e. a second, independently-sandboxed BUILD-worker
execution host, the same category of thing M70s#1 is assigned in the role table
("Isolated fworker execution host").

The role table's M80s row (`Heavy compute, primary x86 inference, OpenHands/Forge host`)
and its "Explicitly NOT" column (`NOT Frank's workstation`) never mention that M80s will
also run an isolated fworker sandbox — even though the document itself names that exact
fact one section later. A reader using the table (the document's primary, scannable
artifact — the whole point of "one source of truth") comes away thinking M70s#1 is the
only fworker execution host among the four nodes, which is false: M80s is getting one too,
per a ticket this very document cites.

To be fair to the shipped doc, the two "isolated fworker" roles are not identical in kind:
M70s#1/sentinel's isolation is independence from Lumen (separate circuit/UPS, survives a
Lumen outage, per `RUNBOOK.md:23-24`), while M80s/wq#3652's isolation is an OS-level
security sandbox that is *more* tightly coupled to Lumen, not less (network egress is
restricted to Lumen's IP only, and the whole sandbox exists to run jobs dispatched by
Lumen's wq). That distinction is real and worth stating — but the document never states
it, because it never acknowledges the overlap exists. Given the document demonstrably knows
how to perform this reconciliation (it did so for wq#941/M70s#1, in detail, in its own
dedicated section) and had the wq#3652 scope in hand at the moment it wrote line 52, failing
to apply the same check to M80s is a gap in the deliverable's core job, not a cosmetic
omission.

## What I did not find broken

- No fabricated facts: every number, hostname, SKU range, and quote in the doc traces to
  either the original wq#3658 ticket text or a cited file at the cited line.
- Deploy order sequencing (BC-250 → M70s#1 → M70s#2 → Limpet → M80s → Mac retirement) is
  internally consistent with every cited ticket's actual status.
- The wq#941/M70s#1 "same physical box, not a duplicate" reconciliation is itself correct
  and well-evidenced.
- The Limpet and M70s#2 rows are appropriately hedged ("not yet filed as its own ticket")
  rather than overclaiming detail that doesn't exist yet.

## Action taken

Contradiction posted to wq#3658 via `wq note`. Not re-implementing the doc per ticket
instruction ("Do NOT re-implement; verify"). Escalating per ticket policy to opus-5@high
judgment as a new ticket (id recorded on wq#3701's done notes). Fix shape for whoever picks
it up: add a row-level note or a dedicated "M80s/wq#3652 fworker sandbox" subsection
(parallel to the existing wq#941/M70s#1 one) that (a) states M80s also hosts an isolated
fworker execution environment, and (b) distinguishes its *security-sandbox,
Lumen-dependent* isolation from M70s#1's *independence-from-Lumen* isolation, so the role
table's M80s row and "Explicitly NOT" column stop implying M70s#1 is the only fworker host
among the four nodes.
