# wq#3221 — restart/upgrade verify must assert a Langfuse trace lands; escalate trace-rate alerts; lint the environment_variables config trap

Follow-up to wq#3211 (47h of LiteLLM->Langfuse traces lost 2026-09-28 10:25Z .. 2026-09-30 08:43Z).
Four parts:

## (1) VERIFY: assert a real completion produces a Langfuse trace

`maint-ops/litellm-upgrade` lives in the `/home/lumen/bin` git repo, a different repo than
this one. A BUILD worker's worktree lives in a different repo's checkout, so a direct `Edit`
there hits the primary-tree write freeze (`kb/ISSUE-LOG.md` row 1027 hit the identical class
of refusal patching this exact script for wq#3097's GHSA fix, and landed it as a diff for the
same reason — this ticket follows that established convention instead of re-discovering the
refusal by trial and error).

`patches/maint-ops-litellm-upgrade-langfuse-verify.patch` adds the trace-assertion to
`verify()`: after the existing `/v1/models` + real-completion smoke checks pass, it polls
`GET /api/public/traces?name=litellm-acompletion&fromTimestamp=<completion_ts>` (Basic auth,
creds from the same `agent/.env` the container loads) for up to `LANGFUSE_TRACE_TIMEOUT_S`
(default 60s) and fails `verify()` — triggering the existing `rollback` path — if no trace
shows up. Verified real, not just described:

- `git apply --check` against the live file: applies cleanly
  (`ops/wq3221-litellm-restart-verify-langfuse/tests/test_patch_applies_and_verifies.py::test_patch_applies_cleanly_to_the_pinned_pre_patch_fixture`).
- The python it injects into the `verify()` heredoc is syntactically valid
  (`::test_patched_verify_heredoc_is_valid_python`).
- The trace-assertion function itself is unit-tested including the **TEST TO FAILURE** case
  (`::test_langfuse_trace_seen_false_when_no_trace_within_timeout`): a run where Langfuse
  reports zero matching traces — the exact shape a poisoned callback credential produces —
  must return `False`, not silently pass.

**Land this before the Mon 2026-10-05 02:00 PDT v1.96.2 window (wq#3115)**: that recreate
re-reads the config and re-triggers the same class of silent failure this ticket exists to
catch. Apply with `patch -p1 < patches/maint-ops-litellm-upgrade-langfuse-verify.patch` (or
`git apply`) from `/home/lumen/bin`, from a lane whose worktree root *is* that repo (a
`bin`-scoped ticket, or an operator), then run `maint-ops/litellm-upgrade --verify` once to
confirm the new PASS line before the window queues.

## (2) DETECTION/OWNERSHIP: escalate a persistent trace-rate breach

`aq 2492` (langfuse-trace-rate-monitor's own alert, wq#3122) was bumped 101 times during the
wq#3211 loss and never escalated — an unassigned `ntfy:lumen-alerts` row with no fleet-fires
ticket, so Frank was the only detector.

`harness/langfuse_trace_rate_monitor.py` now calls `escalate_if_persistent()` on every run
(wired into `run()`): a breach that persists past `LANGFUSE_TRACE_RATE_ESCALATE_AFTER_S`
(default 2h) opens exactly one `[fleet-fires]`-lane ticket via `wq add`, same idempotent
one-ticket-per-condition shape as `ops/wq1649-aq-source-escalate`'s `unit_escalations` table
(state tracked in `LANGFUSE_TRACE_RATE_ESCALATE_STATE`, a small JSON file — this monitor has
no sqlite db of its own). Recovery clears the state so a later breach can escalate again.
10 new unit tests in `harness/tests/test_langfuse_trace_rate_monitor.py`.

`ops/wq3169-litellm-completion-probe/litellm-completion-probe` (already sends one real
completion per gateway host every 30 min) now also polls the same Langfuse trace API once per
run, but only when at least one completion actually succeeded (`check_langfuse_trace()`,
called from `main()`). It is shaped as a normal probe result (`host="langfuse-trace-ingest"`)
and fed through the SAME edge-triggered `apply_results()` this script already had — no new
alert plumbing, and the loss is caught within one 30-min cycle instead of the monitor's 15-min
polling window compounding with a human noticing an unassigned alert. 6 new unit tests
including a TEST TO FAILURE case
(`test_check_langfuse_trace_fails_when_no_trace_within_timeout`).

## (3) CONFIG TRAP: lint `environment_variables: KEY: KEY` self-references

Root cause (as given in the ticket, re-derived from `kb/ISSUE-LOG.md` row 904 which found the
same block's `LANGFUSE_SECRET_KEY` stored as a plaintext literal rather than an
`os.environ/VAR` reference): litellm's `environment_variables:` config block does
`os.environ[key] = value` for every entry at load time. The correct syntax to pull a real
secret is `KEY: os.environ/KEY`; a bare `KEY: KEY` line instead sets the process env var to
the **literal string "KEY"** — for `LANGFUSE_PUBLIC_KEY`/`LANGFUSE_SECRET_KEY` that is a
19-character string that looks nothing like a real Langfuse key but is syntactically a string,
so nothing crashes. PID 1's own environment (set by docker-compose) was never touched, which
is why every env-presence/liveness check kept passing throughout the 47h loss — the corruption
happened entirely inside the litellm process's own re-exec of the config block.

**Why other providers' `NAME: NAME` lines don't (currently) break**: this needs the live
`/home/lumen/agent/litellm_config.yaml` to confirm definitively — it is outside every
`--add-dir` this BUILD worker's session was granted (same reachability gap `kb/ISSUE-LOG.md`
row 904 hit reading this exact file), so it was not re-read here. The mechanism that would
make a `NAME: NAME` line harmless for *some* providers and not others: litellm's
`environment_variables:` block only matters for a credential if something downstream actually
resolves it via `os.environ/NAME` (typically a `litellm_params.api_key: os.environ/PROVIDER_KEY`
reference, or an SDK that reads `os.environ[NAME]` directly at call time, as Langfuse's
`LangFuseLogger` does on every init). A provider whose `litellm_params` instead embeds its key
as a different literal, or whose env var name in the block simply doesn't match any name a
caller ever dereferences, has a dead `NAME: NAME` line — wrong, but inert, because nothing
reads that exact env var back. **This lint does not special-case that distinction**: it flags
every `KEY: KEY` self-reference in the block unconditionally, because a self-referential value
is never correct regardless of whether the current caller graph happens to tolerate it — the
next caller added to read that var would silently poison on the same bug. Confirming which of
the *other* provider lines are presently dead (vs. merely undiscovered) is flagged here as a
follow-up needing operator/live-file access, not resolved by this lint.

`ops/wq2903-model-ref-drift/model_ref_lint.py` gained a third finding kind, `SELF_REF`
(`find_env_self_refs()`), wired into `lint()` so it runs on every invocation of the existing
`model-ref-lint` tool (same `--json`/`--aq`/exit-code contract). 4 new unit tests including
the **TEST TO FAILURE** canary the ticket asks for
(`test_env_self_ref_canary_fails_the_lint_end_to_end`): a config with the two LANGFUSE lines
restored verbatim must make the lint's exit code non-zero.

The second half of part (3) — comparing the *running* proxy's actual outbound callback
credentials against the container env (via a tcpdump decode or similar) — is not implemented
as a separate check: `ops/wq3122-litellm-langfuse-trace-drop/README.md` already established
that `docker exec`/packet capture on the litellm container is unreachable from a BUILD worker
(`kb/ISSUE-LOG.md` #1043). The Langfuse-trace-assertion added in parts (1)/(2) is the practical
substitute this fleet already has reachable: if the running proxy's effective creds diverge
from `agent/.env` (self-ref bug or otherwise), no trace authenticates and lands, and the
assertion fails — same observable outcome as a direct creds comparison, without needing
container access.

## (4) PROCEDURE: a config-only edit is not live until the next restart

Recorded here for the change procedure: editing `agent/litellm_config.yaml` alone does
**not** take effect — litellm only re-reads it on container recreate/restart. Any lane editing
that file MUST, in the *same* change:

1. Restart/recreate the `litellm` service (`maint-ops/litellm-upgrade --apply`, or the
   equivalent attended drain), not just edit the file and stop.
2. Run `maint-ops/litellm-upgrade --verify` (now including the part-1 Langfuse trace
   assertion) before calling the change done.

Skipping step 1 is exactly how wq#3211 happened: the `environment_variables` self-reference
sat in the config, inert, until an *unrelated* restart (the "patch.3 attended drain") applied
it for the first time — 47h after the file was actually edited, with no correlation between
"the edit" and "the outage" for anyone to trace. `model-ref-lint --aq`'s new `SELF_REF` finding
now also catches this class of trap at edit time, before any restart applies it.

## Tests

```
python3 -m pytest harness/tests/test_langfuse_trace_rate_monitor.py \
  ops/wq3169-litellm-completion-probe/tests/test_litellm_completion_probe.py \
  ops/wq2903-model-ref-drift/tests/test_model_ref_lint.py \
  ops/wq3221-litellm-restart-verify-langfuse/tests/test_patch_applies_and_verifies.py -q
```
