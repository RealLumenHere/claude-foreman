# Verify wq#3775 — wq#3771 MC: approval panel for frank_gated research proposals

Target: http://100.121.17.5:3000/lumen/mission-control/pulls/19, commit `009f18a`
("feat(mc): research-proposal approval panel") on top of `89df2ac` in
`/home/lumen/mission-control`. 6 files, +367/-6: `app.py`, `tests/test_research_panel.py`,
`ui/src/components/layout/AppShell.tsx`, `ui/src/components/review/ResearchApprovals.tsx`,
`ui/src/components/workspace/WorkspaceView.tsx`, `ui/src/lib/api.ts`. Author: claude-opus-5-5.
Verifier: claude-sonnet-5@high, fresh instance, archetype 8.

Contract under test: `foreman/research/MC_APPROVAL_PANEL_CONTRACT.md` (wq#3763, itself
verified CONFIRMED at `reports/wq3772_verify_wq3763_mc_approval_panel_contract.md`).

## Method

1. Read `app.py`'s diff line-by-line against contract §1-3 (query, rendering, write paths).
2. Read the full new component (`ResearchApprovals.tsx`), the wiring (`WorkspaceView.tsx`,
   `AppShell.tsx`), and the API client additions (`api.ts`).
3. Cross-checked every file/line claim the diff's own comments make against
   `foreman/research/bridge.py` and `foreman/research/ledger.py` at HEAD.
4. Ran the shipped probe (`tests/test_research_panel.py`, backs up live `queue.db` read-only
   to a scratch copy, runs real `wq`/`bridge.py` subprocesses against it) — 5/5 pass.
5. Wrote and ran an additional adversarial probe outside the shipped test's input population
   (non-existent ticket ids, path-traversal/uppercase/overlong reject classes) against the
   same scratch-DB harness — all five cases correctly 400, ticket row provably untouched.
6. `npm ci && npx tsc -b` (clean, no errors) and `npm run build` (clean production build) on
   `ui/`; `npm run lint` — no new warnings on any file this diff touches (the one hit,
   `AppShell.tsx:31`, is the pre-existing hash-deeplink effect, not this diff's code).

## Checks performed, all confirmed

- **`/api/research` query** (`app.py` new route) is byte-for-byte the contract §1 SQL:
  `gate_type='frank_decision' AND status='pending_review' AND title LIKE 'research: %'`.
  The retry-row query (`frank_gated`, `decision_prompt IS NULL`, same title filter) matches
  contract's "awaiting retry" carve-out exactly.
- **`RESEARCH_PREFIX = "research: "`** and **`REJECT_CLASS_RE`** mirror
  `research/bridge.py:49` `TITLE_PREFIX` and `research/ledger.py:19` `CLASS_RE` exactly
  (confirmed by direct grep, not citation trust).
- **Approve path**: `wq answer <id> <text>` then `wq review <id> accept` — matches contract
  §3. Confirmed end-to-end: `test_2_approve` shows `status` moves `pending_review` → `open`
  and `frank_response` is recorded.
- **Reject path**: `wq answer` then `python3 bridge.py reject <id> --class <slug>` — never
  `wq review <id> reject`, per contract §3's explicit warning. Confirmed end-to-end:
  `test_3_reject` shows `status` → `done` and a `rejections` ledger row written with the
  right `(title, reject_class, frank_reason)`.
- **Retry rows render inert**: `ResearchApprovals.tsx` shows retry items as a plain text
  line ("awaiting retry (wq decide failed, nothing to answer yet)") with no approve/reject
  controls — matches contract's "should render as awaiting retry... not an approve/reject
  row." `test_5_retry_row_refused_by_wq` additionally confirms the backend itself would
  refuse an approve attempt on a retry row even if the UI were bypassed (`wq answer` requires
  a non-null `decision_prompt`).
- **Subprocess safety**: all `wq`/`bridge.py` invocations are argv lists via `subprocess.run`
  (no `shell=True`), so `text` (free-form) and `reject_class` (regex-gated before use) can't
  break out into shell injection. `reject_class` is validated by the endpoint *before*
  `wq answer` runs, so a bad class never writes a `frank_response` the UI would have to
  explain away — confirmed by both the shipped `test_4` and my own adversarial probe.
- **`WQ_DB` plumbing**: `app.py`'s `DB` now reads `os.environ.get("WQ_DB", ...)`, and because
  `run()` doesn't pass `env=`, the subprocess inherits the parent's environment — so a test
  harness setting `WQ_DB` before importing `app` gets consistent reads/writes across
  `app.py`, `wq`, and `bridge.py` (the last two independently re-read `WQ_DB` themselves,
  confirmed in `research/ledger.py:16` and `research/bridge.py`'s `_wq()`). This is exactly
  what the shipped test relies on, and it works (ran it, passed).
- **Dedup with the generic review queue**: `WorkspaceView.tsx` filters only
  `research?.items` (the `pending_review`/actionable set) out of the generic decisions
  bucket, not `research?.retry`. A retry-class research ticket therefore still surfaces in
  the generic Review Queue's "decisions" section with a generic "Answer" button, in addition
  to its muted line in the Research panel. Traced this through: the generic button only
  opens the X-Ray drawer (`onOpen`, no direct API call), and the existing `DecisionTab.tsx`
  (pre-existing code, not touched by this diff) already renders an inert "awaiting the
  orchestrator's decision prompt — nothing to answer yet" box for exactly this state
  (`awaitingPrompt = status==='frank_gated' && !decision_prompt`) with no submit form
  (`canAnswer` false). So the duplicate listing is a cosmetic double-surface, not a
  functional bug — nothing is clickable that does the wrong thing. Noting it since it's a
  real (if minor) gap against a clean implementation, not escalating it.
- **Frontend**: `tsc -b` clean, production `vite build` clean, `oxlint` shows zero new
  warnings on any file this diff touches.

## Minor nit (non-blocking)

`AppShell.tsx`: `const { data: fleet }= usePoll(fetchFleet, FLEET_POLL_MS)` is missing a
space before `=` (cosmetic, this diff's line 40). Doesn't affect behavior or pass lint.

## Verdict

**CONFIRMED.** Every data-contract claim (query, rendering fields, both write paths, the
retry carve-out) matches `MC_APPROVAL_PANEL_CONTRACT.md` exactly and is independently
runnable against live code, not just citation-checked. The shipped test suite is a real
acceptance probe — it exercises real `wq`/`bridge.py` subprocesses against a scratch copy of
live `queue.db` and includes refusal cases that can and do fail on bad input (`test_4`,
`test_5`). My own adversarial probe (nonexistent ids, malformed reject classes) found nothing
the implementation didn't already handle. One cosmetic UX gap (retry rows double-listed) and
one cosmetic formatting nit, neither functional. No contradiction; no escalation to
opus-5@high needed.
