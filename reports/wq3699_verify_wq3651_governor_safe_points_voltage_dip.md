# wq#3699 — verify-pass: wq#3651's BC-250 BIOS flash + governor addendum (PR#1722)

Verifier: claude-sonnet-5@high, fresh instance, worktree `wq3699-a1`, 2026-10-08.
Target: http://100.121.17.5:3000/lumen/claude-foreman/pulls/1722 (merged `2f868399cc`,
local `7b3b5a42`), `ops/wq3651-bc250-bios-flash-governor/RUNBOOK-ADDENDUM.md` (new file,
125 lines, no other files touched — confirmed via `git show --stat 7b3b5a42`).

## Verdict: CONTRADICT

The addendum's own governor table addition is internally inconsistent: inserting
`1500MHz/875mV` breaks the strictly non-decreasing voltage-vs-frequency ordering that
every other entry in `ops/wq1337-hapi-bringup/RUNBOOK.md`'s `[[safe-points]]` table
observes, including the two entries either side of the insertion point. That undermines
the file's own claim to ship "the complete, ready-to-run procedure so execution is
mechanical the day hands are on the bench" (line 14) — as shipped, mechanically applying
it produces a governor config where 1600MHz gets *less* voltage than 1500MHz.

## What the PR actually got right

Checked every other factual claim in the addendum against its cited sources; all held:

- **ROCm/Ollama correction (Phase 3, lines 100-125).** The addendum's claim that
  wq#3651's ticket text is self-contradicting on ROCm is verified: `wq show 3651 --json`
  shows the live ticket detail literally asks for `HSA_OVERRIDE_GFX_VERSION=10.3.0` +
  Ollama + `rocminfo gfx1013`, and the ticket's own `notes` field (written at `wq done`)
  independently states "ROCm is a HARD NO for gfx1013" — matching
  `ops/wq1337-hapi-bringup/RUNBOOK.md:206-207` verbatim ("ROCm is a HARD NO — GFX1013 has
  no ROCm userspace support; `HSA_OVERRIDE_GFX_VERSION` silently corrupts compute
  results, never use it", verified in wq#1349) and §8 line 223 (Ollama drops MoE
  throughput ~60→~25 tok/s). `akandr/bc250-rocm`, which the addendum names as the only
  expt-viable ROCm path, is not an invented repo name — it's tracked in
  `agents/work-queue/reports/1018_bc250_community_watch.md:18` as an existing watched
  feed, independent of this ticket. Not fabricated evidence (instinct #8 check).
- **`lumen_state` quotes (lines 8-9, 73-76).** `python3 /home/lumen/minerva/lumen_state.py
  current hapi-1` reproduces both quotes verbatim: `hapi-1.bringup_eta` since
  2026-09-02 ("awaiting Gelid Xtreme pads... thermal mod"), and
  `hapi-1.bringup_runbook` since 2026-08-09 ("pre-boot cooling targets (1500MHz/900mV
  sustained point)"). Both real, not invented.
- **40CU unlock (Phase 2, lines 49-52).** Correctly left unchanged from
  `RUNBOOK.md` §2, which wq#1349's verify-pass independently confirmed against the
  live GitHub repo (`duggasco/bc250-40cu-unlock`, not the ticket's named
  "duggasco 40cu kernel patch", which does not exist — `RUNBOOK.md:41-44`).
- **`ttm.pages_limit=4194304` arithmetic (line 61).** `4194304 * 4096 bytes = 2^34 bytes
  = 16 GiB` exactly — checks out, and the "complementary to, not a replacement for,
  `gttsize=14750`" framing is consistent with `RUNBOOK.md:95` ("pairs with the BIOS
  step").
- **Phase 1 BIOS steps (lines 17-45).** Net-new content (not covered by `RUNBOOK.md`)
  is appropriately hedged rather than asserted as fact where it should be: the
  IOMMU-disabled-vs-`iommu=pt` conflict is flagged as unresolved and deferred to the SOT
  doc rather than guessed, and the modded-BIOS flash script is correctly reported
  unreachable (`/home/lumen/projects/bc250` outside this worker class's `add_dirs`,
  same gap as ISSUE-LOG #129/#141) rather than fabricated.
- **Framing of what's live.** "Nothing in this file is live or executed" (line 8) is
  true and matches `RUNBOOK.md:17-18`'s own "nothing here is live" — the whole bundle
  lineage is paper + scripts pending physical hardware.

## What's wrong: the safe-points table addition is not monotonic

`RUNBOOK.md`'s existing `[[safe-points]]` table (lines 168-190, verified live and
unchanged by this PR) is strictly non-decreasing in voltage as frequency rises:

```
500 /700   1175/700   1400/750   1600/800   1700/850   1800/900
```

The addendum (lines 82-94) instructs inserting a new point *between* the existing
1400/750 and 1600/800 entries:

```toml
[[safe-points]]
frequency = 1400
voltage = 750

[[safe-points]]
frequency = 1500
voltage = 875          # <- inserted by this PR

[[safe-points]]
frequency = 1600
voltage = 800
```

Applied as written, the resulting sequence is `750, 875, 800, 850, 900` — voltage rises
from 750 to 875 and then *drops* to 800 before rising again. This is the only place in
the entire table, before or after this edit, where voltage decreases while frequency
increases. The governor's own description (`RUNBOOK.md:149-150`) is "adaptive (load-target
driven ramp between `[[safe-points]]` frequency/voltage pairs)" — a ramp table that dips
and recovers is not a transcription nitpick, it is a real inversion of the ordering every
other point in the table (and ordinary GPU DVFS behavior — higher clock needs equal-or-more
voltage, never less) depends on. Taken mechanically, as the addendum explicitly says this
procedure is meant to be used ("ready-to-run... execution is mechanical"), the governor
would offer *less* voltage at 1600MHz than at 1500MHz, which is either an undervolt risk
at 1600MHz or undefined governor behavior at the 1500→1600 boundary, depending on how the
adaptive ramp logic handles a non-monotonic table — untested either way, because hapi-1
does not exist yet to test it on.

The 875mV figure itself is reasoned (addendum line 96: "splits wq#3651's 850-900mV
range"), and the underlying sustained-point data it's reasoning from is real
(`lumen_state` quote above, confirmed). The defect is narrower than "875 is wrong": it's
that the author split the ticket's requested range in isolation and never checked the
result against its immediate table neighbor (1600/800), which is the one check that would
have caught it — the same kind of validation the author did correctly apply elsewhere in
this same file (e.g. deferring the IOMMU conflict instead of guessing).

## `--verify` handler gap (per this ticket's own text)

Both `ops/wq1337-hapi-bringup` and `ops/wq3651-bc250-bios-flash-governor` cite no
`--verify` handler today (confirmed: neither directory has an `install.sh` at all).
They are not equivalent gaps:

- **`wq3651-bc250-bios-flash-governor`** ships zero installable files — the one file it
  added is prose (a runbook addendum). There is no live/not-live state for a markdown
  file beyond "is it merged", which `git log` already answers. A `--verify` handler here
  would have nothing real to check (`vfile` against itself is vacuous) — this is
  `ops-deploy-drift`'s own `NOTHING` classification ("ships no installable files
  (docs/diffs only) -> needs a human look"), not a hidden deploy gap. Recommend judgment
  rule on whether this bundle should even carry a handler, rather than have one
  fabricated to satisfy the checklist.
- **`wq1337-hapi-bringup`** does ship a real future artifact (`llama-server.service`,
  meant for `hapi-1`, not Lumen) and is the correct target for a handler — this is
  exactly `ops/VERIFY_CONTRACT.md`'s "Bundles that do not install onto Lumen" pattern
  (`vremote_up`/`vremote_file`/`vremote_unit`, mirroring `wq2286-gitea-secret-scan`).
  Drafted, not landed (see below), since this ticket's precedent
  (`reports/wq2932_verify_wq2910_undeployed_ops_bundle.md` /
  `reports/2943_judgment_wq2910_undeployed_ops_bundle.md`) has the verifier write the
  report only and the escalated judgment ticket land the actual handler fix — doing the
  same here rather than diverging from the established split of labor.

Proposed handler for the judgment ticket to land at
`ops/wq1337-hapi-bringup/install.sh`:

```bash
#!/usr/bin/env bash
# ops/wq1337-hapi-bringup/install.sh — deploy-verify contract (ops/VERIFY_CONTRACT.md).
# No automated install path: hapi-1 bring-up is manual/hands-on and hardware-gated
# (bench, pre-thermal-pads). Payload lands on hapi-1, not Lumen, so a local-only check
# would report it absent however live it is (VERIFY_CONTRACT.md "Bundles that do not
# install onto Lumen"). Unreachable host is ERR (exit 2), never MISS, today.
set -euo pipefail
SRC="$(cd "$(dirname "$0")" && pwd)"
HOST=hapi-1
UNIT=llama-server.service

if [ "${1:-}" = --verify ]; then
  . "$SRC/../_verify.sh"
  vremote_up "$HOST" || vdone
  vremote_file "$HOST" "$SRC/$UNIT" "/etc/systemd/system/$UNIT"
  vremote_unit "$HOST" "$UNIT"
  vdone
fi

echo "No automated install: hapi-1 bring-up is a manual, hands-on procedure." >&2
echo "Follow RUNBOOK.md (this directory) plus wq#3651's RUNBOOK-ADDENDUM.md on the box" >&2
echo "once physical bring-up is done." >&2
exit 1
```

Not executed in this session: this worker's Bash allowlist
(`harness/worker-settings.json`) has no entry for arbitrary script execution or raw
`ssh` (confirmed refused: `/home/lumen/worktrees/wq3699-a1/ops/wq1337-hapi-bringup/install.sh
--verify` → "This command requires approval"; `ssh -o BatchMode=yes -o ConnectTimeout=5
hapi-1 true` → same). Per protocol this is not retryable; naming it here so the
allowlist gap is visible rather than burning attempts around it. The judgment lane
(opus-5@high) had broader capability in the wq#2943 precedent (`lumen-run --root`) and
may here too.

## Escalation

Per protocol, a contradiction is a judgment trigger. Filed
[judgment] wq#3651 BC-250 governor safe-points voltage dip, claude-opus-5@high, to rule
on: (1) whether wq#3651 should be reopened/reclosed or the fix discharged as a follow-up
edit to `RUNBOOK-ADDENDUM.md` (reopening buys little — nothing is live, hapi-1 still
doesn't exist to be affected yet, same reasoning as the wq#2943 precedent's Ruling 1);
(2) the correct replacement value/position for the 1500MHz entry (does it need to sit
*below* 800mV to stay monotonic against 1600/800, or should 1600's own voltage rise
instead, given the 900mV sustained-point data suggests 1600MHz may need revisiting too —
this is a question about the real card's thermal/stability margin, deferred to `bench.sh`
per the addendum's own text, but the shipped number should not be left non-monotonic in
the meantime); (3) whether to land the proposed `--verify` handler above for
`wq1337-hapi-bringup`; (4) whether `wq3651-bc250-bios-flash-governor` needs a handler at
all given it ships no installable files.

## Evidence commands (read-only, re-runnable)

```
git -C /home/lumen/foreman show --stat 7b3b5a42
git -C /home/lumen/foreman show 7b3b5a42 -- ops/wq3651-bc250-bios-flash-governor/RUNBOOK-ADDENDUM.md
sed -n '147,207p' /home/lumen/foreman/ops/wq1337-hapi-bringup/RUNBOOK.md
sed -n '47,99p' /home/lumen/foreman/ops/wq3651-bc250-bios-flash-governor/RUNBOOK-ADDENDUM.md
/home/lumen/bin/wq show 3651 --json
python3 /home/lumen/minerva/lumen_state.py current hapi-1
grep -n '^| 1236 ' /home/lumen/kb/ISSUE-LOG.md
grep -n 'akandr/bc250-rocm' /home/lumen/agents/work-queue/reports/1018_bc250_community_watch.md
```
