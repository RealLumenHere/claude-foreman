# wq#2625 — verify-pass (archetype 8) of wq#2623 (PR #984, `ab4dded00f`)

Verifier: claude-sonnet-5@high, fresh instance (same model as author, claude-sonnet-5).
Verdict: **CONTRADICTED** — the doc's enumeration is not "every" model-based dispatch/gate
surface as claimed; two live, wired classification gates in mailbot were missed entirely.
Escalated as **wq#2661** (opus-5@high judgment, `deps=2623`).

## What wq#2623 shipped

`research/CORAL_NEEDLE_TIER.md` only (218 insertions, no code touched, no gate touched —
confirmed via `git show ab4dded00f --stat`). Headline: "No-go, fleet-wide, at current
traffic." §2 claims to enumerate every model-dispatching surface on the fleet by reusing
`research/MODEL_ROUTING_AUDIT.md`'s table plus two surfaces found by direct grep
(`minerva_classifier`, LPP persona extractor).

## Confirmed correct

- **§1 grounding.** `classify_archetype()` (`orchestrator/router.py:778`) is a pure
  keyword/regex rubric (`_RUBRIC` at :633, `re.search` in the match loop at :806 — doc
  cites :797, off by 9 lines but the substantive claim holds) with no model call.
  `select()` (`orchestrator/router.py:551`) is a config/table-driven chain walk over
  `research/models.yaml`, also no model call. Matches the doc's claim exactly.
- **`agent_bridge._RE_TRIGGER_PAT`** cited as a tight regex heuristic — confirmed,
  `research/model_routing_audit.py:169-171` extracts it as a compiled regex;
  `MODEL_ROUTING_AUDIT.md:50` says "heuristic is tight," verbatim match.
- **§2 table cadences/models** (brain_healthcheck 5min/deepseek-v4-flash/max_tokens:300,
  lumen_model_watchdog ~10min/max_tokens:5, langfuse evaluator inactive/mistral-small)
  all match `MODEL_ROUTING_AUDIT.md:38-64` and its F5 section (`:205-214`) verbatim.
- **`minerva_proposal.py:112,121`** cited for the `minerva_classifier`/`minerva_verify`
  references — both lines confirmed exact (`minerva-classifier (every 4h): two-axis LLM
  scoring -> classified_intel` at :112; `minerva_verify.py hallucination guard...` at :121).
- **LPP persona extractor citations**, all confirmed exact: wiring at
  `pokemon/src/live_agent.ts:237` (`persona.extractor = gatewayExtractor()`), pinned-cheapest
  rung comment at `pokemon/src/config.ts:100-114`, "the common case" language at
  `pokemon/src/persona_llm.ts:32-33` (doc cites :34, off by one/two, substance correct),
  and the awaited/deadline-bounded comment at `pokemon/src/agent.ts:1360-1368` (doc cites
  :1367-1371, same region).
- **§3 cost arithmetic.** `research/FLEET_COST_PLAN.md`'s minerva-classifier row confirmed:
  3,485 in / 152 out = 3,637 total tokens/call, $2.50/mo → $0.60/mo on the glm-5.3-flash +
  cache fix. Doc's "3,485 of 3,637 tokens... boilerplate" is exactly this row.
- **DIFFICULTY_ROUTING.md quote** ("do not build an embedding classifier or train a router
  at v1," `:47-49`; "puts \[a call\] on the request path... not free," paraphrased from
  `:131-133`) both confirmed verbatim/faithfully paraphrased (bracket-substitution is
  legitimate quotation convention, not a misquote).
- **No impl tickets filed** — confirmed via `wq list`: only pre-existing wq#2622 (vision,
  explicitly out of scope per the ticket text) references Coral; nothing else references
  wq#2623 or "coral."
- **Method note (fleet-search outage)** — `kb/ISSUE-LOG.md:843` (row 731, wq#2627, same
  date) independently confirms `fleet-search web` returning `ok:false` fleet-wide, same
  root cause (wq#2595, no BRAVE_API_KEY). The doc's excuse for skipping live prior-art
  search is real, not a convenience story.

## Contradicted

**The enumeration is not exhaustive, and the doc claims it is.** wq#2623's own scope item 1
says: "enumerate every dispatch/gate decision that costs a model call." The doc's headline
(§0) states "Every model-based classification/gate surface found either (a)...(b)...(c)" —
an implicit completeness claim over the full fleet.

`/home/lumen/mailbot/src/worker.js` (a live Cloudflare Worker, cron every 15 min plus
per-inbound-email triggers, `mailbot/wrangler.toml:38`) contains two model-based
classification gates never mentioned anywhere in the doc, and absent from both source docs
it leans on:

- `screenRomanticDrift()` (`worker.js:323-362`, live call site `worker.js:919`) — a
  single-purpose binary classifier, `{"romantic_drift": boolean}`, over one inbound email
  (2,000-char cap), `deepseek-v4-flash`, `max_tokens: 40`. Gates whether the deterministic
  task-ack path is skipped in favor of the full generation path. Fail-closed on a bad
  verdict (`worker.js:364` comment).
- `runCritic()` (`worker.js:246-306`, live call sites `worker.js:1009`, `:1034`) — a
  6-boolean multi-label QA gate (`answers_question`, `repeats_prior`,
  `defers_to_owner_unasked`, `romantic_drift`, `contains_friend_redirect`,
  `contains_honesty_line`) over draft + inbound + prior-outbound history,
  `deepseek-v4-flash`, `max_tokens: 300`. Gates bounded regen vs. `safeFallback()`.

Both confirmed live/wired by reading the call sites directly, not staged/dead code.
`grep -in mailbot research/FLEET_COST_PLAN.md research/MODEL_ROUTING_AUDIT.md` returns zero
hits in either source doc — mailbot was never in scope for the audit this ticket reused
(`MODEL_ROUTING_AUDIT.md:12`'s own grep was `agents/ services/ agent/ bin/ minerva/`, never
`mailbot/`), and wq#2623 did not independently re-scan for surfaces outside that audit's
original footprint despite its own ticket text demanding "every."

**Materiality:** `screenRomanticDrift()` is, shape-for-shape, a *better* EdgeTPU candidate
than the one conditional pilot the doc does record in §6 (LPP's persona pre-filter) — it is
already a pure binary classifier over a short bounded string, with no restructuring needed
out of an open-vocabulary extractor first. This gate was never go/no-go scored in §5 and
never considered as a §6 pilot candidate, because it was never found. Whether pricing it
changes the fleet-wide no-go verdict is not something I re-derived here (mailbot's call
volume isn't in any cost doc to cite) — that judgment call, and whether the doc needs a
revision/addendum, is exactly what wq#2661 asks Frank/opus-5 to resolve.

## Probe run

`research/model_routing_audit.py` (the doc's cited "reproducible via" script) currently
crashes (`KeyError: 'model'`) re-running `burn()` against live
`/home/lumen/foreman/_runs/*/attempt-*.json` data — older attempt records (e.g.
`_runs/wq1947/attempt-1.json`) predate a `model` field that newer ones have. This is
pre-existing script/data drift, not introduced by wq#2623 (which touched no code), and does
not bear on any claim in `CORAL_NEEDLE_TIER.md` (none of its verdicts depend on re-running
this script live). Noting it here for the record, not filing separately — low value, would
just be "harden a loader against an old schema."

## Recommendation

Do not re-implement or unilaterally rewrite the doc here (out of scope for a verify-pass).
wq#2661 carries the judgment: score the two mailbot gates against §4/§5's existing rubric,
decide whether either changes the fleet-wide verdict or just adds two more no-go rows, and
decide whether `MODEL_ROUTING_AUDIT.md`'s own grep footprint should be widened so future
audits stop missing mailbot by construction.
