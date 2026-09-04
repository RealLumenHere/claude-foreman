# wq#1417 — Adversarial verify-pass of wq#1406 (guard against silent PAID fallback)

**Verdict: CONTRADICT** — items (1) audit and (3) spend tripwire are correct and their
shipped tests pass for the right reasons. Item (2), the real-time `paid_fallback_alert.py`
success-callback, has a wiring bug that makes it fire on **every successful call to an
intentionally-paid primary model group, not just on a real fallback** — the alert text
("X is down, serving via paid Y") would be sent when X is not down at all. This is the
opposite failure mode from the one wq#1406 was filed to fix (silent drain), but it
directly breaks the ticket's own "paid-fallback serves alert" done-criterion: the signal
stops meaning "a fallback happened" and degrades to "a paid model answered," which is
false-alarm noise on any fleet config with a paid model as a primary (which
`audit_fallback_chains.py`'s own rules explicitly permit — "a paid primary is a separate,
intentional-billing question, not a silent fallback").

## What was shipped

PR http://100.121.17.5:3000/lumen/claude-foreman/pulls/284 (merged, commits `d7048b0`
+ `0c8d25b`, 11 files, `ops/wq1406-paid-fallback-guard/`): `audit_fallback_chains.py`
(static audit of `router_settings.fallbacks`), `paid_fallback_alert.py` (LiteLLM
`success_callback`, ntfy on paid-fallback serve), `paid_spend_tripwire.py` + systemd
timer (per-paid-model daily spend page via `/spend/logs`). 28 unit tests, all pure
decision functions.

## Mechanics — CONFIRMED (1) and (3)

Ran all three shipped suites directly against the extracted source
(`python3 tests/test_audit_fallback_chains.py`, `test_paid_spend_tripwire.py`): all pass.

- `audit_fallback_chains.py`'s two violation shapes (no-free-anywhere / paid-before-free)
  are correctly implemented and match the ticket's stated rule; `group_classification`
  correctly marks a mixed alias (free + paid deployment under one `model_name`) as paid
  regardless of list order, and a dangling/typo'd fallback target is correctly left
  `unknown` rather than silently `free`. No issues found.
- `paid_spend_tripwire.py`'s threshold/breach logic, per-model env override, and
  once-per-day-per-model state dedup are correct and match the ticket's "page, don't cap"
  requirement.

## Bug — CONTRADICT (2): `paid_fallback_alert.py`'s wiring collapses "fallback?" into "paid?"

`handle_success_event(requested_model_group, served_model_group, served_model)` needs two
*different* signals from the deployment that actually served the call: a **group/alias**
(to compare against what the caller requested, for `is_fallback_response`) and a
**litellm model string** (to classify paid-vs-free, for `is_paid_model`). The file only
ever extracts one thing from `kwargs` — `extract_served_model()`, the litellm model
string (e.g. `"deepseek/deepseek-v4-flash"`) — and the real call site in
`async_log_success_event` (`paid_fallback_alert.py:162-168`) feeds that same value into
*both* slots:

```python
requested = extract_model_group(kwargs)
served = extract_served_model(kwargs, response_obj)
handle_success_event(requested, served, served)   # same value, two different roles
```

`is_fallback_response` (`paid_fallback_alert.py:65-70`) is a plain string-inequality
check: `requested_model_group != served_deployment_group`. Feeding it an alias
(`"deepseek-primary"`) against a provider/model path (`"deepseek/deepseek-v4-flash"`)
means it reads True — "a fallback happened" — on essentially every successful call,
because a deployment alias is (by this fleet's own naming convention, visible in every
fixture in `tests/test_audit_fallback_chains.py`: `qwen36-local`, `cerebras-primary`,
`deepseek-fallback`) never spelled identically to its `litellm_params.model` string. This
holds independent of the "VERIFY on first deploy: kwargs shape is version-dependent"
caveat the author flagged — no matter what `kwargs.get("model")` ends up meaning on the
live proxy, using the identical extracted value for both an alias-comparison and a
model-string-comparison cannot correctly implement both checks at once, since the two
things being compared (group name, provider path) are never the same string on a real
config.

### Reproduction

Ran directly against the shipped, unmodified `paid_fallback_alert.py` (extracted from
commit `d7048b0`), simulating a **healthy, non-fallback call to an intentionally-paid
primary** — exactly the shape `audit_fallback_chains.py`'s own docstring says is fine
("a paid primary is a separate, intentional-billing question, not a silent fallback"):

```
kwargs = {
    "model": "deepseek/deepseek-v4-flash",
    "litellm_params": {"metadata": {"model_group": "deepseek-primary"}},
}
requested = extract_model_group(kwargs)      # "deepseek-primary"
served    = extract_served_model(kwargs, resp)  # "deepseek/deepseek-v4-flash"
handle_success_event(requested, served, served)  # == production call site
```

Output:
```
requested: deepseek-primary served: deepseek/deepseek-v4-flash
alerted on a NORMAL primary-paid call (no fallback occurred): True
```

The callback pages "deepseek-primary is down, serving via paid fallback
deepseek/deepseek-v4-flash" — on a call where nothing failed over and nothing is down.
This would fire on the *first* successful call to any paid-primary group and re-fire
every cooldown window (default 30min) for as long as that group keeps being used
normally, i.e. a standing false alarm, not a one-off.

### Why the shipped tests don't catch it

`tests/test_paid_fallback_alert.py`'s `TestHandleSuccessEvent` calls
`handle_success_event()` directly with hand-picked, *already-distinct* group/model
values (e.g. `handle_success_event("primary", "deepseek-fallback", "deepseek/x")`) —
correct by construction, so they never exercise the actual bug, which lives entirely in
how `async_log_success_event` derives its arguments. No test calls
`async_log_success_event`, `extract_model_group`, or `extract_served_model` together
against a realistic `kwargs` dict, and there is no `extract_served_model_group()`
function at all — the piece of information the design needs and never obtains.

### Why the deploy checklist wouldn't catch it either

README.md's "Deploy checklist" step 4 says to force one real fallback and "confirm
exactly one ntfy alert arrives, not one per request." Because the bug fires on *any*
paid-served call whether or not a fallback occurred, that manual test would still show
"an alert arrived" and look like it passed — the false-positive-on-healthy-calls failure
mode wouldn't surface unless someone also separately checks that a *normal, non-fallback*
call to a paid primary stays silent, which the checklist doesn't call for.

## Scope note

This is currently latent, not actively paging: `paid_fallback_alert.py` is not yet
deployed (PYTHONPATH/`success_callback` merge is an unchecked step in the same deploy
checklist), so no live false pages have fired. The bug is in the shipped code itself,
independent of current deploy state.

## Disposition

Fix belongs in `async_log_success_event`/`handle_success_event`'s call site: derive an
actual served-group signal (or drop the group-vs-group comparison and instead compare
served *deployment id* to the *deployment id LiteLLM would have picked as primary* for
the requested group — whatever the real kwargs contract turns out to support once
verified live) instead of reusing `served_model` for both roles. Items (1) and (3) need
no changes. Not re-implementing here per verify-pass scope — filing this contradiction
for the fix to land as its own change.
