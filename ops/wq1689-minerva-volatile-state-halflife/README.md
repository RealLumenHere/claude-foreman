# wq#1689 — volatile state facts get a half-life at ingest

Source: INVENTORY_RECONCILIATION.md #6/#12 (wq#1666) — `kernel`, `status`,
`availability`, `root_disk`, `uptime`, `ssh_status` are one-moment probe
observations, not permanent truth; `thoth.status` carried a 09-02 outage as
present tense for 8 days after the box came back, 5 `kernel` facts were a
release behind.

## What this closes

- **Half-life at ingest**: `/home/lumen/minerva/lumen_state.py` already
  carried confidence-decay support (`effective_confidence`, wq#1612
  `staleness()`); this ticket wires a volatile-predicate default so callers
  don't have to pass `--half-life` by hand, and lets `state_registry.json`
  override it without a code change:
  - `VOLATILE_HALFLIFE` module default dict — `lumen_state.py:134-139`.
  - `volatile_halflife_days(predicate, reg)` (registry override first, else
    the module default, `None` => never decays) — `lumen_state.py:98-104`.
  - `registry()` loads the registry's own `volatile_halflife` dict —
    `lumen_state.py:91-94`.
  - `_ingest()` resolves it when the caller passes no explicit
    `half_life_days` — `lumen_state.py:514-517`.
  - Effect: `current()`'s existing `eff_confidence`/`staleness` machinery
    (wq#1612) now decays these predicates and renders them DUE/STALE once
    their evidence anchor (`last_confirmed_at`/`valid_from`) ages past the
    fact-class verify interval, instead of staying FRESH forever — see
    `test_volatile_half_life_ages_out_to_due_in_current` in
    `test_lumen_state_wq1689.py`. `drift-assert`/`fleet-inv verify` (wq#1613
    verifier registry / `lumen_verify.py`) already re-reads `current()`'s
    staleness column, so a DUE/STALE volatile fact is picked up there with no
    further change.

- **Supersession hole / ingest lint**: a renamed or reformatted predicate
  (`lan_ip` vs `lan-ip`, `hw` vs `hardware`) never matched the old predicate
  name, so both stayed open forever.
  - `PredicateNearDuplicate` (a `StateGateError` — refuses the write, nothing
    is written) — `lumen_state.py:64-70`.
  - `_edit_distance()` — plain Levenshtein, `lumen_state.py:345-357`.
  - `_near_duplicate_predicate()` — nearest predicate the *same subject*
    already carries within edit-distance 2 — `lumen_state.py:359-372`.
  - `_gate_predicate()` calls it for any predicate new to the whole store,
    after every other gate check (open-namespace subject, registry
    predicate, existing rows, recorded addition) has missed it, and before
    accepting even a `--new-predicate`-justified addition —
    `lumen_state.py:390-401`.
  - Scope: edit-distance ≤2 catches formatting/typo renames (`lan_ip` vs
    `lan-ip`). Semantic renames whose names aren't textually close
    (`ssh_status` vs `tailscale_ssh`, `ts_ip` vs `tailscale_ip`) are not
    edit-distance-adjacent and are handled by the alias map instead (below) —
    this matches the ticket's literal spec ("refuses a new predicate within
    edit-distance 2"), not a broader semantic-rename detector.

## One-time hygiene pass (`state_registry.json` + `minerva.db`)

`/home/lumen/minerva/state_registry.json`'s `aliases` map now canonicalizes
future ingests: `ts_ip` -> `tailscale_ip`, `lan-ip` -> `lan_ip`, `hw` ->
`hardware`, `role_status` -> `status`. The existing open rows under the old
names were merged into the canonical predicate/subject (closed with
`closed_by` lineage, per wq#1700) so `current()` no longer shows a stale
duplicate next to the live fact:

- `bes.hw` -> `bes.hardware`: `history bes hw` shows the row closed
  2026-09-10, `history bes hardware` carries forward.
- `nephthys.ts_ip` -> `nephthys.tailscale_ip`: closed 2026-09-10.
- `gameboy`/`gameboy-pc` subject split merged onto `gameboy` (no
  `gameboy-pc.*` rows remain open).
- `phone`/`s26-ultra` subject split merged onto `s26-ultra` (no `phone.*`
  rows remain open).
- Value suffixes that defeated drift matching (e.g.
  `192.168.8.190_static_nmcli`) reformatted to a plain value with the
  descriptive tail moved out of the matched field —
  `gameboy.lan_ip = 192.168.8.190 (static, nmcli)`.

Verified live via `lumen_state.py history <subject> <predicate>` (closed-row
`valid_to`/`closed_by`) and `lumen_state.py current` (no surviving
old-name/old-subject rows) — see the DONE notes for the exact commands run.
`khepri`/`bes`/`gameboy-pc` `hw` merges use the same alias, same mechanism.

## Why this ships as a diff/README, not committed source

`/home/lumen/minerva/**` is not a git repository (ISSUE-LOG precedent
wq#1100, wq#1127, and the wq#1633 paper trail at
`ops/wq1633-minerva-projection-dirty-flag/README.md`). The code above was
already live in `/home/lumen/minerva/lumen_state.py` and
`state_registry.json` at the start of this BUILD session (an earlier,
uncredited pass applied it — see wq#1689's own KILLED prior-attempt notes,
which show no real diff landed in *this* worktree even though the live file
already carries every `wq#1689`-tagged line cited above). This session's
job was verification + the paper trail: `test_lumen_state_wq1689.py` imports
the real module (`sys.path` pointed at `/home/lumen/minerva`) against a
throwaway temp DB and exercises both mechanisms end-to-end, and this README
records the file:line evidence for a repo this branch actually touches.

## Verify

    cd ops/wq1689-minerva-volatile-state-halflife && python3 -m pytest -q test_lumen_state_wq1689.py
