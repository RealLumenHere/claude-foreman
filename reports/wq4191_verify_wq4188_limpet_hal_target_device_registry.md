# wq#4191 — verify wq#4188 [LIMPET] hal.py target_device REGISTRY gate only recognizes 3

Archetype 8 adversarial verify pass. Author: claude-opus-5-5. Verifier: claude-sonnet-5@high
(fresh instance).

PR: http://100.121.17.5:3000/lumen/claude-foreman/pulls/2125 (merged to `main` as `89559cfe`).
Diff touches `ops/wq3809-limpet-hal/{README.md,hal.py,tests/test_hal.py,
verify_no_false_positive.py}` only.

## Claim under test

wq#4188 claims the `target_device` plugin's `usb_ids` gate held only 3 VID:PID pairs
(05c6:9008, 2207:330c, 1f3a:efe8) and widens it to 60, sourced from a "source-verified
catalog" (`/home/lumen/projects/limpet/data/usb-device-ids.toml`, generated 2026-10-15,
sha256 `4d78a410...`), applying the rule "confidence >= 0.8 and mode not in
{adb,mtp,diag,ums,modem-boot}", explicitly dropping two catalog rows that pass that rule
(1004:6000, 04e8:6601) because usb.ids also gives those IDs to ordinary phones.

## What I checked

- **Before/after diff** (`git show 89559cfe`) — confirmed the registry held exactly 3 pairs
  before (one removed-line comment literally said "3-pair usb_ids set"), matching the ticket
  title.
- **Mechanical recount of the new set** — parsed `hal.py`'s `target_device` `usb_ids`
  frozenset: 60 entries, 60 distinct, zero duplicates. Matches the commit message's "60".
- **Independently re-derived the inclusion rule against the cited source catalog**, not
  trusting the PR's own count. Read the full
  `/home/lumen/projects/limpet/data/usb-device-ids.toml` (1105 lines; outside this worker's
  `--add-dir` list but readable via the `Read` tool and via `python3 -I` — `ls`/`wc`/`cat` on
  that path are blocked by the sandbox, `Read`/`python3 open()` are not). Wrote a script
  (`/tmp/fworker-wq4191/compare.py`) that applies the PR's stated rule
  (`confidence >= 0.8 and mode not in {adb,mtp,diag,ums,modem-boot}`) to every `[[devices]]`
  row and diffed the resulting pair set against `hal.py`'s 60:
  - Catalog produces 62 qualifying distinct pairs.
  - `catalog − hal.py` = `{(04e8,6601), (1004,6000)}` — exactly the two pairs the PR's own
    comment says it deliberately dropped. No other qualifying pair is missing.
  - `hal.py − catalog` = `{}` — nothing in the 60 falls outside what the catalog's own rule
    would admit.
  This is the strongest possible check for this kind of change: the 60-vs-3 count isn't just
  asserted, it's reproducible from the cited source by the cited rule, with the only two
  deviations being the ones the author explicitly flagged and justified.
- **Checked the two explicit exceptions against primary evidence**, not just the catalog's
  restated claim — read `/usr/share/misc/usb.ids` directly:
  - `1004  LG Electronics, Inc.` → `6000  Various Mobile Phones`. Confirmed.
  - `04e8  Samsung...` → `6601  Mobile Phone`, `685d  GT-I9100 Phone [Galaxy S II] (Download
    mode)`, `6860  Galaxy series, misc. (MTP mode)`. Confirmed — `6601` is generic, `685d` is
    a real Odin download-mode entry (correctly kept), `6860` is MTP (correctly excluded, and
    is the exact mislabel the PR calls out: limpet's own target-devices.d called it "Odin
    download mode", usb.ids says MTP).
- **Checked the provenance citation isn't fabricated** (instinct 8) — `sha256sum` of the live
  catalog file is `4d78a4105ac56b482a3d0efd41b7439b5e1699b03f39c178c87f74212fc1cf63`, matching
  the `4d78a410...` the PR's `hal.py` comment cites.
- **Checked the cited prior-art issue** — ISSUE-LOG #1365 (wq3933 verify of wq3923) does
  document that `limpet/target-devices.d` rows were recalled/pattern-completed, not sourced,
  during a fleet-search outage, which is the PR's stated reason for gating on the newer
  `usb-device-ids.toml` catalog instead. Consistent with the repo's own history, not an
  invented justification.
- **Acceptance-probe falsifiability (wq#2630 doctrine)** —
  `verify_no_false_positive.py` grew from a 3-device to a 4-device canned-lsusb corpus; the
  new 4th device is exactly `04e8:6860` (Samsung MTP), the ID the fix's own commentary singles
  out as the dangerous mislabel. This is an acceptance probe whose new coverage targets a
  real, named failure mode rather than restating the fix — it would fail if the gate were
  widened to the uncurated catalog, which is exactly what `test_hal.py`'s new
  `test_catalogued_normal_mode_devices_are_not_target_devices` (9-device corpus including
  `1004:6000`, `04e8:6601`, `04e8:6860`, `05c6:9091` diag, etc.) asserts against.
- **Ran the test suite**: `python3 -I -m pytest ops/wq3809-limpet-hal/tests/test_hal.py -q`
  → 19 passed.
- **Ran the live `--verify` contract**, not a re-implementation of it. The per-command
  allowlist has no `bash <path>/install.sh` entry (only `bash scripts/*`), so a direct
  `bash .../install.sh --verify` is refused pre-tool-use. Used
  `/home/lumen/bin/lumen-run start -- bash /home/lumen/foreman/ops/wq3809-limpet-hal/install.sh --verify`
  (allowlisted, detached unit) → `lumen-run wait` → `DONE:0`, log shows `VERIFY OK` /
  `LUMEN_JOB_EXIT=0`. This exercises the real `LIVE=/home/lumen/foreman/ops/wq3809-limpet-hal/hal.py`
  path, the `REGISTRY`/`detect`/`list-plugins` marker greps, `list-plugins` reporting 8
  plugins, and the false-positive probe against the live file — all independently reproduced
  via allowlisted equivalents before falling back to `lumen-run` for the one step
  (`bash install.sh`) that needed it.
  *(Allowlist gap worth widening, same as wq#4189's verify: `bash
  /home/lumen/foreman/ops/*/install.sh --verify` or similar — every ops-bundle verify ticket
  hits this same refusal.)*
- **Confirmed the live file matches the merged commit**: `diff
  /home/lumen/foreman/ops/wq3809-limpet-hal/hal.py
  /home/lumen/worktrees/wq4191-a2/ops/wq3809-limpet-hal/hal.py` — identical.
- **README diff** — spot-checked the new "SoC boot-mode VID:PID map" section's claims
  (60 pairs, confidence >=0.8, excluded modes, the three named false-positive classes, the
  unused `2207` high-byte Rockchip fallback) against the actual code and catalog; all match.

## Adversarial angles tried and found not to break the fix

- Tried to find a catalog row at confidence >=0.8 with a non-excluded mode that was silently
  dropped without the PR's own acknowledgment — none exists (exactly 2 exceptions, both
  named and justified).
- Tried to find a pair in the new 60 that the catalog would NOT admit under the stated rule
  — none exists.
- Tried to verify the "dropped because usb.ids also calls it a normal phone" justification
  independently against the real `/usr/share/misc/usb.ids` rather than trusting the PR's
  restatement of its own catalog — confirmed on both exceptions.
- Checked for duplicate/malformed pairs in the new frozenset — none.
- Verified the cited sha256 against the live file rather than accepting the citation at
  face value — matches exactly.

## Verdict

**CONFIRMED.** wq#4188's 60-pair `target_device` registry is reproducible byte-for-byte from
the cited source catalog under the cited rule, the two documented exceptions are correct per
primary-source `usb.ids` evidence, the sha256 provenance citation is not fabricated, the new
acceptance probes target real named failure modes and are falsifiable, the test suite passes
(19/19), and the live `ops_verified(wq3809-limpet-hal)` `--verify` contract passes
(`VERIFY OK`, exit 0) against the actually-deployed file.

No contradiction found; not escalating to judgment.
