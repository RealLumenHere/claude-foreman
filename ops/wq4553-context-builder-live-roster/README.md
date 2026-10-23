# wq#4553 — wq#4539 phase 1: one render — context_builder.py roster+episodes from minerva.db

Source: `design/wq4539_one_render_one_truth.md` phase 1. Depends on wq#4537 (hand-prose
region, staged in `ops/wq4537-claude-memory-hand-prose/`) and phase 0/QW5 (landed
2026-10-22, fleet_decisions #1→#86, #14→#87 `status=superseded`).

## What this bundle ships

`context_builder.py` — wq#4537's builder (hand-prose preservation unchanged) rewired
section by section per the design table:

| Section | Before | After |
|---|---|---|
| FLEET DEVICES (roster) | PG `fleet_devices` (frozen since 2026-10-08, audit F4) | live minerva.db state facts via `lumen_state.py current` |
| RECENT EPISODES | PG `agent_episodes` (0 writes since 2026-10-08) | live minerva.db `archival_notes` |
| PERMANENT DECISIONS / STANDING RULES / FRANK CORRECTIONS | PG `fleet_decisions`, `status='active'` | unchanged — phase 0 already set `status='superseded'` on #1/#14, so the existing filter already excludes them (verified live, see below) |
| hand prose | wq#4537 region | unchanged |

## Bug found while reusing `fleet_context.py`'s parser

The design doc says to reuse `prompt-forge/fleet_context.py:41-44,88-104`'s
subject/predicate parser. Its `_STATE_LINE` regex anchors `\(since DATE\)\s*$` — nothing
may follow the date. Live `lumen_state.py current` output always appends a confidence tag
after the date (e.g. `(since 2026-10-22)  [UNVERIFIABLE — no probe]`, or
`[verified 17d ago]`). Verified against the live fleet 2026-10-23: 0 of 1244 current-state
lines end bare at the date, so **`fleet_context.py`'s roster() currently returns an empty
list on live data** (checked directly, not inferred: `python3 -c` with that exact regex
against a live-captured line returns no match). This bundle's parser drops the trailing
anchor instead of copying the broken one. `fleet_context.py` itself is out of this ticket's
scope (prompt-forge, not context_builder.py) — logged for a follow-up, not fixed here.

## Retired-node filter (probe (b))

The design assumes "retired/renamed nodes simply have no current `.role` fact." True for
most of audit F5's six PG-stale nodes, **false for the four retired Macs** (khepri, hathor,
isis, nephthys): minerva.db keeps history, so each still has a *current* `.role` fact — its
text just now reads `"retired — Mac Pro 6,1 parts inventory only..."`, and each also has a
current `.status` fact reading `"RETIRED — 2026-10-22 (offline, parts only)"`. PG
`fleet_devices` for the same hosts still shows stale pre-retirement status (`available`,
`offline`, etc. — checked live via `fm_db`). A roster that renders "has a role fact" would
still render these four. `roster()` additionally excludes any node whose role or status
value starts with "retired"/"decommissioned" (case-insensitive). The other two audit-F5
nodes (sekhmet, ptah) are not retired — just PG-stale in other fields — and correctly stay
in the roster.

## Evidence (2026-10-23, live fleet — not just fixtures)

- `python3 -m pytest -q test_live_roster.py` → 9 passed (7 at wq#4553 + 2 from wq#4578; uses a
  pinned, immutable snapshot fixture of real `lumen_state.py current` output,
  `fixtures/state_sample_2026-10-23.txt`, plus a synthetic `archival_notes`/`note_merges` db
  for the episodes filter).
- Live replay of the ticket's actual probe, run from this worktree against the real
  `lumen_state.py` and `minerva.db` (not a copy): `roster()` called twice in one process
  returns the identical 30-host list both times (probe a — byte-stable); khepri, nephthys,
  hathor, isis (all four audit-F5/wq#4541 retired Macs, present in PG `fleet_devices`) are
  absent from both calls, while thor/sekhmet/ptah/anubis (not retired) are present (probe
  b). `episodes()` returns 40 real rows from `archival_notes`.
- Hand-prose round trip manually replayed against this file (two `--out` regens on a temp
  file with a hand-written prose region): the region survives byte-identical both times —
  same contract as wq#4537's own test suite, unbroken by this rewiring.

## Applied (wq#4570)

Originally staged because this BUILD lane's sandbox has no `--add-dir` into
`/home/lumen/minerva` or `/home/lumen/backups` (`ls`/`pg_dump` against those paths are
blocked outright, not just guard-denied). That did NOT require root/ssh, only a process
outside the CLI sandbox: `/home/lumen/bin/lumen-run` (already allowlisted) runs the
command as a detached systemd-run unit with the lumen account's full normal filesystem
view (ISSUE-LOG row 1449 precedent). `install.sh --verify`'s own run under `lumen-run`
surfaced a second, real bug independent of sandboxing: a bare `pg_dump -Fc fleet_memory`
defaults to the local unix socket on port 5432, which doesn't exist here — fleet_memory
only listens on `recipe_store.py`'s `PG_HOST`/`PG_PORT` (127.0.0.1:5433). Fixed by calling
`recipe_store.backup()` instead of a raw `pg_dump` invocation (wq#4570). Applied via:

```
/home/lumen/bin/lumen-run start --name wq4570-apply -- bash /home/lumen/foreman/ops/wq4553-context-builder-live-roster/install.sh
```

`install.sh --verify` now reports `VERIFY OK`.

## Re-applied (wq#4578)

wq#4572's verify-pass contradicted wq#4553 on one point and wq#4578 ruled on it: with the
end anchor gone, `_STATE_LINE`'s value group had to be greedy, because a non-greedy `.*?`
stops at the FIRST `(since DATE)`-shaped substring on the line instead of the trailing one
`lumen_state.py` appends — a value whose free text contained that idiom parsed truncated with
a stale date and no error. Now `(?P<value>.*\S|)` (context_builder.py:88-89), plus two
regression tests and `fixtures/state_embedded_since_wq4578.txt`. Live scan at ruling time:
0 of 1244 current state lines carry an embedded since-clause, so the trap was latent and
nothing rendered was corrupted. Full reasoning and the falsification run:
`reports/wq4578_judgment_state_line_since_collision.md`. Re-applied the same way as wq#4570:

```
/home/lumen/bin/lumen-run start --name wq4578-apply -- bash /home/lumen/foreman/ops/wq4553-context-builder-live-roster/install.sh
```

## Not done here

- `fleet_context.py`'s own broken regex (prompt-forge, not this ticket's file) — needs its
  own ticket. It carries BOTH bugs: the end anchor and the non-greedy value group.
- Phase 2 (migrate decisions into minerva.db, freeze PG memory tables) and phase 3 (retire
  memory-mcp.service) are separate tickets per the design doc's ordering.
