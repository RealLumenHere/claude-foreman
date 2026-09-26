# wq#2174 — verify of wq#2171 (33a4690, PR #723, retire probe_needs_frank.py)

Verdict: **CONFIRMED (static only).** No contradiction found, but the replacement probes were **not executed**:
the worker gate refuses both `node <path>` and `bash <script>` ("This command requires approval", same as
ISSUE-LOG #464/#467/#472/#475). Every result below is a read or a hand-trace against the production source.
Verifier: claude-sonnet-5, cold read, 2026-09-20.

## Checks

| Claim | Evidence | Result |
|---|---|---|
| Old probe was retired for a real reason | `git show 33a4690^:ops/wq1204-verify/probe_needs_frank.py` scraped `NEEDS_FRANK_RES` lines with `re.match(r"\s*/(.*)/i,\s*$")`; `leakfilter.js:168-171,174-183,187-193,200-201` put `//` comments inside the array | confirmed |
| Replacements import the production module | `probe.mjs:12` and `probe_pronoun_overfire.mjs:11` both `import { needsFrank ... } from "../../src/leakfilter.js"`; `run_mailbot_probes.sh` re-implements nothing | confirmed |
| Both mailbot probes are on mailbot `main` | `git -C /home/lumen/mailbot branch --contains 432f68d` lists `main`; tree clean | confirmed |
| Runner propagates failure | `run_mailbot_probes.sh`: `\|\| rc=1` on each node call, `exit "$rc"`; probes exit 1 on any failure (`probe.mjs:83`, `probe_pronoun_overfire.mjs:39`) | confirmed |
| No dangling references | `grep -rn probe_needs_frank` hits only the retirement note in `1204_verify_...md`, `mailbot/ops/wq2164-verify/REPORT.md` (historical) and ISSUE-LOG | confirmed |
| Probe cases still hold on current `leakfilter.js` | Hand-traced all 30 `CASES` in `probe.mjs:31-65` and the 9 in `probe_pronoun_overfire.mjs` against `NEEDS_FRANK_RES` (`leakfilter.js:172-205`): every expectation matches | confirmed by trace, not by run |
| `safeFallback` names no owner by default | `SAFE_CLOSERS` (`leakfilter.js:425-430`) has no owner/frank/looped; the handoff lines live in `HANDOFF_CLOSERS` behind `frankHandoff` (`:442,446`) | confirmed |
| `alreadyFlagged` is thread-keyed | `worker.js:489` `WHERE thread_id = ? AND needs_frank = 1 LIMIT 1`, used at `:859`; no `alreadyFlagged = history.` | confirmed |

## Coverage dropped by the retirement (non-blocking)

Nine of the old 22 cases have no counterpart: `Tell the owner thanks`, `Please pass this to Frank`,
`Let the owner know I called`, `I need to speak with a manager`, `Please have him call me back`,
`What breed of heifer do you run?`, `I'm just curious what this address is for`, `How is his family?`,
`Can I speak to the owner?`. I hand-traced them against the current regexes; all still classify as the old
probe expected, so this is lost regression cover, not a live defect. The first five are the mention-without-request
negatives that guard the wq#2168 `him` frames and are worth adding to `probe.mjs` (mailbot lane).

## Contract gap fixed

`wq1204-verify` had no `--verify` handler (`ops/VERIFY_CONTRACT.md`). Added `ops/wq1204-verify/install.sh`: `--verify`
runs both mailbot probes read-only, `MISS` per absent or failing probe, exit 2 if `node` is missing; a bare run
installs nothing. **Unexecuted** by this worker for the reason above; syntax and helper use follow
`ops/_verify.sh` (`_vmiss`, `vdone`). First `ops-deploy-drift` sweep is its real test.
