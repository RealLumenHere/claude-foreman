# wq#2024 — Label defers by TYPE so Frank can skip the ones not worth reviewing

Frank: he keeps opening deferred proposals and finding they were dismissed on
theoretical/untested grounds — wasting his review time. Fix: tag every defer with a
TYPE label, shown wherever he reviews, filtered to only `defer-needs-decision` by
default.

## What this directory adds (real, tested here)

- `defer_type_labels.py` — `classify_defer_type(reason) -> label|None`. Extends the
  PILOT/RESEARCH/RETIRE classifier concept named in the ticket
  (`minerva_defer_review.py`'s docstring, per `research/DEFER_PILOT_LANE.md`'s
  `PILOT_SHAPED`/`INFO_GAP`/`SETTLED` regex style) with the 7 labels Frank specified,
  regex-matched against `decision_reason`, in a deliberate priority order (comment in
  the module explains why `defer-theoretical` — the one he explicitly asked for — is
  checked last, so it never swallows a more actionable category).
- `backfill_defer_labels.py` — additive `proposals.defer_type_label TEXT` column (same
  convention as wq#1500's `proposals.wq_ticket`), labels every existing `status='defer'`
  row, leaves everything else untouched, idempotent, `--dry-run`, reports unmatched rows
  instead of guessing, and flags `defer-duplicate-suspect` rows for a human check (the
  ticket calls this label out by name: "VERIFY — the duplicate-route false-positive bug
  from wq#2019 lives here; do not trust blindly").
- `test_defer_type_labels.py` — 38 checks: one real-shaped example per label straight
  from the ticket's own reason-cluster phrasing, unmatched-text handling, two
  cross-category priority cases, and an end-to-end backfill against a synthetic sqlite
  fixture matching the real `proposals` schema (columns per
  `ops/wq1500-minerva-promoter/test_minerva_promote.py` and
  `ops/wq1396-minerva-proposal-promoter/test_minerva_proposal_promoter.py`'s fixtures).
  `python3 ops/wq2024-label-defer-types/test_defer_type_labels.py` → 38/38 pass.

## What this directory does NOT do, and why

This ticket's own text says "land via lumen-pr on the minerva/mission-control repos."
This BUILD worker's sandbox cannot reach either:

- `/home/lumen/minerva` (the live minerva repo, `minerva_defer_review.py`, `minerva.db`)
  is not in this session's `--add-dir` set (`harness/agents.yaml` `defaults.add_dirs`:
  `agents/work-queue`, `mailbot`, `pokemon`, `bin`, `foreman`, `kb`, `projects/nest` —
  no minerva). Nor is the worker-readable mirror,
  `/home/lumen/research/minerva-ref` — despite it being listed in
  `harness/fleet-exec-policy.yaml`'s `allow.read_dirs` (line ~122) and used by exactly
  this class of prior ticket (wq#1500's README: "worker-readable copy at
  `/home/lumen/research/minerva-ref/`, the sweep-gated hourly mirror") — because
  `harness/capability_lint.py`'s `_derive_dirs()` only resolves dir tags from **explicit
  `/home/lumen/...` paths cited in the ticket text**; this ticket names the target by
  filename (`minerva_defer_review.py docstring`) and repo name ("the minerva/
  mission-control repos"), never the literal mirror path, so no dir tag was derived and
  no extra `--add-dir` was granted at dispatch. Logged as `kb/ISSUE-LOG.md` #— (see
  below) with the concrete fix: capability_lint should also resolve a bare mention of
  "minerva" / "the minerva repo" / "minerva_defer_review.py" to
  `/home/lumen/research/minerva-ref`, the way `router.TITLE_REPO_MARKERS` already
  resolves bracketed repo tags like `[mission-control]` for lane dispatch.
- The mission-control repo (`lumen-mission-control.service`, wq orchestration UI,
  `mctl.lumennode.uk`, port 8136) has no worker-readable mirror anywhere in this
  session's reach at all — no ops/ precedent exists for it (checked: no
  `ops/*mission*` directory in this repo), unlike minerva which has three prior
  vendor-and-fast-forward tickets (wq#1997/#2005/#2010's `minerva_verify.py`,
  wq#2019's `minerva_dedup.py`/`minerva_proposal.py`, wq#1500's `minerva_promote.py`).
- `minerva.db` itself is equally unreachable, so the backfill above is written and
  tested against a synthetic fixture, not run against the real 316-row defer pile the
  ticket references. It is ready to run once pointed at a reachable copy.

This is the same sandbox-reach class documented in `kb/ISSUE-LOG.md` #240 (wq#1503),
#300 (wq#1729), and the wq#1559 pilot-lane build (that ticket's own foreman commit,
`c55e98c`, only touched `dispatcher/config.json` / `harness/fleet-exec-policy.yaml` /
`orchestrator/router.py` / templates — never `minerva_defer_review.py` itself, for the
same reason). The convention those tickets established, and this one follows: land the
reachable, real, tested piece via `lumen-pr` on **this** repo (foreman), document the
exact integration points precisely enough that a worker or ticket with minerva/
mission-control reach can wire it in without re-deriving the design.

## Exact integration points (for the follow-up ticket with minerva/mission-control reach)

1. **`minerva_defer_review.py`** — in `cmd_review`, right after a proposal's
   `decision_reason` is set and `status` becomes `'defer'` (before the `PILOT_SHAPED`
   check per `research/DEFER_PILOT_LANE.md` §3.1's ordering), call
   `classify_defer_type(decision_reason)` and store the result in the new
   `defer_type_label` column (`ALTER TABLE proposals ADD COLUMN defer_type_label TEXT`,
   already written in `backfill_defer_labels.py`, additive, no migration of existing
   columns). Re-review passes (`cmd_reap`'s `rerun`/retire paths) should recompute the
   label the same way whenever `decision_reason` changes.
2. **Backfill** — run `backfill_defer_labels.py --db /home/lumen/minerva/minerva.db`
   once from a host that can reach it (or via `install.sh`-style replay matching
   wq#2019's convention). `--dry-run` first; check the `defer-duplicate-suspect` note
   before trusting those rows.
3. **mission-control proposals view** — render `defer_type_label` as a badge/column in
   the proposals list; default filter `WHERE status='defer' AND defer_type_label =
   'defer-needs-decision'` (Frank's queue), with a control to show all 7 labels or
   filter to any one of them. `defer-dep-blocked` rows should auto-surface once their
   named blocker's status flips (the ticket's own wording — this needs the blocker
   reference parsed out of `decision_reason`, e.g. a `wq#\d+` capture, which is a
   follow-up beyond regex labeling and is flagged here, not silently absorbed).
4. **wq** — proposals that already promote to wq tickets (wq#1500's `minerva_promote.py`
   grounding block, or wq#1559's pilot-lane ticket) should include the label in the
   ticket detail (e.g. a `Defer-Type: <label>` line in the grounding block), so it is
   visible and greppable from `wq show`/`wq list` without any change to `wq` itself —
   `bin/wq` has no proposal-specific fields and none are needed; the label is just
   another line in the existing free-text detail convention.

## Rollback

Nothing here touches a live system: `defer_type_labels.py`/`backfill_defer_labels.py`
are net-new files, tested only against a scratch sqlite fixture created and destroyed
in `tempfile.mkdtemp()`. Reverting this PR removes the vendored files and nothing else.
