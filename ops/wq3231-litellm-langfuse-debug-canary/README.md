# wq#3231 — LiteLLM logs "Unexpected error occurred" (langfuse SDK) once per
completion again; replay + extraction tooling on top of the debug capture

## Relationship to ops/wq3231-langfuse-debug-capture

A prior attempt at this same ticket (killed mid-session before it could call
`wq done`, per this dispatch's own rework notes) already shipped
`ops/wq3231-langfuse-debug-capture/` — `langfuse_debug_capture.py` +
`sitecustomize.py`, which sets the `langfuse` logger's own level to `DEBUG`
and attaches a dedicated `FileHandler`, so `handle_exception()`'s
`log.debug(exception)` call (currently a no-op under the proxy's `WARNING`
root level) starts writing the real exception to a log file — without
raising the whole gateway's log verbosity. Its README has the full root-cause
narrowing (ruling out both of the SDK's own typed `APIError`/`APIErrors`
paths via source reading of the pinned `langfuse==2.57.13a0` wheel) and the
reachability gap (container-exec access denied to this lane, `kb/ISSUE-LOG.md`
#1043; the gateway's compose/env files live outside every directory this lane
is granted). That work is cherry-picked onto this branch rather than
duplicated here — read that README first.

What that module does not yet provide: something to actually trigger a few
of the ticket's own candidate-cause request shapes once the capture file
exists, and something to pull the one relevant line back out of a debug file
that will otherwise fill with routine per-request noise. That is what this
directory adds.

## What this ships

`litellm-langfuse-debug-canary` — built and tested now instead of re-derived
from scratch during the next maintenance window:

- `build_request_shapes()` — the real caller-shape replay set the ticket asks
  for (messenger, a fleet-worker attempt span, a judge verdict call), plus
  two shaped at the ticket's own candidate causes: a multimodal/base64
  `image_url` block (`_media_manager.process_media_in_event`) and an
  oversized metadata field (`_truncate_item_in_place`'s size limit).
- `build_canary_launch_cmd()` — builds the exact launch command for an
  ISOLATED, throwaway canary (`--rm`, its own name, its own port) with
  `LANGFUSE_DEBUG=true` and `LITELLM_LOG=DEBUG` set, from an image/config/env
  path the operator supplies (never guessed — those files are outside every
  directory this lane can read). Never touches the running production
  container.
- `replay_all()` — sends the built request shapes at the canary's own port.
- `extract_swallowed_exceptions()` — the actual payoff: given raw log text,
  finds every "Unexpected error occurred" line and pulls out the real
  exception `handle_exception()` already logged at DEBUG just before it
  (plus the batch's own `making request:`/`uploading batch of` line for
  correlation), instead of an operator eyeballing a noisy log by hand.
  Reads BOTH log shapes this fleet produces: a bare `logging.basicConfig()`
  stream (`DEBUG:langfuse:...`) and `langfuse_debug_capture.install()`'s own
  `FileHandler` format (`%(asctime)s %(levelname)s %(message)s`) — so it can
  point directly at the file that module writes once it is deployed. Returns
  `likely_cause: None` rather than a wrong guess when no exception-shaped
  DEBUG line precedes the marker (TEST TO FAILURE, wq#2630).
- `build_canary_launch_cmd()` — an alternative path for an operator who would
  rather test against an isolated, throwaway canary (`--rm`, its own name,
  its own port, `LANGFUSE_DEBUG=true`/`LITELLM_LOG=DEBUG`) than wait for the
  next scheduled restart to pick up `langfuse_debug_capture`'s `PYTHONPATH`
  wiring. Never touches the running production container; every path
  (image/config/env) is operator-supplied, never guessed.

13 unit tests (`tests/test_litellm_langfuse_debug_canary.py`), no container
runtime or network required — the launch-command and replay-shape builders
are pure functions, and the extractor is tested against fixture log text
shaped like the real DEBUG output this fleet (wq#3122's citations, and
`langfuse_debug_capture`'s own Formatter) and upstream langfuse-python issue
reports actually produce.

## How an operator uses this

Once `ops/wq3231-langfuse-debug-capture` is deployed at the next restart
(see that README's Deploy section) and a completion has been sent:

```
ops/wq3231-litellm-langfuse-debug-canary/litellm-langfuse-debug-canary extract \
  --log-file /var/log/litellm/langfuse-debug.log
```

Or, to test against an isolated canary instead of waiting for that restart
window (an operator with container-exec access only):

```
ops/wq3231-litellm-langfuse-debug-canary/litellm-langfuse-debug-canary print-cmd \
  --image <same image tag the prod gateway runs> \
  --config /path/to/the/real/litellm_config.yaml \
  --env-file /path/to/the/real/.env
# run the printed command, wait for the canary to come up, then:
ops/wq3231-litellm-langfuse-debug-canary/litellm-langfuse-debug-canary replay \
  --url http://localhost:14000/v1/chat/completions --key <canary master key>
# capture the canary's log output to a file, then run `extract` on it.
```

Until an operator runs one of these and the real exception is read, treat
the "Unexpected error occurred" line as unexplained noise (per the ticket),
not as proof of data loss — tracing itself was independently confirmed
healthy (trace-rate tracking completions, 207/201 on the captured ingestion
POST).
