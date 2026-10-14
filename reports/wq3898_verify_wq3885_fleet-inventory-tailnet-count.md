# wq#3898 — verify-pass of wq#3885 fleet-inventory: test_real_inventory_records_tailnet_identity

**Verdict: CONFIRMED.** The updated assertions (31 machines, 11 tagged nodes, 3
hostname mismatches) match the real `machines/*.yaml` roster exactly, independently
recomputed from the raw files rather than trusted from the diff or the test's own pass/fail.

PR: http://100.121.17.5:3000/lumen/fleet-inventory/pulls/17 (merged `f171664`, branch
`fix/wq3885-fleet-inventory-tailnet-count-a1`, commit `30058e3`)

## What wq#3885 claimed

`tests/test_fleet_inventory.py::test_real_inventory_records_tailnet_identity` was failing
on `main` because `machines/` grew from 25 to 31 records (commit `d91c9ab`, "add six node
machine records": horus, khnum, maksoor, neith, ptah, sekhmet) without the test being
updated. The fix changes the assertions from 25→31 total, from 6→11 tagged
(`tag:lumen-fleet`) node names, and leaves the hostname-mismatch dict (`be3600`, `toilet`,
`phone`) unchanged.

## What I checked, independently of the diff's own narration

1. **Read the diff cold** (`git show 30058e3 -- tests/test_fleet_inventory.py`) — a single
   test-only hunk, no production code touched.
2. **Recomputed `len(tn) == 31`** directly: `ls machines/*.yaml | wc -l` → 31 files,
   matching without relying on `inv.load()`.
3. **Recomputed the tagged-node list from raw YAML, not from the test**:
   `grep -o "tags: \[[^]]*\]" machines/*.yaml` → exactly 20 files with `tags: []` and 11
   with `tags: [tag:lumen-fleet]` (20+11=31 accounts for all files, no other tag value
   exists anywhere in the roster). The 11 file basenames, sorted, are exactly
   `bes, horus, khepri, khnum, neith, nephthys, ptah, sekhmet, sia, sobek, wadjet` — matches
   the PR's new assertion character-for-character.
4. **Cross-checked against commit `d91c9ab`** (the commit that actually added the 6 new
   records): it added `horus.yaml, khnum.yaml, maksoor.yaml, neith.yaml, ptah.yaml,
   sekhmet.yaml`. Of those 6, `maksoor.yaml` carries `tags: []` (confirmed by direct read)
   and the other 5 carry `tag:lumen-fleet` — so "6 new machines, 5 newly tagged" is
   internally consistent with the old 6-tagged → new 11-tagged delta, not just an
   assertion that happens to match.
5. **Recomputed the hostname-mismatch dict independently** with a standalone script
   (not `inv.load`) that parses `name:`/`hostname:` out of every `machines/*.yaml` and
   diffs them: the only three mismatches in the entire 31-file roster are
   `be3600→GL-BE3600`, `toilet→toilet-940X5N`, `phone→"Galaxy S26 Ultra"` — exactly the
   dict the test asserts, no additions or omissions.
6. **Ran the specific test**: `pytest tests/test_fleet_inventory.py -k
   test_real_inventory_records_tailnet_identity` → PASSED.
7. **Ran the full suite** (`pytest tests/`) to check for collateral breakage from a
   test-only change: 41 passed, 0 failed.
8. **Checked `inv.load(strict=True, fallback=False, ...)` semantics** (`fleet_inventory.py`)
   to confirm the test reads live YAML through validation, not a stale last-good cache —
   `fallback=False` means a parse/validation error raises instead of silently serving cached
   data, so a passing run is reading the actual current roster.

## What would have broken this verdict (and didn't)

- If any machine file carried a tag value other than `[]` or `[tag:lumen-fleet]`, the
  test's `all(t["tags"] == ["tag:lumen-fleet"] ...)` assertion would be checking a fiction
  — a repo-wide `grep -o "tags: \[...\]"` found only those two values across all 31 files.
- If `maksoor.yaml` (the 6th new record) had also been tagged, the PR's "5 new tagged"
  arithmetic and the sorted name list would both be wrong — confirmed its `tags: []` by
  direct read, independent of the ticket's own notes.
- If a mismatch existed outside the three named (e.g. a node renamed since the last test
  update), the dict assertion would under-count — recomputed all 31 name/hostname pairs
  from scratch rather than checking only the three named entries.

## Non-issue noted, not a blocker

The ticket's dispatcher-added capability `needs: ssh(anubis)` suggested a live SSH probe
to node `anubis` might be part of this verification. Grepped the repo for `anubis` — the
only hits are fixture data in `tests/test_identity_verify.py` (an unrelated test using
"anubis" as a sample node name for a mocked identity-verify scenario). `anubis.yaml` itself
carries `tags: []` and is untouched by this PR. No real SSH-to-anubis probe exists for this
test; the capability grant appears to be inert dispatcher boilerplate for this ticket, not
something this verify-pass needed to exercise. Per instinct 8, I did not invent an SSH call
to anubis to manufacture a check this diff doesn't require.

No escalation to judgment needed — board this as verified.
