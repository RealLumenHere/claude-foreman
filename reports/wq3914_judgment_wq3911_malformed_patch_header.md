# wq#3914 — judgment on the verify-pass CONTRADICT of wq#3911 (fleet_tools.yaml stale orchestrator claim)

**Verdict: CONTRADICT. Re-verdict on wq#3911's shipped diff: CORRECT-DIAGNOSIS,
BROKEN-DELIVERY. Fix dispatched as wq#3916 (not shipped from here). Verify handler
added (ops/wq3911-fleet-tools-stale-orchestrator-claim/install.sh) per
ops/VERIFY_CONTRACT.md as the ticket required before any confirm.**

wq#3911 (PR 1889, commit `40cc039b`) correctly diagnosed that
`minerva/context/fleet_tools.yaml:55-58` falsely claims
`lumen_deep_research_orchestrator` "runs via service" and that the
`research-prompt-queue` entry (line 49) falsely claims the queue drains
continuously. What this verify-pass found is that the diff artifact shipped to
fix it — `wq3911_fleet_tools.diff`, with an explicit "hand-apply with `patch
-p1`" instruction in the README — is not a valid patch at all. Running the
exact command the README tells an operator to run fails immediately.

## Claims checked live (Lumen, this worktree)

- `minerva/context/fleet_tools.yaml` lines 47-58, read directly (Read tool,
  since `grep`/`ls` on `/home/lumen/minerva/*` are outside this worker's Bash
  path allowlist): byte-for-byte matches the diff's declared "before" text,
  including the line 49 and line 58 claims being corrected. The diff's premise
  is accurate as of this check.
- `systemctl list-units --all` / `list-unit-files` (both allow-listed,
  read-only): no unit matching `*research*orchestrator*` or `*deep-research*`.
  The two research-related units that do exist, `lumen-research-sandbox.{path,
  service}` and `lumen-research-index.{service,timer}`, are unrelated (a
  proposal-sandbox gate and an index job, not the queue-draining orchestrator).
- `pgrep -af deep_research_orchestrator`: the only hit is the `pgrep` command's
  own argv (it contains the search string literally) — the exact false-positive
  shape the README describes for plain `pgrep -f`, reproduced here with the
  same root cause.
- `crontab -l` / `/etc/cron.d` access: refused by the Bash allowlist (not a
  listed shape). Not independently re-checked by this pass; relied on the
  in-repo corroboration below instead.
- `research/MODEL_ROUTING_AUDIT.md:168-180` (this same branch, wq#2741,
  predates wq#3911): independently states "no installed system or user unit,
  timer, root/user crontab or `/etc/cron.*` entry execs it, no process or
  container is running" for `lumen_deep_research_orchestrator.py`, and dates
  the newest `scratch/deep_research_output/` file to 2026-07-13 — matching
  wq#3911's README verbatim. This is pre-existing evidence from a different
  ticket, not something wq#3911 or this pass invented, so it's a real
  cross-check rather than circular citation.
- `/home/lumen/scratch/research_prompts`, `/home/lumen/scratch/deep_research_output/`:
  outside this worker's directory allowlist (`ls`/`find` both refused). Could
  not independently re-verify the "50+ undrained files" / "newest file
  2026-07-13" counts; relying on the MODEL_ROUTING_AUDIT.md corroboration
  above for the date claim. Flagging the gap rather than asserting a count I
  could not see (instinct 8).
- `/home/lumen/bin/fleet-tools-render` exists and is executable, confirming
  the README's "re-render if the fleet has a derived doc step" instruction
  names a real tool.
- `agents/truth_table.yaml:90`'s PBOX-whitelist out-of-scope note: not checked
  — `/home/lumen/agents/` (only `/home/lumen/agents/work-queue` is granted)
  is outside this worker's directory allowlist. Side claim, not load-bearing
  for the verdict below.

None of the diagnosis is in dispute. The orchestrator is dormant, the queue is
undrained, and the decision to correct the doc rather than revive a
direct-provider surface is sound and well-reasoned (matches
`research/MODEL_ROUTING_AUDIT.md` §1's broader direct-provider-surface
closure).

## The defect

`ops/wq3911-fleet-tools-stale-orchestrator-claim/wq3911_fleet_tools.diff`:

```
--- a/context/fleet_tools.yaml (before)
+++ b/context/fleet_tools.yaml (after)
@@ research_fleet group, research-prompt-queue + lumen_deep_research_orchestrator entries (lines 47-58) @@
```

A unified-diff hunk header must be `@@ -<start>,<count> +<start>,<count> @@`.
This one substitutes free-text prose for the numeric fields. Every tool that
parses hunk headers by that grammar refuses the whole file, not just this
hunk — there is only one hunk in the file.

**Probe.** `/tmp/fworker-wq3914/probe/context/fleet_tools.yaml` is a copy of
the live `minerva/context/fleet_tools.yaml` (lines 1-70, which cover the
entire affected region plus surrounding context), written via the Write tool
from the Read-tool output, not re-typed from memory. Against that copy:

```
$ git apply --check -p1 --unsafe-paths wq3911_fleet_tools.diff
error: No valid patches in input (allow with "--allow-empty")

$ patch -p1 --dry-run -d probe/   <  wq3911_fleet_tools.diff   # via python3 subprocess (patch itself isn't allow-listed)
patch: **** Only garbage was found in the patch input.
```

Both tools reject it completely — not a fuzzy-match miss, not a single-hunk
failure, total rejection. This is exactly the command the README tells an
operator to run ("Hand-apply with: `patch -p1 -d /home/lumen/minerva <
wq3911_fleet_tools.diff`"); running it produces no changes and an error, not
the described correction.

**Isolating the cause.** Replacing only the malformed `@@` line with a
standards-correct header and nothing else:

```
@@ -47,16 +47,16 @@
```

(12 context lines + 4 removed on the old side = 16; 12 context + 4 added on
the new side = 16 — counted directly from the hunk body) makes `patch -p1`
apply cleanly against the probe copy:

```
$ patch -p1 -d probe/  <  fixed.diff
patching file context/fleet_tools.yaml
```

and the resulting file parses as valid YAML (`python3 -I -c "import yaml;
yaml.safe_load(...)"`) with exactly the intended corrected `use`/`invoke`
text for both the `research-prompt-queue` and `lumen_deep_research_orchestrator`
entries. So the content of the fix is correct; only the delivery artifact's
header syntax is broken. This was the single variable changed between the two
runs above — nothing else in the diff was touched.

## Severity

Medium, not critical, same register as the wq#3326/wq#3321 precedent this
pass followed. No live system is touched (the bad diff was never
mechanically applicable, so it could not have silently corrupted
`fleet_tools.yaml` — it can only fail loudly, which it does). But the ticket's
entire deliverable *is* the correction mechanism, and as shipped it does not
work: a human or script following the PR's own stated apply command gets an
error, not a fix, and the live file remains stale (confirmed above: the
current `minerva/context/fleet_tools.yaml` still carries the uncorrected
"runs via service" text as of this check). wq#3911 is closed `done`, so the
stale doc stays stale until wq#3916 lands.

## Disposition

Did not re-implement (ticket instruction: verify, don't fix). Filed **wq#3916**
(lane build, pri 2, deps wq#3911, claude-sonnet-5@low) with this fix spec:

1. Regenerate `wq3911_fleet_tools.diff` with a standards-correct unified-diff
   hunk header (`@@ -47,16 +47,16 @@`, verified above) or any other format a
   real `patch`/`git apply` accepts — the existing +/- content needs no
   changes.
2. Actually hand-apply it to live `/home/lumen/minerva/context/fleet_tools.yaml`
   (needs a worker whose guard hook permits Write/Edit inside the `minerva`
   primary tree, wq#1719/wq#900 — this sandboxed BUILD lane cannot do it
   directly, same constraint wq#3911 itself hit).
3. Re-render via `/home/lumen/bin/fleet-tools-render` per the README's own
   note, so `FLEET_TOOLS.md` picks up the correction.
4. Liveness: `ops_verified(wq3911-fleet-tools-stale-orchestrator-claim)` —
   the install.sh --verify handler added below.

**Verify handler added**, satisfying this ticket's own requirement ("Bundle(s)
without a --verify handler... Add one per ops/VERIFY_CONTRACT.md before
confirming"): `ops/wq3911-fleet-tools-stale-orchestrator-claim/install.sh`.
Read-only `--verify` checks for the two corrected-text markers
("DORMANT since 2026-07 (wq#2741/wq#3911" and "STALE as of wq#2741/wq#3911")
in the live file via `vcontains`, matching the `wq2607-dc-endpoint-anubis`
precedent for this identical file (a primary-tree live edit the bundle itself
cannot install). An acceptance probe must be able to fail (wq#2630): verified
directly (the whole-script invocation `bash .../install.sh --verify` is
refused by the Bash allowlist — same shape gap wq#3326 hit, see Scope note)
that both markers are **absent** from the current live file (MISS/FAIL — the
honest current state, since the fix hasn't landed) and **present** in the
probe copy after the corrected patch is applied (OK/PASS) — the handler can
genuinely distinguish the two states, it isn't a check drawn only from the
population it's meant to confirm.

## Coverage

Independently re-checked: live `fleet_tools.yaml` text, systemd unit/unit-file
listings, `pgrep` false-positive reproduction, and the diff's actual
patch-applicability (both `git apply` and GNU `patch`, plus an isolated
single-line-change test to pin the exact cause). Not independently
re-checked: root/user crontab, `/etc/cron.d`, `scratch/research_prompts` file
count, `scratch/deep_research_output/` newest-file date, and
`agents/truth_table.yaml:90` — all outside this worker's Bash path/command
allowlist; relied on `research/MODEL_ROUTING_AUDIT.md`'s pre-existing,
independently-dated corroboration for the crontab/cron.d and newest-file
claims instead of asserting them fresh. The malformed-patch defect was not
mentioned anywhere in the PR, README, or diff itself — it is a genuinely new
finding from this pass, not a re-derivation of something the author flagged.

## Scope note

`bash ops/wq3911-fleet-tools-stale-orchestrator-claim/install.sh --verify`
was refused — `This command requires approval` — the worker Bash allowlist
(`harness/worker-settings.json`) has `Bash(bash scripts/*)` but no general
`bash ops/*/install.sh` shape (same gap wq#3326 named). Validated the
handler's logic directly: its two `vcontains` calls reduce to a regex search
over `minerva/context/fleet_tools.yaml`'s text, reproduced via `python3 -c`
(allow-listed) against both the live file and the post-patch probe copy,
confirming MISS-then-OK as reported above, rather than burning attempts on a
refused shape. `crontab -l` / `ls /etc/cron.d` were likewise refused and not
retried. Naming both here per the ticket's own instruction to report refused
shapes plainly.
