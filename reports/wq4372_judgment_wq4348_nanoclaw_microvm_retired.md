# wq#4372 — verify-pass of wq#4348 (PR#2248, NanoClaw dissection)

**Verdict: CONTRADICT, escalated to opus-5@high — judgment: CONFIRM wq#4348 as
delivered, file a scoped doc-only errata ticket.** The research doc's core
deliverable — the dissection and Limpet recommendations A–E — holds up. Two of
its factual claims about NanoClaw's current architecture do not: one is stale
(a feature the doc frames as current was retired by the time of the doc's own
claimed research window), the other is internally self-contradictory. Neither
finding touches the actionable guidance Limpet would act on.

## What wq#4348 shipped

`git show 48af32d791 --stat` (merged to `main` as PR#2248): one new file,
`research/WQ4348_NANOCLAW_DISSECTION.md`, 356 lines, no code. A dissection of
the "NanoClaw" agent framework (container-per-agent, MicroVM Docker Sandboxes,
hardening layers, auditable small codebase) ending in 5 lettered
recommendations (A–E, lines 266–315) for Limpet's RE-agent isolation design.

This is a research deliverable, not code — there is no runnable acceptance
probe in the code-change sense. "Running its probe" here means independently
reproducing the doc's sourced claims via the same tool (`fleet-search web`)
the author doc says it used, and checking internal consistency.

## Finding 1 — Section 2 presents a retired feature as current architecture

Doc lines 107–131 ("## 2. MicroVM-based Docker Sandboxes (the second
isolation ring)") and the architecture diagram (lines 44–69) present a
"MicroVM (Docker Sandbox)" layer as a current, live second isolation ring —
hedged only as "platform support is currently partial... Linux support coming
in a few weeks... treat as a thing to re-verify at adoption time" (lines
123–127).

Ran `fleet-search web` twice today (2026-10-19 — the doc's own claimed
research date), two independently-worded queries:

- `"NanoClaw container-per-agent Docker Sandboxes microVM"` and
  `"NanoClaw Sandboxes retired announcement historical post microVM container
  lifecycle guide"` — both returned the live `nanoclaw.dev` blog post
  announcing the Docker-Sandboxes partnership (EN and KO localizations), and
  both carry the same current banner: *"Historical post — March 13, 2026.
  This announcement describes a retired experimental integration. Current
  NanoClaw releases use Docker containers directly; Docker Sandboxes and
  micro-VM runtimes are not supported."*
- `"NanoClaw mount-allowlist.json egress lockdown OneCLI Agent Vault
  SECURITY.md"` — returned `docs.nanoclaw.dev/operate/hardening` (the doc's
  own cited source at line 157–158, for a different claim) stating
  independently: *"NanoClaw's isolation boundary is the Docker container ...
  There is no micro-VM or alternative sandbox runtime ... hardening means
  tightening what those containers can reach."*

Two independent current primary sources — the project's own blog (now
carrying a retraction banner) and the project's own current hardening docs —
both explicitly deny that a microVM/Docker-Sandboxes layer currently exists.
The doc's "partial, re-verify at adoption time" hedge materially understates
this: as of the doc's own claimed research date, the feature isn't partial,
it's retired. This also means the doc's own citation of
`docs.nanoclaw.dev/operate/hardening` at line 157–158 (for the sender-gate
claim) sits one page away from a sentence that contradicts Section 2 — a
sourcing lapse against the doc's own anti-pattern #5 ("NanoClaw does X needs
a repo+commit... carry that discipline into any writeup").

All instances of the MicroVM framing: intro (lines 14–16), "container/VM
boundary" (line 23), diagram title and outer box (lines 44, 59–68), Section 2
(107–130), feature-list item 5 (243–245).

## Finding 2 — internal self-contradiction on the project-root mount claim

Line 82: *"The project root itself is never mounted (current `nanocoai/
main`) — host source (`src/`, `dist/`, `package.json`) is simply
unreachable... closing the earlier 'agent edits its own sandbox code' failure
mode an older fork generation... handled only by mounting that root
read-only."*

Lines 101–105: *"Main (trusted, self-chat) gets the project root read-only
plus cross-group admin; non-main groups get only their own group folder...
and no project access at all."*

Both passages describe "current `nanocoai/main`" and directly contradict
each other: one says the project root is never mounted and that mounting it
read-only was the superseded old behavior; the other says Main currently
gets it mounted read-only. A `fleet-search web` query independently surfaced
a real GitHub PR, `nanocoai/nanoclaw#392` ("fix: mount project root read-only
to prevent container escape," gavrielc), whose description matches the
second claim (read-only, not unmounted) as the real fix. A PR alone doesn't
prove current `main` state — this needs a commit-pinned re-check, not a
blind preference for one of the doc's own two claims.

## Escalation and opus-5@high judgment

Per archetype 8, a contradiction escalates. Spawned a fresh opus-5@high
instance with both findings' full text and evidence, asked it to judge
materiality (does this sink the deliverable) and next steps, not to
re-implement or re-research.

Its verdict: **CONFIRM wq#4348 as delivered, file a scoped errata ticket.**
Key points:

- Recommendations A–E (lines 266–315) were read directly: A is an ephemeral
  container (`--rm`, non-root, cap-drop, no-new-privileges), B is a mount
  allowlist, C is egress-on-by-default, D is resource limits on by default,
  E is enforce-in-code. None reference a microVM/second ring or the
  project-root-mount specifics. The *real* NanoClaw ("isolation boundary is
  the Docker container," no microVM) supports A–D at least as well — if
  anything it strengthens D, since the container is the only ring.
- Still more than cosmetic: the MicroVM claim appears in five places stated
  as current fact, and Limpet's RE-agent workload (untrusted firmware/binary
  execution) is exactly the context where a future reader would reach for
  "add NanoClaw's microVM ring" on the strength of this doc.
- Recommended follow-up scope: rewrite Section 2 as a historical note citing
  both sources found here; strip MicroVM framing from the intro/diagram/
  feature list; reconcile the project-root passages into one commit-pinned
  statement; optionally cross-link wq#4363's sandbox-tier doc for whether
  Limpet's RE tier needs a second ring at all (not to be re-researched in
  the errata ticket).

Filed `[judgment] wq#4372 contradicted wq#4348: NanoClaw MicroVM/project-root
claims are stale or self-contradictory` as **wq#4382** (`deps: 4348`, lane
build, pri 3 — doc-only, low priority per the judgment) carrying this finding
forward as actionable follow-up work. wq#4348 itself is not reopened or
reverted.

## Not re-litigated

- Every other sourced claim in the doc (egress lockdown mechanics, mount
  allowlist validation order, OneCLI vault, PR#1605 sender/command gate,
  resource-limit defaults, the 4k-LOC/contribution-policy claim) — spot
  checked the egress-lockdown passage against the live
  `docs.nanoclaw.dev/operate/hardening` page and it matches closely; did not
  independently re-verify the rest line-by-line, since the two findings
  above already met the bar for escalation and the ticket asks to verify,
  not exhaustively re-research.
- Rewriting the doc myself — that is wq#4382's job, not this verify pass's.
