# wq#1637 — core prose migration: rules → instincts, procedures → playbooks, stubs + session blocks out

Source ticket: MINERVA_PIPELINE_MAP.md §4.2 (ownership table) / §5 row 11. Standing law:
consolidation of `claude_memory.md` / `instincts.yaml` is **proposal-only** (instincts.yaml
`consolidation-proposal-only`, wq959) — this directory is the plan + diffs; nothing here has
been applied to `/home/lumen/claude_memory.md`, `/home/lumen/instincts.yaml`, or
`/home/lumen/instructions/playbooks/*.md`. Frank/orchestrator applies.

`/home/lumen/claude_memory.md` was read directly (not via Bash — Bash is sandboxed to this
worktree + add-dirs; the `Read` tool reaches the real file, see `harness/fleet-exec-policy.yaml`
for why Bash can't: `/home/lumen/minerva` is excluded as a credential surface but
`/home/lumen/claude_memory.md` itself is simply outside every granted root and add-dir).
Mapped by a subagent (full paginated read, 5671 lines) then spot-verified line-for-line by this
session at 6 locations (all matched exactly) before any deletion was drafted — no line range or
quote below is guessed.

## State at dispatch

554 KB, 5671 lines, 279 H2 sections, 137.6K tok total. **26 `#CORE` sections confirmed** by
eye (ticket cites ~29 from an earlier automated count — see "count discrepancy" below), ~11.06K
tok of that CORE budget against an 8K target.

## 1. `#CORE` imperative sections → instincts.yaml candidates

See `instincts_candidates.yaml`. 13 `#CORE` sections carry NEVER/MUST/STANDING RULE/MANDATE
language. Disposition:

| # | claude_memory.md section (line) | disposition | destination |
|---|---|---|---|
| 1 | STATE MARKERS — 2026-05-18 (233) | migrate (prompt-drafting rule only; the marker legend itself is a content-format spec, not a rule — leave format spec as regular prose) | `instincts_candidates.yaml`: `fleet-state-markers` (tier 2, fleet-ops) |
| 2 | CURRENT STATE OVERRIDES — 2026-05-13 (297) | **do not migrate as instinct** — this is dated fact content (Ghost deletion, LiteLLM state) owned by `state_facts` per pipeline-map §4.2 row 1/5, superseded by tickets #1/#3/#5, not this ticket. Delete once state_facts covers it; flagged, not actioned here. | out of scope |
| 3 | MEMORY V2 PHASE 1 — bi-temporal (4004) | migrate (query/ingest/never-hand-edit doctrine is universal) | `instincts_candidates.yaml`: `bi-temporal-state-query` (tier 1) |
| 4 | FLEET IDENTITY CORRECTION — Thor=Samsung (4049) | split: identity/IP fact → state_facts (out of scope, already tracked); the *rule* half (never SSH dead identity, never gate GPU-B on amdgpu-both-bound Macs, never edit initramfs headless) → migrate | `instincts_candidates.yaml`: `gpu-b-gating-and-initramfs-ban` (tier 2, fleet-ops) |
| 5 | LLM SLEEP-TIME REFLECTOR (4057) | migrate (hard guarantees: never mutates claude_memory.md/state/episodic store, fail-loud, disabled-pending-Frank) | `instincts_candidates.yaml`: `reflector-hard-guarantees` (tier 2, fleet-ops) |
| 6 | BUILD NOW, NEVER DEFER TO A TIMER (4350) | migrate verbatim, short, universal | `instincts_candidates.yaml`: `build-now-never-defer` (tier 1) |
| 7 | CCC composer-overlap fix (4702) | **demote, don't migrate** — one-off tool-specific debugging lesson, not a standing behavioral rule (no NEVER/MUST on inspection, "MUST restart the app" is a fact about the tool, not fleet doctrine). Drop the `#CORE` tag, leave as ordinary history prose. | stays in claude_memory.md, tag removed |
| 8 | LUMEN STORAGE TOPOLOGY — verified, do not re-derive (4709) | split: the topology numbers are `state_facts`/declared-config territory (out of scope); the rule "verify existing inventory before recommending hardware spend" → migrate | `instincts_candidates.yaml`: `verify-inventory-before-spend` (tier 2, hardware) + append to `hardware-claims.md` |
| 9 | ANTHROPIC ACCESS = MAX 20x, NEVER API (4798) | migrate verbatim intent, short, high blast-radius (cost control) | `instincts_candidates.yaml`: `anthropic-max-not-api` (tier 1) |
| 10 | GITHUB STANDARDS INSTATED (4939) | **delete, no migration needed** — `session-start.md:103-106` already carries this exact pointer ("Any session that touches git/GitHub MUST read .../github-standards.md first"); the CORE copy is a pure duplicate | delete |
| 11 | LANE BOUNDARIES — wq#512 (4989) | **delete, no migration needed** — `/home/lumen/agents/work-queue/LANE_BOUNDARIES.md` already exists as the canonical contract; CORE copy is a stale summary | delete |
| 12 | GITEA CREDENTIAL — one file, one rotation point (5001) | migrate (masking rule + never-re-embed-token is exactly `mask-before-print` tier-0's domain, but Gitea-specific detail belongs in a playbook) | append to `github-standards.md`; index via `instincts_candidates.yaml`: `gitea-credential-domain` (tier 2) |
| 13 | MEMORY V4 P3 — lumen-memoryd (5082) | migrate one sentence only (identity trust is server-side, never request-body) — the rest is architecture narrative, not a rule | `instincts_candidates.yaml`: `memoryd-identity-trust` (tier 2, fleet-ops) |
| 14 | AQ ESCALATION RESPONDER (5261) | migrate (suppress-after-page rule is operational and easy to violate by re-paging) | append to `fleet-ops.md`; index via `instincts_candidates.yaml`: `aq-escalation-domain` (tier 2) |
| 15 | CORD — Frank secret handoff (5466) | migrate (NEVER stalls/asks in chat, stdout-only, no CF ingress — high-consequence secret-handling rule) | `instincts_candidates.yaml`: `cord-secret-handoff` (tier 2, secrets) + append to `cloudflare-ops.md` (the "not behind CF, by design" clause is exactly that playbook's domain) |

Two borderline CORE sections (`CURRENT STATE OVERRIDES — 2026-06-07`, `GITHUB PRO + CLASS-A
RULESET LIVE`) are fact/decision records, not imperatives — left alone (row 2's disposition
applies to both: state-store or declared-config territory, tickets #1/#3, not this one).

## 2. Procedure sections → playbooks

| # | section (line) | disposition |
|---|---|---|
| 1 | MEMORY V2 PHASE 2 — episodic recall guard (4029) | migrate as `instincts_candidates.yaml`: `episodic-recall-guard` (tier 1) — the precheck/commit cycle is universal operating doctrine, not domain-specific, and isn't in current instincts.yaml (spot-checked: no `lumen_episode` entry exists today) |
| 2 | ARTICLE-RESEARCH QUEUE (4451) | the lane/dispatch description is already covered by `agents/work-queue/ARTICLE_RESEARCH_DISPATCH.md` (confirmed present). The generic `wq` CLI table (add/list/claim/edit/done/release/block) is NOT lane-specific and isn't fully covered in `session-start.md` today (which only documents claim/heartbeat/preflight, not add/list/edit/done/release/block) → append the missing verbs | `playbook_appends/session-start-wq-cli-append.md` |
| 3 | PARALLEL-SESSION COORDINATION (4526) | **delete, no migration needed** — verbatim-confirmed already fully present in `session-start.md:74-92` plus a 2026-07-17 enforcement addendum at line 92. The CORE copy is the stale one (predates the enforcement update) | delete |
| 4 | HARDWARE-RE RESEARCH GATE (5278) | migrate — this is a live enforcement mechanism (`action_gate.py`) description with a clear command, not covered elsewhere found so far | append to `fleet-ops.md` |
| 5 | Lesson — Argus audio diagnostic order (2295, non-CORE) | leave as-is — short, self-contained, non-CORE, costs no selector budget; migrating every non-CORE procedure is out of this ticket's scope (drift-per-change is near zero) |
| 6 | SWITCHOVER INCIDENT + RECOVERY procedure (4157, non-CORE) | leave as-is, same reasoning as #5; flag as a candidate for a future `vault-ha` playbook append if #7 (audit chokepoint) or the HA tickets touch that area |

## 3. Dead `MIGRATED … to playbooks/` stubs (2026-07-09) — delete outright

All 6 confirmed byte-for-byte by direct `Read`, plus one non-stub casualty (`TABLE OF CONTENTS`)
that only makes sense pointing at material two of these stubs already migrated away:

| heading | lines | verified |
|---|---|---|
| `## 🧭 MCP Toolkit Index — READ THIS FIRST` | 454–458 | yes |
| `## 🛡️ CLASSIFIER-SHAPE ROUTING RULE` | 541–544 | yes |
| `## 0. QUICK REFERENCE CARD` | 650–654 | yes |
| `## TABLE OF CONTENTS` (sits between two stubs, lists 12 sections most of which no longer exist in that form) | 656–669 | yes |
| `## 1. SESSION START CHECKLIST` | 672–675 | yes |
| `## HANDOFF SYSTEM (built 2026-07-01) #CORE` | 4340–4342 | yes |
| `## LONG COMMANDER JOBS → lumen-run … #CORE` | 4419–4421 | yes |

Diff: `diffs/01-delete-migrated-stubs-and-toc.diff`.

## 4. `SESSION <date>` blocks stop being written

Strict all-caps `## SESSION <date>` match: **5 blocks, 2026-06-26 through 2026-09-08**
(lines 4112, 5525, 5577, 5621, 5628 — all 4 of the 09-08 blocks verified byte-for-byte; the
06-26 block taken on the subagent's report, not independently re-verified). Combined ≈155
lines / ~2.3K tok.

Per the ownership table (§4.2 row "episodes / lessons / what-changed"), these three streams
replace the convention going forward:
- **in-flight state** → the merged HANDOFF.md (wq#1630: session-handoff writes into a generated
  region, `--core` re-renders at read)
- **lessons / what-worked / what-failed** → `lumen_episode.py commit` (already the tier-1
  `episodic-recall-guard` instinct from §1 above — this ticket makes the commit path the
  *only* path, not a parallel one)
- **fleet facts that changed** → `lumen_state.py ingest` (ticket #1, MEMORY V2 PHASE 1,
  already an instinct per §1 row 3)

Action for *this* ticket: delete the 5 strict-match blocks (`diffs/02-delete-session-blocks.diff`)
and add one line to the `episodic-recall-guard` instinct text (see
`instincts_candidates.yaml`) stating the convention is retired — a rule that isn't stated
anywhere gets re-invented by the next session, which is exactly how 4 of 5 blocks landed on
the same day (2026-09-08) from 4 different concurrent orchestrators.

**Not actioned, flagged for a follow-up ticket:** the broader population of lowercase
`Session end/update/continued`, `FABLE BATCH`, and `SHIFT REPORT` headings (rough estimate:
several hundred more lines, scattered ~line 1136–4600). The ticket's own example
("SESSION 2026-09-08") only names the strict convention; broadening scope to the informal
variants risks catching content that's actually still load-bearing narrative rather than a
dead convention, and needs its own read-through rather than a rider on this diff.

## 5. `minerva_brief.py` daily prepend — label as generated

Confirmed live at lines 29–83: wrapped `<!-- MINERVA_BRIEF_START/END -->`, dated to the day
of read (2026-09-10 at read time), first content block in the file. `## MINERVA_SYSTEM`
(375–397) independently documents the mechanism: "minerva-brief (daily 13:00 UTC) →
briefings/YYYY-MM-DD.md + auto-prepend to this file."

Two problems, one fix:
1. The prepended block has no `generated-from:` header (pipeline-map §4.4's projection
   contract) — a reader can't tell it's a projection rather than something someone typed.
2. **A stale second copy was found** at lines 399–447, dated 2026-07-04/07-05 content sitting
   inside an unrelated `<!-- SECTION:MESSENGER_WEBAPP_2026_04_23 -->` wrapper — direct evidence
   the auto-prepend has left orphaned copies behind before. Not cleaned up by any later prepend
   because each prepend only knows how to *add* at the top, never remove a stray earlier copy.

Proposed fix (`minerva_brief_fix.md`): `minerva_brief.py`'s prepend step (a) writes a
`<!-- generated-from: minerva.db proposals table @ <ts> -->` header immediately inside
`MINERVA_BRIEF_START`, and (b) before inserting the new block, scans the file for *any* existing
`MINERVA_BRIEF_START/END` pair (not just the one it thinks it wrote yesterday) and removes it —
so there is only ever one live copy. Delete the stale 399–447 copy now as part of this ticket's
diff (`diffs/03-delete-stale-brief-duplicate.diff`), independent of whether/when the script fix
lands (this ticket cannot edit `minerva_brief.py` itself — `/home/lumen/minerva` is excluded
from this session's read/write roots per `harness/fleet-exec-policy.yaml:67`, wq#1467 credential
surface — the fix is proposed as a diff description for whoever holds that grant).

## Count discrepancy

The ticket text says ~29 `#CORE` sections; this pass found 26 by eye (paginated `Read`, no
`grep` available against the real file from this sandbox — `grep`/`find` against
`/home/lumen/claude_memory.md` are blocked the same way `ls`/`find` are, only single-file
`Read` calls get through). Recommend a session with `harness/fleet-exec-policy.yaml` read
access to `/home/lumen` proper run `grep -c '#CORE' /home/lumen/claude_memory.md` before
applying, to confirm nothing was missed in the densest stretch (roughly line 1800–4000, which
this pass read but did not re-verify line-by-line).

## Expected token effect

Deleting: 6 stubs + TOC (~35 lines), 5 SESSION blocks (~155 lines), stale brief duplicate
(~49 lines), 3 duplicate CORE sections (§1 rows 10/11 + §2 row 3, ~35 lines) = **~274 lines**
removed outright. Migrating 10 more CORE sections out (§1 rows 1,3,4,5,6,8,9,12,13,15) plus
2 procedure sections (§2 rows 1,4) drops their content from the CORE-selector budget entirely
even though the underlying text moves rather than deletes — that content stops costing every
session token budget, which is the ticket's real target (11.06K → ≤8K CORE tok), not raw line
count. Exact post-migration tokenization should be re-measured with `context_selector --stats`
after apply; this plan does not have access to run that tool (lives under the same excluded
`/home/lumen/minerva` root) so the ≤8K target is not verified here, only argued from the
removed section list.

## Files in this proposal

- `instincts_candidates.yaml` — 12 new instincts.yaml entries (tier 1 ×4, tier 2 ×8), schema 2
  (matches `ops/wq959-instincts-tiering/instincts.yaml`)
- `playbook_appends/*.md` — content blocks to append to existing playbooks, with target file
  named in each
- `diffs/01-delete-migrated-stubs-and-toc.diff` — removes the 6 dead stubs + orphaned TOC
- `diffs/02-delete-session-blocks.diff` — removes the 5 strict SESSION blocks
- `diffs/03-delete-stale-brief-duplicate.diff` — removes the orphaned 2026-07 brief copy
- `diffs/04-delete-duplicate-core-sections.diff` — removes the 3 CORE sections that duplicate
  already-canonical playbook/doc content (GITHUB STANDARDS, LANE BOUNDARIES, PARALLEL-SESSION
  COORDINATION)
- `minerva_brief_fix.md` — proposed `minerva_brief.py` change (out-of-reach to implement
  directly from this worktree)
- `session_blocks_sunset.md` — the retirement plan for requirement 4, including the
  out-of-scope broader-population note

## Apply order (for whoever runs this)

1. Merge `instincts_candidates.yaml` entries into `/home/lumen/instincts.yaml` (append under
   the correct tier headers), run `python3 harness/instincts_render.py --check`.
2. Apply each `playbook_appends/*.md` to its named target file.
3. Apply `diffs/01`–`04` to `/home/lumen/claude_memory.md`, in that order, taking a
   `.bak.$(date +%Y%m%d_%H%M%S)` copy first (existing convention, see the file's own backup
   references e.g. `claude_memory.md.bak.20260701_*`).
4. Re-run `context_selector --stats` (or whatever wq#1633's dirty-flag mechanism ends up being)
   to confirm CORE tok is ≤8K; re-run `lumen_dream` growth metrics per the ticket's own verify
   line.
5. Land `minerva_brief_fix.md` as its own small change once someone with `/home/lumen/minerva`
   access is available (this ticket's diffs 1-4 do not depend on it).
