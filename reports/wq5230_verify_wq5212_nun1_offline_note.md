# wq#5230 — verify-pass of wq#5212 (PR #2700, merge 4b2e86a9)

Verdict: **CONFIRM**. The diff is one `selfheal.yaml` `nodes.notes.nun1` text change (+18/-1). Every factual claim in it was re-checked cold on 2026-11-22 ~05:03Z. One non-blocking gap in the follow-up gate is noted below.

## What shipped
`ops/wq3354-fleet-selfheal/selfheal.yaml` nodes.notes.nun1 gets a wq#5212 paragraph appended: the drop is not self-resolved, it is a new fault rather than the ACPI-S5 hang, wq#5225 asks for the physical fix, the judge falls back while nun1 is down, and it says how to dedupe the next page. Nothing else changed: no code, and nun1 is still in neither nodes.ignore nor nodes.sleepers.

## Claim-by-claim check
| Claim | Evidence (re-run by verifier) | Result |
|---|---|---|
| YAML stays valid; note is a string | `yaml.safe_load` → `nodes.notes.nun1` str, 2514 chars; nun1 is in neither ignore nor sleepers | OK |
| Last packet ~13:46:42Z, first miss 13:47:31 lastRecv=49s | `journalctl -u tailscaled` 13:47:31 `timeout opening (TCP …=> 100.122.44.81:22) … lastRecv=49s` (49s before = 13:46:42) | OK |
| Dark on tailnet and LAN | Live TCP probe 05:02Z: 100.122.44.81 :22/:11434 timed out; 192.168.8.150 :22/:11434 `OSError 113 No route to host` | OK (still dark) |
| No worker event naming nun1 before the drop | queue.db: no task claimed or done 2026-11-21 12:30–13:50Z; none mentioning nun1/BC-250 10:00–14:10Z. Episode recall "nun1": no restart or power episode on 11-20/21 | OK |
| wq#5190: 86.8C Tctl, recheck never ran | validate_qwen3_14b.log: `max_temp=86.8C … VERDICT FAIL`, then `THERMAL RECHECK GAVE UP: nun1 never idle 10 min within 8h` | OK |
| wq#5225 is [physical], frank_gated, and asks for /tmp/nun1-prevboot*.txt | wq show 5225: status frank_gated; the detail writes /tmp/nun1-prevboot.txt and -kern.txt | OK (the note's glob matches) |
| Judge falls back to lumen-qwen25coder7b | judge/JUDGE_MODEL_PIN=nun1-qwen25coder7b, FALLBACK=lumen-qwen25coder7b. Live `resolve_judge_model()` → `('lumen-qwen25coder7b','fallback:…',"primary rung 'nun1-qwen25coder7b' unreachable…")` | OK |
| 0 fail_closed_queue rows since the drop | fail_closed_queue.jsonl: 43 rows, newest 2026-11-19T06:11:58Z | OK |
| ISSUE-LOG #1687 recorded | kb/ISSUE-LOG.md:1836 row 1687 present | OK |
| Note reaches the next page | sh_nodes.py:185-187 adds `known issue: {note}`. wq#5212's stored detail was 2045 chars with no truncation (wq add sends the detail in full; only the title is capped at 180) | OK |
| Selfheal suite | `pytest -q ops/wq3354-fleet-selfheal/tests` → 86 passed, 1 skipped | OK |
| Landed | 4b2e86a9 is on main and origin/main | OK |

## Probe (can fail): does the seed merge carry the new text live?
`selfheal_merge.py --check --seed <4b2e86a9 yaml> --live <4b2e86a9^ yaml>` → rc=0 with no output. The merge only compares key paths, so a changed value under an existing key looks like "no drift". This matches the author's statement that the live note needs a manual copy (wq#5229).

## Non-blocking finding
`install.sh --verify` (install.sh:41-51) runs that same key-path `--check`. That makes it **structurally blind to this change**: it passes whether or not the live `nodes.notes.nun1` was updated. Two things follow:
- The `[OPS-DEPLOYED: … --verify OK]` stamp on wq#5212 is not evidence that the note is live.
- wq#5229's liveness `ops_verified(wq3354-fleet-selfheal)` can go green without the copy ever happening.

The real check for wq#5229 is grepping `wq#5212` in `/home/lumen/agents/fleet-selfheal/selfheal.yaml` (that path is outside this worker's dirs, so it is not checked here). Impact while the copy is pending is small: the next nun1 page carries the wq#5000 note, which still says "no reboot before LastSeen → new fault", so it costs at most one redundant triage. Not a defect in the PR.
