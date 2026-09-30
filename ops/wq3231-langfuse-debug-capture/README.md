# wq#3231 — LiteLLM's "Unexpected error occurred" (langfuse SDK): narrowing + debug capture

## What this ticket found

The literal log line is `langfuse-python`'s catch-all fallback string
(`langfuse/parse_error.py:31`, `defaultErrorResponse`, pinned wheel
`langfuse==2.57.13a0`, pulled via `pip download langfuse==2.57.13a0 --no-deps`
for source inspection — not guessed), raised from the background
`IngestionConsumer.upload()` thread
(`langfuse/_task_manager/ingestion_consumer.py:254-257`) whenever
`_upload_batch()` raises, after retries. This confirms and extends
`ops/wq3122-litellm-langfuse-trace-drop/README.md`'s prior finding for the
same string in a different incident (85% trace *drop*; this one is not a
drop — tshark showed the one captured `/api/public/ingestion` POST answered
`207` with both events `201` and `errors: []`).

**New narrowing (not established by wq#3122):** `handle_exception()`
(`langfuse/parse_error.py:98-102`) calls `generate_error_message()`
(`langfuse/parse_error.py:77-95`), which special-cases the two typed
exceptions the SDK itself raises:

- `APIError` (single HTTP failure, any status 4xx/5xx) -> message is
  prefixed `"API error occurred: ..."`
- `APIErrors` (a `207` response whose `errors` array is non-empty -- one or
  more events individually rejected) -> message is prefixed
  `"API errors occurred: ..."`
- anything else -> falls through to the bare `defaultErrorResponse`, no
  prefix.

The lines this ticket's overnight-1 report quotes are the bare, unprefixed
string. That rules out both of the SDK's own typed API-error paths as the
cause: it is **not** a clean 4xx/5xx, and **not** a `207` with a populated
per-event `errors` array (which matches the tshark capture showing
`errors: []` for at least one such request). The exception reaching
`handle_exception()` must be some other, generic exception raised inside
`_upload_batch()` -- most plausibly a transport-layer failure from the
shared `httpx.Client` (`langfuse/request.py:66`, `self._session.post(...)`)
that survives the SDK's own `backoff`-wrapped retry budget
(`ingestion_consumer.py:278-292`, `max_retries`), since a plain
`httpx.ConnectError`/`RemoteProtocolError`/`PoolTimeout` is a generic
`Exception`, not an `APIError`. That is consistent with onset ~1h after the
08:43Z restart (connection-pool/keep-alive staleness under sustained
concurrent completions) but is **not confirmed** -- see below.

## What this could not confirm from a build worker this session

Same reachability gap as wq#3122 (`kb/ISSUE-LOG.md`, and re-verified live
this session): `docker` is fully denied to this worker (`docker ps`/`docker
logs` are nominally allow-listed but a blanket `Bash(docker *)` deny entry
overrides them -- confirmed by a live `docker ps` attempt returning "denied"
with no prompt to approve), the litellm compose/env file lives under
`/home/lumen/docker`, outside every `--add-dir` this lane was granted, and
restarting the litellm gateway to attach a live debugger is explicitly
out of scope without a Frank-scheduled maintenance window
(`maintenance-window.md`; next window 2026-10-05 02:00-05:00 PT, already
carrying the queued v1.96.2 recreate, wq#3115/wq#3221). Guessing the exact
exception type beyond the narrowing above would be winging it.

## What this ships instead

`langfuse_debug_capture.py` -- a small, independently-tested module
implementing the ticket's own suggested fix ("a sitecustomize... setting
`logging.getLogger('langfuse').setLevel(DEBUG)`"). `install()` sets the
`'langfuse'` logger's own level to `DEBUG` and attaches a dedicated
`FileHandler`, so `handle_exception()`'s `log.debug(exception)` call
(currently a no-op because `'langfuse'` inherits the proxy's `WARNING` root
level) starts writing the real exception to
`/var/log/litellm/langfuse-debug.log` (overridable via
`LANGFUSE_DEBUG_CAPTURE_PATH`) -- without turning on full litellm `DEBUG`
verbosity. `sitecustomize.py` wires this up automatically for any Python
process that has this directory on `PYTHONPATH` (CPython auto-imports
`sitecustomize` at startup).

4 unit tests in `tests/test_langfuse_debug_capture.py`, including a TEST TO
FAILURE canary (`test_without_install_debug_is_suppressed_by_root_level`)
that reproduces the suppression bug itself -- it asserts the *unpatched*
behavior (`logger.isEnabledFor(DEBUG)` is `False` under a `WARNING` root),
so the suite could not have passed vacuously; `test_install_sets_debug_level_independent_of_root`
is the same assertion inverted after `install()`, proving the fix actually
changes the outcome rather than restating a mock.

## Deploy (follow-up, outside this lane)

1. Bind-mount this directory read-only into the litellm container.
2. Prepend it to `PYTHONPATH` in the container's env
   (`PYTHONPATH=/opt/wq3231-debug-capture:$PYTHONPATH`).
3. At the next restart (the already-queued 2026-10-05 v1.96.2 window),
   `langfuse-debug.log` will contain the real exception for the next
   occurrence of the "Unexpected error occurred" line. Read it, then either
   fix the underlying transport issue or file the upstream SDK bug.

This is queued as a physical/access-gated follow-up (whoever manages
`/home/lumen/docker` + the maintenance window), not a blocker on this
ticket -- full-autonomy instinct: do the reachable diagnostic narrowing and
ship the capture mechanism now, don't park on a restart nobody can grant
from this lane.
