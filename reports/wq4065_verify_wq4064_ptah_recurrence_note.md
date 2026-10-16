# wq#4065 — verify-pass on wq#4064 ([selfheal] fleet node ptah offline on tailscale for 30 min)

PR: `claude-foreman#2013`, merge commit `772779e7` (squashing `9d7db4ef`) on
this branch, 1 file / 2 insertions / 1 deletion: `ops/wq3354-fleet-selfheal/selfheal.yaml`.
Author: claude-sonnet-5 (fworker-wq4064) · Verifier: claude-sonnet-5@high (fresh
instance, wq#4065).

## Claim under test

wq#4064 is the expected daily re-page of the same unresolved ptah outage that
wq#3957 already classified the day before (kernel-upgrade boot cycle that
never returned, physical check still open as wq#3949, no smart plug). The PR
appends the wq#4064 recurrence to the existing `nodes.notes.ptah` entry in
`selfheal.yaml` (same pattern already used for `nephthys`'s repeat AER
outages) so the next page doesn't have to re-derive the context. It makes no
change to `nodes.ignore` / `nodes.sleepers` — the page is expected to keep
firing daily until wq#3949 lands.

## What I checked

1. **Read the diff cold** (`git diff 57b256c2 772779e7 -- ops/wq3354-fleet-selfheal/selfheal.yaml`).
   Single hunk, touches only the `ptah:` line under `nodes.notes`: closes the
   existing wq#3957 sentence's quote, appends a new sentence (`wq#4064
   (2026-10-16T03:20Z): still offline, ...`), reopens/closes the quote.
   `hathor`, `neith`, `nephthys` entries are untouched (confirmed byte-for-byte
   identical in the diff).

2. **Validated the YAML.** `python3 -c "import yaml; yaml.safe_load(...)"` —
   parses cleanly; `nodes.notes` is still a 4-key dict (`hathor`, `neith`,
   `nephthys`, `ptah`) and `notes['ptah']` is one continuous string containing
   both the wq#3957 and wq#4064 sentences (YAML double-quoted line-folding
   collapses the newline+indent to a single space, as intended).

3. **Traced how `nodes.notes` is consumed** (`sh_nodes.py:185-187`): the note
   text is only appended as an extra `known issue:` line on the ticket body
   (`lines.append(f"known issue: {note}")`). It plays no part in the
   `ignore`/`sleepers` checks above it or in the
   `if f"node-offline:{node}" not in ctx.section("tickets")` de-dup gate, so
   this change cannot suppress or alter future paging — confirms the PR's "no
   ignore/sleepers change" claim structurally, not just by assertion.

4. **Verified every factual claim in the new sentence against live state**
   (not just against the author's own ticket notes):
   - `/home/lumen/bin/lumen_state.py current ptah` →
     `ptah.availability = offline ... (since 2026-10-14) ... [conf 0.90]` —
     matches "lumen_state confirms ptah.availability=offline since
     2026-10-14, conf 0.90" exactly.
   - `/home/lumen/bin/wq show 3949` → `status: open`, `claimed_by: None` —
     matches "wq#3949 is still open/unclaimed".
   - Same lumen_state output: no smart plug assigned to ptah — matches "no
     smart plug added".
   - `/home/lumen/bin/wq show 3957` → `done`, notes cite PR #1912, "added
     nodes.notes.ptah entry ... since neither ignore nor sleepers applies and
     the page must keep firing" — matches the claimed lineage and confirms
     wq#4064 is a same-day-pattern recurrence, not a new incident.
   - `/home/lumen/bin/wq show 4064` → ticket detail's own `LastSeen
     2026-10-16T03:20:00.1Z` matches the sentence's `(2026-10-16T03:20Z)`
     timestamp exactly.

5. **Tried to break it.** Looked for: a schema/length cap on `notes` values
   (none — it's a free-text dict consumed only for string interpolation);
   whether appending indefinitely growing per-recurrence lines could itself
   be a defect (it isn't new — this is the same shape nephthys's entry
   already uses, just inline-summarized there instead of a dated sub-line);
   whether the stale-looking `status = healthy 2026-10-11 ... no action
   needed [conf 0.65]` line inside wq#4064's auto-filed ticket body
   contradicts the fix — it doesn't: that line comes from a different,
   pre-existing `power_state()` facts cache unrelated to this diff, predates
   both wq#3957 and wq#4064, and is not something this PR touches or could
   fix within its scope.

No contradiction found. The diff is minimal, scoped to exactly the line it
claims to touch, doesn't change selfheal gating/paging behavior, and every
factual assertion in the new text checks out against live `lumen_state` and
`wq` data at verification time.

## Verdict

**CONFIRM.** wq#4064's fix does what it claims: records the ptah recurrence
in `nodes.notes.ptah` without silencing future pages, consistent with the
established nephthys pattern and verified against live fleet state.
