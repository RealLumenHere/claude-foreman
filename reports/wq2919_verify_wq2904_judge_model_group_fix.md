# wq#2919 — verify: wq#2904 judge model-group fix (wq2821-judge-llama8b)

Verifier: claude-sonnet-5@high, worktree `wq2919-a2`, 2026-09-28, fresh instance. No
credentials read or constructed.
Target: PR http://100.121.17.5:3000/lumen/claude-foreman/pulls/1225, commit `2ece36b`
("fix(judge): register wq2821-judge-llama8b + referenced-model lint"), confirmed on `main`
(`git merge-base --is-ancestor 2ece36b HEAD` → true, HEAD `97d3ca5`).

## Verdict — CONFIRM

wq#2904's diff does what it claims and holds up under an adversarial read, independent
re-execution of its tests, and a live re-run of its own acceptance probe. No contradiction
found. Escalation to opus-5@high not triggered.

## What the diff ships (in-repo, all on `main`)

- `judge/judge_common.py::verify_model_registered(model, key=None, timeout=10)` — GETs
  `{LITELLM_BASE}/models`, returns `None` if `model` is in the id set, else an error string
  naming the model; never raises (catches key-load and network failures too).
- `judge/judge_bench.py::main()` — calls `verify_model_registered()` for every `--models`
  entry *before* the run loop; prints one `ERROR:` line per miss and `sys.exit(1)` on any
  failure, replacing the prior behavior (a missing model degraded into N per-case
  `[PARSE_FAIL: ...]` prints with no page — how minerva-bench-2 burned 169 silent calls
  over 5 days, fleet-audit-2, 2026-09-28).
- `judge/JUDGE_MODEL_IDENTITY.json` — new `wq2821-judge-llama8b` entry, identical
  ollama/tag/blob_sha256 to the existing `hathor-llama8b` entry (same physical backend,
  distinct alias — correct, since the snippet below wires both names to the same rung).
- `judge/test_referenced_model_lint.py` — 4 unit tests (registered passes, missing model
  fails loud with the name in the message, unreachable gateway fails loud, missing key
  fails loud).
- `ops/wq2904-judge-model-group-fix/{README.md,litellm_config_snippet.yaml}` — the
  gateway-side merge fragment (not deployed by the diff itself) plus deploy checklist.

## Independent checks run this session

**Unit tests, re-run from scratch:**
```
$ python3 -m pytest judge/test_referenced_model_lint.py -v
4 passed
$ python3 -m pytest judge/test_judge_identity.py judge/test_judge_ensemble.py -v
8 passed   # adjacent judge suites, no regression from the JUDGE_MODEL_IDENTITY.json edit
```

**Code read, adversarial pass.** Traced `verify_model_registered`'s three failure paths
(key-load exception, urlopen exception, model absent from `{id}` set) against the four
tests — each path has a dedicated test and the error-string content the tests assert on
matches what the function actually returns. `judge_bench.py::main()` calls `litellm_key()`
once up front (existing "fail loud now, not N calls in" convention), then
`verify_model_registered()` per model — the latter re-derives the key internally per call
since none is threaded through from `main()`; redundant (one extra local key-file read per
model, not a network call) but not a correctness bug — noting it, not flagging it.
`DEFAULT_MODELS` is unchanged, so this adds one gateway round-trip to every `judge_bench.py`
invocation, not just `--models wq2821-judge-llama8b` runs — deliberate, matches the ticket's
"referenced-model lint" ask.

**Acceptance probe, re-run live (the README's own "ticket's explicit ask" step 5):**
```
$ python3 judge/judge_bench.py --models wq2821-judge-llama8b
eval set: 155 cases from /home/lumen/foreman/judge/judge_eval_set.jsonl
ERROR: wq2821-judge-llama8b: model 'wq2821-judge-llama8b' is not registered on the LiteLLM
gateway (http://127.0.0.1:4000/v1/models) -- add it to litellm_config.yaml's model_list
before referencing it (wq#2904 class)
```
Exit 1, one `ERROR:` line naming the model, no case ran — exactly the pre-fix-reproduction
half of the README's step 5 ("run once *before* step 2 ... pre-flight can fail on the very
input it was built for"). This is **not** a contradiction of the PR: the README states in
its own words that the live gateway merge (steps 1-4) is an operator action outside any
worker sandbox's reach (`/home/lumen/agent` is a `guard.sh` PRIMARY_TREES root — confirmed
this session too, a direct `Grep` on `/home/lumen/agent/litellm_config.yaml` from this
worktree was refused as outside the session's allowed directories, same wall wq#2904's
author hit on `Edit`). The diff's own claim is "code ships correct and tested, live deploy
is a documented follow-up" — that is exactly the state found. minerva-bench-2's actual
164-and-counting `ProxyModelNotFoundError` calls will keep failing until an operator runs
the Deploy checklist; that is a real open gap, but it is wq#2904's documented Follow-up
section, not an undisclosed defect in the diff.

## Bundle without a --verify handler — resolved

`ops/wq2904-judge-model-group-fix/` shipped `README.md` + `litellm_config_snippet.yaml`
only, no `install.sh` — per `ops/VERIFY_CONTRACT.md` a repo-only check would not count.
Added `ops/wq2904-judge-model-group-fix/install.sh`: a `--verify` handler (before any
install step, there being no automated install step beyond the manual checklist — same
shape as `wq1658-cron-stamp-dropin/install.sh`) that `vcontains`-checks
`/home/lumen/agent/litellm_config.yaml` for the `model_name: wq2821-judge-llama8b` marker.

Did not exercise it against the real live path — `litellm_config.yaml` carries live API
keys for other `model_list` entries (wq2627/wq1406/wq1590 siblings), and instinct 4 (mask
before print) applies to *exercising a check against* a credential-bearing file, not just to
printing one outright; `vcontains` itself only greps quietly, but the safer and sanctioned
route is the one the diff's own acceptance probe already uses (an HTTP call to `/models`,
which returns ids only, never keys). Instead added
`ops/wq2904-judge-model-group-fix/test_verify_handler.py` (wq#2359/wq#2817 pattern:
`subprocess.run(["bash", ...])` under `python3 -m pytest`, staged against a scratch
`tmp_path` copy so the handler's own file-path constant is swapped, never the real one) —
4 tests, all passed:

```
$ python3 -m pytest ops/wq2904-judge-model-group-fix/test_verify_handler.py -v
test_handler_precedes_any_install_step PASSED
test_verify_fails_when_the_alias_is_absent PASSED
test_verify_fails_when_the_config_file_is_missing PASSED
test_verify_passes_once_the_snippet_is_merged PASSED
```
The pass case stages the bundle's own committed `litellm_config_snippet.yaml` (a pinned
fixture, not a live mutable file — wq#2630) rather than a hand-typed marker string, so the
probe can fail on real drift between the snippet and what `--verify` checks for.
`test_verify_fails_when_the_alias_is_absent`/`..._config_file_is_missing` are the
falsifiability half (wq#2630): both reproduce today's actual live state (config exists,
alias absent) and correctly report `MISS`/`VERIFY FAIL`, exit 1.

Run against the real box, this handler would report `VERIFY FAIL` right now — consistent
with the live acceptance-probe result above. Once an operator completes the Deploy
checklist, `ops-deploy-drift`/`ops_verified(wq2904-judge-model-group-fix)` will flip to
`VERIFY OK` without any further code change.

## Notes

- Did not re-implement or modify `judge_common.py`, `judge_bench.py`,
  `JUDGE_MODEL_IDENTITY.json`, `litellm_config_snippet.yaml`, or `README.md`.
- Follow-up gaps the PR already discloses (not re-litigated here): `bin/judge` (lumen-bin
  repo) doesn't yet call `verify_model_registered()`; minerva-bench-2's own caller (outside
  every worktree's `--add-dir` reach so far) hasn't been read to confirm it has no separate
  hardcoded copy of the model name.
