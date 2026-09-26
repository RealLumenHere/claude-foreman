# wq#2145 — verify of wq#2136 (7a00cca, hathor disable-gpus unit)

> **Follow-ups closed by wq#2157 (2026-09-20).** The runbook was replaced with a retirement note, as
> the Contradiction section below asked. `--verify` no longer checks that the unit is *installed*, so
> the "expected to exit 1" note below is obsolete: it now asserts the retired state and exits 0 when
> hathor and nephthys are disabled/inactive/unwanted with both D300s amdgpu-bound. It was executed —
> see `ops/tests/test_wq2136_disable_gpus_verify.py`.

Verdict: **CONFIRMED on the static claim, NOT CONFIRMED live.** The quoting defect is fixed in the
shipped unit; whether hathor or nephthys run it could not be checked from a worker. The unit should not
be installed at all (see Contradiction).

## Static checks (`ops/wq2136-hathor-disable-gpus-unit/disable-gpus.service`)

- One `ExecStart=` line (`grep -c "^ExecStart="` = 1); the `for p in 0000:02:00.0 0000:06:00.0; do ...; done`
  list is on that one line, which is the original defect (two-line list inside the single-quoted `bash -c` argument).
- Exactly two `'` characters (`grep -o "'" | grep -c ^` = 2): balanced.
- systemd expansion: `$p` sits inside a quoted multi-word argument, so it is not a "separate word" and is not
  substituted; there is no `${...}` and no `%` specifier. The `;` characters are inside the quoted argument, so they
  are not command separators.
- File ends in a single `\n`. Matches the isis form `RUNBOOK.md:11` calls correct (not compared byte-for-byte; isis unreadable here).
- Not run: `systemd-analyze verify` (refused, "requires approval").

## Contradiction with later evidence

`research/ISIS_POWEROFF_RCA.md` §5 (0222da3, after 7a00cca) says `disable-gpus.service` violates the June
standing rule (leave both GPUs amdgpu-bound, dpm=low), fails its unbind step on every boot, and adds
+16.6 W idle on isis (103.2 W vs hathor's 86.6 W), and concludes "retire it regardless". The runbook's HOLD
banner (`RUNBOOK.md:1-4`) still says "wait for the RCA verdict"; the RCA has landed, and the verdict for this
unit is retire, not install. The `## Apply` section should not be executed. Follow-up: replace the runbook
with a retirement note (remove the unit on isis; leave hathor/nephthys `disabled`).

## Verify handler (wq#1696)

Added `ops/wq2136-hathor-disable-gpus-unit/install.sh`. `--verify` reads `/etc/systemd/system/disable-gpus.service`
over `ssh` on hathor and nephthys, read-only, and exits 0/1/2 per `ops/VERIFY_CONTRACT.md`. It is expected to
exit 1 (`MISS differs: ...`) while the hold stands, since the broken copy is still on both nodes on purpose. A
node it cannot reach yields exit 2, not a drift alert. The non-verify path installs nothing and exits 3.
The handler was written but **not executed**: `bash ops/<bundle>/install.sh --verify` was refused.

## Issues (ISSUE-LOG row could not be appended: guard.sh blocks Write/Edit in /home/lumen/kb from workers)

| symptom | root cause | solution | status |
|---|---|---|---|
| `fleet-exec run hathor cat ...` denied `grant:grant_missing_env`; bare `ssh hathor ...`, `systemd-analyze verify <unit>`, `bash ops/<bundle>/install.sh --verify` all "requires approval" | same worker gaps as ISSUE-LOG #257, plus the allowlist omits ssh, systemd-analyze and `bash <script>` | verified statically; handler written unrun | OPEN: widen allowlist for `systemd-analyze verify` and `bash ops/*/install.sh --verify` |

Also: `lumen_busy check` returned a stale key for wq#2151 (pid 1600198 not alive, phase `research-done-baseline`);
it belongs to another lane, so it was left untouched.
