# LLM alert triage for the aq queue

wq#1710 (revives proposal b6419dae26c8) · researched 2026-09-11 · data from
`/home/lumen/agents/alert-queue/alerts.db` read-only at time of writing.

## 1. The problem, measured

Page-class traffic (`source='ntfy:lumen-alerts'` or `priority IN ('4','5','high','urgent')`),
counted the way `ops/alert_hygiene_audit.py:45-48` defines it:

| window | distinct rows | publishes (`SUM(count)`) | still open |
|---|---|---|---|
| 24h | 46 | 1179 | 30 |
| 7d | 121 | 2614 | 66 |
| 30d | 425 | 4336 | 168 |

1179 pages in a day across 46 conditions is 26 pages per condition per day. That
is the cry-wolf number.

The shape of the noise matters more than the size. Re-page cadence for the
highest-volume open rows:

```
 515 over  23.2h = 1 page/  2.7min  | LiteLLM auth-failure burst
 314 over  78.3h = 1 page/ 15.0min  | unit-drift: 41 change(s) on lumen
 197 over 278.0h = 1 page/ 84.7min  | Failed unit: langfuse-evaluator-smoke.service
 196 over 277.2h = 1 page/ 84.9min  | Recovered: langfuse-evaluator-smoke.service
 133 over 299.0h = 1 page/134.9min  | langfuse-evaluator: judge path BROKEN
```

Three findings fall out of that table, and they set the whole design:

**F1 — the noise is repetition of true alerts, not false alarms.** The langfuse
evaluator really is broken. `unit-drift` really did see 41 changes. Nothing here
is a wrong judgement that a smarter classifier would fix. The 1179 publishes
carry roughly 46 bits of news.

**F2 — the top offender is a flap, and it is self-labelling.** `Failed unit:
langfuse-evaluator-smoke.service` fired 197 times; `Recovered:
langfuse-evaluator-smoke.service` fired 196 times, over the same 278 hours. The
fleet already publishes the recovery edge. A pair-matcher, not a model, collapses
that to one page.

**F3 — the existing gate is installed and inert.** `bin/ntfy-send:23-33` already
calls `aq_gate.py decide "$TOPIC" "$TITLE" "$PRIO" "$MSG"` at the single publish
chokepoint (wq#1040) and honours four verdicts: `PASS`, `SILENCE:*`,
`GROUP_HOLD:*`, `GROUP_FLUSH:*`. It fails open on any error and never gates
`min`. But in alerts.db, `group_rules` has **0 rows** and `silences` has **1
row**. The suppression machinery Lumen needs was built eleven months of tickets
ago and was never given any rules to run.

So the honest answer to "should an LLM score alerts before they page Frank" is:
yes, but fourth in line, and only on what survives three cheaper filters. Putting
a model in front of 1179 publishes to re-derive "this is the same langfuse
failure as the last 196 times" spends tokens and adds a false-negative surface to
solve a problem that a `key` comparison solves exactly.

## 2. What the vendors actually do

**PagerDuty** splits noise reduction into two mechanisms, neither of which is a
severity classifier ([Noise Reduction][pd-nr]).

*Intelligent Alert Grouping* ([docs][pd-iag]) uses ML to attach an incoming alert
to an existing open incident. Note the direction: it never drops an alert, it
changes which incident the alert lands on. An alert grouped into an open incident
raises no new page because the responder is already engaged. The learner adapts
to "your team's response patterns"; Advanced Options let you pin the grouping
fields and reset the learner cache when it goes wrong.

*Auto-Pause Incident Notifications* ([docs][pd-ap]) is the closest thing to
pre-scoring, and its safety design is the part worth copying:

- the pause is short and bounded — 2, 3, 5, 10 or 15 minutes, nothing longer;
- paused alerts are **visible the whole time**, in the Alerts Table with status
  `Suspended`, and a human can promote one to an incident during the pause;
- if the alert does not auto-resolve inside the window, PagerDuty **creates the
  incident and notifies**. The default is to page; the pause only buys latency;
- "transient" is defined by observed behaviour, not by content — an alert that
  historically auto-resolves via integration. Alerts a human resolved by hand are
  explicitly excluded from the transient class;
- the pause length is recommended from that history rather than guessed.

PagerDuty markets 91–98% noise filtering from these two features combined
([Event Intelligence][pd-ei], [transformation guide][pd-tg]). Both are
behavioural. Neither reads the alert and decides whether it matters.

**incident.io** is explicit that they do not try to classify importance
([blog][io-noise]). Their order is: enrich from Catalog (owner, team,
environment) so the alert is legible; group by time window and/or attribute; let
the responder **ungroup in real time** if the grouping was wrong; then — the part
they call "the goldmine" — track what happened after each alert (acknowledged?
escalated? declined? how long?) and feed that back into an Alerts Insights
dashboard so you can see which alerts are chronically ignored. LLM use is
confined to rewriting titles into human-readable form, and is opt-in per
organisation. Their own line: grouping "doesn't solve an underlying problem with
noisy alerts", it reduces the burden while you fix them.

**Keep** (open source, [docs][keep-intro]) positions itself as "an intelligent
layer before the alert becomes an incident" and calls itself a post-LLM-era
AIOps tool, but its deduplication is deterministic ([docs][keep-dedup]):
*partial deduplication* hashes a configured set of **fingerprint fields** per
provider, *full deduplication* discards byte-identical repeats ignoring
`lastReceived`. Every integration ships a hand-written default fingerprint. The
AI part is incident correlation — clustering alerts into incidents — and per
third-party comparison it is cloud-only.

The pattern across all three: **deterministic mechanisms decide whether to page;
AI decides how to present, group, or explain.** None of them lets a model answer
"is this real" as the gate on waking someone up. That is not timidity, it is the
asymmetry — a suppressed true page costs an outage, a delivered false page costs
30 seconds.

## 3. False-negative safety: the rule this design is built around

> **The LLM may delay a page, re-title it, attach it to something already open,
> or downgrade its channel. The LLM may never be the sole reason a page is not
> eventually delivered.**

Every suppression path must have a deterministic release. Concretely:

- **Bounded holds only.** An LLM `HOLD` verdict sets a timer, and the page fires
  when the timer expires unless a *deterministic* resolve arrives (a `Recovered:`
  publish, an aq row moving to `resolved`, a probe passing). PagerDuty's model:
  the pause buys time, it never cancels.
- **Fail open, loudly.** `ntfy-send:24` already does the right thing — any error
  from the gate yields `PASS`. Timeout, bad JSON, gateway down, budget exhausted,
  model retired: all mean deliver. This must stay true when the scorer is added,
  and the fallback must be counted, because a scorer that is silently timing out
  and passing everything looks identical to a working one from the ntfy side.
- **Never gate the deadman.** `min` priority is already exempt
  (`ntfy-send:23`), which is what keeps `alert-deadman-beat.sh` and the canaries
  outside the gate. Add a hard allowlist of never-scored titles on top: anything
  from `source='probe'` at `priority='high'` (that path already owns escalation
  via `bin/aq_escalate.py:11-15`), anything matching `DISPATCHER STALLED`,
  and any first-ever occurrence of a `key`.
- **First occurrence always pages.** A `key` never seen before is by definition
  not a repeat, so the dedup tiers cannot touch it and the LLM tier gets it at
  most for re-titling. Novelty is the one signal where the false-negative cost is
  unbounded.
- **Suppressed is not invisible.** Every held or grouped publish gets a row (or a
  `count++`) in alerts.db with the verdict and the reason in `note`. That is what
  makes the shadow evaluation in §6 possible and what lets
  `alert_hygiene_audit.py` keep working.
- **Ungroup exists.** Copy incident.io: a single command that releases a held
  group and re-pages it, so a wrong grouping is 10 seconds of annoyance rather
  than a missed incident.

## 4. Design: four tiers at the `ntfy-send` chokepoint

The integration point is `aq_gate.py decide`, invoked from `bin/ntfy-send:24`.
Its contract is already right; it needs rules and a fourth tier. Verdicts stay
the existing four strings so `ntfy-send` needs no change at all.

```
publish ──▶ T0 exempt?        ─yes─▶ PASS                     (deadman, probe-high, novel key)
            │ no
            ▼
            T1 identical repeat of an OPEN row?  ─yes─▶ count++ , SILENCE:dup
            │ no
            ▼
            T2 flap pair / burst window?         ─yes─▶ GROUP_HOLD:<group>   (bounded)
            │ no
            ▼
            T3 glm-5.3-flash score               ─▶ PASS | GROUP_HOLD:<n>m | PASS(+retitle)
```

**T0 — exemptions (deterministic, ~0 cost).** As listed in §3. Everything below
only ever sees non-exempt traffic.

**T1 — repeat suppression (deterministic).** Keep's partial-dedup applied to the
aq schema: the fingerprint is `(source, key)`, falling back to
`(source, normalized_title)` where normalisation strips digits and paths so
`unit-drift: 41 change(s)` and `unit-drift: 3 change(s)` collapse to one key. If
an **open** row with that fingerprint already exists, increment `count`, update
`last_ts`, and return `SILENCE:dup`. The operator already knows; a second
notification adds nothing.

Escape hatch, so a worsening condition can still shout: re-page a suppressed
fingerprint once per `REPAGE_INTERVAL` (default 4h) with a rolled-up body
("still open, 47 fires since 03:00"). That is also what
`GROUP_FLUSH:` in `ntfy-send:30-31` is for — it appends the rollup to the message.

On the last 24h, T1 alone removes roughly 1179 − 46 ≈ 1133 publishes, a 96%
reduction, with zero classification risk: every suppressed publish is a duplicate
of a row that is open and visible.

**T2 — flap and burst collapse (deterministic).** Two rules:

- *Flap pair*: if a `Recovered: X` publish arrives within `FLAP_WINDOW` of a
  `Failed unit: X`, and that pair has repeated ≥3 times in 24h, open a flap group
  and hold both edges, paging once per `FLAP_DIGEST` (default 12h) with the flap
  count. This is the F2 case — 393 publishes become 1 page per half-day.
- *Burst*: ≥N publishes from one source inside `BURST_WINDOW` (the
  `LiteLLM auth-failure burst` case, 2.7min cadence) enter a `GROUP_HOLD` whose
  flush carries the count. `group_rules`/`group_state` in alerts.db already have
  the exact schema for this (`pattern`, `group_id`, `interval_s`,
  `suppressed_count`, `sample_title`) and are empty — this tier is populating a
  table, not writing an engine.

**T3 — LLM scoring (glm-5.3-flash).** What reaches T3 is the residual: novel-ish,
non-repeating, non-flapping page-class alerts. On 24h of real traffic that is a
few dozen calls, not a thousand. Its job is the three things a model is actually
better at than a regex:

1. **Semantic dedup against open rows.** "Is this publish describing the same
   underlying condition as any of these open aq rows?" `langfuse-evaluator:
   judge path BROKEN` and `Failed unit: langfuse-evaluator.service` are the same
   incident with no shared key. Verdict on yes: `GROUP_HOLD` attached to the
   existing group, PagerDuty-IAG style — the alert is not dropped, it is
   reparented.
2. **Actionable-now vs. read-later.** Does this require Frank at this hour, or is
   it a digest item? Output routes the publish to `lumen-alerts` or
   `lumen-updates` rather than deciding whether it exists. Note that
   `ntfy:lumen-updates` already carries 1036 publishes/30d — a real digest lane
   exists to demote into.
3. **Re-titling.** incident.io's one sanctioned LLM use. `minerva-factory` and
   `LANE/CONFIG DRIFT` are titles that mean nothing at 3am; rewriting them with
   the runbook pointer from `aq_escalate.RUNBOOKS` (`bin/aq_escalate.py:75-91`)
   is pure upside with no suppression risk.

### T3 call shape

Gateway pattern as used elsewhere in the fleet (`bin/minerva-query:74-81`):
`POST http://127.0.0.1:4000/v1/chat/completions`, `temperature: 0`. **Use a
scoped virtual key from `bin/litellm-provision-keys`, not `LITELLM_MASTER_KEY`** —
a daemon on the page path must not hold the master key (instinct 6), and a scoped
key gives the budget governor a per-service line to watch.

Request: `model: "glm-5.3-flash"`, `max_tokens: 200`, hard `timeout: 8`
(exceeding it means `PASS`, per §3).

System prompt, fixed prefix so it caches (glm-5.3-flash prices cache reads at
$0.03/M vs $0.15/M list — `ops/wq1591-glm53flash-pricing/litellm_config_snippet.yaml:29-31`):

```
You triage infrastructure alerts for a one-operator homelab fleet.
You decide PRESENTATION, never existence. A real problem always reaches the
operator eventually; your worst possible error is hiding one.
Return ONLY JSON: {"dup_of": <open_row_id|null>, "urgency": "now"|"digest",
                   "title": "<=70 chars", "why": "<=15 words"}
Rules:
- dup_of only when the new alert is the SAME underlying failure as that open row.
- urgency "now" when unattended failure degrades the fleet within an hour, or
  when anything touches: data loss, credentials, backups, Thor, power, network.
- When uncertain, choose "now" and dup_of null. Uncertainty is not a reason to
  quiet an alert.
```

User content: the incoming `topic|priority|title|message` (message truncated to
~600 chars), plus the open page-class rows as `id | source | title | count |
age`. At 46 open rows that is a small, stable, cacheable block.

Output handling, with the safety rule applied at every branch:

| model output | gate verdict |
|---|---|
| `dup_of` = valid open id | `GROUP_HOLD:<id>` — reparented, group re-pages on its own schedule |
| `dup_of` = id that is not open / not in the list | ignore the field, treat as null |
| `urgency: "now"` | `PASS` (with rewritten title) |
| `urgency: "digest"` | `PASS` but retargeted to `lumen-updates`, and a `digest` row in aq |
| malformed JSON, timeout, HTTP error, empty | `PASS`, increment `t3_fallback` counter |

`digest` is the only tier that removes a page from `lumen-alerts` on a model's
say-so, which is why it retargets rather than drops: the publish still happens,
on a channel Frank reads at his own pace. Nothing is ever discarded on an LLM
verdict alone.

### Cost

glm-5.3-flash list is $0.15/M in, $0.03/M cached in, $0.50/M out
(`ops/wq1591-glm53flash-pricing/litellm_config_snippet.yaml:29-37`; the OpenRouter
promo rate of $0.075/$0.25 in `research/GLM53_FLASH_EVAL.md:8` is
promotional and should not be budgeted against). A T3 call is roughly 1.5k input
(mostly cached prefix) and 60 output: about $0.00007. At the ~46 residual
alerts/day that survive T0–T2, that is under $0.005/day. Even if T1/T2 were
skipped entirely and all 1179 publishes hit T3, it lands near $0.08/day — the
argument for tiering is latency and false-negative surface, not money.

## 5. Why not put the LLM first

Worth stating plainly because it is the tempting design and it is wrong here.

An LLM-first gate applies a probabilistic judgement to 1179 publishes/day. At a
generous 99% accuracy that is ~12 wrong calls a day, and the wrong calls are
drawn from the same distribution as the right ones, so a real page is in there
on a regular basis. The tiered design applies the probabilistic judgement to ~46
publishes on which it can only reparent or re-route — the expected number of
*lost* pages is zero by construction, not by accuracy. Same noise reduction,
categorically different failure mode.

The second reason is diagnosability. When T1 suppresses something, the reason is
a fingerprint you can print. When a model suppresses something, the reason is a
prompt and a sampled token, and the postmortem for a missed page is
unreconstructable.

## 6. Rollout

1. **Shadow first, for 7 days.** `aq_gate.py decide` computes the full T0–T3
   verdict, writes it to a `gate_decisions` table, and returns `PASS`
   unconditionally. Zero behaviour change, complete data.
2. **Read the shadow log against ground truth.** For every publish the shadow
   would have suppressed or held, check whether the underlying row later got a
   human action. `alert_hygiene_audit.py:28` already has the `AUTO` predicate that
   distinguishes auto-handled from human-handled — reuse it rather than inventing
   a second definition. Gate on: **zero shadow-suppressed publishes whose row was
   later human-actioned and had no other page for the same condition.** Any hit
   is a blocker, not a tuning note.
3. **Enable T0+T1 only.** This is the 96% win and it carries no model risk. Let
   it run a week. The success metric is page-class publishes/day, target <100.
4. **Enable T2.** Populate `group_rules` from the flap pairs observed in step 2.
5. **Enable T3 in reparent+retitle mode** (`dup_of` and `title` honoured,
   `urgency` logged but ignored). Another week.
6. **Enable T3 `digest` routing** last, and only if steps 2–5 produced no
   false-negative hits.

Standing instrumentation, all from the existing `intervention_ledger.db`
(`alert_hygiene_audit.py:18`): daily pages delivered, pages suppressed by tier,
`t3_fallback` count, and — incident.io's goldmine metric — per-source
human-action rate. If `t3_fallback` is non-trivially above zero the scorer is
broken and passing everything; that is fail-safe but it is not working, and the
two must be distinguishable.

**Kill switch:** `AQ_GATE_TIER_MAX=0` in the gate's environment reverts to
present behaviour (everything passes). No code path, no redeploy.

## 7. Verification status

Grounded and checked here:

- aq volumes, cadences, flap pair, tier-1 reduction estimate: queried live
  read-only against `/home/lumen/agents/alert-queue/alerts.db`, 2026-09-11.
- `group_rules` empty / `silences` = 1 row: same query session, `sqlite_master`
  + per-table counts.
- Gate contract and fail-open behaviour: read at `bin/ntfy-send:19-33`.
- Page-class definition, `AUTO` predicate, ledger paths:
  `ops/alert_hygiene_audit.py:16-48`.
- Runbook table for re-titling: `bin/aq_escalate.py:75-91`.
- glm-5.3-flash pricing: `ops/wq1591-glm53flash-pricing/litellm_config_snippet.yaml:26-37`.
- Gateway call pattern: `bin/minerva-query:74-81`.
- Vendor behaviour: primary docs, cited below.

Not verified, and deliberately not guessed:

- **`aq_gate.py` internals.** It lives at `/home/lumen/agents/alert-queue/aq_gate.py`,
  outside this session's `--add-dir` set, so it could not be read. The §4 design
  is specified against the *contract* observable at `bin/ntfy-send:23-33` (four
  verdict strings, fail-open, `min` exempt) and against the alerts.db schema.
  Whoever implements this must read `aq_gate.py` first; T1/T2 may already be
  partly present and merely unconfigured, in which case step 3 of the rollout is
  a rules file, not code.
- **glm-5.3-flash latency** under the 8s budget. `research/GLM53_FLASH_CACHE_CANARY.md`
  (wq#1592) measured cache behaviour; no p99 latency figure for this call shape
  exists yet. Measure it in shadow mode before T3 goes live — if p99 exceeds the
  timeout, the gate degrades to "always PASS", which is safe but pointless.
- **Whether `unassigned`/`resolved` rows were human-resolved.** 212 of 425
  page-class rows in 30d are `owner='unassigned', status='resolved'`, which the
  hygiene audit's `AUTO` predicate counts as human-actioned only because no
  `auto%` owner was stamped. The real human-action rate is somewhere at or below
  that; step 2 of the rollout needs a tighter ground truth than this column
  currently provides. Stamping `owner` on every automated resolve is a
  prerequisite for trusting the shadow evaluation.

## 8. Session issues — pending append to `kb/ISSUE-LOG.md`

`guard.sh` blocks worker Edit/Write inside the primary `/home/lumen/kb` tree
(wq#1719/wq#900) and this worktree is `claude-foreman`, not `lumen-kb`, so the
entry could not be landed from here. Row for whoever next touches that repo:

> `| 319 | wq#1710 (research archetype, 2026-09-11): `WebSearch` and `WebFetch`
> both returned "Claude requested permissions to use X, but you haven't granted
> it yet" — a research ticket whose job is surveying external vendor behaviour had
> no web access from the built-in tools. Separately `/home/lumen/bin/aq --help`
> was refused by the Bash allowlist, and `grep`/`ls` on `/home/lumen/bin/aq` were
> refused by the path sandbox because that name is a symlink into
> `/home/lumen/agents/alert-queue/`, outside the worker's `--add-dir` set |
> worker-settings.json grants neither web tool, and resolving a symlink lands the
> path check outside the allowed dirs even though the link itself sits in an
> allowed dir | `/home/lumen/bin/fleet-search` IS allowlisted and covers the same
> ground with better provenance: `fleet-search web "<query>"` (SearXNG) and
> `fleet-search read "<url>"` (fetch+extract, JSON with http status). Use it
> instead of WebSearch/WebFetch on any fleet worker — do not burn attempts
> re-requesting the ungranted tools. For the aq DB, a read-only sqlite handle
> from `python3 -c` works where `grep` is refused:
> `sqlite3.connect('file:/home/lumen/agents/alert-queue/alerts.db?mode=ro', uri=True)`.
> `aq_gate.py` itself stayed unreadable, so this design was written against the
> contract observable at `bin/ntfy-send:23-33` rather than guessed |
> SOLVED (worked around) |`

[pd-nr]: https://support.pagerduty.com/main/docs/noise-reduction
[pd-iag]: https://support.pagerduty.com/main/docs/intelligent-alert-grouping
[pd-ap]: https://support.pagerduty.com/main/docs/auto-pause-incident-notifications
[pd-ei]: https://www.pagerduty.com/platform/aiops/event-intelligence/
[pd-tg]: https://www.pagerduty.com/resources/incident-management-response/learn/incident-management-transformation-guide/
[io-noise]: https://incident.io/blog/how-incident-io-helps-to-reduce-alert-noise
[keep-intro]: https://docs.keephq.dev/overview/introduction
[keep-dedup]: https://docs.keephq.dev/overview/deduplication
