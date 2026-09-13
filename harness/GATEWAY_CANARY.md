# Gateway-backed spawn canary (wq#1888 despag P2-2)

`fworker.py:run_child()` (`FW:1633`) spawns `claude -p` directly and parses its
`stream-json` stdout. `run_child_gateway()` (`FW:1511`) is a same-signature, same-6-tuple
sibling: it POSTs `despag`'s `POST /v1/responses` with `stream:true` instead, and maps the
UHP `response.*` events it gets back onto the canonical frame shape `classifier.RunState`
already understands (`_observe_gateway_event`, `FW:1466`), so `classify()` runs unmodified
either way. The point: once a lane runs through despag, `despag/admission.py`'s
`check_admission` is the *only* admission gate that pool's spawns pass — not a second,
independent one inside fworker.

Both backends still exist; a ticket only takes the gateway path if its `lane` is in
`FOREMAN_GATEWAY_LANES`. Default is empty — **no lane is on the gateway path until an
operator opts one in**.

## Enabling one lane

```
export FOREMAN_GATEWAY_LANES=despag-canary   # comma list; matched against ticket.lane
export FOREMAN_GATEWAY_URL=http://127.0.0.1:8790   # despag's own bind address
export FOREMAN_GATEWAY_TOKEN_FILE=~/.secrets/despag-api.token  # fworker's OWN copy —
                                                                # never the CLI's OAuth token
```

Set these in the `fworker-wq<id>@.service` environment (or its `EnvironmentFile`) for the
node(s) picking up that lane. Every other lane is unaffected: `gateway_lane_enabled()`
(`FW:1352`) only changes behavior for tickets whose `lane` is in the list.

## What to watch for two weeks

Every attempt record (`attempt-N.json`) now carries three new fields regardless of which
backend ran it:

- `gateway_backend` — `true` if this attempt actually ran through despag.
- `gateway_fallback` — `true` if the gateway lane was selected but the attempt fell back to
  a direct `claude -p` spawn (connect failure or a despag 5xx — **never** a 4xx, which is
  despag rejecting the request on purpose, e.g. its own admission gate).
- `gateway_fallback_reason` — the exception string, when `gateway_fallback` is true.

Watch, per lane in `FOREMAN_GATEWAY_LANES`:

1. **`classification` distribution** — should track the same lane's own history before the
   canary started. A new skew toward `INDETERMINATE`/`CRASH`/`LAUNCH_FAILURE` that the
   direct-path lanes don't show is the mapping losing fidelity somewhere (`_observe_gateway_event`).
2. **`gateway_fallback` rate** — should be ~0. A sustained nonzero rate means despag itself
   is flaky (connect refused/5xx), not a fworker bug; check despag's own logs/`GET /v1/uhp`
   `lumen.budget` before touching this code.
3. **LIMIT parity** — every `classification: LIMIT` attempt on a gateway lane should show
   `saw_limit: true` and a real `latest_resets_at` (`lumen.limit`/`lumen.run_state` sidecar,
   `despag/app.py`). A LIMIT with no `resets_at` means the sidecar didn't attach — treat as
   a bug, not a quirk.
4. **`cpu_s: 0.0` / no local pid** on every gateway-backed attempt — expected (there is no
   local child to `RUSAGE_CHILDREN`), not a regression; don't let cost-triage tooling flag it.
5. **`usage-N.json` cost/duration fields non-null** on gateway-backed attempts — these come
   from the `lumen.run_state` sidecar; a null `total_cost_usd`/`duration_ms` there means the
   sidecar is missing fields, not that the turn was free.

## The revert

Two independent kill switches, either one is sufficient and reversible with zero risk to
in-flight tickets (the fallback logic means a mid-flight gateway outage already degrades
to the direct path on its own):

- **Per-lane**: drop that lane from `FOREMAN_GATEWAY_LANES` (or unset it) on the affected
  node(s) and restart the `fworker-wq<id>@.service` units that read it. Every ticket in that
  lane goes back to a direct `claude -p` spawn on its next attempt — no code change, no
  redeploy.
- **Full revert**: `git revert` this ticket's commit(s). `run_child()` and every existing
  call site are untouched by this change (`run_child_gateway` is additive, selected only by
  the conditional at the `do_attempt()` call site), so reverting drops the gateway path
  entirely with no follow-up cleanup required.
