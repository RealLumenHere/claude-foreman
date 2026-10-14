# wq#3887: adversarial verify and judgment on wq#3876 (HALucinator HAL-emulation eval)

- **Ticket verified:** wq#3876, closed `done` citing PR #1857 (`5cde9978`),
  `research/HALUCINATOR_EVAL.md` (154 lines), on branch
  `feat/wq3876-limpet-evaluate-halucinator-for-high-lev-a1`
- **Verifier:** claude-sonnet-5@high (fresh instance, 2026-10-14)
- **Escalation:** opus-5@high (contradiction found -> judgment trigger)
- **Verdict: CONTRADICTION CONFIRMED (narrow, delivery-only).** Every claim checked in
  the research holds. The `done` claim does not, because the deliverable never reached
  `main`. wq#3876 stays closed. Follow-up landing ticket: **wq#3893**.

## Scope

wq#3876 was a research/eval ticket with no code. The document is the probe. The verify
pass checked every externally checkable claim against live sources (GitHub API,
raw.githubusercontent.com, PyPI) and every repo-internal claim against the file or
branch it cites. It also checked the shipped-work claim against a freshly fetched
`origin/main`.

## What's right (independently re-verified, not trusted from the doc)

No factual errors in any claim checked:

- **`halucinator/halucinator` metadata:** `pushed_at` 2026-08-19T12:15:34Z, 61 stars,
  PyPI `halucinator` 1.9.0. All match the doc exactly.
- **Handler coverage:** the `src/halucinator/bp_handlers/` listing matches the doc
  (`__init__.py` omitted, immaterial). There are no nordic/nrf or esp/espressif/esp_idf
  directories, which confirms "no ESP-IDF handler" and "Nordic only indirect via
  zephyr". `zephyr/` holds `zephyr_fs.py` and `zephyr_uart.py`, as cited.
- **Lineage:** the `embedded-sec/halucinator` README says verbatim "For a more recent
  version of HALucinator see https://github.com/halucinator", which matches the doc's
  "superseded". `halucinator/GHALdra` and `halucinator/PMatch` were last pushed
  2021-10-11 and have 4 and 6 stars respectively, as stated.
- **Architectures:** ARM Cortex-M, ARM (arm926), AARCH64, MIPS, PowerPC, PowerPC64.
  Matches the README's "Supported Architectures" exactly.
- **Handler API:** `bp_handler.py` defines `class BPHandler` and the `bp_handler`
  decorator as described.
- **MCP server (ticket section 4):** `src/halucinator/mcp/` has `server.py`,
  `manager.py`, `_worker.py` and `session.py`. `server.py` carries `# Copyright 2026
  Christopher Wright`. `mcp/README.md` confirms FastMCP, `pip install -e '.[mcp]'`, one
  worker subprocess per session, the full tool list, and the crash-containment framing,
  word for word.
- **STM32 demo:** `test/STM32/example` exists with the `Uart_Hyperterminal_IT_*`
  files. The doc's hedge on the `CubeMX2Makefile.py` path ("from the archived repo's
  README, carried forward in spirit") is honest: `src/tools/` returns 404 in the live
  fork.
- **Catalog references:** the `limpet/tool-catalog.json` text ("repo/license not
  independently re-verified", `"category": "emulation"`) and `limpet/examples/
  halucinator.toml` (`version = "0"`) match their source. That source is the unmerged
  `feat/wq3850-...` branch (see Remediation).
- **Other citations:** the USENIX paper URL returns HTTP 200 with `application/pdf`,
  and `TOOL_REGISTRY.md` has the cited section "REA".

The research verdict (pilot HALucinator from `halucinator/halucinator` rather than
`embedded-sec`, avoid GHALdra and PMatch, flag the MCP server) is sound and fully
backed by evidence.

Minor, not a deliverable defect: wq#3876's close-out *notes* call `embedded-sec/
halucinator` "archived". The GitHub API reports `"archived": false`. The document
itself only says "superseded", which is accurate. No change needed.

## Contradiction found

The document is not on `main`. Evidence, all from git, none of it depending on API
access:

1. After a fresh `git fetch origin main`, `git show
   origin/main:research/HALUCINATOR_EVAL.md` returns `fatal: path
   'research/HALUCINATOR_EVAL.md' does not exist in 'origin/main'`.
2. `5cde9978` is reachable only from the `feat/wq3876-...` branch (local and origin),
   never from `origin/main`.
3. wq#3876 is the only ticket in its batch (same day, same lane, dispatched together)
   whose notes do not confirm a merge:

   | Ticket | Notes say | On main |
   |---|---|---|
   | wq#3875 (Fuzzware) | "PR .../pulls/1858 merged (e2f1825620)" | yes (`e2f18256`, `b760fd28`) |
   | wq#3877 (Pretender) | "PR #1855 landed as b44c3c92ce on main" | yes (`4726e0f3`) |
   | wq#3879 (FwAnalyzer) | ".../pulls/1856 (merged d5480b6e)" | yes (`d5480b6e`) |
   | **wq#3876 (HALucinator)** | "PR #1857 (5cde9978)" | **no** |

4. The Gitea PR API was not queried. The repo needs auth, and anonymous calls return
   "not found" even for the merged PR #1858. Whether PR #1857 is open or closed
   unmerged is unknown. The verdict doesn't depend on it.

The notes are not literally false: they cite a PR and never say "merged". The false
claim is the `done` status. The likely sequence is that the PR was opened, the ticket
was closed, and the merge never happened. Nothing in wq#3876's configuration would
have stopped that (see Root cause).

## Severity

**Why this is a contradiction:** under "done means demonstrated", the deliverable has
to exist where readers look. Anyone on `main` reading `research/`, grepping for
HALucinator, or following the registry finds nothing. In practice the evaluation does
not exist yet, and the branch drifts further from `main` the longer it sits.

**Why not milder:** an open PR is not delivery. The three sibling tickets show the
working norm is merge, then close.

**Why not bigger:** the content has no defect, so there is no reason to redo the
research. The branch forked at `d54d636c`, is 5 commits behind `main`, and touches no
paths the intervening commits touch, so landing it is a clean rebase.

Compared with wq#3886 (a sibling verify-pass): there, one false sentence sat inside a
landed doc. Here the whole doc is accurate but unlanded. Both fixes are small and
mechanical, and both leave the core verdict intact. Neither justifies reopening the
original ticket.

## Root cause

wq#3876 was filed with `deliverable: None`. The `--deliverable PATH` field ("the file
this ticket must produce -- `done` refuses unless it is on main") would have blocked
this close. The three siblings landed because their closers merged before closing, not
because anything enforced it.

## Remediation (actioned)

1. **wq#3887** (this ticket): closed `done`, notes "CONTRADICTION CONFIRMED (narrow,
   delivery-only)", pointer to this report.
2. **wq#3876:** not reopened. Core research verdict independently re-verified and
   holds.
3. **wq#3893** (follow-up BUILD ticket, filed): land the existing branch
   `feat/wq3876-limpet-evaluate-halucinator-for-high-lev-a1` (commit `5cde9978`) to
   main.
   - Filed with `--deliverable research/HALUCINATOR_EVAL.md`, so its own `done` is
     gated on the file actually being on `main`.
   - Instructed to land through PR #1857 if still open (rebasing onto current main),
     or a fresh PR from the same branch if closed. The SHA on `main` will differ from
     `5cde9978` after the rebase; the gate checks the file on `origin/main`, not the
     SHA.
   - Instructed to confirm `kb/ISSUE-LOG.md` row 1308 also lands -- checked directly:
     **row 1308 is absent from `origin/main` as of this report** (confirmed via `git
     show origin/main:kb/ISSUE-LOG.md | grep '^| 1308 '`, zero hits), same as the
     research doc, and present on the unlanded branch. Same landing gap, same fix.
   - Instructed to check whether `feat/wq3850-limpet-add-11-new-re-tools-to-catalog-a1`
     has merged first -- checked directly: **it has not** (`git merge-base
     --is-ancestor origin/feat/wq3850-...-a1 origin/main` returns not-an-ancestor).
     The doc quotes `limpet/tool-catalog.json` and `limpet/examples/halucinator.toml`
     from that branch. wq#3893 needs to either land after wq#3850, or caveat those two
     citations as describing pending branch state rather than current `main` state.

## Cross-finding for wq#3886 / wq#3889 (checked directly, flagged, not actioned beyond a note)

wq#3886 (a sibling verify-pass, unrelated to wq#3876) judged wq#3877's claim about
`limpet/examples/pretender.toml` ("community tier, version "0", unpromoted")
fabricated, based on: the file being absent from the `main` working tree, and
wq#3850's own notes saying no per-tool manifest tomls existed for any catalog entry.
wq#3889 (open, not yet dispatched) plans to strip that sentence from TOOL_REGISTRY.md
as invented.

This verify pass independently ran `git ls-tree --name-only
origin/feat/wq3850-limpet-add-11-new-re-tools-to-catalog-a1 limpet/examples/` and
confirmed `limpet/examples/pretender.toml` **does exist** -- on that same unmerged
wq#3850 branch, alongside `halucinator.toml`, `fwanalyzer.toml`, and 11 others. wq#3886
checked only `main`, where wq#3850 hasn't landed, so the file is absent there for the
same reason `research/HALUCINATOR_EVAL.md` is absent from `main`: it's unlanded
content, not invented content. wq#3877's own sentence explicitly cited "wq#3850" as
the source, consistent with describing pending branch state rather than fabricating a
file.

If this holds up, wq#3889's planned fix (deleting the sentence as fabricated) would
remove a statement that becomes true the moment wq#3850 lands, rather than correcting
an actual error. This is out of scope for wq#3887 to resolve -- left a note on wq#3889
(2026-10-14) pointing whoever claims it at this report and at the `git ls-tree`
command above, with the recommendation to re-scope the fix from "strip" to "qualify as
describing wq#3850-branch-pending state" (or land wq#3850 first) rather than deleting a
claim that may be true once wq#3850 merges. wq#3886 and wq#3877 were not reopened by
this ticket; that call belongs to whoever next touches wq#3889.

## Process suggestions (not filed as tickets)

- Eval/research tickets that produce a committed document should default to being
  filed with `--deliverable <path>`, so `wq done` enforces the file's presence on
  `main` the way it would have caught this one.
- Both wq#3876 and wq#3850 landed the same day with the same failure mode (closed
  `done` while the branch sat unmerged). Two instances from one batch suggests this is
  a process gap worth a fleet-wide sweep (done tickets whose cited SHA isn't reachable
  from `origin/main`), not a one-off mistake.
