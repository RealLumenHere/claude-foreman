# auto-dispatch (wq#4956) + park-and-resume hook (wq#5009)

`auto-dispatch` is the 5-minute dispatch pass over open wq tickets, behind Frank's gates.
It is **staged, not enabled**: `lumen-auto-dispatch.timer` ships disabled and nothing in
this bundle enables it.

| file | what |
|---|---|
| `auto-dispatch` | the script (v3). Deployed copy: `/home/lumen/bin/auto-dispatch` |
| `lumen-auto-dispatch.service`, `.timer` | units, installed in `/etc/systemd/system`, timer disabled |
| `test_auto_dispatch.py` | 81 cases; every external binary stubbed, including `park` and the admission gate |
| `install.sh` | install / `--verify` / `--dry-run`; never enables the timer |

Config: `/home/lumen/foreman/auto-dispatch.yaml` (read its ENABLEMENT NOTES before enabling).

## v3: park-and-resume (wq#5009)

A worker that needs a decision only Frank can make parks its round
(`harness/park.py`, `design/WQ5009_PARK_RESUME.md`): the ticket sits in
`frank_gated` / `pending_review`, which this script never lists, so a parked ticket holds
no slot, no process and no tokens.

Each pass runs `park.py sweep` first — before the halt files, because it dispatches
nothing. The sweep routes Frank's answer onto the decision card and reopens the ticket.
A ticket whose round is ANSWERED is a resume of an existing session:

- first in the candidate order;
- exempt from the redispatch cooldown and the per-day attempt cap;
- not counted by the failure breaker as "dispatched and still open";
- dispatched inside Frank's waking hours even with a small queue.

Halt files, the broker-conflict refusal, budget floors, the concurrency ceiling and every
per-ticket gate still apply to it. `park_resume: {enabled: false}` in the yaml turns the
hook off. A sweep failure is logged (`park_sweep_error`) and the pass carries on.

## Why this bundle exists

Until 2026-11-16 the script lived only as an untracked file in `/home/lumen/bin` and its
tests and units in an untracked directory, so wq#4956 could not close (the done gate
requires evidence on origin/main) and wq#5009, which depends on it, could not be claimed.

The tests always stub `park`: without the stub a test pass would run the real sweep
against the live `_runs` tree and could accept a real parked ticket's answer.

## Admission defers are not dispatches (wq#5117)

`dispatch-batch` detaches, exits 0 and only then runs `dispatcher/admission_gate.py`; a
denied ticket is logged `QUEUED` and no worker starts. The ledger entry was already
written, so two defers used up `max_dispatch_per_ticket_per_day` and the ticket could not
be dispatched again that day (wq#4890, wq#5002 on 2026-11-18: 2 dispatches logged, 0 workers).

The ledger now counts started dispatches only:

- the pass asks the same gate (`check --ticket <id>`) before it writes a ledger entry and
  skips on a deny (`admission denied: ...`). No entry, no `dispatch-batch` call. The
  pre-check fails open: gate missing, crashed, timed out or unreadable means the pass
  carries on and `dispatch-batch` decides (`admission_gate_error` in the log);
- if `dispatch-batch` still queues the ticket (the count moved after the pre-check), the
  next pass reads `_runs/dispatch/<id>.log` and drops that batch's entry (`ledger_refund`).
  Only the newest entry, only from a log written within 10 minutes after it, once per log.

The cap itself, the cooldown value, the failure breaker and the lane config are unchanged.
The breaker reads the same ledger, so a deferred ticket no longer counts toward it as
"dispatched and still open".
