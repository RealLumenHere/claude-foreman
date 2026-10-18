# wq#4231 — verify-pass of wq#4227 (hal-spec.md dup `## 9`, bind/auth fail-closed)

Archetype 8 adversarial verify-pass, fresh instance, of PR#2157 (merged `a9b37fe5aa4a`,
landed on this branch as `30e1253a2d9f` — `git diff a9b37fe5 30e1253a -- docs/hal-spec.md`
is empty, so no drift between the merged PR and the current tree). Author: claude-sonnet-5.
Verdict: **CONFIRM**.

## Claims checked against live evidence

1. **Only one `## 9` heading remains.** `grep -n '^## ' docs/hal-spec.md` shows sections
   1–9 exactly once each, ending `682:## 9. Safety — the Energize-Confirm Gate (MANDATORY)`.
   Subsections run `9.1, 9.2a, 9.2b, 9.3, 9.4, 9.5, 9.6` with no gaps or re-used numbers.

2. **Both original texts were preserved, not dropped.** The new §9.6 ("Reconciling §9.2a
   and §9.4 item 2", `docs/hal-spec.md:891-908`) explicitly carries forward both of the
   original duplicate sections' "missing X defaults to Y" rules (§9.2a capability-class
   `energizing` default vs §9.4 item 2 action-class `destructive` default) and argues they
   are fail-closed defaults on two independent axes, not a contradiction. A `>` callout at
   `docs/hal-spec.md:688-693` states the merge rationale and cites wq#4227 directly.

3. **Every cross-reference in the new/merged text resolves to real content, not a
   fabricated citation:**
   - `plugin-spec.md §6.5` (`docs/plugin-spec.md:409-437`) — contains the exact quoted
     rule, "the stricter of (capability class, verb class) wins." Confirmed by direct read.
   - `target-wizard-spec.md §4.3` (`docs/target-wizard-spec.md:171-176`) — contains the
     exact quoted phrase, "any step that **energizes the device** (applies probe power,
     drives JTAG/SWD, writes flash) requires an explicit operator confirm before it runs."
     Confirmed by direct read.
   - `design/wq3806_limpet_architecture.md §4`/`§6` — §4 is "Hardware abstraction layer
     (HAL)", §6 is "Agent core." Both exist at the claimed numbers.
   - `mcp-meta-spec.md §6.3/§2.2/§9` — this file is not git-tracked in this repo (confirmed
     absent from `foreman`; lives at `/home/lumen/projects/limpet/docs/mcp-meta-spec.md`,
     outside this worktree's allowed paths), but a snapshot captured by an unrelated ticket
     (`_runs/wq4080/snapshots/20261016T071356Z-..._mcp-meta-spec.md`) confirms §6.3 names
     `limpet_confirm_destructive` as the one confirm tool with "no separate
     `energize_confirm` tool (wq#4080)" — matching hal-spec.md's item 8 claim verbatim —
     and §9's `sse_host = "127.0.0.1"` matches the "binds to loopback ... by default" claim.
   - `wq#4209`'s `lampOS-evaluation.md` — snapshot (`_runs/wq4225/snapshots/..._lampOS-
     evaluation.md:1`) is titled "lampOS evaluation (wq#4209)" and its line 83 states
     lampOS's "Control API bound to 0.0.0.0 with wildcard CORS and auth that disables
     itself when a key is unset" — matches hal-spec.md §9.4 item 9's counter-example
     verbatim. Ticket attribution is correct, not a fabricated source.

4. **The new §9.4 item 9 (bind/auth fail-closed) is grounded in actual code, not just
   asserted.** Read both implementations directly:
   - `ops/wq3809-limpet-hal/hal_daemon.py:30,66-73` — uses only `socket.AF_UNIX` (`DGRAM`
     for the notify input, `STREAM` for subscribers); no `AF_INET`/TCP path exists anywhere
     in the file. Supports the claim "`limpet-hald` never listens on a network socket ...
     is a Unix domain socket."
   - `ops/wq3808-limpet-mcp-meta-server-a1/meta_server.py:94-95` — any registry entry whose
     `transport != "stdio"` is explicitly skipped as unsupported. Supports the claim that
     the Meta-Server's only currently-live transport is stdio (no network exposure).

5. **ISSUE-LOG #1421** (`kb/ISSUE-LOG.md:1570`) exists, was appended via the row-locking
   script per doctrine, and its cause/fix columns match the PR notes exactly: same L682/
   L723 locations, same backup filename convention (`hal-spec.md.bak.20261018T083922Z`),
   same claim that the fix was ported into git-tracked `docs/hal-spec.md`. The ticket row
   also correctly notes `mcp-meta-spec.md` was *not* ported (pre-existing gap, out of
   wq#4227's scope — not a defect introduced by this PR).

## Probe (can fail; verified it actually can)

`grep -n '^## [0-9]' docs/hal-spec.md | sort -t. -k1` — confirms headings 1 through 9,
each appearing exactly once, in order. This probe is falsifiable: run against the two
pre-fix fragments described in ISSUE-LOG #1421 (two independent `## 9` headings), it would
report two matching lines for `9`; against the merged file it reports one. Also ran
`grep -c '^## 9'` → `1` (would be `2` on the pre-fix shape).

## Gaps disclosed, not defects

- The live source of truth for this doc is the untracked `/home/lumen/projects/limpet`
  staging tree (ISSUE-LOG #1317/#1328 pattern), outside this verify worker's allowed
  directories, so the actual backup file and live-tree edit could not be directly
  re-read from this session — only corroborated via the ISSUE-LOG row and the identical
  ported copy now in git. This is an environment/sandboxing limit, not evidence against
  the fix.
- `mcp-meta-spec.md` remains unported into foreman's git-tracked `docs/`, so the three
  cross-references to it in the merged hal-spec.md point at a file invisible to this
  repo's own diff review. Pre-existing condition, correctly flagged in ISSUE-LOG #1421,
  out of wq#4227's stated scope (duplicate heading + bind/auth requirement only).

## Verdict: CONFIRM

No duplicate heading survives, no text was lost in the merge, every citation added or
relied upon by the new prose resolves to real content that says what it's quoted as
saying, and the new fail-closed bind/auth requirement is grounded in the actual
`hal_daemon.py`/`meta_server.py` implementations rather than asserted blind. No
contradiction found; no escalation.
