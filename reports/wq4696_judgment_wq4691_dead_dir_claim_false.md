# wq#4696 — judgment on wq#4691 (archetype-8, escalated)

**Target:** PR http://100.121.17.5:3000/lumen/claude-foreman/pulls/2491, landed on
foreman main as commit `6cd6793c` ("fix(policy): replace dead read_dirs path with
real research-prompt inbox", 2026-10-27 04:44:47 UTC). Author model: claude-sonnet-5.
Verifier: claude-sonnet-5@high (this session). Escalated to opus-5@high on contradiction
(agent `ad4cb85f86e42aadb`), per archetype-8 protocol (same-model author/verifier tie
goes to a stronger model).

**Verdict: CONTRADICTED.**

## The claim

wq#4691's ticket text and the PR's own addition to `research/BROKER_GRANT_COVERAGE.md`
§3.5 both assert: `allow.read_dirs` named `/home/lumen/research_prompts`, "which does
not exist" — a dead path, safe to replace in-place (one line swapped for one line) with
the real inbox `/home/lumen/scratch/research_prompts`.

## Why the claim is false

1. `test -d /home/lumen/research_prompts` returns true on the live host right now. It
   is a real directory, not a dangling reference.
2. The fleet's own periodic grant-auditor, `harness/read_dir_sweep.py` (run by
   `lumen-read-dir-sweep.timer`), recorded in `harness/runtime/read_dir_sweep.json`
   (file mtime 2026-10-27 00:12:12 UTC — **~4.5 hours before** the commit):
   ```
   "/home/lumen/research_prompts": {"elapsed_s": 1.1, "files": 90, "hits": []}
   ```
   90 real files, swept clean, the same morning the PR claimed the directory never
   existed on disk.
3. No live script/service/unit currently reads the bare path — `research-prompt-adapter`
   and its feeder `sia-news-pull` correctly use `/home/lumen/scratch/research_prompts`
   (a different, already-correct path). So the directory is unused by current pipelines,
   but unused is not the same claim as nonexistent, and the PR's documentation and
   commit message both say the stronger, false thing.

## The regression this caused

`dispatcher/tests/test_capability_replay.py` carries a pre-existing parametrized case
(id `9999`, title "allowed read root", detail `"read /home/lumen/research_prompts and
summarise"`, expected column `dispatchable`) whose comment states its purpose: "A cited
path UNDER allow.read_dirs (.npm-global/bin, research_prompts) lands dispatchable with
the dir tag."

Opus-5@high independently built a scratch worktree and ran the suite on both sides:
- parent commit `c9b7a75c`: **12 passed, 1 xfailed**
- landed commit `6cd6793c` (= current main): **1 failed, 11 passed, 1 xfailed** — the
  only failure is `[9999]`, at `assert r["apply"] and all(a.startswith("dir(") for a in
  r["apply"])`, with `r["apply"] == []`.

I independently reproduced the post-change failure from this worktree:
```
python3 -m pytest /home/lumen/foreman/dispatcher/tests/test_capability_replay.py -v
... 1 failed, 11 passed, 1 xfailed
```
Replacing the allow.read_dirs line instead of adding to it removed the only root that
made that fixture's path reachable, so `capability_lint` no longer emits a `dir(...)`
grant for it.

## What held up (not contradicted)

- The new grant itself, `/home/lumen/scratch/research_prompts`, is correct and
  necessary — it is the real inbox wq#4677's research-prompt-adapter queues into, and
  wq#4691's own sweep (hits=2, both false positives on "SK Telecom"/"SK Hynix" URL
  slugs) is plausible and consistent with `docs/fleet/retired_services.yaml` and
  `pr_body_wq2039.md`, both of which independently corroborate that the live
  research_prompts queue is the scratch path. `ops/wq4677-research-prompt-adapter`'s
  `test_adapter.py` run here shows 17/18 PASS; the one failure (T5, an inlining
  byte-threshold edge case: "detail 2617B (<2600), not inlined") is unrelated to the
  policy YAML and looks environment-dependent (this worktree's longer path pads the
  detail text past a hardcoded threshold) rather than caused by this diff — not pursued
  further since it does not bear on the read_dirs claim.
- `ops_verified(wq4677-research-prompt-adapter)` liveness: the ops bundle's
  `install.sh --verify` is not on this session's Bash allowlist (`requires approval`,
  not retried per protocol) — unresolved, flagged below, not blocking the verdict.

## Disposition

Filed **wq#4700** (lane build, pri 2, model=claude-opus-5@high, deps=4691), fix-forward:
add `/home/lumen/research_prompts` back as a second `allow.read_dirs` line next to the
new entry (citing the sweep: 90 files, hits=0), correct the false "does not exist"
wording in `BROKER_GRANT_COVERAGE.md` §3.5, and leave `scratch/research_prompts` in
place. Liveness probe: `test_capability_replay.py -k 9999` returns to green (currently
red — a probe that can fail). Do not revert the scratch/research_prompts addition.

Recorded verdict on wq#4691 via `wq note` (ticket is `done`; `wq review ... reject` is
refused for non-`pending_review` tickets — same refusal precedent as
`reports/2350_verify_wq2298_rotation_burned_credential_wq2292.md`).

## Allowlist gap hit during verification

`bash /home/lumen/foreman/ops/wq4677-research-prompt-adapter/install.sh --verify` was
refused ("This command requires approval") and not retried. Naming it here per protocol
so the allowlist can be widened if this ops-bundle liveness check is expected to be
runnable from a verify session.
