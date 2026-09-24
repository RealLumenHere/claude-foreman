# wq#2428 — timer spend guard

wq#2426 made the dispatcher's `cloud_ceiling` budget-aware. That ceiling governs the
wq dispatch path only. A systemd timer is not on that path: it fires on its own clock
and calls whatever its `ExecStart` calls, with nothing reading the weekly Max pool
first. On 2026-09-21 the orchestrator's 95% brake stopped five timers by name, and
nobody could say which of them actually spent Anthropic tokens.

House rule this ticket establishes: **no scheduled service defaults to Max.** A timer
either never reaches Anthropic, or it pins itself at the LiteLLM gateway
(`ANTHROPIC_BASE_URL=http://localhost:4000` plus a local rung — harness/README.md
§"Local rung"). `timer_spend_audit.py` is what makes that rule checkable, and
`lumen-timer-spend-guard.timer` is what keeps it checked.

```
python3 timer_spend_audit.py                 # report; exit 1 on any cloud finding
python3 timer_spend_audit.py --strict        # exit 1 on unknown too — the real sweep
python3 timer_spend_audit.py --json --verbose
```

It reads only through `systemctl show` / `systemctl list-unit-files`, never `/etc`
directly, so it runs unprivileged. For each installed timer it resolves the service,
parses every `ExecStart*` argv, reads the unit's `Environment=`, and opens the script
the argv names. Four verdicts: `cloud` (reaches Anthropic, no gateway pin), `pinned`,
`clean`, `unknown` (a target it could not read — an unknown state is never reported as
a good one).

## What the 2026-09-21 brake actually stopped

All five named timers are **`enabled` but `inactive`**: the brake stopped the units,
it did not disable them. Re-arming is `systemctl start <timer>`, not `enable`, and a
reboot would have re-armed them on its own.

| unit | verdict | evidence |
| --- | --- | --- |
| `sia-news-pull` | **clean** | `/home/lumen/bin/sia-news-pull` is 13 lines of `rsync --remove-source-files` and a line count. No model call of any kind. |
| `motd-news` | **vendor** | `/usr/lib/systemd/system/motd-news.service`, `ExecStart=/etc/update-motd.d/50-motd-news --force` — stock Ubuntu message-of-the-day. Not a Lumen unit; nothing to repoint. |
| `lumen-scout` | **no model call observed** | journal 2026-09-21 06:00:00→06:00:24: GitHub search → 124 repos → deterministic `score_repos` → 3 prompt files written. Scout *queues* gap-analysis prompts; the tokens are spent later by whoever dispatches them, and that consumer is on the wq path wq#2426 just gated. |
| `lumen-ebay-watch` | **no model call observed** | journal 2026-09-21 08:50/09:20: eBay listing polls and `ingest <subject> price` memory writes, nothing else. |
| `lumen-news-digest` | **unknown** | already on deepseek per the ticket; `/home/lumen/agent/lumen_news_digest.py` is outside the worker sandbox and its journal has no retained entries. Needs the full-read sweep to confirm. |

So the ticket's premise does not survive contact with the evidence: four of the five
did not spend Max on a timer, and stopping them bought no quota — it starved the
intake that feeds the (now budget-gated) dispatch path. The one real risk the ticket
names is real, it just is not these units; that is what the guard is for.

## Fleet sweep, 2026-09-24

`171 timers — cloud=0 unknown=104 pinned=0 clean=67` over the roots this worker could
read (`/home/lumen/bin`, `/home/lumen/foreman`, `/home/lumen/mailbot`,
`/home/lumen/pokemon`, `/home/lumen/agents/work-queue`, `/home/lumen/kb`,
`/home/lumen/projects/nest`). Two uninstantiated templates are excluded: one never
fires, and `systemctl show` aborts its whole batch on the first template it is handed.

Zero cloud findings among the 67 readable targets. The 104 `unknown` are scripts under
`/home/lumen/{agent,autonomy,services}`, `/usr/lib`, `/usr/local/sbin` and similar —
outside the sandbox, not outside the fleet. **Running `install.sh` (or
`timer_spend_audit.py --strict`) as `lumen` is what finishes this audit.**

Four findings the first run produced were prose, not calls, and the tool now rejects
all four shapes (see `tests/test_timer_spend_audit.py`):

- `wq:313` / `wq:408` — `` `claude --model opus-5` `` inside a docstring.
- `retirement-audit:323` — `--owner claude`, an aq field.
- `dispatch-watchdog.py:117` — a comment.
- `dispatch_recover.py:856` — `["claude", "auth", "status"]`: a real invocation from a
  real timer that runs no inference. `_NON_BILLING` keeps auth/mcp/doctor/version out.

One genuine `claude -p` caller lives in `/home/lumen/bin/gh-lane-worker:64` (with
`ANTHROPIC_API_KEY`/`ANTHROPIC_BASE_URL` scrubbed, so it rides the Max OAuth sub). No
timer's `ExecStart` names it, so it is out of this ticket's scope — but note the scan
is depth-1: it reads the script `ExecStart` names, not what that script shells out to.

## What the worker could not do

The sandbox for this ticket has no `sudo`, no write access to `/etc/systemd/system`,
and no `systemctl start/enable` on the allowlist; `fleet-exec` denied with
`grant_missing_env`. So none of the operational half of the ticket ran from here:

1. installing/starting `lumen-timer-spend-guard.timer` — `install.sh` does it.
2. re-arming the five stopped timers — `install.sh` prints the exact command.
3. reading `/home/lumen/{agent,autonomy,services}` to finish the audit — `--strict`
   as `lumen` does it.

Repointing a unit that was never read would have been a guess, so none was written.
Once the full-read sweep names the real cloud-reaching timers, the fix per unit is a
drop-in that sets `ANTHROPIC_BASE_URL=http://localhost:4000` and a local rung, with the
key supplied by `EnvironmentFile=` rather than inlined.

## Deploy checklist

1. `bash install.sh` as `lumen` on Lumen (prints the full-read sweep, then installs).
2. Repoint or budget-gate every `CLOUD` line the sweep reports.
3. Re-arm the five stopped timers (command printed by `install.sh`).
4. `python3 -m pytest tests -q` — 32 tests, no systemd needed.
