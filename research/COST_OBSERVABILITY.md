# Cost/observability dashboard for LiteLLM spend + budget tripwires

wq#1718. Build spec, not a survey. Every claim below is cited to a file or a
prior measurement; unverified items are named as such in §9 with the one-line
command that settles them.

---

## 1. What already exists, and what it actually answers

| tool | source of truth | answers | state |
|---|---|---|---|
| `bin/litellm-cost` | Langfuse ClickHouse `observations` (`bin/litellm-cost:3-10`) | 7d cost by model, effective-tokens proxy | works |
| `bin/et-report` | same ClickHouse table (`bin/et-report:4-10`) | ET efficiency metric by model | works |
| `bin/litellm-budget-alert` | `/key/list` admin API (`:53`) | per-key spend vs `max_budget`, ntfy at 80% (`:13`) | **auth-dead** (§2.1) |
| `ops/wq1406-paid-fallback-guard/paid_spend_tripwire.py` | `/spend/logs` admin API (`:86`) | today's paid spend ≥ $5 → ntfy | **auth-dead, silently** (§2.1) |
| `bin/spend-alert.py` | `messenger/data/deepseek_spend.db` | provider-balance %-remaining, 2x runaway burn | works, DeepSeek only |
| `foreman/governor/governor_core.py` | Anthropic `/api/oauth/usage` | Claude *rate* limits for dispatch gating | works; not dollars |
| Grafana 13.1.1 + VictoriaMetrics on Bes | wq#384 | fleet inventory panels | exists, no cost data |

Three ledgers, none joined: Langfuse ClickHouse (what `litellm-cost` reads),
`LiteLLM_SpendLogs` (what the caps are enforced against), and the providers'
own balances (`deepseek_spend.db`). The caps that actually stop traffic are
enforced against **SpendLogs**, so SpendLogs is the ledger a cap dashboard must
be built on. That is the ticket's framing and it is the right one.

What nothing answers today:

- **per key / per workload spend.** `litellm-cost` groups by model only. The
  workload axis already exists and is unused: `bin/litellm-provision-keys:17-51`
  provisions 21 per-service virtual keys (`lumen-agent`, `lpp`,
  `minerva-pipeline`, `model-watchdog`, …), so `key_alias` *is* the workload
  dimension. Nothing reads it as one.
- **trend.** Every tool reports a window. None stores a snapshot, so nobody can
  say "today vs the trailing 7d" or "days until this key hits its cap".
- **price movement.** No component notices a provider changing its rate, or our
  registered rate being wrong. This has already cost us once (§2.3).

---

## 2. Three defects that make today's cost alarms unsafe to trust

### 2.1 Both spend tripwires authenticate with a dead key

`ISSUE-LOG.md:371` (row 322, OPEN): `/model/info` and `/spend/logs` both return
401 with `/home/lumen/.secrets/litellm-master.env`, run as the full `lumen` user
via `lumen-run`. Measured 2026-09-11T03:42Z
(`ops/wq1712-prompt-cache-survey/spend_accounting_probe.json`). The key parses
(41 chars, `LITELLM_MASTER_KEY`) — it is stale against the running proxy.

`paid_spend_tripwire.py:136-139` catches the fetch exception and pages, so it is
noisy-dead rather than silent-dead — but only if its ntfy path works, and it
reads the same key through `read_text().split("=", 1)[1]` (`:129`), which
swallows every line after the first `=` in a multi-entry env file. The budget
alarm and the paid-spend alarm are both down, and have been for an unknown
period.

**Design consequence: the new collector must not depend on the admin API as its
only transport.** §3.

### 2.2 A hit cap never recovers, so "at cap" is terminal, not a wrap

`bin/litellm-provision-keys:38-40`, citing `docs/litellm/service_keys.md`:
`reset_budget_job.py` fails every run, so a key that reaches `max_budget` stays
dead past its `budget_duration`. Every service key is `30d`
(`litellm-provision-keys:17-51`).

Live key state at 2026-09-10 (`research/FLEET_COST_PLAN.md:354-364`):

| key | spend | max_budget |
|---|---|---|
| `lpp-2` | 40.0037 | 40 |
| `lpp-cerebras` | 10.0038 | 10 |
| `lpp-glm` | 0.0061 | 20 |

Two keys sitting *exactly* on their caps. That cost plan concluded the fleet's
08-29 → 09-01 → 09-09 rung migration was not a quality decision — the workload
was chasing whichever key still had headroom. A cap hit is therefore not a
budget event, it is a **routing event that silently changes which model runs**,
and nothing today reports it as either.

An 80%-of-budget alert is the wrong shape for this. §6 T1/T5.

### 2.3 A wrong registered price reads as $0.00 spend, and no alarm fires

`FLEET_COST_PLAN.md:421-422`: `glm-5.3-flash-zai` logged **273 successful
requests and 1,274,490 prompt tokens at $0.00000 spend**. The fleet's largest
workload was free according to the ledger the caps are enforced against. Fixed
under wq#1591, but nothing detects the next instance.

Related: `ops/wq1712-prompt-cache-survey/spend_accounting_probe.py:9-11` — if a
rung carries no `cache_read_input_token_cost`, LiteLLM prices every prompt token
at the full input rate and `SpendLogs.spend` is an upper bound, not the invoice.
Either direction of error is invisible today.

### Non-defect worth pinning

LiteLLM's Prometheus `/metrics` callback is enterprise-gated
(`kb/tooling-findings-review.md:82`). Do not design the pipeline around scraping
the proxy. This spec does not.

---

## 3. Source-of-truth decision

**Primary transport: read `LiteLLM_SpendLogs` directly from LiteLLM's Postgres,
read-only.** Not the admin API.

Rationale: the admin API is the component that is currently broken (§2.1), it
paginates 200 rows at a time over HTTP for what is one indexed range scan, and
it couples the cost pipeline to master-key rotation hygiene — a thing this fleet
has demonstrably not kept up. A read-only Postgres role is rotated on its own
schedule and cannot be invalidated by a proxy restart.

**Fallback transport: `GET /spend/logs?start_date=&end_date=&page=&page_size=200`**
with the master key in the `Authorization` header, exactly as
`paid_spend_tripwire.py:85-99` does it. Selected only when the Postgres DSN is
absent. Never put the credential in a URL query string (wq#948).

**Transport health is itself a tripwire (T6).** Today's failure mode is that the
alarm dies and the fleet reads silence as "under budget". The collector treats
"I could not read the ledger" as a page, not a log line.

### Verified row fields

From in-repo uses of real responses — `paid_spend_tripwire.py:95-96` and
`spend_accounting_probe.py:91-98`:

```
startTime  model  model_group  spend  prompt_tokens  completion_tokens
cache_hit  cache_key
```

`/key/list` records carry `key_alias`, `spend`, `max_budget`
(`bin/litellm-budget-alert:82-84`).

Fields this spec wants but has **not** confirmed on a live row: `api_key` (the
hashed key id, needed for the key→workload join), `end_user`, `request_tags`,
`team_id`, `custom_llm_provider`, `metadata`. Column names on the Postgres side
are quoted PascalCase in LiteLLM's Prisma schema (`"LiteLLM_SpendLogs"`,
`"startTime"`). §9 R1 settles both in one query. The build gates on it:
`spend-sync` refuses to create its schema until it has introspected the real
column set and written it to `schema_snapshot.json`.

---

## 4. Local ledger

SQLite at `/home/lumen/agents/litellm-spend/spend.db`, matching the
`spend-alert.py` / `deepseek_spend.db` house pattern: a poller snapshots, alarms
are pure functions over the snapshot, the report renders from it. This survives
the gateway being down, which a live-query dashboard does not, and it is what
makes trend possible at all.

```sql
-- one row per SpendLogs row, idempotent on request_id
CREATE TABLE calls (
  request_id        TEXT PRIMARY KEY,
  start_time        TEXT NOT NULL,      -- ISO8601 UTC
  model             TEXT NOT NULL,
  model_group       TEXT,
  provider          TEXT,
  api_key_hash      TEXT,               -- joins -> keys.key_hash
  spend_usd         REAL NOT NULL,
  prompt_tokens     INTEGER NOT NULL,
  completion_tokens INTEGER NOT NULL,
  cache_hit         INTEGER,            -- 0/1/NULL
  cache_read_tokens INTEGER,
  ingested_at       TEXT NOT NULL
);
CREATE INDEX calls_start ON calls(start_time);
CREATE INDEX calls_key   ON calls(api_key_hash, start_time);

-- key identity + cap state, snapshotted each poll (this is the trend series)
CREATE TABLE key_snapshots (
  ts             TEXT NOT NULL,
  key_hash       TEXT NOT NULL,
  key_alias      TEXT,
  workload       TEXT,                  -- from SERVICES, §5 C2
  spend_usd      REAL,
  max_budget     REAL,
  budget_duration TEXT,
  budget_reset_at TEXT,
  PRIMARY KEY (ts, key_hash)
);

-- implied unit prices per rung, fitted nightly (§6 T2/T3)
CREATE TABLE price_observations (
  ts              TEXT NOT NULL,
  model           TEXT NOT NULL,
  in_usd_per_mtok REAL,
  out_usd_per_mtok REAL,
  r_squared       REAL,
  n_samples       INTEGER,
  PRIMARY KEY (ts, model)
);

-- pinned expected prices; a diff against price_observations is the tripwire
CREATE TABLE price_registry (
  model            TEXT PRIMARY KEY,
  in_usd_per_mtok  REAL NOT NULL,
  out_usd_per_mtok REAL NOT NULL,
  pinned_at        TEXT NOT NULL,
  pinned_by        TEXT              -- wq id or 'auto-accept'
);

-- alert dedup, mirroring spend-alert.py's once-per-threshold-until-reset rule
CREATE TABLE fired (
  tripwire   TEXT NOT NULL,
  subject    TEXT NOT NULL,          -- key alias or model
  level      TEXT NOT NULL,
  fired_at   TEXT NOT NULL,
  cleared_at TEXT,
  PRIMARY KEY (tripwire, subject, level)
);

CREATE TABLE sync_state (k TEXT PRIMARY KEY, v TEXT);
-- watermark, last_ok_ts, last_error, consecutive_failures
```

Ingest is watermarked on `start_time` with a **60-second replay overlap**
(`WHERE startTime > watermark - 60s`), de-duplicated by `request_id` primary
key. Without the overlap, rows written by in-flight requests whose `startTime`
predates the watermark are lost forever; with it, they are re-read and
discarded. This is the single most common silent-undercount bug in this shape of
collector.

Retention: `calls` raw rows 90d, then fold into a `daily_rollup` table
(`date, model, workload, calls, spend_usd, in_tok, out_tok, cache_read_tok`)
kept indefinitely. At the fleet's ~60k requests/month that is a few MB/yr.

---

## 5. Components

### C1 — `bin/litellm-spend-sync` (Python, systemd timer, every 5 min)

Pulls new SpendLogs rows into `calls`, snapshots `/key/list` into
`key_snapshots`, updates `sync_state`. Exits 0 on success, exits 2 and pages on
transport failure. `--dry-run` prints the row count it would ingest and writes
nothing. `--since ISO8601` for backfill.

Credential handling: parse the env file **line-wise** (the
`read_key()` shape at `spend_accounting_probe.py:44-56`, written precisely
because `paid_spend_tripwire.py:129`'s `split("=", 1)[1]` returns a broken key
from a multi-entry file). Never print it; never place it in a URL.

### C2 — the workload map

`key_alias → workload` is a static dict derived from
`bin/litellm-provision-keys:17-51`, which already carries budget, duration and a
human description per service. C2 is a ~30-line module that imports that
`SERVICES` dict rather than restating it, so a new provisioned key appears on the
dashboard automatically. Unmapped aliases render as `unmapped:<alias>` and are
counted — an unmapped alias with real spend means someone is calling the gateway
off-book, which is itself worth seeing.

Rotated keys (`litellm-provision-keys:129-164` mints `<alias>-N` on rotation)
normalize to the base alias by stripping a trailing `-\d+`, so a rotation does
not split a workload's history in two.

### C3 — `bin/litellm-spend` (the report; primary consumer is an agent)

The house idiom for status is a CLI with `--json` (`fleet-status`,
`litellm-cost`, `et-report`) because the reader is usually the orchestrator, not
a human at a screen. The dashboard is therefore text-first:

```
litellm-spend report [--days 7] [--json]
litellm-spend keys                 # cap table + ETA, the §7 panel B
litellm-spend prices [--pin MODEL] # observed vs registered, pin a new price
litellm-spend check                # run tripwires, exit 1 if any fired
```

`report` renders panels A–E (§7) as fixed-width text. `--json` emits the same
structure for the governor and for a future Grafana JSON datasource. Must run in
under 1s — it is a local SQLite read with no network.

### C4 — `bin/litellm-spend-check` (tripwires, systemd timer, every 15 min)

Wraps `litellm-spend check`. Each tripwire is a pure function
`(ledger_rows, config) -> list[breach]`, testable without a gateway — the shape
`paid_spend_tripwire.py:61-73` already uses. Pages via
`bin/ntfy-send -t lumen-alerts -p <prio> -T <title> --quiet`, which exits nonzero
unless an endpoint returned 200 (`bin/ntfy-send:4`), so a failed page is itself
detectable.

### C5 — static HTML (optional, after C1–C4 are green)

`litellm-spend report --html > /home/lumen/www/spend.html` on the same timer, for
the Glance container. No JS, no build step, one table per panel. Grafana on Bes
(`FOREMAN_RESEARCH.md:258`) is a **third** consumer, not the first: it lives
behind an SSH tunnel that already restart-loops
(`FLEET_SERVICE_ATLAS.md:23`, `lumen-powerdash-vmtunnel` NRestarts=6), so a cost
alarm must not depend on it. If Grafana panels are wanted later, push
`daily_rollup` to VictoriaMetrics with cumulative temporality
(`FOREMAN_RESEARCH.md:254-255` — VM silently drops delta series).

---

## 6. Tripwires

Config in `/home/lumen/etc/litellm-spend.conf`, env-overridable, defaults below.
Dedup rule for all: fire once per `(tripwire, subject, level)` until the
condition clears for a full evaluation cycle, then mark `cleared_at` and re-arm
(`spend-alert.py`'s once-per-threshold-until-top-up rule, generalized).

### T1 — key nears cap, measured in days not percent

Percent is the wrong unit when a cap never resets (§2.2). For each key:

```
burn_24h = sum(spend) over trailing 24h from calls joined on key
days_to_cap   = (max_budget - spend) / burn_24h
days_to_reset = (budget_reset_at - now) / 1 day
```

- **warn** (`default`): `days_to_cap < days_to_reset` — this key will exhaust
  before it resets. Message names the workload and the ETA date.
- **urgent**: `days_to_cap < 2`.
- **max**: `spend >= max_budget` — the key is *dead*, and per §2.2 dead is
  permanent until someone acts. This is a traffic-routing incident.

Suppress T1 for keys with `burn_24h == 0` (a dormant key is not approaching
anything) unless it is already at cap.

### T2 — provider price moved

No admin scope, no new API calls. `lumen-model-watchdog` already probes all 7
rungs every 10 minutes at `max_tokens=5` (`litellm-provision-keys:35-37`), so
each rung accumulates ~144 priced rows/day in `calls` with varying
`completion_tokens`. Nightly, least-squares fit
`spend ≈ a·prompt_tokens + b·completion_tokens` over the trailing 7d for each
model, write `(a, b, r², n)` to `price_observations`.

Fire when `|observed − registered| / registered > 1%` on either coefficient, with
`n >= 50` and `r² > 0.99`. Below those thresholds, record and stay silent — a bad
fit is not a price move. Priority `high`; message gives old rate, new rate, and
the resulting change to the trailing-30d bill, which is the number that decides
whether anyone cares.

`litellm-spend prices --pin <model>` writes the observed rate into
`price_registry` with the wq id, which is how a confirmed provider change is
acknowledged. Unpinned drift keeps firing daily.

### T3 — priced at zero (the §2.3 detector)

`sum(prompt_tokens) > 100000 AND sum(spend) == 0` over trailing 24h for any
model. Priority `urgent`. This is the glm-5.3-flash-zai failure exactly — 1.27M
prompt tokens at $0.00000 — and it is the cheapest tripwire here to write.

Companion, lower priority (`default`): a rung with `cache_hit` rows but no
`cache_read_tokens` discount visible in the fitted price means SpendLogs is
pricing cache reads at full rate and the ledger is an upper bound
(`spend_accounting_probe.py:9-11`). Report it on the dashboard; do not page.

### T4 — runaway burn

Fleet-wide and per-workload: `spend_24h > RUNAWAY_X * mean(daily spend, 7d)`,
`RUNAWAY_X` default 2.0, with a floor of `$1.00/24h` so a fleet that spent 2¢
last week does not page on 5¢. Direct port of `spend-alert.py`'s rule to the
SpendLogs ledger. Priority `high`.

Per-workload matters more than fleet-wide here: `FLEET_COST_PLAN.md:77-88` found
spend is bursty rather than steady, so a fleet-wide 2x threshold is noisy while a
single workload doubling is a real signal.

### T5 — cap stuck past its reset

`spend >= max_budget AND budget_reset_at < now`. This is §2.2's broken
`reset_budget_job.py` observed from the outside, and it distinguishes "throttled
until the 1st" from "permanently dead". Priority `urgent`, message includes the
`litellm-provision-keys` rotate command as the remedy. If this never fires, the
reset job got fixed — which is worth knowing too, so `litellm-spend keys` prints
the observed reset behaviour of every key regardless.

### T6 — ledger staleness / dead man

`now - sync_state.last_ok_ts > 30 min` → the collector or the gateway is down and
**every other tripwire above is now blind**. Priority `urgent`. Message says so
explicitly, because §2.1's lesson is that a quiet alarm reads as good news.

Second dead-man at a different layer: `consecutive_failures >= 3` pages even if
the timer is still firing. `FLEET_SERVICE_ATLAS.md:16` notes the fleet has no
convention distinguishing "guard fired" from "guard broke", so C4 exits **0** on
a fired tripwire (it worked) and **2** on its own failure (it broke), and the
unit carries `OnFailure=systemd-onfailure-ntfy@%n.service`. Do not join the
four-units-permanently-red set.

---

## 7. Dashboard panels

`litellm-spend report` output, top to bottom, most-actionable first.

**A. Headline.** Trailing 24h spend, 7d, 30d; 30d run-rate projection; delta vs
the previous equivalent window. One line each.

**B. Keys — cap table.** `alias | workload | spend/cap | % | burn/24h | days to
cap | reset | status`. Sorted by days-to-cap ascending, so the key about to go
dark is the first thing on the screen. Status is the T1/T5 level. This panel is
the one that would have shown `lpp-2` and `lpp-cerebras` pinned at their caps
before the workload started rung-hopping.

**C. Workloads.** `workload | calls | spend | $/call | in_tok | out_tok |
cache_read | share of fleet`. Sorted by spend. `FLEET_COST_PLAN.md:29` already
found the workload, not the model, is the cost story — this is the panel that
says so continuously instead of once per research ticket.

**D. Models.** `model | calls | spend | $/Mtok in | $/Mtok out | observed vs
registered | cache-hit %`. The price column is T2's evidence surfaced before it
becomes an alert. Also where the D6 class shows up as a visible `$0.000000`.

**E. Trend.** 14-day sparkline-as-text of daily fleet spend, plus per-workload
daily for the top 5. Sourced from `daily_rollup`, so it keeps working after raw
rows age out.

**F. Health footer.** Last successful sync, rows ingested last cycle, consecutive
failures, which tripwires are armed, which are currently fired. If the data is
stale, every panel above it is stamped `STALE as of <ts>` rather than rendering
numbers that look current. A dashboard that silently shows yesterday's figures is
worse than one that shows an error.

---

## 8. Build order and acceptance

Each step is independently landable and independently useful.

1. **R1 (§9) — introspect the real schema.** Blocks everything; one query.
2. **C1 sync + schema.** Accept: `litellm-spend-sync --dry-run` reports a row
   count matching `/spend/logs` for the same window ±0; running it twice ingests
   the second time zero new rows (idempotence); killing it mid-run and re-running
   loses nothing (watermark overlap). Unit tests against a fixture DB, no
   gateway.
3. **T6 + T3.** The two tripwires that catch "the ledger is lying to you". Ship
   before any panel — a dashboard whose input is unvalidated is worse than no
   dashboard. Accept: fixture with a 45-minute-old `last_ok_ts` fires T6; fixture
   with 1.27M prompt tokens at $0 spend fires T3 (replay the real
   glm-5.3-flash-zai numbers from `FLEET_COST_PLAN.md:421-422`).
4. **C2 workload map + C3 `report`/`keys`.** Accept: panel B reproduces the
   `FLEET_COST_PLAN.md:354-358` key table from live data; `--json` round-trips;
   runs in <1s.
5. **T1 + T5 + C4 timer.** Accept: fixture at `spend == max_budget` with
   `budget_reset_at` in the past fires T5 at `urgent` and not T1-warn; a dormant
   zero-burn key fires nothing; dedup holds across two consecutive runs.
6. **T2 + T4 + `prices`.** Accept: fit on 7d of `model-watchdog` rows returns
   r² > 0.99 for at least 5 of 7 rungs (if it does not, the probe shape is too
   uniform to fit — fall back to T2-as-fingerprint, alerting on any change in
   cost-per-fixed-probe, and say so); a synthetic +10% price change fires once
   and stops after `--pin`.
7. **Retire the dead tools.** `litellm-budget-alert` and `paid_spend_tripwire.py`
   both become thin wrappers over `litellm-spend check` or get deleted. Leaving
   two auth-dead alarms installed next to a working one is how the next operator
   concludes the caps are monitored when they are not.
8. **C5 HTML / Grafana.** Optional, last.

"Done" for steps 2–6 means tests run and pass against fixtures plus one live
run whose output is pasted into the ticket notes.

---

## 9. Unresolved, with the command that settles each

**R1 — SpendLogs column set and the Postgres DSN.** Needs a live introspection;
`ls /home/lumen/docs/litellm` and the LiteLLM compose file are outside a build
worker's sandbox (`agents.yaml` add_dirs), so this is deliberately not guessed.
Run as `lumen` via `lumen-run`, output to a file, never to a terminal:

```
ssh lumen@100.117.66.102 '/home/lumen/bin/lumen-run start --name wq1718-schema -- \
  psql "$LITELLM_DATABASE_URL" -c "\d \"LiteLLM_SpendLogs\"" '
```

If the DSN is not in the environment, it is in LiteLLM's own env file; read the
*variable name* only and report the path, not the value (instinct 4).

**R2 — is the master key dead or rotated?** ISSUE-LOG row 322 is OPEN and blocks
the fallback transport and `/key/list` snapshotting, which T1/T5 need. T1/T5 can
instead derive cap state from `calls` summed per key plus the static `SERVICES`
budgets, at the cost of missing manually-created keys. Prefer fixing the key;
this needs its own ticket per row 322's own recommendation.

**R3 — does `model-watchdog`'s probe vary `completion_tokens` enough to fit?**
`max_tokens=5` may pin every probe to the same shape, collapsing the T2
regression. Settled by one query against `calls` after step 2:
`SELECT model, count(DISTINCT completion_tokens) FROM calls WHERE model_group IS
NOT NULL GROUP BY 1`. The fallback (T2-as-fingerprint) is specified in §8 step 6.

**R4 — Langfuse vs SpendLogs disagreement.** Two ledgers priced by the same
gateway should agree. They have never been compared. Panel D gains a
reconciliation column once someone runs `litellm-cost 7` beside
`litellm-spend report --days 7`; a persistent gap means one of them is mispriced
and the invoice will tell us which.

---

## 10. What this does not cover

- **Provider invoices.** SpendLogs is LiteLLM's opinion of cost, and §2.3 shows
  that opinion can be $0.00 while tokens flow. Only `deepseek_spend.db` tracks a
  real provider balance. Reconciling all providers to invoices is a separate
  ticket; without it, every number here is "our ledger says", not "we were
  billed".
- **Anthropic/Claude Code spend.** That rides OAuth rate limits, not the gateway,
  and is the governor's domain (`governor_core.py`). It will never appear in
  SpendLogs. A single pane over both is a later join, not this build.
- **Local rungs.** `qwen36-local`, ollama — zero marginal cost, so they are
  correctly $0 here. Their cost is electricity, tracked by the power inventory,
  not this ledger.
- **Cost attribution below the key.** Per-ticket or per-worker cost needs
  `request_tags` or `metadata` set at call time by the caller. R1 tells us
  whether the column exists; wiring foreman to populate it is its own ticket.
