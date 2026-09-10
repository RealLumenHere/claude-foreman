# wq#1599 — verify-pass: wq#1590 deepseek-v4-flash connect-failure guard (PR#391)

**Verdict: CONTRADICT** — the guard's own claim (title, docstring, ntfy message: "pages
when deepseek-v4-flash's connect-failure rate spikes") does not hold in general. It pools
every `deepseek/`- or `deepseek-`-prefixed model into ONE shared windowed counter with no
per-model dimension, while the fleet has at least two live, independently-routed deepseek
deployments (`deepseek-v4-flash`, `deepseek-v4-pro`). A sustained 100%-failure spike
isolated to one deepseek rung can be diluted below the alert threshold by healthy traffic
on another deepseek rung sharing the same window, and any alert that does fire always
attributes it to the literal string "deepseek-v4-flash" regardless of which model actually
failed.

PR: http://100.121.17.5:3000/lumen/claude-foreman/pulls/391 (merged, commit cf5da72).
Diff read cold via `git show cf5da72` against `/home/lumen/foreman` main, not the PR's own
description of itself.

## What the diff does

Adds `ops/wq1590-deepseek-connect-guard/deepseek_connect_alert.py`: a LiteLLM
success/failure callback that classifies connect-level errors (`is_connect_failure`),
filters to deepseek models (`is_deepseek_model`), records a `(timestamp, is_failure)`
sample in a trailing window, and pages ntfy (edge-triggered, cooldown) when the windowed
rate crosses `DEEPSEEK_ALERT_THRESHOLD` with at least `DEEPSEEK_ALERT_MIN_SAMPLES` samples
— explicitly mirroring `dispatcher/circuit_breaker.py`'s shape. 20/20 unit tests pass
(`python3 ops/wq1590-deepseek-connect-guard/tests/test_deepseek_connect_alert.py`).

## Verification performed

1. **Read the diff cold**: `deepseek_connect_alert.py`, `README.md`,
   `litellm_config_snippet.yaml`, `tests/test_deepseek_connect_alert.py` in full.
2. **Ran the shipped test suite** — all 20 tests pass as claimed.
3. **Adversarial read of `is_deepseek_model`** (`:90-93`): matches any model string
   starting with `deepseek/` or `deepseek-` — not scoped to `deepseek-v4-flash` despite the
   file's own docstring title ("page when deepseek-v4-flash's connection failure rate
   spikes") and the ntfy message text.
4. **Adversarial read of the state layer**: `_events_path()` (`:114-115`) returns one fixed
   `events.json` with no model key; `record_event()` (`:148-158`) takes `is_failure` and
   `now` only — no model — and appends into that single shared list; `windowed_rate()`
   (`:141-145`) computes one pooled rate over it. `notify_connect_spike()` (`:161-181`)
   hardcodes the literal string `"deepseek-v4-flash"` in the alert body regardless of which
   model(s) contributed. Only the cooldown/alert-state (`:200-203`) is keyed per exact
   model string — an inconsistency between the pooled stats and the per-model gate, but it
   only affects re-alert spacing, not attribution.
5. **Confirmed the fleet has multiple live deepseek deployments**, not just
   `deepseek-v4-flash`: `research/FLEET_COST_PLAN.md:21` lists `deepseek-v4-pro` at 8,720
   requests / 14.3% failure over the same window class, distinct from `deepseek-v4-flash`'s
   94,339/159,474 (59.2%); `research/MODEL_ROUTING_AUDIT.md:55,233` documents
   `deepseek-v4-pro` as the LPP brain's live escalation model, separate from
   `deepseek-v4-flash`'s role in the same routing chain; a live systemd unit
   (`ops/wq1302-n8n-eval/inventory.json:1585`) also references `deepseek-v4-pro` traffic.
6. **Reproduced the dilution failure mode** with the actual shipped module
   (`DEEPSEEK_ALERT_STATE_DIR` in a fresh temp dir, `MIN_SAMPLES=15`, `THRESHOLD=0.5`,
   `NTFY=/bin/true`, fresh interpreter state): 30 healthy `deepseek/deepseek-v4-pro` calls
   interleaved with 20 back-to-back `deepseek/deepseek-v4-flash` calls, every one of them
   the ticket's exact connect-failure string, produced a pooled rate of `20/50 = 0.40` —
   under the 0.5 threshold — and the alert never fired, despite `deepseek-v4-flash` failing
   100% of its own calls in the window. A control run using only the 20 `deepseek-v4-flash`
   failures (no `deepseek-v4-pro` traffic) alerts at call 15, confirming the guard works in
   isolation and the miss is caused specifically by cross-model pooling. A second control
   (failures on `deepseek-v4-pro` only, healthy `deepseek-v4-flash` traffic) fires — but the
   ntfy message still reads "deepseek-v4-flash connect-failure rate...", misattributing the
   page to the wrong model.
7. **Independent escalation**: per protocol, a contradiction escalates to opus-5@high as a
   judgment trigger. Spawned a fresh opus agent with no access to this document, given only
   the code paths and the claim to check; it independently re-read the code, re-ran its own
   reproduction script, and returned **PARTIALLY CONFIRMED** — same code facts, same
   reproduction result (pooled rate 0.333 on its own scenario, alert silent; control alone
   fires at call 15) — with the caveat that at the *historical* incident's measured volumes
   (`deepseek-v4-flash` outweighs `deepseek-v4-pro` roughly 18:1), the original 59.2%
   incident would still have tripped the pooled counter. The defect is real regardless: it
   inverts under lower relative volume on the failing rung, which is exactly the shape of
   spike this guard exists to catch on any rung that isn't the fleet's current highest-volume
   one — and every fired alert misattributes the model name.

## Severity assessment

Not a fabricated-evidence false positive (Tier-0 instinct 7 checked and does not apply —
`deepseek-v4-pro` is grepped as a real, live, referenced deployment, not a value this
session invented). This is a genuine, reproducible correctness gap between the guard's
stated scope (`deepseek-v4-flash`) and its actual implementation (all `deepseek*`
deployments pooled), which both narrows detection (a spike on a lower-volume deepseek rung
can go unpaged) and corrupts the page's content (wrong model name) whenever more than one
deepseek deployment is live — which, per `FLEET_COST_PLAN.md`/`MODEL_ROUTING_AUDIT.md`, is
already true today.

## Conclusion

CONTRADICT. The guard is real, tested, and would have caught the exact historical incident
at today's traffic mix, but it does not do what it claims for the general case its own
README half-acknowledges ("deepseek* deployments"): per-model isolation is missing from the
stats layer even though the cooldown layer assumes it, and the alert text is unconditionally
mislabeled. Filed wq#1604 (build lane, deps=1590) to add a model dimension to the windowed
counter and make the ntfy message reflect the actual breaching model(s), referencing this
report and the reproduction above. Did not re-implement per this ticket's instruction —
verify only.
