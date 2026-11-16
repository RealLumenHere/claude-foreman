# auto-dispatch (wq#4956) + park-and-resume hook (wq#5009)

`auto-dispatch` is the 5-minute dispatch pass over open wq tickets, behind Frank's gates.
It is **staged, not enabled**: `lumen-auto-dispatch.timer` ships disabled and nothing in
this bundle enables it.

| file | what |
|---|---|
| `auto-dispatch` | the script (v3). Deployed copy: `/home/lumen/bin/auto-dispatch` |
| `lumen-auto-dispatch.service`, `.timer` | units, installed in `/etc/systemd/system`, timer disabled |
| `test_auto_dispatch.py` | 69 cases; every external binary stubbed, including `park` |
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
