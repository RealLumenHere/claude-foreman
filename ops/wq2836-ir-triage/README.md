# lumen-ir-triage (wq#2836)

Read-only, proposal-only incident triage lane, modelled on X-MASIR
(doi:10.34218/ijaiml_05_02_007). Minerva proposal f23901c2e85c, factory plan
973865076d29. It is not a SOC and it never acts on the fleet.

## What a pass does

1. **Collect.** It reads three sources and writes to none of them:
   - `/home/lumen/agents/alert-queue/alerts.db`. It takes open rows at priority
     high/urgent (or 4/5), plus every `guard-fired` row. At most 40 rows.
     Gatus probe failures, ECC, load, spof dead-man alerts and the rest of the
     ntfy alerts all arrive here, through `lumen-alert-queue`.
   - `/home/lumen/agents/failed-units/state.json`, written by `lumen-failed-units`.
   - The newest `/home/lumen/services/trivy-weekly/reports/trivy_*.json`. A report
     that can't be parsed (a FATAL scan) counts as a fragment too, because it means
     host CVE coverage is blind.

   Each fragment gets a stable ID (`aq#2080`, `unit:<name>`, `trivy:<stamp>`), a
   source and a timestamp. Its text is masked through `harness/masking.py`.
2. **Skip if nothing is new.** The model runs only when a fragment ID appears that
   the last completed pass didn't see. The seen-set is kept in
   `/home/lumen/minerva/logs/ir_triage.state.json`.
3. **Three roles, called one after another** through the LiteLLM gateway
   (`localhost:4000`, on the same minerva-pipeline key that minerva-proposal uses):
   - *evidence*: picks the fragments that point to an incident.
   - *correlate*: writes one hypothesis and cites fragment IDs.
   - *plan*: writes ranked steps. Each step names the artifact it touches and
     cites fragments.

   Any cited ID that isn't in the fragment set is dropped. The pass writes nothing
   if the hypothesis is left with no valid citation, or if no step is left with
   both an artifact and a valid citation.
4. **Emit.** It writes one `minerva.db` `proposals` row, with the same columns
   minerva-proposal writes, plus
   `/home/lumen/minerva/proposals/pending/<date>_<id>.md`. The cited fragments
   go into `eval_result.explanation`.

## Why status = `blocked`

The `/proposals` UI at propose.lumennode.uk lists `deploy_ready`, `defer`, `blocked`
and `shipped` by default. Of those, `blocked` is the only status that no automated
stage consumes:

| status | consumed by | effect |
|---|---|---|
| `accept` | minerva_eval | promoted to `deploy_ready` |
| `deploy_ready` | minerva_factory | GREEN lane auto-applies |
| `defer` | minerva_defer_review | re-scored, and can become `accept` |
| `blocked` | nothing (only counted by minerva_eval) | waits for a human |

So a triage plan can reach the fleet only when a person presses **Ship**, which
opens a Scribe ticket. **Reject** dismisses it.

## Install / verify / rollback

```
ssh lumen@<lumen> '/home/lumen/bin/lumen-run start --root --name ir-triage-install -- bash /home/lumen/foreman/ops/wq2836-ir-triage/install.sh'
python3 /home/lumen/minerva/ir_triage.py --collect-only   # masked fragments, no LLM, no writes
python3 -m pytest -q ops/wq2836-ir-triage/                # zero-quota, stub model
journalctl -u lumen-ir-triage -n 20
```

Rollback: `systemctl disable --now lumen-ir-triage.timer && rm -f /etc/systemd/system/lumen-ir-triage.{service,timer} /home/lumen/minerva/ir_triage.py && systemctl daemon-reload`.
This leaves any proposal rows it already wrote. Reject them in the UI.

### Timer is intended-off (wq#5267)

The 2026-10-08 subtraction disabled `lumen-ir-triage.timer` on purpose
(`/home/lumen/scratch/subtraction/timers-disable-list.txt:2`). With `IR_TRIAGE_ON=0` in
`install.sh`, install ships the script and unit files but does not enable/start the timer
or run a smoke pass, and `--verify` checks the script and unit files but does not assert
the timer live. Re-enabling is a decision, not a heal: flip `IR_TRIAGE_ON=1`, then install
enables+starts it and verify asserts enabled+active+scheduled.
Test: `ops/tests/test_wq5267_ir_triage_intended_off.py`.
