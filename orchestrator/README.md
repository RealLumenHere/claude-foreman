# orchestrator — T3 orchestrator tier (routing + judgment)

wq#617, BUILD_PLAN.md §T3. The layer that decides *which* (model, effort) works a ticket
and *when* the foreman itself thinks. It spends no quota: the router decides, fworker
(T2a, `../harness/`) spawns, the governor (T1, `../governor/`) gates.

## Layout

    router.py       models.yaml loader; archetype -> (model, effort) with the downgrade /
                    escalation chain, the dominated_never_select hard guard, effort-first
                    downgrade under soft pressure, §15 keyword classifier, wq assign,
                    refusal-fallback awareness, fworker dispatch-cmd
    judgment.py     verify-pass filing on a shipped diff (archetype 8) + Fable-5@high
                    judgment-session cadence / burn-down triggers (`due` / `mark`)
    tests/          pytest (zero quota); the E2E case needs a wq with the wq#617 columns

Policy is data, not code: `../research/models.yaml` carries `routing:`,
`dominated_never_select:`, `refusal_fallback:`, `effort_resolution:` (from the research
docs) plus the T3 blocks `verify_pass:` and `judgment:`. `router.py check` refuses a
manifest whose routing chain contains a dominated tuple.

## The tuple lives on the ticket

`wq` (lumen-bin) gained additive `model`, `effort`, `archetype` columns and
`wq add/edit --model --effort --archetype`. `router.py assign <id>` classifies + selects +
writes them; `agent-prompt` prints the tuple in its header; `fworker run` reads them when
`--model/--effort/--archetype` are omitted (explicit flags still win; an unassigned
ticket gets routed on the spot).

    python3 orchestrator/router.py classify 640
    python3 orchestrator/router.py assign 640 [--live]      # --live asks the T1 governor for pressure
    python3 orchestrator/router.py route rca-investigation --fails 2
    python3 orchestrator/router.py dispatch-cmd 640 --repo /home/lumen/foreman
    python3 orchestrator/router.py roster | check

## Doctrine (FOREMAN_RESEARCH §10/§15, CLAUDE_CLI_INTERNALS §C)

- Primary tuple per archetype; chain after it = downgrade path. Escalation rungs are
  annotated in the manifest and unlock only on facts (`--fails`, `--cross-subsystem`,
  `--contradiction`).
- Dominated tuples are never emitted (fable-5@low/medium, opus-5@low, sonnet-5@xhigh/max
  on archetypes 1-2, every opus-4-x / sonnet-4-6 tuple while the 5-gen sibling is LIVE).
  Exhaustively asserted in `tests/test_router.py`.
- Effort-first: soft pressure (`tight`, i.e. within `SOFT_MARGIN_PCT` of a governor floor
  or a warning severity) steps the same model down one legal effort before the chain
  moves models; a hard governor `deny` skips to the next model (a lower effort cannot
  un-deny a pool). fable-5 has no legal lower effort and stays @high or is skipped.
- Refusal awareness: opus-4-8 stays LIVE + `keep_entitled` (cyber landing zone) but is
  never routed. The harness records `model_refusal_fallback` / `model_refusal_no_fallback`
  / `model_fallback` / `api_retry` frames into `_runs/wq<ID>/attempt-N.json`;
  `refusal_no_fallback` classifies REFUSED and is a judgment trigger.
- Verify-pass: on SUCCESS reconcile fworker calls `judgment.py verify-pass --for <id>`;
  policy `verify_pass.required_for` (rca-investigation, standard-engineering) plus any
  local-rung author. Verifier sonnet-5@high, fresh instance, deps on the shipped ticket,
  idempotent per ticket, `[verify] wq#<id> ...` title. `prefer_distinct_model` (default
  false) would swap to opus-5@high when the author was sonnet-5; left off until CAL-1
  says the burn is worth it. Contradiction escalates opus-5@high.
- Judgment sessions: `judgment.py due` -> fable-5@high when cadence (6h / 8 tickets
  closed) or a burn-down trigger fires (blocked-twice, verify contradiction, rca
  cross-subsystem, governor floor breach, DECOMPOSE needed, refusal_no_fallback);
  `min_gap_minutes` 60; cadence waits for off-peak, triggers do not. fable-5@max only for
  `hardest_investigation_triggers` and only once models.yaml carries `measured` data
  (CAL-1). `judgment.py mark` after a session ran.

## Not here (deliberately)

The foreman loop that calls `due` on a timer and opens the Fable session is the
dispatcher's job (today: ORCH sessions; FOREMAN_DISPATCH.md). No live-quota exercise in
the probe: `probe_wq617.py` runs this suite + the harness suite + the three named
acceptance checks against a scratch wq.
