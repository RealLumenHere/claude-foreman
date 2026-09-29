# wq#3122 — LiteLLM->Langfuse traces dropped ~85%: root cause + trace-rate monitor

## Root cause

The running gateway is **litellm 1.83.10** (confirmed live: `curl -s
http://127.0.0.1:4000/health/readiness` on Lumen returns
`"litellm_version":"1.83.10"`, `"log_level":"WARNING"`, and `"langfuse"` in
`success_callbacks` — this needs no docker access, it is litellm's own local
HTTP port). That version's bundled `litellm/integrations/langfuse/langfuse.py`
(pulled as the exact wheel via `pip download litellm==1.83.10`, not guessed)
wraps the whole callback in a try/except that only surfaces failures through
`verbose_logger.exception(...)`, which is gated behind the proxy's configured
log level — reported live as `WARNING`, so that line never reaches stdout.

The literal text this ticket found in `docker logs` — "Unexpected error
occurred. Please check your request and contact support:
https://langfuse.com/support." — is not litellm's own message. It is
`langfuse-python`'s catch-all fallback string
(`langfuse/parse_error.py:31`, `defaultErrorResponse`), returned by
`generate_error_message()`/`generate_error_message_fern()` for any exception
that isn't one of their few typed API errors. It is raised from the
**background** `IngestionConsumer.upload()` thread
(`langfuse/_task_manager/ingestion_consumer.py:254-257`) — the thread that
batches queued trace/generation events and POSTs them to
`/api/public/ingestion` — whenever that batch POST fails for any reason,
after retries are exhausted. `handle_exception()`
(`langfuse/parse_error.py:98-102`) does call `log.debug(exception)` first,
which would show the real cause — but at `WARNING` that call is a no-op, so
only the generic contact-support line survives. That is exactly why grepping
the litellm/langfuse `.py` sources for the actual fault text (per the
ticket) found nothing: the real exception text is never logged at the level
this gateway runs at, only the boilerplate is. Confirmed by pulling and
inspecting the exact wheels (`pip download litellm==1.83.10`,
`pip download langfuse==2.57.13a0` — the same langfuse SDK version this
ticket's own manual in-container reproduction used), not by guessing at
GitHub issue descriptions.

This also means the "manual python langfuse 2.57.13a0 trace (debug=True)
uploaded 207/201" reproduction the ticket describes is not a contradiction:
that ad-hoc script gets `Langfuse(..., debug=True)`, so *its* `log.debug`
calls print. The gateway's real `Langfuse` client is constructed by
`litellm.integrations.langfuse.langfuse.LangFuseLogger.__init__` with
`debug=os.getenv("LANGFUSE_DEBUG")` (unset -> `None`/falsy) and the proxy's
own `log_level: WARNING` — a different logging configuration, not a
different SDK, host, or set of keys.

## What this could not fix from a build worker this session

Confirming *which* underlying exception the background upload thread is
hitting (bad batch schema, a non-JSON-serializable field slipping into
`clean_metadata` from a real production request, TLS/proxy behavior specific
to litellm's shared `httpx_client`, etc.) requires either enabling
`LANGFUSE_DEBUG=true` on the container and re-reading its logs, or `docker
exec`. Both are out of reach here: `docker` is fully denied to this worker
(every subcommand, not just the exec/logs/run and edit ones — confirmed live,
`kb/ISSUE-LOG.md` #1043), the container's env/compose file lives outside
every `--add-dir` this session was granted (`/home/lumen/docker`), and the
ticket explicitly forbids restarting the litellm gateway to test from this
lane (a concurrent litellm-upgrade harness run, wq#3097/2989, was using
`slock` on it). Actually silencing the error line therefore needs an
orchestrator-lane maintenance window (same class of gap as wq#3115,
`kb/ISSUE-LOG.md` #1037): bump `LANGFUSE_DEBUG`/log level for one restart,
capture the real exception, then fix it or roll the langfuse pin forward.

## What this ships instead

`harness/langfuse_trace_rate_monitor.py` — polls Langfuse's own
`/api/public/traces?fromTimestamp=...` for the trace count in a rolling
window (default 1h) and alerts via `ntfy-send` if the count is at/under the
non-LiteLLM floor this ticket measured (11-66/hr; default floor 66,
`LANGFUSE_TRACE_RATE_FLOOR`). The response shape (`meta.totalItems`) is
confirmed against the actual pinned `langfuse` 2.57.13a0 API client
(`langfuse/api/resources/trace/client.py`,
`.../pagination/types/meta_response.py`), not guessed. 10 unit tests
(`harness/tests/test_langfuse_trace_rate_monitor.py`), no network required.

The 30-hour gap between the 2026-09-28T10:25:19Z restart and this ticket
existing at all happened because nothing was watching the trace *rate* —
only Langfuse's own export lag (`ops/wq1554-langfuse-lag-monitor`), which
stayed healthy throughout since whatever traces did arrive kept exporting
fine. This closes that blind spot regardless of whether the next drop has
the same root cause.

## Install

```
lumen-run --root /home/lumen/foreman/ops/wq3122-litellm-langfuse-trace-drop/install.sh
```

Idempotent, safe to re-run after every `git pull` in foreman.
