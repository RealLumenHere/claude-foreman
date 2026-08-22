# MODEL_ROUTING_AUDIT.md — fleet-wide model-routing discipline (wq#750, CAL-1+)

Measured 2026-08-22. Read-only audit: no rung was re-routed, no service was restarted.
Every number below is reproducible with `python3 research/model_routing_audit.py`.

**Headline:** no fleet surface is flagship-by-default, and **no always-on service touches
opus or fable** — the thing the ticket was most worried about does not happen. The real
routing gaps are upstream of the router: the archetype classifier over-ranks 34% of
tickets into an expensive archetype, and the two cheap rungs (haiku, local) are
structurally unreachable because they sit behind a governor-pressure gate that has never
fired.

---

## 0. Read this before the tables — the measurement is censored

117 of 149 launches (79%) hit their `max_turns` ceiling and only 16 (11%) returned
SUCCESS. Every per-tuple burn figure below is therefore **truncated at the cap**, not a
natural completion cost. Turn ceilings are already under active repair (commits `62fb976`
"recalibrate turn ceilings", `300e903` "lift turn ceilings until real fix"); this audit
does not re-ticket that.

Consequences you must respect when reading section 2:

- Comparing opus-5@high against sonnet-5@high on "did it finish" is invalid — neither
  finished, both ran out of turns. The honest claim is **"no measured advantage"**, never
  "opus is unnecessary."
- **Token counts do not exist in this dataset.** `~/.claude/projects` transcripts are
  guard-denied, and `_runs/*/attempt-*.json` records no usage fields. Burn is reported in
  turns, wall-clock, and CPU seconds. The `api_price_mtok` figures in `models.yaml` remain
  list prices, not observed spend.

Evidence grades used throughout: **A** = direct measurement over the complete population;
**B** = code/config read, behaviour confirmed by executing the code; **C** = inference.

---

## 1. Every model-dispatching surface on the fleet

Enumerated from `systemctl list-unit-files`, the timer roster, `crontab -l`, and a
fleet-wide grep for flagship CLI defaults (`--model opus|fable|best|claude-opus|claude-fable`
over `agents/ services/ agent/ bin/ minerva/` — **zero hits**).

| # | Surface | Cadence | Model in use | Path | Anthropic quota | Cheapest-sufficient | Grade |
|---|---|---|---|---|---|---|---|
| 1 | foreman worker dispatch (`router.py` + `models.yaml` + `fworker.py`) | per ticket claim | sonnet-5 ×111, opus-5 ×34, fable-5 ×4 | `claude` CLI, OAuth | **yes** | partly — §3 F1/F2 | A |
| 2 | foreman orchestrator judgment | 6h cadence / burn-down triggers | fable-5@high | `claude` CLI | **yes** | yes (§2 note) | B |
| 3 | foreman verify-pass gate | per shipped diff | sonnet-5@high | `claude` CLI | **yes** | yes | A |
| 4 | messenger chat (`agent_bridge.py`) | per user turn, ~4.5/active day | **sonnet** | `claude` CLI, OAuth | **yes** | gap — §3 F6 | A |
| 5 | messenger RE tier | heuristic, 0.4% of turns | **opus** | `claude` CLI | **yes** | yes — heuristic is tight | A |
| 6 | `brain_healthcheck.py` | every 5 min (288/day) | `messenger-chat` → deepseek-v4-flash, `max_tokens:300` | LiteLLM | no | cheap but wrong target — §3 F5 | A |
| 7 | `lumen_model_watchdog.py` | every ~10 min | 5 cloud + 1 local probes, `max_tokens:5` | LiteLLM | no | yes | A |
| 8 | `minerva/model_audit.py` | timer | same cloud roster | LiteLLM | no | yes | B |
| 9 | `langfuse-ops/evaluator.py` (LLM-as-judge, 5% sample) | inactive | mistral-small | LiteLLM | no | yes | B |
| 10 | LPP brain (`pokemon/src/config.ts`) | per game turn | primary `cerebras-oss-120b`; escalate `deepseek-v4-pro`; actRetry + summarize `deepseek-v4-flash`; vision `lumen-vision` | LiteLLM | no | note only, wq#726 | B |
| 11 | `research/article_ingest.py` | every 15 min | deepseek-v4-flash | LiteLLM | no | yes | B |
| 12 | `lumen_self_healer.py` | ~10 min | **none** — docker/systemd restarts only | — | no | n/a | B |
| 13 | `lumen_watchdog.py` | 1 min | **none** — TCP port checks (incl. `localhost:4000`) | — | no | n/a | B |
| 14 | `lumen_corrector.py` | 30 min | **none** — ntfy POST only | — | no | n/a | B |
| 15 | `smart_watchdog.py` | timer | **none** — ntfy POST only | — | no | n/a | B |
| 16 | `jay_watch.py` | 15 min | **none** — GitHub REST only | — | no | n/a | B |
| 17 | `lpp-watchdog.sh` | timer | **none** | — | no | n/a | B |
| 18 | foreman dispatcher lanes (`dispatcher/config.json`) | 5 min | **no model choice** — `max_per_day` volume gate only | — | no | n/a | B |
| 19 | research cron (`watcher_agent`, `radar_dispatcher`, `kb_dispatcher`, `watchdog`, `lumen_scout_expanded`) | cron | **none** — keyword/HTTP only | — | no | n/a | B |

**Scope-2 verdict (always-on services): PASS.** Surfaces 6–8 and 12–17 are the always-on
set. None of them dispatches an Anthropic model. Nothing to flag for opus/fable contact.

**Note on surface 2 (fable-5@high judgment).** fable-5@high is also the only tuple in the
whole dataset with a clean success rate — 2/2 on novel-design, at the highest measured
burn (698s median wall-clock, 75.6s CPU, 64 turns). Its 0/2 on standard-engineering
supports the manifest's own "never a volume worker" line. Leave as-is.

---

## 2. Measured burn matrix (replaces the `est:` values in `models.yaml`)

n = 149 launches, `/home/lumen/foreman/_runs/*/attempt-*.json`, 2026-08-15 16:59 →
2026-08-22 04:43. Grade **A** for the counts; the burn columns are cap-censored per §0.

| archetype | tuple | n | SUCCESS | capped | turns med | turns max | elapsed med (s) | cpu med (s) |
|---|---|---|---|---|---|---|---|---|
| mechanical-edit | sonnet-5@low | 5 | 3 | 3 | 19 | 27 | 42.4 | 6.2 |
| mechanical-edit | sonnet-5@medium | 2 | 0 | 2 | 17.5 | 19 | 86.8 | 6.0 |
| novel-design | fable-5@high | 2 | **2** | 0 | 64.0 | 81 | 698.2 | 75.6 |
| novel-design | opus-5@high | 2 | 0 | 2 | 45.0 | 45 | 205.4 | 17.9 |
| novel-design | opus-5@xhigh | 2 | 0 | 2 | 43.0 | 45 | 157.9 | 18.9 |
| probe-build | sonnet-5@medium | 6 | 1 | 5 | 27.5 | 30 | 98.7 | 8.4 |
| rca-investigation | opus-5@xhigh | 11 | 1 | 10 | 65 | 105 | 532.9 | 25.8 |
| rca-investigation | sonnet-5@high | 4 | 0 | 4 | 64.0 | 65 | 256.0 | 29.3 |
| research-synthesis | opus-5@high | 17 | 1 | 15 | 30 | 125 | 128.0 | 9.6 |
| research-synthesis | opus-5@xhigh | 2 | 0 | 2 | 30.0 | 30 | 97.0 | 8.6 |
| research-synthesis | sonnet-5@high | 2 | 0 | 2 | 27.5 | 28 | 102.9 | 8.8 |
| research-synthesis | sonnet-5@medium | 2 | 0 | 2 | 30.0 | 30 | 110.5 | 40.0 |
| standard-engineering | sonnet-5@high | 68 | 8 | 49 | 44.0 | 141 | 221.8 | 15.8 |
| standard-engineering | sonnet-5@medium | 9 | 0 | 5 | 45 | 59 | 236.7 | 13.1 |
| standard-engineering | fable-5@high | 2 | 0 | 2 | 45.0 | 45 | 235.6 | 17.9 |
| verify-pass | sonnet-5@high | 12 | 0 | 12 | 28.5 | 30 | 80.9 | 9.0 |
| research (legacy label) | sonnet-5@high | 1 | 0 | 0 | 32 | 32 | 300.6 | 8.3 |

Fleet totals: **sonnet-5 111 / opus-5 34 / fable-5 4**. `model_downgraded` 0, `saw_limit`
0, **haiku launches 0**, **local-rung launches 0**.

Governor state at audit time: session **13%** utilised, weekly_all **22%**, fable_weekly
**16%**, `floor_breaches: []`, `can-dispatch claude-sonnet-5 → allow`. The fleet is
nowhere near a floor.

### Haiku vs sonnet@low — the CAL-1 open item

**Undecidable from this data, and the ticket's suspected cause is wrong.** Zero haiku
launches is not the classifier over-ranking simple work. It is mechanical:

1. `models.yaml routing:` lists haiku **only** as the second rung of `probe-build`. It is
   the primary for no archetype.
2. The chain after a primary is walked **only on governor pressure**, never on attempt
   count. Verified by executing the router (grade **B**, confirmed by running the code):

   ```
   router.py route probe-build --fails 0|1|2         -> claude-sonnet-5@medium   (never moves)
   router.py route mechanical-edit --fails 0|1|2     -> claude-sonnet-5@low      (never moves)
   router.py route probe-build --pressure claude-sonnet-5=deny
                                                     -> claude-haiku-4-5         (reachable)
   ```
3. Pressure has not occurred in the measured window (`saw_limit` 0/149, utilisation 13%).

So haiku is reachable, correctly wired, and dead in practice. **Placing haiku against
sonnet@low needs a deliberate A/B, not more observation** — see follow-up ticket in §4.
Any manifest claim that CAL-1 "measured" the haiku row would be false.

### What is honestly replaceable in `models.yaml`

The `burn:` fields are updated in this branch with the measured launch data above and an
explicit `measured_tokens: null` where tokens remain unmeasurable. The `est:` strings for
token and dollar cost stay, because nothing in the available data set can retire them.

---

## 3. Findings, ranked (local-first moves first)

### F1 — P0: `research-synthesis` routes 34.8% of all ticket text to opus-5@high

`classify_archetype()` replayed over all 772 filed tickets sends **269 (34.8%)** to
`research-synthesis`, whose primary rung is opus-5@high. Of those, **113 rest solely on
`\bresearch\b` and 64 solely on `\breport\b`** — 66% of the archetype's entire volume
hangs on two words that appear in ordinary ticket prose ("report back", "research the
options"). Measured outcome gives opus-5@high no edge here: 1/17 SUCCESS against sonnet-5@high's
0/2, both cap-censored at `max_turns: 25`. Grade **A** for the classification counts,
**C** for the "no edge" reading.

### F2 — P0: `rca-investigation` (opus-5@xhigh, the priciest routed rung) fires on `why` and `audit`

142 tickets (18.4%) classify as `rca-investigation`. **43 rest solely on `\baudit\b`, 25
solely on `\bwhy\b`** — 48% of the archetype on two of the most common words in a ticket
detail. This ticket is itself an example: it is titled "audit", so it drew opus-5@xhigh.
Grade **A**.

### F3 — P1: the two cheap archetypes are structurally outranked

`_RUBRIC` is first-match-wins in list order, and the cheapest archetypes sit at the
bottom: `probe-build` 6th, `mechanical-edit` 7th (last). Any co-occurring expensive
signal beats them. **152 tickets carry a cheap signal but lose it** — to
`rca-investigation` (56), `research-synthesis` (52), `long-horizon-campaign` (23),
`verify-pass` (14), `novel-design` (7).

Counterfactual, reordering first-wins by cost instead of by list position:

| archetype | now | cheapest-hit-wins |
|---|---|---|
| mechanical-edit | 19 | 46 |
| probe-build | 23 | **147** |
| standard-engineering | 186 | 207 |
| research-synthesis | 269 | 274 |
| rca-investigation | **142** | **58** |
| long-horizon-campaign | 54 | 8 |
| novel-design | 55 | 17 |

**262 tickets (34%) would route strictly cheaper.** Grade **A** for the arithmetic. This
counterfactual is a *bias measurement, not a proposal* — pure cheapest-wins would misroute
genuinely hard RCA work. The fix is narrowing signals and requiring corroboration, not
flipping the sort.

Also: 207 tickets (27%) hit the no-signal fallthrough to `standard-engineering`. That
default is sound (sonnet-5@high, not a flagship) and needs no change.

### F4 — P1: the local rung has served 0 of 149 launches, and one of its members is not local

- Isis (`100.100.129.24:11434`) and Nephthys (`100.98.74.87:11434`) both answer
  `/api/tags` right now and are serving models. Local capacity exists and is idle.
- `models.yaml` lists `deepseek-v4-flash` under "LOCAL RUNG" with `pools: [local]` and
  `status: LIVE-local`. The LiteLLM entry has **no `api_base`** — it resolves to
  `deepseek/deepseek-v4-flash` at api.deepseek.com. It is a cheap *cloud* API that burns
  zero Anthropic quota, which is what the comment actually means, but "local" in a
  capacity-planning document should mean local compute. Only `qwen36-local`,
  `isis-qwen36`, and `nephthys-qwen3-14b` are local compute.
- `qwen36-local` was dropped from the model-watchdog roster on 2026-08-11 when
  `llama-qwen36.service` was stopped on Lumen for the resource-offload directive — but its
  LiteLLM entry still points at Isis, which is up. The rung may be alive and unmonitored.

Highest-value local-first moves, in order: (a) route `mechanical-edit` primary at the
local rung with the existing sonnet-5 verify gate — 7 mechanical-edit attempts all went to
cloud sonnet; (b) re-probe `qwen36-local`/`isis-qwen36` and reinstate it in the watchdog
roster if Isis serves it. Grade **A** for the launch counts and host reachability, **B**
for the config reading.

### F5 — P2: `brain_healthcheck` monitors a path production no longer uses

The healthcheck probes LiteLLM alias `messenger-chat` → deepseek-v4-flash every 5 minutes.
The live messenger runs `LUMEN_LLM_BACKEND=anthropic` (`/home/lumen/agent/.env`), so real
chat traffic goes through the `claude` CLI on OAuth to sonnet/opus and never touches
`messenger-chat`. **The probe would report green while the real brain is down.** This is a
correctness gap, not a cost one — the probe itself is cheap and Anthropic-free. Two smaller
notes: `max_tokens: 300` for a one-word "reply with exactly: HEALTHY" probe, and
`lumen_model_watchdog.py` explicitly lists `messenger-chat` in `NEVER_PROBE` (bug #22249)
while `brain_healthcheck` probes it directly every 5 minutes. Grade **A**.

### F6 — P2: messenger has no tier below sonnet, but the volume does not justify urgency

Measured over all 807 real user turns in `messenger.db` (2026-03-28 → 2026-08-13):

- RE-escalation heuristic fires **3 times (0.4%)**, all on the token `exploit`. It is
  **not** over-firing; `[re]->opus` is justified per-call. Scope-3 question answered: the
  heuristic does not need tightening.
- Explicit `[pro]`/`[re]`: **0**. Explicit `[local]`/`[q]`/`[m:…]`: **0**.
- Median **4.5 user turns per active day**; last message 2026-08-13.

So every ordinary chat turn goes to sonnet on the shared Max session pool, competing with
foreman workers, with no cheaper tier auto-selectable — the local `isis-qwen36` rung is
opt-in-only and has been used zero times. At 4.5 turns/day this is a small leak. Worth a
haiku or local default eventually; not worth pre-empting F1–F4. Grade **A**.

### F7 — note only: LPP brain profiles (scope 4, do not fix here)

`pokemon/src/config.ts`: `primary: cerebras-oss-120b` (good), `escalate: deepseek-v4-pro`,
`actRetry: deepseek-v4-flash`, `summarize: deepseek-v4-flash`, `vision: lumen-vision`
(openrouter qwen3-vl-32b). No Anthropic quota on any path. Cross-referenced to wq#726 as
the ticket directs; no change proposed here. Grade **B**.

---

## 4. Follow-up tickets

Filed, local-first ordered.

| Ticket | Finding | Pri | Ask |
|---|---|---|---|
| **wq#777** | F4 | 1 | Route `mechanical-edit` primary to the local rung behind the existing sonnet-5 verify gate; re-probe `qwen36-local` on Isis and reinstate it in the model-watchdog roster; correct the `pools: [local]` label on `deepseek-v4-flash` to say cheap-cloud / zero-Anthropic. |
| **wq#778** | F1 + F2 | 1 | Drop or corroborate `\bresearch\b`, `\breport\b`, `\baudit\b`, `\bwhy\b`; require ≥2 signals or a phrase-level match before an archetype whose primary is opus; add a regression fixture from the 772-ticket replay so the distribution can't silently drift back. |
| **wq#779** | F3 | 2 | Make `_RUBRIC` resolution cost-aware instead of list-order-first-wins, and give the downgrade chain an attempt-count trigger so haiku and the local rung are reachable without a governor floor breach. |
| **wq#780** | haiku placement | 2 | Deliberate paired haiku vs sonnet@low trial. CAL-1 cannot close this by observation — utilisation is 13% and pressure never fires. Give both arms the same turn ceiling or the result measures the ceiling. |
| **wq#781** | F5 | 2 | (lane `fleet-fires`) Make `brain_healthcheck` follow the live `LUMEN_LLM_BACKEND` so it fails when production fails; drop `max_tokens` to ~10; reconcile the `NEVER_PROBE` disagreement with the model-watchdog. |

F6 (messenger has no tier below sonnet) and F7 (LPP profiles) are deliberately **not**
ticketed: F6 is a real but small leak at 4.5 turns/day and should wait behind F1–F4, and
the ticket directs that F7 be noted against wq#726 rather than fixed here.

---

## 5. Reproducing this

```
python3 research/model_routing_audit.py            # all sections
python3 research/model_routing_audit.py burn       # §2 burn matrix
python3 research/model_routing_audit.py classify   # §3 F1/F2/F3
python3 research/model_routing_audit.py messenger  # §3 F6
```

The script is read-only: `sqlite3 mode=ro` on both databases, plain reads on the run
journal, and it never dispatches a model. Router behaviour in §2 was confirmed by
executing `orchestrator/router.py route` directly, not by reading the code alone.

Both source populations grow, so a later run will not reproduce these counts exactly. The
figures above are the 2026-08-22 snapshot: **n=149** launches and **n=772** filed tickets
(the five follow-ups in §4 were filed after the replay, which is why a re-run now reports
777). Percentages move by fractions of a point at this rate; the findings do not turn on
them.
