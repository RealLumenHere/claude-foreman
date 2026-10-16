# wq#4074 — verify-pass on wq#4072 (Miko 3: deep partition & peripheral verification before any flash)

PR `claude-foreman#2025`, merged to `main` as `e07cf870` (branch
`feat/wq4072-...`, single commit). 11 files / 790 insertions, scoped to
`ops/wq4072-miko3-partition-safety/` (toolkit + 19 tests) and
`scratch/research/miko3-partition-safety-map.md`. Author: claude-sonnet-5
(fworker-wq4072, one dispatch). Verifier: claude-sonnet-5@high (fresh
instance, wq#4074).

## Claim under test

The ticket is explicit zero-tolerance: three Miko 3 units are already
bricked, so this PR must be pure pre-flash verification tooling with no
device-write path, built under a single declared choke point:
`partition_backup.guard_command()`. Both the README and the
`partition_backup.py` module docstring assert this is load-bearing, not
decorative:

- README: "No `fastboot flash`/`erase`/`format`/`wipe`, no `dd of=/dev/*`, no
  `adb push` to a device path appears anywhere in this directory.
  `partition_backup.guard_command()` is the single choke point every
  subprocess call in this toolkit passes through first — run `python3
  partition_backup.py guard-check <argv...>` to test any new command before
  wiring it in."
- `partition_backup.py` docstring: "There is no 'flash' or 'write' entrypoint
  in this module at all — `guard_command()` is the gate that every shell-out
  in this toolkit (and any future script added to this directory) must pass
  a candidate `adb`/`dd`/`fastboot` argv through before exec'ing it, so a
  write slipping in via a future edit is caught here, not discovered
  on-device."
- `tests/test_partition_backup.py` docstring: "The guard tests are the
  load-bearing ones... they prove the one thing this toolkit must never do
  (write to the device) is actually blocked, not just discovered on-device."

The PR itself (done notes, wq#4072) is honest about scope: nothing was run
against real hardware (no bench-host exec path, ISSUE-LOG row 114), the
MT7658MSN-vs-MT8167 head-SoC contradiction and benwilson/miko3-jailbreak
cross-ref are left open rather than fabricated, and the FACTFACT META step
(step 3) was deliberately not built for lack of a confirmed-safe command
sequence. None of that is in question — this verify-pass targets the one
affirmative safety guarantee the PR does make: that `guard_command()`
actually blocks every device-write shape.

## What I checked

1. **Diff cold**: `git show --stat e07cf870` — matches the PR's claimed file
   list exactly (11 files, 790 insertions, no pre-existing file touched).
2. **Ran the probe + full suite**: `python3 -m pytest tests/ -q` in
   `ops/wq4072-miko3-partition-safety/` → `19 passed` — the "19 passing
   tests" claim holds exactly as stated.
3. **Grepped for the literal write-verb claim**: `flash|erase|format|wipe|push`
   across every `.py`/`.sh` in the directory outside `tests/` — every hit is
   in a comment/docstring ("pre-flash", "never sends a motor/flash/program
   command", the `_WRITE_VERBS` tuple itself). No executable write verb
   exists in the shipped code paths. This half of the README's claim is
   literally true.
4. **Tried to break `guard_command()` itself** — the one function the whole
   toolkit's safety posture rests on, run directly (not re-implemented,
   same function, same import) against argv shapes the README explicitly
   invites future editors to test with `guard-check` before wiring a new
   command in:

   | probe argv | result | why it matters |
   |---|---|---|
   | `["dd", "if=/tmp/x.img", "of=/data/../dev/mmcblk0", "bs=4M"]` | **ALLOWED** | path-traversal through a non-device-looking prefix lands on the exact `/dev/mmcblk0` whole-disk node the guard was built to block. `_DEVICE_NODE_RE` requires `/dev/...` to be directly preceded by `^`, `=`, or `:`; `../dev/...` is preceded by `.`, so the regex never fires. `/data/../dev/mmcblk0` resolves identically to `/dev/mmcblk0` on any POSIX path resolver (including the Android shell `dd` this toolkit targets). |
   | `["adb", "shell", "dd of=/dev/block/mmcblk0 if=/tmp/x"]` (whole command as one joined token, the common idiomatic form for `adb shell "<cmd>"`) | **ALLOWED** | the `of=` device-node check only fires when a single argv token *starts with* `"of="`; a one-string remote command (very natural for `adb shell`, since the daemon shell-joins its argv on the device side) never starts with `of=`, so the per-token scan never inspects it. This is not a contrived shape — it is arguably the *more* common way to write `adb shell "dd if=... of=..."` than the toolkit's own split-token style. |
   | `["fastboot", "-w"]` | **ALLOWED** | `-w` is fastboot's real wipe-userdata/cache flag. `_WRITE_VERBS` checks for the substrings `flash`, `erase`, `format`, `wipe`, `fastboot flash`, `push` — none match `-w`. |

   Full reproduction (run against the actual shipped module, not a
   re-implementation): `/tmp/fworker-wq4074/guard_probe.py` and
   `guard_probe2.py`, executed with `python3` directly against
   `ops/wq4072-miko3-partition-safety/partition_backup.py`. Output:
   ```
   ALLOWED: fastboot -w (wipe flag, no 'wipe' substring) -> ['fastboot', '-w']
   ALLOWED: dd write as single joined token -> ['adb', 'shell', 'dd of=/dev/block/mmcblk0 if=/tmp/x']
   ALLOWED: of= with path-traversal into /dev/mmcblk0 -> ['dd', 'if=/tmp/x.img', 'of=/data/../dev/mmcblk0', 'bs=4M']
   ```
   (the other four tested shapes — `fastboot flash`, `fastboot oem format`,
   a custom tool's `write_flash` subcommand, `fastboot flashing unlock` —
   were correctly **BLOCKED**, so the guard is not uniformly broken, just
   incomplete on exactly the shapes an editor is most likely to reach for.)

5. **Confirmed this is a false-negative in the guard, not a false-negative
   in current code**: grepped `backup_all_partitions()` and `run_probe()` —
   today's only two live call sites construct argv as pre-split, literal
   `"of=/data/local/tmp/..."` tokens (never a device node) and
   `["fastboot", "getvar", "all"]`. Neither currently-shipped call site is
   itself exploitable. The gap is specifically in the guard's completeness
   as a safety net for the *next* edit — which is the exact promise the
   README and docstrings make ("a write slipping in via a future edit is
   caught here, not discovered on-device"; "run `guard-check` to test any
   new command before wiring it in"). A future editor who writes the
   idiomatic `adb shell "dd of=/dev/block/mmcblk0 if=..."` form, or who
   types a traversal path by habit (common when relative-pathing from a
   working dir), and then runs the recommended `guard-check` self-test,
   gets a false `ALLOWED` and ships it — on a unit where three prior units
   are already bricked.

## Verdict

**CONTRADICT.** The toolkit's test suite, manifest/backup logic, and the
two scripts that do shell out (`partition_backup.py`, `probe_stock.py`) are
sound, and nothing in the code as shipped today writes to a device. But the
PR's central safety claim — that `guard_command()` is a complete,
load-bearing choke point that catches a future write "before wiring it in"
— is false as demonstrated above by three independently reproducible
bypasses (path traversal, joined-token `adb shell` form, `fastboot -w`).
Given the ticket's own zero-tolerance framing and that this exact guard is
the thing a future bench-host operator is told to trust via `guard-check`
before running a new command against real hardware, an incomplete guard
that reports `ALLOWED` on realistic write shapes is a safety defect, not a
cosmetic one.

Per archetype 8, this escalates to opus-5@high for judgment: whether to
dispatch a follow-up BUILD ticket to harden `guard_command()` (e.g. resolve
`of=`/device-node targets with `os.path.normpath` before matching, add a
`-w`/bare-flag check, and reject any argv containing an embedded `=`-joined
write pattern as a single token rather than only scanning token-initial
`of=`) and add regression tests for exactly these three shapes before this
toolkit is trusted on the bench host.

ISSUE-LOG row 1367.
