# wq#2904 — register wq2821-judge-llama8b in litellm_config.yaml + referenced-model lint

fleet-audit-2 (2026-09-28): `LiteLLM_SpendLogs` shows key alias `minerva-bench-2`
calling `model=wq2821-judge-llama8b` → `ProxyModelNotFoundError`, 169 rows in 5d,
last 2026-09-28 04:23Z. wq#2821 (`judge/judge_common.py` commit `67987ef`) pinned
the central judge's identity check to the existing `hathor-llama8b` rung
(`judge/JUDGE_MODEL_PIN`, `judge/JUDGE_MODEL_IDENTITY.json`) but never registered
a `wq2821-judge-llama8b` model_name in `litellm_config.yaml` — whatever caller
minerva-bench-2 wraps (outside this worktree's `--add-dir` reach; could not be
read directly from this sandbox) has been requesting a name that was never wired
to a backend.

**Investigation finding (ticket's "fail-closed or no-verdict" question):**
`judge/judge_common.py::call_judge()` catches the gateway's error and returns it
as an ordinary per-case `error` field — `judge/judge_bench.py::run_model()` then
just prints `[PARSE_FAIL: ...]` and keeps going. That path pages nobody. Only
`/home/lumen/bin/judge` (a separate repo, lumen-bin, not in this worktree) treats
`verdict is None` as FAIL_CLOSED — logs to `fail_closed_queue.jsonl` and pages
`aq` lane `judge`. minerva-bench-2's calls are the silent "no verdict" path, not
the fail-closed one, which is exactly how 169 failures went unnoticed for 5 days.

## Components

| File | Deploy target | Purpose |
|---|---|---|
| `litellm_config_snippet.yaml` | merge into the live proxy `config.yaml` | registers `wq2821-judge-llama8b` as an alias for the existing hathor-llama8b backend (llama3.1:8b, ollama, `http://100.80.71.13:11434`) |
| `../wq1295-litellm-health-hardening/backup_and_verify.sh` | run on the gateway node | reused as-is (`backup` before merge, `verify` after restart) — see that ticket's README, same convention as wq#897/wq#911/wq#948/wq#1406/wq#1590/wq#2627 |

Landed in this same branch (in-repo, not gateway-side, so no hand-merge needed):

- `judge/judge_common.py` — new `verify_model_registered(model, key=None, timeout=10)`: GETs `{LITELLM_BASE}/models` and fails loud (returns an error string, never raises) if the model isn't in the gateway's list. This is the referenced-model lint asked for in the ticket, in the spirit of wq#1648's `check_judge_config()` (`/home/lumen/services/langfuse-ops/evaluator.py`, not version-controlled) but landed in the git-tracked judge code this repo actually owns.
- `judge/judge_bench.py::main()` — calls `verify_model_registered()` for every `--models` entry *before* running any case, and exits 1 with all bad names printed if any miss. Turns the "N silent per-case errors" failure mode into one loud, obvious startup failure.
- `judge/JUDGE_MODEL_IDENTITY.json` — added a `wq2821-judge-llama8b` entry (same blob/tag/ollama as `hathor-llama8b`) so `verify_model_identity()` also covers this alias once it exists on the gateway.
- `judge/test_referenced_model_lint.py` — unit tests for the new lint (registered passes, missing model fails loud with the model name in the message, unreachable gateway fails loud, missing key fails loud).

## Why the live config isn't touched directly

Same convention as wq#897/wq#911/wq#948/wq#1295/wq#1406/wq#1590/wq#2627 (see
their READMEs) and enforced live by `harness/guard.sh`'s primary-tree wall
(wq#900/wq#1719): this BUILD worker's sandbox cannot write to
`/home/lumen/agent/litellm_config.yaml` (a PRIMARY_TREES entry, not a per-worker
allowlist gap) or restart the LiteLLM container — confirmed this session by a
direct `Edit` attempt, blocked with `guard.sh: BLOCKED — Write/Edit inside
PRIMARY tree /home/lumen/agent denied`. This delivers the merge fragment + the
in-repo lint, not a live change.

Likewise `/home/lumen/minerva` (where minerva-bench-2's actual caller lives) and
`/home/lumen/bin` (where `bin/judge`, the fail-closed CLI, lives) are both
outside this worktree's write reach — see "Follow-up" below.

## Deploy checklist (operator, on the gateway node)

1. `../wq1295-litellm-health-hardening/backup_and_verify.sh backup /home/lumen/agent/litellm_config.yaml`
2. Append `litellm_config_snippet.yaml`'s one `model_list` entry to the live
   config, anywhere outside the `# BEGIN/END GENERATED inference_nodes.yaml`
   sentinel block (e.g. immediately before the existing `qwen36-live` entry —
   same "hand-added alias next to its backend's canonical entry" shape).
3. Restart the gateway via detached `systemd-run` (instinct 5 — Commander path):
   `systemd-run --unit wq2904-litellm-restart --collect docker restart <litellm-container>`
4. `../wq1295-litellm-health-hardening/backup_and_verify.sh verify` — confirms
   `/health/readiness` still 200 post-restart.
5. **Acceptance probe (ticket's explicit ask):**
   `python3 /home/lumen/foreman/judge/judge_bench.py --models wq2821-judge-llama8b`
   — expect the new pre-flight to print no `ERROR:` line and exit past it, then
   one `judge_bench_results` row per eval case in `minerva.db` with a non-null
   `actual_verdict` for `model=wq2821-judge-llama8b` (not `ProxyModelNotFoundError`
   in `error`). This is also the regression probe: run it once *before* step 2
   to reproduce the failure (`verify_model_registered` should print the exact
   "not registered on the LiteLLM gateway" error and exit 1 — the fix's
   pre-flight can fail on the very input it was built for), then again after to
   confirm it passes.

## Follow-up (out of this worktree's reach, needs a session with access)

- `/home/lumen/bin/judge` (lumen-bin repo) should also call
  `judge_common.verify_model_registered()` before `verify_model_identity()` in
  `resolve_model()`'s caller path, so a future missing-model-group bug is
  fail-closed (paged to `aq`) instead of merely loud-on-stderr like
  `judge_bench.py`'s offline-eval path. Not done here: `bin/judge` lives in a
  different repo with no worktree checked out in this session, and
  `harness/guard.sh` blocks direct writes to `/home/lumen/bin` (a
  PRIMARY_TREES entry) from this sandbox.
- Whatever minerva-bench-2's own caller is (referenced by its LiteLLM key
  alias, `/home/lumen/minerva/` per kb/ISSUE-LOG.md#24 — also outside this
  worktree's `--add-dir` reach) should be read to confirm it now succeeds
  against the newly-registered alias, and to check whether it has its own
  copy of a model-name literal that should instead import
  `judge_common.verify_model_registered` or otherwise be validated at
  startup.
