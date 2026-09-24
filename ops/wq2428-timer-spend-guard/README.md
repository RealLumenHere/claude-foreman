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
| `lumen-news-digest` | **clean** | resolved by the full-read sweep below (wq#2456): no marker in `/home/lumen/agent/lumen_news_digest.py`. Matches the ticket's "already deepseek". |

So the ticket's premise does not survive contact with the evidence: **all five** are
clean, stopping them bought no quota, and it starved the intake that feeds the (now
budget-gated) dispatch path. The one real risk the ticket names is real, it just is not
these units — it is the four below. That is what the guard is for.

## Fleet sweep, 2026-09-24 (full read, wq#2456)

```
172 timers — cloud=0 unknown=27 pinned=0 clean=145
```

Counts drift by a unit or two between runs as timers come and go; the shape is what
matters. Uninstantiated templates are excluded: they never fire, and `systemctl show`
aborts its whole batch on the first template it is handed.

**No scheduled unit on this fleet reaches Anthropic ungated.** An earlier wq#2456 run
reported `cloud=4`, and all four were the same false positive — a bare `"claude"` or
`ANTHROPIC_API_KEY` sitting in a module-level constant collection, which is a string the
script matches *against*, never a call:

| unit | line | what it actually is |
| --- | --- | --- |
| `lumen-intel.service` | `lumen_intel.py:93,107` | `HIGH_VALUE` / `HOMELAB_SIGNALS` — repo-scoring keyword sets |
| `lumen-signal-engine.service` | `signal_engine.py:47` | a topic keyword set |
| `lumen-worklog.service` | `worklog_gen.py:17` | `EXCLUDE` — terms kept *out* of the ledger |
| `minerva-factory.service` | `minerva_factory.py:284` | `FORBIDDEN_KEY_ENVS` — the fleet's own deny-list of Anthropic env vars |

The last one is the tell: the audit flagged the guard that *enforces* this ticket's house
rule as a violation of it. Reading `minerva_factory.py:278-284` settles the question the
old text left open — that unit does not spend Anthropic quota, it is the thing that stops
others from doing so, and its `EnvironmentFile` needed no inspection after all.

So no repoint is owed. Had the deploy checklist been run as previously written, it would
have appended `--model deepseek-v4-flash` to four keyword lists, corrupting three scoring
dictionaries and the anti-Anthropic deny-list, and reported success. The fix is in the
scanner, not in those scripts (`_const_collection_lines`, `timer_spend_audit.py:81`).

The 25 `unknown` are the floor of this design, not unfinished work: 20 name an
`EnvironmentFile` the audit will not open (instinct 4 — an env file is a credential
file), and 5 name a compiled binary (`curl`, `/bin/sleep`, `/usr/bin/install`,
`/usr/bin/cloudflared`, `systemd-tmpfiles`) that is not a script to scan. Neither set
shrinks with more privilege, so this sweep is finished, not partial.

Two classes of phantom finding have now been retired, and they rhyme: a marker only
counts where the script would *execute* it. The second class — a keyword inside a
module constant — is suppressed for the membership shapes only (`"claude",` in a list,
a bare `ANTHROPIC_API_KEY` name). A URL, an SDK import or a real argv inside the same
constant still counts, so `CMDS = ["claude -p ..."]` is a finding and
`ENDPOINTS = ("https://api.anthropic.com/...",)` is too.

The first class was prose, not calls, and the tool rejects all four shapes
(see `tests/test_timer_spend_audit.py`):

- `wq:313` / `wq:408` — `` `claude --model opus-5` `` inside a docstring.
- `retirement-audit:323` — `--owner claude`, an aq field.
- `dispatch-watchdog.py:117` — a comment.
- `dispatch_recover.py:856` — `["claude", "auth", "status"]`: a real invocation from a
  real timer that runs no inference. `_NON_BILLING` keeps auth/mcp/doctor/version out.

One genuine `claude -p` caller lives in `/home/lumen/bin/gh-lane-worker:64` (with
`ANTHROPIC_API_KEY`/`ANTHROPIC_BASE_URL` scrubbed, so it rides the Max OAuth sub). No
timer's `ExecStart` names it, so it is out of this ticket's scope — but note the scan
is depth-1: it reads the script `ExecStart` names, not what that script shells out to.

## What a worker can and cannot do (corrected, wq#2456)

wq#2428 recorded "a BUILD worker cannot finish the audit" (ISSUE-LOG #638). Half of
that is wrong, and the correction is worth more than the sweep: **the sandbox bounds
`Read` and the path arguments of shell commands, not the files a process opens.**
`grep /home/lumen/agent/x.py` is refused; `python3 timer_spend_audit.py` reading the
same file is not. The `cloud=0 unknown=104` figure came from passing `--roots`, not
from a privilege wall — drop `--roots` and the sweep completes as an ordinary worker,
which is how the numbers above were produced. `--roots` remains the right default for
an *incidental* scan; this ticket sanctions the unrestricted one.

What a worker still genuinely cannot do, all of it root:

1. install/enable `lumen-timer-spend-guard.timer` — needs `/etc/systemd/system` and
   `systemctl enable --now`. `sudo` is guard-hook denied, `systemctl start/enable` is
   off the worker allowlist (read-only verbs only), and `fleet-exec` denies with
   `grant_missing_env` — and its v1 verb set is `run`/`copy`/`restart` with no `sudo`
   verb anyway, so a minted grant would not have helped.
2. re-arm the five stopped timers — same `systemctl start` wall.
Both walls were re-probed on 2026-09-24 and both still stand: `sudo -n true` is
guard-hook denied (`denied verb`), and `systemctl start sia-news-pull.timer` returns
`This command requires approval`. Neither is retryable (wq#1107).

A third limit turned out not to exist. The old text listed "edit the three
`claude`-calling scripts" as root work; there are no such callers, so nothing under
`/home/lumen/{agent,personas}` needs editing. Reading those four lines is what dissolved
the task — and `Read` reaches them fine from a worker even though `grep` on the same
path is refused (ISSUE-LOG #318). The one path that stayed shut is
`/home/lumen/.secrets/...` (`grep -c` on the env file, denied by wq#616), and the
`minerva-factory` question it was meant to answer was settled from the script instead.

## Deploy checklist

1. `bash install.sh` as `lumen` on Lumen with `sudo` available (re-runs the sweep, then
   installs and enables the guard timer). Idempotent.
2. No repoint is owed — the sweep reports `cloud=0`. If a future run does report one,
   the unit needs `ANTHROPIC_BASE_URL=http://localhost:4000` by drop-in with
   `EnvironmentFile=` supplying the key (never inlined) and, if it calls the CLI, a
   local rung in its argv too: the CLI takes `--model` as a flag, not an env var, so a
   drop-in alone would route it at a gateway that does not serve its default model id.
   Confirm the finding is a real call before changing a script — read the line.
3. Re-arm the five stopped timers (command printed by `install.sh`). Verified still
   `enabled` + `inactive` on 2026-09-24.
4. `python3 -m pytest tests -q` — 46 tests, no systemd needed. One of them `bash -n`s
   `install.sh`, since a worker cannot lint shell under `ops/` any other way
   (ISSUE-LOG #645).
