# wq#1299 — Phoenix tracer on the gateway node via LiteLLM OTLP (bounded)

Week-3, lowest-priority Tier-1 item from `kb/fleet-upgrade-plan-review.md`
§3: a single-container tracer attached at the **LiteLLM gateway**, not the
Ollama/GPU box and not across the inter-node bridge, so exporter traffic
stays on loopback and survives a bridge drop. Not Langfuse — see
`kb/tooling-findings-review.md` (Fable) and `kb/fleet-upgrade-plan-review.md`
(Opus), both of which land on Arize Phoenix for this stack: single
container, SQLite-backed, OTel-native, ELv2 (source-available, fine for
internal solo use).

Priority note carried over from the review: `fallback_stats.jsonl` already
had the evidence for the incident that motivated this work and nobody read
it — the tracer is diagnosis depth, not the missing-alert fix. Scoped
accordingly: bounded, not a platform build-out.

## Components

| File | Deploy target | Purpose |
|---|---|---|
| `phoenix.service` | `/etc/systemd/system/phoenix.service` | runs the `arizephoenix/phoenix` container, loopback-only ports, `MemoryMax=1G` |
| `litellm_config_snippet.yaml` | merge into the live LiteLLM proxy `config.yaml` (outside git, see below) | registers the sampling callback |
| `litellm_phoenix_sampler.py` | onto the LiteLLM proxy's PYTHONPATH | success/failure-aware sampling wrapper around LiteLLM's built-in OTel logger |
| `phoenix_disk_guard.py` | run in place at `/home/lumen/foreman/ops/wq1299-phoenix-tracer/` | bounded-retention purge + disk-usage alert |
| `phoenix-disk-guard.service` + `.timer` | `/etc/systemd/system/` | runs the guard every 6h |
| `install.sh` | — | idempotent installer, run from a root/interactive session |

## Guardrails (ticket requirements, and how each is met)

- **Bounded retention (7–14d).** `phoenix_disk_guard.py` purges rows older
  than `--retention-days` (default 14) directly from the SQLite file on
  every timer run, schema-defensively (see below). Phoenix itself may also
  ship a native project-level retention setting in the deployed image
  version — `VERIFY` on first deploy, and treat the script as the enforced
  backstop either way, not as redundant with an unconfirmed native feature.
- **Sample successes / trace all errors.** Not implemented as an OTel head
  sampler — the outcome isn't known at span-start, so a head sampler can't
  conditionally trace on error. Instead `litellm_phoenix_sampler.py`
  subclasses LiteLLM's built-in `OpenTelemetry` logger and makes the
  sampling decision in `async_log_success_event` (probabilistic,
  `PHOENIX_SAMPLE_RATE`, default 0.2) vs. `async_log_failure_event` (always
  calls through). Registered as both `success_callback` and
  `failure_callback` in the config snippet.
- **Disk alert.** Same script, edge-triggered ntfy (breach→clear/
  clear→breach only, matching wq#911's pattern) so a sustained breach pages
  once, not every 6h.
- **`MemoryMax=`.** Set on `phoenix.service` (1G max, 768M high) so the
  container cannot OOM the gateway host. Starting value, not measured —
  `VERIFY` against observed RSS after a burn-in period and tighten or
  loosen.
- **Exporter async + fails-open.** Two layers: (1) LiteLLM's own callback
  dispatch is documented to isolate callback exceptions from the request
  path (`kb/fleet-upgrade-plan-review.md` §3 flags this itself as
  `VERIFY`); (2) belt-and-suspenders in code regardless of (1) —
  `litellm_phoenix_sampler.py` wraps every export call in try/except and
  only logs a warning on failure, never raises into the gateway's response
  path. **Action needed on first deploy:** confirm (1) directly — kill the
  Phoenix container while the gateway is live and verify chat completions
  keep succeeding; don't just trust the docs. Not verifiable from this
  BUILD worker's sandbox (no docker start/stop access, no live gateway).

## Why the LiteLLM config and Phoenix container aren't landed as executable
diffs against a live file

This BUILD worker's sandbox has no filesystem access outside its worktree
plus `--add-dir` roots (`/home/lumen/bin`, `/home/lumen/kb`, etc.) — the
live LiteLLM `config.yaml` and the gateway's docker state aren't in that
set, and the guard hook blocks `docker` beyond `ps`/`logs` and blocks writes
under `/etc`. Same convention as wq#897/wq#911/wq#948: this PR ships the
files and an idempotent `install.sh`; a root/interactive session applies
them on the gateway node and records the result (container active, timer
enabled, one real trace visible in the Phoenix UI for a success and a
forced-failure call).

## Schema-defensive purge

`phoenix_disk_guard.py`'s `TABLE_TIMESTAMP_COLUMNS` names the tables/columns
Arize Phoenix's SQLite schema is documented to use (`spans.start_time`,
`traces.start_time`) — `VERIFY` against `sqlite3 phoenix.db .schema` on
first deploy, since this is training-era knowledge, not something checked
against a live install. If a table or column doesn't exist, the purge step
skips it and logs a warning rather than guessing at a `DELETE` target — a
no-op is safe, a wrong `DELETE` is not. The disk-usage alert (guardrail #2)
is independent of this and fires regardless of whether the purge actually
matched anything, so a schema mismatch degrades to "no purge, but you still
get paged before disk fills" rather than silent data loss of the retention
guarantee.

## Bridge safety

Everything here binds to `127.0.0.1` only — the Phoenix container's UI/query
port (6006) and OTLP ports (4317/4318), and the LiteLLM callback's default
`PHOENIX_OTLP_ENDPOINT`. No router/bridge IPs are referenced anywhere in
this ticket, and nothing here triggers an `ollama pull` or any cron-driven
model pull.

## Test procedure

```
python3 ops/wq1299-phoenix-tracer/tests/test_litellm_phoenix_sampler.py
python3 ops/wq1299-phoenix-tracer/tests/test_phoenix_disk_guard.py
```

Both run standalone against fixtures/temp SQLite files — no live Phoenix,
LiteLLM, or ntfy required. `litellm_phoenix_sampler.py`'s pure
`should_sample_success()` decision function is tested directly since
`litellm`/`opentelemetry` aren't installed in this sandbox (confirmed: `pip
show litellm` — not found); the module degrades gracefully (`_HAVE_LITELLM =
False`) rather than failing to import when they're absent, which is what
lets these tests run here and in CI without the full LiteLLM dependency
tree.

## Deploy checklist (for whoever runs `install.sh`)

1. `install.sh` — brings up `phoenix.service` + `phoenix-disk-guard.timer`.
2. Merge `litellm_config_snippet.yaml` into the live proxy config; deploy
   `litellm_phoenix_sampler.py` onto its PYTHONPATH; restart the proxy.
3. Confirm the proxy startup log shows the custom callback loaded (exact
   log line is version-dependent — `VERIFY`).
4. Send one success and one forced-failure call through the gateway;
   confirm both appear in the Phoenix UI at `http://127.0.0.1:6006`.
5. Kill the Phoenix container; confirm gateway calls still succeed
   (fails-open check called out above).
6. `systemctl list-timers phoenix-disk-guard.timer` — confirm it's
   scheduled; optionally run `phoenix_disk_guard.py check --dry-run` once by
   hand to sanity-check the schema-match warnings against the real db.
