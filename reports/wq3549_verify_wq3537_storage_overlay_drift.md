# wq#3549 — verify of wq#3537 (probe-storage-integrity FAIL: manifest:overlay-drift)

Verifier: claude-sonnet-5@high, fresh instance (author was claude-opus-5-5).
Verdict: **CONFIRM** the diff shipped by wq#3537 —
http://100.121.17.5:3000/lumen/claude-foreman/pulls/1620 (merged `092412fa`,
live fix `/home/lumen/agents/storage-audit` commit `b2a422a`). The diagnosis,
the code change, and every live-remediation claim in the PR hold up under
adversarial probing against the real production manifest/overlay, not just
the shipped test fixtures.

## Per claim

| wq#3537 claim | Result | Evidence |
|---|---|---|
| Root cause: `manifest_add_node.py` stamped `meta.updated` (overlay-owned key) on every add, so `apply_overlay.build()` always disagreed with the written manifest afterward, and `storage_probe`'s `assert_overlay_sync()` escalated `manifest:overlay-drift` | CONFIRMED | Read live source via `python3 -c "import inspect; ...inspect.getsource(ao.build)"`: `build()` unconditionally overwrites `merged["meta"] = deepcopy(ov["meta"])` for any top key the overlay declares — old code's `man["meta"]["updated"] = date.today()` could never survive that round-trip. |
| Fix: `add_entry()` sets the node's entry, drops `provision_pending`, then returns `ao.build(ov, man)` so the written manifest is already a fixed point of the overlay; no more `meta.updated` stamp | CONFIRMED | `git -C foreman show 092412fa:ops/wq3537-storage-overlay-drift/manifest_add_node.diff` — one clean hunk per call site, matches live `/home/lumen/agents/storage-audit/manifest_add_node.py` byte-for-byte (read via `python3 -c "print(open(...).read())"`). |
| `test_add_node_overlay.py`: 19/19 pass (3 fail pre-fix: fresh add, `--replace` calibration, committed `overlay.yaml` fixture) | CONFIRMED, and reproduced the pre-fix failure independently | `pytest -q /home/lumen/agents/storage-audit` → **23 passed** today, not 19 — explained, not a discrepancy: `git -C storage-audit log` shows `a0866f8` (wq#3470) landed at 08:01:37Z, **37 minutes after** `b2a422a` (07:24:40Z), adding 4 new tests to `test_parked_set.py`. 23 − 4 = 19, exactly the author's count at commit time. Independently reproduced the "3 fail" claim by monkey-patching `mad.add_entry` back to the literal pre-fix 3 lines (`entry.pop(...); man["nodes"][node]=entry; man["meta"]["updated"]=today`) and running the 6 overlay tests: **3 failed — `test_fresh_add_leaves_no_drift`, `test_replace_keeps_overlay_calibration`, `test_committed_overlay_fixture`** — exact match to the PR's named list. |
| `apply_overlay.py --check` before: 1 drift; after: in sync | CONFIRMED, live, right now | `python3 /home/lumen/agents/storage-audit/apply_overlay.py --check` → `overlay in sync with manifest — nothing to do`. |
| `storage_probe.py --run` / `sentinel_probes.py`: PASS, esc=0 sla=0 | CONFIRMED, live, right now | `python3 /home/lumen/agents/storage-audit/storage_probe.py --run` → `PASS storage-integrity: 12/16 nodes ... esc=0 sla=0 critical=False`. `python3 /home/lumen/sentinel/probes/sentinel_probes.py` → `PASS probe-storage-integrity: ... esc=0 warn=4 sla=0`. The `warn=4` unreachable set (farida, apep, gameboy, seshat) and the horus "no ssh mapping, re-probe after 15s OK" transient both reproduce exactly as the PR's "Not this ticket" section describes, and are correctly tracked separately (wq#3548, open/unclaimed, correctly scoped to the horus SSH-map gap only). |
| ISSUE-LOG entry | CONFIRMED | `/home/lumen/kb/ISSUE-LOG.md` row **#1177** matches the PR's root cause/fix description verbatim. |

## Adversarial probes that did NOT find a defect

The shipped test suite's own population is somewhat narrow (a synthetic toy
overlay `OV` for 5 of the 6 new tests, the real `overlay.yaml` for only one —
`test_committed_overlay_fixture` — and a synthetic base manifest even there).
Per the "a probe must be able to fail" standard, ran `add_entry()` (the real,
live, imported function — not a reimplementation) against the **live
production** `/home/lumen/agents/storage_manifest.yaml` and `overlay.yaml`,
read-only, result never written back:

- Fresh add of a throwaway node (`zzz_probe_adversarial`, carrying
  `provision_pending: True`) against the real manifest → zero drift,
  `provision_pending` dropped, `meta.updated` unchanged, original in-memory
  manifest object left untouched (confirms the pure-function/no-mutation
  claim implicit in the diff, not just asserted by the toy-fixture test).
- `--replace` of `lumen` — the one node the *real* `overlay.yaml` actually
  overrides (RAID arrays, `required_units`, `capacity`, `max_temp_c`) — with a
  deliberately conflicting fake survey (`max_watts: 999`,
  `mounts: ['/','/data']`) → zero drift, and the overlay's RAID/units/capacity
  calibration survived the replace exactly as `test_replace_keeps_overlay_calibration`
  predicts, now proven against the real fixture instead of the toy one.
- Checked whether `yaml.safe_load` would silently promote an unquoted
  `meta.updated` date string to a `datetime.date` object (which would make
  `diff_keys`' `a != b` fire on a type mismatch even when the calendar date
  matches) — both `overlay.yaml` and `storage_manifest.yaml` quote the date as
  a string on disk, so this does not apply; checked instead of assumed.
- Confirmed `deep_merge()`'s "scalars/lists from `over` win outright"
  semantics make repeated `build()` calls idempotent (no list-append
  accumulation across repeated adds/replaces) — read the source, did not just
  trust the docstring.

## Actions taken

| Action | Rationale |
|---|---|
| This report at `reports/wq3549_verify_wq3537_storage_overlay_drift.md` | Required write-up location per ticket instructions (wq#2805) |
| No `wq note` correction filed against wq#3537 | Nothing to correct — diff and live claims both hold |
| No judgment/escalation ticket filed | Not a contradiction — archetype-8 confirm, do not re-implement |
| No live system state changed | All verification above is read-only against production (`--check`, `--run`, in-memory `add_entry()` calls never written back) |
