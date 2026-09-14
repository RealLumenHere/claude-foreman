# wq#1677 — deploy report

## Finding: the fix is already live

Before writing any code, checked live state and the `fleet-units` baseline
history for the nine units named in `wq1654-timer-label-drift/DECISIONS`:

- `systemctl show <unit>.timer -p Description,ActiveState,UnitFileState` for
  all nine returns the exact corrected text from `DECISIONS` (e.g.
  `lumen-familiarity.timer` → `Entity-familiarity ingest every 6h`, no
  `DISABLED`/`Frank-gated` suffix), all `active`/`enabled`.
- `python3 ops/wq1654-timer-label-drift/label_drift_check.py check` no
  longer flags any of the nine — the only remaining hit is
  `power-remediate.timer`, which `wq1654-timer-label-drift/README.md` notes
  is out of scope for this decision set.
- `/home/lumen/fleet-units` (git baseline) already carries the corrected
  `Description=` for all nine, landed in commit `afa213e` ("snapshot
  2026-09-13T04:25:13Z…"). That snapshot's own driver, `wq1774`'s
  `deploy.sh`, only touches `After=`/`Wants=` on five unrelated units — the
  Description= fix was already on live disk before that snapshot ran and
  the snapshot simply captured it. Confirmed against the prior baseline
  commit `90d651b` (2026-08-28), which still has the stale
  `(installed DISABLED; Frank-gated enable)` / `(STAGED — Frank enables;
  wq#NNN)` text for all nine — so the correction happened, on live disk,
  between 08-28 and 09-13, then got baselined as a side effect of an
  unrelated deploy.

So the disk-and-baseline application this ticket asks for is done and
verified live; nothing to push to `/etc/systemd/system` today.

## Deliverable

`deploy_descriptions.py` (this directory) is the missing piece: the actual,
reusable applier — `ingest_decisions.DECISIONS` → live
`Description=` — that should have landed the fix in the first place and
that makes any future drift on these nine (or a re-provision) a one-command
fix instead of hand-editing. It is idempotent by construction
(`rewrite_description`, `deploy_descriptions.py:34-41`): a unit whose
`Description=` already matches `DECISIONS` is left untouched.

Verified against the real current baseline content, not just synthetic
fixtures: copied the nine live `fleet-units/units/*.timer` files into a
scratch dir and ran `deploy_descriptions.py apply --sys-dir <scratch>` —
output `nothing to do — all 9 units already match`, confirming the script
agrees with the live-state finding above. Scratch dir removed after.

`python3 -m pytest ops/wq1677-deploy-wq1654-live` — 4 passed (rewrite logic,
idempotency, and `apply()` over a multi-unit directory using the exact
stale/corrected Description= text pulled from `fleet-units` git history at
`90d651b`/`afa213e`).
