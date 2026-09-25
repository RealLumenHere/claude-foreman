# wq#463 — Wadjet-named failback path, Sobek validation

## Scope actually reachable from this sandbox

`bin/wadjet-ha-failback.sh(.new)` live in `/home/lumen/bin`, a **separate git repo**
from this worktree's repo (`/home/lumen/foreman`, confirmed via
`git -C /home/lumen/bin rev-parse --git-common-dir` = `.git` vs.
`git -C <worktree> rev-parse --git-common-dir` = `/home/lumen/foreman/.git`).
`ha.env` itself lives under `/home/lumen/ha`, which is outside every
`--add-dir` granted to this session and is unreadable here (guard: "may only
search ... from the allowed working directories"). Neither path can ever show
up in *this branch's* diff, so no edit made to them can satisfy this ticket's
evidence gate (AC1: "cite file:line ... in a file this branch actually
touches"). This report is the citable deliverable instead; the rename/drill
themselves are handed off as a ready-to-run patch + runbook for an operator
with `/home/lumen/ha` access and a Maat maintenance window.

## Canonical pick: `.sh`, not `.sh.new`

Compared byte-for-byte (`diff -u wadjet-ha-failback.sh wadjet-ha-failback.sh.new`):

- `wadjet-ha-failback.sh` — mtime 2026-08-15, **already** carries the
  Sobek-aware fix: `show_status()` derives the standby host dynamically from
  `WADJET_SSH` (`SB="${WADJET_SSH##*@}"`, `SBN="${WADJET_SSH%%@*}"`) and
  prints "standby target : $SBN@$SB (WADJET_* vars = compat alias, NOT the
  wadjet host)" so a healthy Sobek is never misread as a healthy Wadjet.
- `wadjet-ha-failback.sh.new` — mtime 2026-07-17 (**older**, despite the
  `.new` suffix), predates the 2026-08-10 Sobek retarget (aq#429). Its own
  header note ("REPOINT NOTE (wq#325): WADJET_* vars in ha.env now target
  BES") shows it was frozen at an even earlier retarget (BES), and it
  hardcodes the literal string "Wadjet" in `show_status()` output instead of
  resolving the real host — exactly the staleness this ticket exists to catch.

**Verdict: `wadjet-ha-failback.sh` is canonical. `wadjet-ha-failback.sh.new`
is a stale pre-Sobek fork and should be deleted once the rename below lands**
(deletion left to the operator applying the patch, since it's outside this
sandbox's writable scope).

## Sobek inheritance — read-only check (source, not exec)

Both scripts source `$HA_DIR/ha.env` (`wadjet-ha-failback.sh:14`) before
using `$WADJET_SSH`/`$WADJET_LAN`/`$MAAT_SSH`/`$MAAT_LAN` — i.e. neither
script hardcodes a host; both inherit whatever `ha.env` currently binds
`WADJET_SSH` to. Per the ticket, aq#429 already re-pointed that binding at
Sobek `.178`. That inheritance is structurally correct; it has simply never
been *exercised* end-to-end since the repoint (no clean drill since
pre-Bes-migration, per ticket). No live check of `ha.env`'s current value was
performed here (path not readable from this sandbox) — do not treat this
report as confirmation the current binding is correct, only that the script
*would* pick up whatever it is.

## Rename spec: `WADJET_* -> STANDBY_*`

Ready to apply once an operator has `/home/lumen/ha` write access and a
maintenance window. Grep-confirmed reference sites in the canonical script
(`wadjet-ha-failback.sh`):

- L22 `wadjet_root(){ ssh ... "$WADJET_SSH" ... }`
- L24 `wadjet_psql(){ ssh ... "$WADJET_SSH" ... }`
- L74 `if [ "$who" = wadjet ]; then sshtarget="$WADJET_SSH"; ip="$WADJET_LAN"; ...`
- L107-114 `show_status()` comment + `SB=/SBN=` derivation + the
  `WADJET_SSH` reference inside the printed compat-alias caveat.

Apply as a straight token rename (`WADJET_SSH`->`STANDBY_SSH`,
`WADJET_LAN`->`STANDBY_LAN`) across `ha.env` and both `wadjet-ha-*.sh`
scripts in `/home/lumen/bin`, then delete `.sh.new` (superseded, see above).
Function/file names (`wadjet_root`, `wadjet_psql`, `wadjet-ha-failback.sh`)
were left alone here — only the `ha.env`-sourced variables are in scope per
the ticket title ("rename WADJET_* -> STANDBY_*"); renaming the files
themselves is a separate call an operator can make when scheduling the drill.

## Blocked: live failover+failback drill

The ticket itself marks the drill Frank-gated ("drill fences Maat") and it is
a destructive, shared-production action against the live Maat/Sobek HA pair
— outside the "physical act" carve-out in instinct 3 only in the sense that
it needs a scheduled maintenance window, not a keystroke only Frank can make;
but it is the kind of blast-radius action instinct-set item under "Executing
actions with care" reserves for explicit confirmation, and the ticket already
pre-decided this. Not attempted here. Recorded as the remaining work.
