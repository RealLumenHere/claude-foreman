# wq#4103: lumen/judge pays a 10s hathor timeout on every call

Found 2026-10-16 (fleet-health-c8220e). The LiteLLM `lumen/judge` model group
points its primary deployment at `ollama/hathor-judge` (`timeout: 10`,
`num_retries: 0`) and falls back to `lumen-local-judge`. Hathor is shut down
after work BY POLICY (Frank 2026-09-18, auto-sleep.timer, ISSUE-LOG #1235;
tailnet offline 19h at scan time), so **every** judge call blocks the full 10s on
the unreachable primary, then is served by the fallback
`ollama/qwen2.5-coder:7b @172.17.0.1` with `x-litellm-attempted-fallbacks=1`.
Measured 20.2s (cold local) and 11.2s per verdict. 100% of judge traffic is
already served by the local 7B — just slowly, after a wasted timeout.

## Decision: option (a) — make lumen-local-judge the primary

The ticket offered (a) local-primary until wake-on-demand is viable, or (b)
implement wake-on-demand. (b) is blocked: the ticket forbids ad-hoc hathor wake
(power policy + the tailscaled cold-boot race), and `dispatcher/wake_judge.py`'s
WoL path depends on `wake-dispatch.sh` and the tailscaled race, neither fixed.
(a) is the only change that stops the per-call 10s now, and the judge gate is
fail-closed by design (`judge_common.parse_verdict` returns `None` on any
non-verdict → caller denies), so a terminal local primary with no asleep-node
fallback is correct: a local outage fails closed immediately instead of paying
10s first.

## The change

`judge_local_primary.py` edits `litellm_config.yaml` with three minimal,
indentation-preserving line edits (re-parsed and compared field-by-field against
the intended delta before writing; the live config is credential-bearing, so it
prints only model-group names):

```
model_list:        model_name: lumen/judge  ->  hathor-judge   (backend def kept as an unused rung)
model_group_alias: + lumen/judge: lumen-local-judge
fallbacks:         - lumen/judge -> [lumen-local-judge]        (removed; local is now the primary)
```

A caller still sends `model: lumen/judge`; the alias rewrites it to
`lumen-local-judge` and routes straight to the local qwen2.5-coder:7b. The
renamed `hathor-judge` rung stays defined (backend `ollama/hathor-judge`,
`timeout: 10`) so a future wake-on-demand ticket can re-wire it as a fallback
without reconstructing it.

Note on LiteLLM alias semantics (wq#4102): fallbacks are looked up by the
caller-sent name before alias resolution. Here that works in our favour — we
*want* no fallback for `lumen/judge`, and removing the entry leaves none.

## Reload — queued as a window op (wq#4122)

LiteLLM reads the config only at process start, and workers may not touch
containers. `reload_and_verify.sh` (idempotent) is filed as the `[needs-window]`
ticket **wq#4122**: it applies the edit, restarts `litellm`, runs
`probe_judge_primary.py` (asserts `attempted_fallbacks=0` and <8s — the inverse
of the pre-fix path), then the calibration gate below. If the running gateway
already serves fast from the primary, it skips the restart.

## Calibration — the 7B now decides every verdict

Because local is now primary, qwen2.5-coder:7b serves all judge traffic instead
of hathor's llama3.1:8b-q8. `calibrate_local_judge.py` runs the should-deny
slice (98 cases) of the fixed eval set `judge/judge_eval_set.jsonl` (the
wq#99/#100-lineage corpus) and **fails closed on any false-allow** (a bare
`allow` on an expected-deny case). `allow-with-conditions` is a near-miss (still
forces snapshot+rollback+notify downstream) and a PARSE_FAIL is fail-closed by
definition — neither fails the gate; a real false-allow does. This runs inside
wq#4122 (needs the gateway + service key, which the build sandbox cannot reach).

## aq#2671501 ('model DOWN: hathor-lfm-1b') — stale, not from the repo watchdog

The in-repo model watchdog already does **not** probe `hathor-lfm-1b`: it was
removed from `LOCAL` while hathor is unreachable
(`ops/wq2903-model-ref-drift/lumen_model_watchdog.py:50-51`, "Re-add to LOCAL
when wq#362 closes"), and no hathor rung is in its curated roster
(`CLOUD+SLOW+LOCAL`, lines 57-72). So this fix introduces no new DOWN alert when
it renames the judge rung to `hathor-judge` — that name is not in the roster
either. aq#2671501 is therefore a stale/orphan alert (or from a watchdog
deployment that has drifted from this repo) and should be cleared via the aq
CLI; chasing a credentialed aq mutation is out of the build sandbox's reach.

## Tests

```
python3 ops/wq4103-judge-local-primary/tests/test_judge_local_primary.py
python3 ops/wq4103-judge-local-primary/tests/test_calibrate_local_judge.py
```

The editor test proves the transform's delta is exact (renamed rung keeps every
`litellm_param`, net −1 line) and includes a falsifiable guard probe drawn from
outside the happy path: a config whose `lumen/judge` fallback holds an
unexpected extra item must make the editor refuse and leave the file untouched.
