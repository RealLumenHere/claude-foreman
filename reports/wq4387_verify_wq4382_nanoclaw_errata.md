# wq#4387 — verify wq#4382 (PR#2282, errata on wq#4348 NanoClaw MicroVM/project-root claims)

**Verdict: CONFIRM.** Every primary-source citation in the errata diff was independently
re-checked against live sources (not re-cited from the diff's own word) and matches
verbatim or near-verbatim. The diff fixes both findings wq#4372 raised, stays inside the
scope opus-5@high recommended, and introduces no new self-contradiction.

## What wq#4382 shipped

`research/WQ4348_NANOCLAW_DISSECTION.md`, 356 → 392 lines (two commits: `56b903a7` content,
`61af003c` merge). Diff hunks touch only: intro (old 11–18), sourcing-note anchor (old
41–47), architecture diagram + caption (old 56–75), Section 1's project-root/mount passages
(old 79–96), Section 2 in full (old 98–156, retitled "historical, retired"), and feature-list
items 3 and 5 (old 235–248). Confirmed via `diff -u` against the pre-errata blob
(`git show 652c6f7c:research/WQ4348_NANOCLAW_DISSECTION.md`) that nothing outside those six
hunks changed — Section 3 (hardening), Section 4 (codebase-size), Recommendations A–E, and
Anti-patterns are byte-identical to wq#4348's original. This matches opus-5@high's recorded
scope exactly (rewrite Section 2 as historical, strip MicroVM framing from
intro/diagram/feature-list, reconcile the project-root passages, cross-link wq#4363 — not
re-research recommendations A–E).

## Probe: re-ran the sourced claims independently, didn't trust the diff's word

Same method wq#4372 used on wq#4348 (`fleet-search web`), plus direct primary-source pulls
the verify pass before mine didn't do (raw GitHub file, GitHub REST API) — a stronger probe
than citation-matching, since it can catch a citation that merely sounds right.

1. **nanoclaw.dev retraction banner** — `fleet-search web` independently returned
   `nanoclaw.dev/blog/nanoclaw-docker-sandboxes/` carrying: *"Historical post — March 13,
   2026. ... Current NanoClaw releases use Docker containers directly; Docker Sandboxes and
   micro-VM runtimes are not supported."* Matches the doc's quote (line 132–134) verbatim.
2. **docs.nanoclaw.dev/operate/hardening** — fetched directly (`curl`, stripped HTML):
   *"NanoClaw's isolation boundary is the Docker container ... There is no micro-VM or
   alternative sandbox runtime — hardening means tightening what those containers can
   reach."* Matches the doc's quote (line 135–137) verbatim, word for word.
3. **PR `nanocoai/nanoclaw#392`** — GitHub REST API (`/pulls/392`): title *"fix: mount
   project root read-only to prevent container escape"* by `gavrielc`, `merged_at:
   2026-02-22T18:57:57Z`, `merge_commit_sha: 5fb10645cda1...`. Matches the doc's "merged
   2026-02-22 at `5fb10645`" (line 90–91) exactly — date and 8-char SHA prefix both correct.
4. **"v2 perimeter" rewrite commit** — GitHub commit search (`v2 perimeter`, scoped to the
   repo) returned `3906104960...`, dated `2026-07-04T16:10:46+03:00`, message *"docs:
   rewrite security docs to match the v2 perimeter."* The doc cites this commit as `39061049`
   (line 92) — the first 8 hex chars of the same SHA (`3906104960` is the 10-char prefix used
   in the commit message body); not a discrepancy once checked against the actual SHA, just
   two different truncation lengths of one commit. Flagged this explicitly as the one thing
   worth someone else double-checking, then resolved it myself rather than leaving it as an
   open question.
5. **Current `nanocoai/main` state** — GitHub REST API commit list pinned exactly to
   `7be8d7f6b9863e7c78c513717c21a6fcf04c3d28`, authored `2026-10-18T12:41:18Z` — matches the
   doc's "commit `7be8d7f6`, 2026-10-18" (lines 87, 273) exactly. Fetched
   `raw.githubusercontent.com/nanocoai/nanoclaw/main/docs/SECURITY.md` directly and grepped
   it: line 53 *"The project root is **never mounted**"*, line 168 *"The project root and
   `.env` — never mounted"*, plus `user_roles` table references matching the doc's
   "`user_roles`-based trust model" framing (lines 93, 113). No self-contradiction found in
   the live file — it says "never mounted" everywhere, consistently, which is what the
   errata's reconciled statement (lines 86–97) now also says.

## Internal-consistency re-check (the self-contradiction wq#4372 found)

Grepped the full new doc for every remaining "project root" / "MicroVM" / "second ring"
mention (16 hits) and read each in context: all 16 are now either (a) explicitly framed as
historical/retired/superseded, or (b) the single reconciled "never mounted" statement cited
in three places (Section 1, feature-list item 3, and the mount-allowlist bullet in Section 3,
which was never part of the contradiction and is unchanged). No instance states or implies
that the project root is currently mounted, or that a MicroVM ring is currently live. The
specific contradiction wq#4372 flagged (old line 82 "never mounted" vs. old line 101 "Main
gets it read-only") no longer has two competing "current" claims — the second one is now
explicitly marked "historical, v1" (line 112) and points at the same reconciled statement.

## Tried to break it

- Checked whether the errata quietly changed the Cohen/Cavage pitch quotes while rewriting
  around them — both quotes (lines 148–150 in Section 2) are byte-identical to the original,
  only the surrounding frame changed from present-tense claim to "preserved as a historical
  record... as pitched." No misquote introduced.
- Checked whether dropping the old "Threat model it buys you" bullet (old lines 128–130,
  which claimed a *current* containment benefit) was a silent deletion of content vs. an
  honest removal — it's the latter: that bullet's claim (microVM contains an escape today)
  is simply false post-retirement, and there's no historical-record version of it worth
  keeping, unlike the pitch quotes which are legitimately historical artifacts.
- Checked recommendations A–E and the anti-patterns list for any residual dependency on the
  now-retired MicroVM ring (e.g., recommendation A citing a second isolation layer) — none
  found; A–E were already container-only, which is why opus-5@high judged the deliverable's
  actionable guidance unaffected in the first place.

## Not re-litigated

Sections 3 (hardening layers) and 4 (codebase size), and the Recommendations/Anti-patterns —
wq#4372 already spot-checked egress lockdown against the live hardening page and found it
accurate; this diff doesn't touch those sections, and re-verifying unchanged text is outside
an archetype-8 pass on a diff.

**No escalation.** No contradiction found; opus-5@high judgment stands.
