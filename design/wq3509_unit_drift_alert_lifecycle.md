# wq#3509 — unit-drift alert lifecycle (design)

## Problem (class, not instance)

`unit-drift check --digest` runs every 15 min and ntfys on any drift. The alert
had no lifecycle: rows never resolved, and the same unchanged drift re-notified
every cycle. Observed 2026-10-07: 40 open `ntfy:lumen-alerts` aq rows back to
2026-09-02, repeat counters to x433, every one hand-resolved
(`lumen-job-wq3288-aq-resolve`). Second occurrence in 6 days (wq#3288 on 10-01,
again 10-07). The ticket names three class fixes plus one adjacent crash.

## State at dispatch

wq#3466 (landed in `bin` before this ticket ran) already fixed two of the three:

- **One row, not one per count**: the digest publishes under the fixed title
  `unit-drift: drift on lumen` (`bin/unit-drift` `DIGEST_TITLE`), so aq's
  title-keyed ingest dedups to a single row instead of one row per distinct
  change count.
- **(1) auto-resolve on CLEAN**: `resolve_digest_rows()` runs whenever
  `check --digest` returns CLEAN and resolves every open
  `ntfy:lumen-alerts` row whose key starts with `unit-drift:` (also sweeps the
  old count-keyed rows). Tests: `bin/tests/test_wq3466_unit_drift_aq_key.py`.

What wq#3466 did **not** fix: the ntfy itself still fires every 15 minutes
while drift persists. One row, but its repeat counter climbs x4/hour and the
phone buzzes with it. That is remaining fix (2).

## (2) Content-hash dedup of the digest — implemented this ticket

`cmd_check(digest=True)` now hashes the full change set (every drift line,
dangling-target and guard-lint finding, sorted order as emitted) with sha256
and compares against the last-notified hash in a state file
(`~/.cache/unit-drift-digest.state`, env-overridable for tests):

- **hash unchanged** → suppress the ntfy, print an advisory line instead.
  A stable drift notifies exactly once: one row, x1.
- **hash changed** (new drift, or the set grew/shrank/moved) → send the ntfy
  (hash prefix included in the body so distinct drift states are tellable
  apart in aq), then record the hash — but only if `ntfy-send` exited 0, so a
  failed send retries next cycle instead of going silent.
- **CLEAN** → `resolve_digest_rows()` as before, and the state file is removed
  so the next drift episode always notifies.

Escalation of a long-lived open row stays aq's job (`aq_escalate`); the digest's
job is one notification per distinct drift state, not a 15-minute metronome.

Change: `bin/unit-drift` (`DIGEST_STATE`, `_read_digest_state` /
`_write_digest_state` / `_clear_digest_state`, `cmd_check` digest branch).
Tests: `bin/tests/test_wq3509_unit_drift_digest_dedup.py`.
Landed via nested `_probe/` bin worktree + `lumen-pr` (cross-repo pattern of
wq#2569/wq#2573; see `wq3509_cross_repo_evidence.md`).

## (3) Should ops install.sh bundles snapshot the baseline on deploy?

**Decision: yes, but scoped — never a blanket `unit-drift snapshot`.**

A blanket snapshot as a deploy step would commit *all* current drift, including
anything unreviewed that happened to be sitting on the box, defeating the
reconciler. The deploy step must only be able to bless what the bundle itself
ships:

- Each ops bundle that installs/edits/removes systemd units declares them in a
  manifest line (e.g. `UNITS="foo.service foo.timer"` at the top of
  `install.sh`, or a `units.txt` next to it). The declaration is reviewed with
  the bundle's PR — review stays in the loop.
- A new scoped verb `unit-drift accept <unit>...` updates the baseline
  (units/, dropins/, enable-state, MANIFEST) **only** for the named units and
  git-commits with the unit list and ticket id in the message. Anything the
  bundle didn't declare still drifts and still alerts.
- `install.sh` calls `unit-drift accept $UNITS` as its last step. A legit
  deploy therefore never opens a drift alert at all; fix (2) covers everything
  else (hand edits, host changes, partial deploys).

The `accept` verb is deliberately **not** implemented in this lane — it touches
the snapshot/MANIFEST write path and deserves its own tests and its own review.
Filed as **wq#3579**, with this doc as its design.

## Adjacent: `aq resolve --help` crashes

`aq` (symlink `bin/aq` → `/home/lumen/agents/alert-queue/aq.py`) does
`int(argv)` on the row-id positional, so `aq resolve --help` tracebacks with
`ValueError: invalid literal for int()` instead of printing usage. The
alert-queue repo is outside this lane's sandbox (not in `--add-dir`), so this
is filed as **wq#3578** rather than fixed here: guard the id parse (print
usage and exit 2 on a non-integer) and give the subcommand a real `--help`
path.

## Acceptance mapping

- (1) auto-resolve: pre-existing via wq#3466 — verified, not re-implemented.
- (2) hash dedup: lumen-bin PR #233
  (http://100.121.17.5:3000/lumen/lumen-bin/pulls/233; see
  `wq3509_cross_repo_evidence.md`).
- (3) decision recorded above; `accept` verb + bundle convention = wq#3579.
- `aq resolve --help`: wq#3578 (repo outside sandbox).
