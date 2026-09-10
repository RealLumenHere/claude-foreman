# wq#1657 — Guard exit-code contract: finding vs crash

Source: `research/FLEET_SERVICE_ATLAS.md` §5 #1 (wq#1627). Class defect behind 4 of 8
failed units on 2026-09-10: guards that exit 1 *to signal a finding* are
indistinguishable from guards that crashed, in `systemctl --failed`, `handoff verify`,
and the `lumen-failed-units` sweep (37 open `failed-unit` aq rows). Red becomes
wallpaper; a real failure hides among findings.

## The contract (one contract, two compliance modes, one tag)

**Mode A — exit 0 always (preferred; the `lumen-load-watch` template).**
A guard that runs to completion exits 0 whether or not it found anything. Findings are
delivered via `ntfy-send` (priority per severity) **and** `aq add` (deduped on
source+key). Under Mode A, *any* nonzero exit means the guard itself crashed — the unit
failure is always real and always urgent. New guards MUST use Mode A.

**Mode B — exit 1 = finding, declared.** Where exit 1 is load-bearing (OnFailure-driven
alerting like `lumen-retirement-audit`, or CI-style consumers), the unit file MUST
declare **both**:

```ini
[Service]
X-Lumen-Guard=finding-exit
OnFailure=ntfy-onfailure@%n.service
```

- `X-Lumen-Guard=finding-exit` is the machine-readable marker (systemd ignores `X-*`
  keys; visible via `systemctl cat`). It may live in the unit file or a drop-in.
- `OnFailure=` is mandatory with the tag: once `lumen-failed-units` downgrades tagged
  failures to info, OnFailure is what keeps the finding loud.
- Exit-code semantics under Mode B: **1 = finding; anything else nonzero, or any
  signal/timeout/start-limit, = crash.**

**Undeclared exit-1 is a contract violation** — that is the current state of the 10
guards below, and what the lint exists to burn down.

## Classifier — `lumen-failed-units` (staged/lumen-failed-units)

For each NEWLY failed unit `u`, `is_finding_exit u` returns true when the unit is
tagged `X-Lumen-Guard=finding-exit` (checked via `systemctl cat`) **or** listed in the
bridge allowlist `/home/lumen/bin/guard-finding-exit.list` (one unit name per line;
exists so triage is fixed *before* the 10 unit files are edited).

- finding-exit **and** `Result=exit-code` **and** `ExecMainStatus=1`
  → **"Guard fired: u"** — ntfy priority `low`, aq source `guard-fired`.
- everything else (untagged unit, tagged unit with exit ≠ 1, signal, timeout,
  start-limit) → **"Failed unit: u"** — ntfy `high`, aq source `failed-unit`,
  exactly as today.

Edge-triggering, CLEARED notices, fail-open behavior unchanged.

## Lint — `unit-drift check` (staged/unit-drift + staged/guard_exit_lint.py)

`guard_exit_lint.py` deploys next to `unit-drift` in `/home/lumen/bin`; `unit-drift
check` imports it fail-open (missing module = no lint) and appends a `guard-lint`
section. Violations count as drift → exit 1 → the existing `--digest` ntfy path.

- **GEL1** — unit declares `X-Lumen-Guard=finding-exit` but no `OnFailure=`:
  a finding would be silent everywhere except `systemctl --failed`.
- **GEL2** — unit is a known finding-exit guard (seed list below) but is not tagged:
  the fired/crashed conflation persists for it.

Drop-ins are honored: tag or OnFailure in `<unit>.d/*.conf` satisfies both rules.

## Migration table — the 10 undeclared exit-1 guards (atlas §3)

| unit | today | target |
|---|---|---|
| `lumen-pat-scan.service` | exit 1 + OnFailure | Mode B: add tag (OnFailure already present) |
| `lumen-dispatch-lint.service` | exit 1 + OnFailure | Mode B: add tag |
| `lumen-retirement-audit.service` | exit 1 + OnFailure (chosen design) | Mode B: add tag |
| `wq-drift-guard.service` | exit 1 + own ntfy urgent | Mode A (already ntfys; drop the exit 1) |
| `primary-tree-guard.service` | exit 1 + own ntfy | Mode A |
| `unit-drift.service` | exit 1 silent (timer) | Mode B short-term (tag + OnFailure); CLI keeps exit 1 |
| `lumen-budget-alert.service` | exit 1 | Mode B short-term; Mode A preferred |
| `lumen-repo-sweep.service` | exit 1, files wq tickets | Mode A (tickets are the channel) |
| `git-guard-sentinel.service` | exit 1 | Mode B short-term |
| `repo-visibility-sentinel.service` | exit 1 | Mode B short-term |

All 10 are seeded in both the lint (GEL2) and `guard-finding-exit.list`, so triage is
correct from day one and the lint keeps pressure on until each unit is tagged or
migrated to Mode A.

## Deploy order (follow-up ticket; not done in this docs lane)

1. Copy `staged/guard-finding-exit.list` → `/home/lumen/bin/` (fixes failed-units
   triage immediately, no unit edits needed).
2. Deploy `staged/lumen-failed-units` → `/home/lumen/bin/lumen-failed-units`
   (via the bin repo, commit on main — never a dirty working-tree edit;
   primary-tree-guard fires on dirty).
3. Tag the 10 units via drop-ins (`/etc/systemd/system/<u>.d/50-guard-tag.conf`),
   `daemon-reload`, then `unit-drift snapshot` to re-baseline.
4. Deploy `staged/guard_exit_lint.py` + `staged/unit-drift` → `/home/lumen/bin/`.
   Order matters: tagging before lint deploy avoids 10 instant GEL2 digests.
5. Later, per migration table: move Mode-A targets off exit 1 and un-list them.

Verification after step 2: `systemctl start lumen-budget-alert.service` next cycle
should produce an aq row with source `guard-fired` (info), not `failed-unit` (high).

## Files

- `staged/guard_exit_lint.py` — lint module (tested: `test_guard_exit_lint.py`)
- `staged/unit-drift` — `unit-drift` with the lint wired into `cmd_check`
- `staged/lumen-failed-units` — sweep with the finding-exit classifier
- `staged/guard-finding-exit.list` — bridge allowlist (the 10 units)
- `unit-drift.diff`, `lumen-failed-units.diff` — review diffs vs live `/home/lumen/bin`
