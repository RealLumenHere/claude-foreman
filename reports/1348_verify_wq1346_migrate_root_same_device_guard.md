# wq#1348 — Adversarial verify-pass of wq#1346 (migrate_root.sh same-device guard, PR#251)

**Verdict: CONTRADICT** — the shipped regression test (`test_same_device_guard.sh`) passes
and correctly demonstrates that the *original* bug (digit-stripping a partition suffix) is
fixed. But the replacement mechanism (`base_disk()`'s `lsblk -no PKNAME` lookup, called
without `--nodeps`) has its own false-negative: on any disk with more than one layer of
block-device children below it — most commonly an LVM logical volume on a partition, the
**default guided-partitioning layout for Ubuntu Server** — `migrate_root.sh <that-disk>`
silently **allows** wiping the disk the live root actually lives on. Reproduced live,
deterministically, on this host's real device tree (not a fabricated device name).

## What was shipped (PR#251, commit c0b6af2, Refs: wq#1346)

`base_disk()` resolves a device node to its whole disk via `lsblk -no PKNAME "${dev}"`,
taking the first line of output as that device's own parent, falling back to a naming-regex
(`nvme0n1p2`→`nvme0n1`, `sda2`→`sda`) when `-b "${dev}"` is false or `lsblk` returns nothing.
`is_same_disk()` compares `base_disk(TARGET_DEV)` against `base_disk(root_dev)`. The
migration script's guard now calls `is_same_disk` instead of the old
`${root_dev%[0-9]*}` prefix-strip. `test_same_device_guard.sh` sources the script and
exercises `base_disk`/`is_same_disk` against a table of device names.

## Probe: the shipped test passes, but never exercises the code it's supposed to cover

```
$ python3 -c "...bash /home/lumen/worktrees/wq1348-a1/.verify-scratch/test_same_device_guard.sh..."
EXIT 0
ok   base_disk /dev/nvme9n9p2 -> /dev/nvme9n9
...
all checks passed
```

All 13 checks pass. But every device name in the table (`nvme9n9`, `sdz`, `mmcblk9`, ...) is
deliberately absent from any real host (the test's own comment says so), so `[[ -b "${dev}" ]]`
is false for all of them and `base_disk()` always takes the regex fallback. **The test suite
never calls `lsblk` at all** — the primary, "authoritative" path the code comment describes
is shipped with zero coverage.

## The contradiction: `lsblk -no PKNAME "$dev"` (no `--nodeps`) is not "that device's PKNAME"

`lsblk`, given a device argument, prints that device's entire dependency subtree (every
descendant), not a single row for the device itself — unless you pass `--nodeps`/`-d`.
`base_disk()` takes `${parent%%$'\n'*}` (the *first* line) and assumes it's the queried
device's own PKNAME. That assumption breaks whenever the device has more than one level of
descendants, because `lsblk`'s row order is not "queried device first."

Reproduced on this foreman host's real disks (`lsblk -no NAME,PKNAME,TYPE`): root filesystem
is `/dev/mapper/ubuntu--vg-ubuntu--lv`, an LVM LV built on `/dev/nvme5n1p3`, a partition of
disk `/dev/nvme5n1` — the standard Ubuntu guided-LVM layout. This same host also carries
`nvme0n1/nvme1n1/nvme2n1 → md0` (RAID5) and `nvme3n1/nvme4n1/nvme6n1/nvme7n1 → md1` (RAID0),
so multi-level device nesting is in active use on the fleet, not a hypothetical.

```
$ lsblk -no PKNAME /dev/nvme5n1
nvme5n1p3
<blank>
nvme5n1
nvme5n1
nvme5n1
```

First line is `nvme5n1p3` — a *grandchild* (the LV's parent), not the disk's own (blank)
PKNAME. Sourcing the PR's `migrate_root.sh` directly and calling its functions:

```
$ base_disk /dev/nvme5n1          -> /dev/nvme5n1p3     # wrong: a disk has no parent
$ root_dev="$(findmnt -n -o SOURCE /)"                   # /dev/mapper/ubuntu--vg-ubuntu--lv
$ is_same_disk /dev/nvme5n1 "$root_dev"  -> allow         # WRONG: nvme5n1 IS root's disk
```

Reproduced 3x, deterministic each time (not a race). This is the exact operator invocation
the runbook documents (`migrate_root.sh <target-dev>` taking a whole-disk name, e.g.
`/dev/nvme0n1` on Seshat) against the exact failure mode wq#1346 exists to close: the script
would proceed to `parted -s /dev/nvme5n1 mklabel gpt` on the disk the live root sits on.

## Root cause and fix shape (not applied — verify-pass does not re-implement)

Add `--nodeps`/`-d` to the `lsblk` call in `base_disk()`. Confirmed this closes the
disk-vs-partition confusion on the same real devices:

```
$ lsblk -no PKNAME --nodeps /dev/nvme5n1     -> (blank)      # correct: no parent
$ lsblk -no PKNAME --nodeps /dev/nvme5n1p3   -> nvme5n1       # correct
```

That fixes the false-ALLOW for plain multi-level block trees (partition + LVM/RAID/LUKS
sitting on a *named disk device*). A second, separate gap remains even with `--nodeps`:
`lsblk -no PKNAME --nodeps /dev/mapper/ubuntu--vg-ubuntu--lv` is also blank — an LVM LV has
no PKNAME at all — so `base_disk()` of an **LVM root** (root_dev itself, as opposed to the
target) still can't resolve up to the physical disk; `is_same_disk(target-disk, lvm-root)`
returns `allow` regardless of `--nodeps` (my first test above, `SAME_ROOT_VS_NVME5=allow`,
demonstrates this independently of the ordering bug). Closing that requires walking
`lsblk`'s reported dependency graph (e.g. `lsblk -no PKNAME` without `--nodeps`, but parsed
as a graph rather than "take line one," or `lsblk -sn -o NAME <lv>` to walk up) rather than a
single PKNAME lookup. Whether Seshat's actual root is LVM or a plain partition is unknown
from the README — it doesn't say — so this may or may not be live on the ticket's actual
target host, but the guided-LVM case is common enough (default on this very node) that it
should not be assumed away.

## Other checks — no issues found

- `is_same_disk` correctly **allows** genuinely different disks in every case tested
  (`/dev/sda` vs. this host's LVM root; the test suite's `sdz`/`nvme9n8` control cases) — no
  false positives (over-refusal) found.
- The `if [[ "${BASH_SOURCE[0]}" != "${0}" ]]; then return 0; fi` sourcing guard behaves as
  documented: sourcing the script for tests defines the helpers and stops before any
  destructive command runs, confirmed by successfully sourcing it standalone with no side
  effects.
- README's line-number citations (`migrate_root.sh:45-48`, `:74-77`, `:84-91`) match the
  shipped file.

## Disposition

Filing a judgment escalation (opus-5@high) with this report and the fix shape above per
ticket instructions. Not fixing directly — this ticket is a verify-pass, not a
re-implementation gate (precedent: wq#872/wq#1268/wq#1312 verify-pass contradictions).
