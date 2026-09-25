# MODEL_ROUTING_AUDIT.md — fleet-wide model-routing discipline (wq#750, CAL-1+)

Measured 2026-08-22. Read-only audit: no rung was re-routed, no service was restarted.
§2's burn numbers are reproducible with `python3 research/model_routing_audit.py`; §1's
surface enumeration is reproducible with `python3 research/model_routing_endpoint_grep.py`
(added by wq#2681 — `model_routing_audit.py` enumerates no surfaces at all, §1.1).

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

**Enumeration scope (re-audited 2026-09-25 by wq#2664, corrected the same day by wq#2681
— read this before trusting the table as complete):** six instruments, three
on-Lumen-process and three deploy/endpoint based. On-Lumen process instruments: `systemctl list-unit-files`, the timer roster, and
`crontab -l`. Off-box-aware instruments added by this re-audit, closing the gap §1.1
recorded: a search for every `wrangler.toml`/deploy manifest under the registered
primaries this worktree has read access to (`foreman, mailbot, pokemon, work-queue, kb,
bin, nest` — `UID_SPLIT_FWORKER.md:126`), and a content grep for provider endpoint
strings/SDK imports (`api.deepseek.com`, `api.anthropic.com`, `api.openai.com`,
`generativelanguage.googleapis.com`, `@anthropic-ai/`, `openai`) across those same
roots. Plus the original fleet-wide grep for flagship CLI defaults
(`--model opus|fable|best|claude-opus|claude-fable` over `agents/ services/ agent/ bin/
minerva/` — **zero hits**).

**What this still does not cover:** any primary tree outside that registered read-access
list — `questfree`, `pstack`, `mission-control`, `minerva`, `agent`, `logs`,
`fleet/inventory` are registered primaries too (`primary_tree_guard.py:96-122`) but were
not in wq#2664's `--add-dir` grant, so the two new passes never searched them. wq#2681's
worktree *did* hold `minerva`, `agent` and `logs`, and ran the same six patterns over
them as an out-of-scope falsification probe: **187 files match (147 of them source or
config)** — `minerva/minerva_factory.py` and `minerva/judgearena_bench.py` among them, and
`agent/` holds the live `litellm_config.yaml` plus orchestrator scripts. Row 21 below is
the one that leaked into a searched root (via its `bin/` symlink) and is fixed here; a
real enumeration of those three trees is its own pass — **wq#2741**, not done here.

Nor does either pass cover any integration that calls a model provider without a manifest
or a matching endpoint string landing in a searched repo at all (e.g. a provider reached
through a customer SDK wrapper whose import name isn't in the grep list above).

The two new passes found two `wrangler.toml` manifests
(`mailbot/wrangler.toml`, `foreman/ops/wq1252-lumen-public-guardian/mail-probe/
wrangler.toml`). The second (`lumen-mail-probe`) is a synthetic Cloudflare-cron sender
with no model call of its own — `mail-probe/src/worker.js` contains no `deepseek`/
`anthropic`/`openai` string beyond one code comment naming the mailbot pipeline it feeds.

**The endpoint-string population (corrected by wq#2681).** wq#2664 reported "13
endpoint-string hits… read individually." That count is wrong and the read-through did
not happen: the six patterns over those seven roots match **1,445 files**
(`model_routing_endpoint_grep.py`, 2026-09-25). The population is only tractable once you
say out loud which parts are not fleet code — wq#2664 never did:

| bucket | files | why it is excluded |
|---|---|---|
| agent-session artifacts (`_runs/`, `events/`, `pilots/`, `*.jsonl`, `*.log`) | 1,327 | transcripts of agents discussing these endpoints, not code that calls them |
| prose (`.md`, `.txt`, `.diff`) | 48 | docs and tickets |
| data snapshots (`queue.db` and 17 of its backups, 11 `*.bak` copies of files already counted) | 29 | ticket text and superseded revisions |
| vendored binary (`bin/trivy`) | 1 | third-party scanner binary |
| **live source/config** | **40** | **the set that actually had to be read** |

Only the bottom four rows are stable: the run-artifact bucket grows every time an agent
runs (1,445 → 1,464 inside this session), so re-running will not reproduce the total and
is not supposed to. `source` is the row to compare against.

All 40 are enumerated with a per-file verdict in `model_routing_endpoint_grep.py`'s
`ADJUDICATED` table; `--check` exits non-zero the moment a hit appears that nobody has
ruled on. Do not re-run this pass with the box's `grep`: `/usr/bin/grep` here is
**ugrep 7.8.4**, which honours `.gitignore` on `-r`, so the same pattern over
`/home/lumen/foreman` (59 files) and over `/home/lumen/foreman/_runs` (1,277 files)
disagree — that is most of the distance between wq#2664's number, wq#2675's 102 and the
1,445 here (`kb/ISSUE-LOG.md`).

Of the 40, the ones wq#2664 named do check out: `models.yaml` is surface 1's own config
(a comment on `deepseek-v4-flash`'s `api_base`, not a new call); `foreman_usaged.py` and
`ops/wq1252-lumen-public-guardian/checks_email.py`'s `check_e3_deepseek` hit
account-status endpoints (`/api/oauth/usage`, `/user/balance`) — balance/usage checks,
not completions; `deepseek_connect_alert.py` is the D4 connect-failure monitor
(`FLEET_COST_PLAN.md` §2); `oauth_passthrough_probe.py` and `headroom_proxy_gate.py` are
research-tier proxy tooling that stands in for `api.anthropic.com`, not production
dispatch; `cli_canary.py` matches on the `@anthropic-ai/claude-code` **package name**
(checking the `claude` CLI is installed), not an API call; `wq1252…config.json` only
holds the URL `checks_email.py` reads. `claude-shim/claude_shim.py` has its own
OpenAI-compatible handler but is dormant — `CLAUDE_SHIM_DESIGN.md:10` says
`Status: DESIGN FOR REVIEW`, `claude-shim.service` is not installed and nothing listens
on `:8091`. Nine more are test fixtures, four are LiteLLM config snippets, and the last
eleven are the config generator `node_render_gateway.py` plus vendor-name string lists in
spend guards, the gitea secret scanner, two report artifacts and mailbot's leak filter.

**Net result — the corrected one: mailbot was not the only surface. Three more live
dispatch surfaces sat unread in wq#2664's own grep population** and are now rows 21-23:
`bin/minerva-factory` (a symlink into `minerva/minerva_factory.py`, hit at line 283, **on
an active 6×/day timer**), `tools/council/council.yaml` → `tools/council/council.py`
(`bin/research-council`, landed `e1a89d6` at 08:47, two hours *before* wq#2664's own
commit `a15b19b`, so it was in the tree when the grep ran), and
`harness/evals/worker-quality/promptfooconfig.yaml` (the wq#1300 promptfoo judge, landed
`37f1f6c` on 2026-09-02). mailbot's own eval/probe harnesses
(`mailbot/evals/lib/score.mjs`, `test_persona.mjs`, `test_dodge.mjs`) make the same direct
DeepSeek call as the worker on the same key — folded into row 20's note, not separate rows.

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
| 20 | mailbot (Cloudflare Worker, `mailbot/wrangler.toml:1-5`) — 4 DeepSeek calls/inbound email: `screenRomanticDrift` (`worker.js:323-362`), `runCritic` (`:246-306`), `generateReply` (`:176`), `generateNotesUpdate` (`:576`) | per inbound email | deepseek-v4-flash | direct `fetch("https://api.deepseek.com/...")`, **not** LiteLLM or the `claude` CLI | no | yes — already the cheapest cloud rung; a local accelerator can't reach it either, it's on the wrong side of the network (`CORAL_NEEDLE_TIER.md` §2.2/§5) | B |
| 21 | `minerva-factory` (`minerva/minerva_factory.py`, symlinked `bin/minerva-factory`) — plans and `--apply`s minerva proposals | timer, 6×/day (`minerva-factory.timer`, `03,07,11,17,19,23:35 UTC`), `run 15 --apply` | `deepseek-v4-pro`; retries a give-up on `cerebras-oss-120b` (`minerva_factory.py:43,46,628-644`) | LiteLLM `:4000/v1/chat/completions` | no | yes — already a cheap rung | B |
| 22 | research-council (`tools/council/council.py`, `bin/research-council`, wq#2628) — fans one prompt to N seats, then a chairman synthesizes | on demand (CLI, no timer/cron) | seats `claude-max-opus`, `gemini-flash`, `deepseek-v4-pro`, `kimi-k3` (off by default); chairman = first seat | LiteLLM (`COUNCIL_LITELLM_URL`, default `127.0.0.1:4000`) | no — the opus seat goes through `claude-shim`, which is not running (see §1 header), so that seat times out today | note only — a council is deliberately redundant | B |
| 23 | worker-quality eval (`harness/evals/worker-quality/`, wq#1300) — promptfoo LLM-as-judge over the done-claim critic | on demand (`run_eval.sh`; no timer) | `WQ1300_JUDGE_MODEL`, default alias `claude-sonnet-5` | LiteLLM loopback, OpenAI-compatible (`promptfooconfig.yaml:14-20`) | no | yes | B |

**Note on row 20.** `mailbot/evals/lib/score.mjs`, `test_persona.mjs` and `test_dodge.mjs`
issue the same `fetch("https://api.deepseek.com/chat/completions")` on the same worker key
when run by hand. They are dev-run, not per-email, so they do not get their own row — but
they are real spend and they were in wq#2664's grep population unnamed.

**Scope-2 verdict (always-on services): PASS, re-checked by wq#2681.** Surfaces 6–8,
12–17 and the newly-added 21 are the always-on set. None dispatches an Anthropic model:
21 runs `deepseek-v4-pro`/`cerebras-oss-120b`. Rows 22 and 23 can dispatch
`claude-max-opus` and `claude-sonnet-5`, but both are operator-invoked CLIs with no timer
or cron entry, so the headline claim — no *always-on* service touches opus or fable —
still holds. It is now a narrower claim than it reads: a human running
`research-council` puts opus on the fleet's bill on demand.

**Flagged, not fixed (wq#2681):** row 9 says `langfuse-ops/evaluator.py` is *inactive*,
but `langfuse-evaluator.timer` is enabled and fired 14 minutes before this edit
(`OnUnitActiveSec=15min`, `--sample-pct 5 --max-evals 20`). Whether it dispatches depends
on trace volume in the window, which this pass did not measure — the cadence column is
wrong either way. **wq#2742**, rather than guessed at here.

**Note on surface 2 (fable-5@high judgment).** fable-5@high is also the only tuple in the
whole dataset with a clean success rate — 2/2 on novel-design, at the highest measured
burn (698s median wall-clock, 75.6s CPU, 64 turns). Its 0/2 on standard-engineering
supports the manifest's own "never a volume worker" line. Leave as-is.

### 1.1 History: the on-Lumen-process gap (wq#2661 found it, wq#2664 half-closed it, wq#2675 caught that, wq#2681 closed it — all 2026-09-25)

**Status: CLOSED.** mailbot is row 20, the three surfaces wq#2664 missed are rows 21-23,
§1's header states the enumeration scope and the real population, and the pass now has a
reproducer with a failing mode (`model_routing_endpoint_grep.py --check`). This subsection
is kept as the record of how the gap was found and why the obvious fixes didn't work —
read it for that, not as an open item.

**mailbot was missing from the table above and makes four live DeepSeek calls per
inbound email**, two of them classification gates:
`screenRomanticDrift` (`mailbot/src/worker.js:323-362`, live at `:919`, binary
`{"romantic_drift":boolean}`, `max_tokens:40`) and `runCritic` (`:246-306`, live at
`:1009`/`:1034`, 6-boolean fail-closed QA gate, `max_tokens:300`), plus the generative
`generateReply` (`:176`) and `generateNotesUpdate` (`:576`). Model is
`deepseek-v4-flash` on all four, reached by direct
`fetch("https://api.deepseek.com/chat/completions")` with a worker-held key — **not via
LiteLLM and not via the `claude` CLI**, so it also sits outside the gateway's usage
accounting. Found by wq#2625's verify of wq#2623; scored and priced in
`research/CORAL_NEEDLE_TIER.md` §2.2/§3/§5.

The root cause is the enumeration method, not the grep scope, and the distinction
matters because the obvious fix does not work:

- All four instruments named above — `systemctl list-unit-files`, the timer roster,
  `crontab -l`, and the CLI-flag grep — are **on-Lumen process** instruments. mailbot is
  a Cloudflare Worker (`mailbot/wrangler.toml:1-5`) running at the edge with no unit, no
  timer and no cron entry, so all four are structurally blind to it.
- Adding `mailbot/` to the §1 grep's path list would **not** have caught it: that grep's
  pattern is `--model opus|fable|best|…`, a CLI-flag shape that mailbot's source does not
  contain. Any component that calls a provider HTTP endpoint directly is invisible to a
  CLI-flag pattern no matter which directories it is pointed at.
- `model_routing_audit.py` does not cover the gap either. It reads `_runs/`, the queue DB
  and the messenger DB (`model_routing_audit.py:32-37`) to compute burn statistics, and
  enumerates no surfaces at all — so this doc's "reproducible via
  `model_routing_audit.py`" claim applies to §2's numbers, never to §1's list.

**Done by wq#2664:** enumerated from *deployment manifests plus endpoint strings*, not
from the local process table alone — the two passes described in §1's header. mailbot's
generative calls
(`generateReply`/`generateNotesUpdate`), left unpriced by wq#2661/wq#2661's
`CORAL_NEEDLE_TIER.md` pass, are now priced in `FLEET_COST_PLAN.md` §8.

**Corrected by wq#2681 (via wq#2675's CONTRADICT):** wq#2664 also claimed its
endpoint-string pass returned 13 hits, "read individually," and that mailbot was the only
surface it added. Both halves were wrong. The pass returns 1,445 files (40 of them live
source/config); the read-through wq#2664 described did not happen, and doing it turned up
rows 21-23. Two of the three would also have been caught by the *original*
on-Lumen-process instruments this subsection blames for missing mailbot:
`minerva-factory.timer` is in the timer roster and `bin/research-council` is a symlink in
a searched root. So §1.1's root-cause story is incomplete as written — the instruments
were not only structurally blind to edge workers, they were **not actually run to
exhaustion**. The standing fix for that is the reproducer, not another prose assertion:
`model_routing_endpoint_grep.py --check` fails on any hit without a recorded verdict.

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
