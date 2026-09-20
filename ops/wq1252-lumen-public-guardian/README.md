# lumen-public-guardian (wq#1252)

A dedicated watchdog for the two surfaces the outside world reaches Lumen
through — email (`lumen@lumennode.uk`) and the LPP stream
(`pokemon.lumennode.uk`) — with automatic heals, a fire-worker escalation, and
a dead man's switch on itself.

## Why liveness checks were not enough

On 2026-09-02 a `lumen-mailbot` deploy shipped without its D1 migrations.
`storeMessage()` logs and swallows D1 errors, so the worker kept answering mail
normally while storing none of it, for about 7.5 hours, until someone tested by
hand. Every liveness check you could have pointed at that system was green.

So every check here asserts *correctness or freshness*, never just reachability:

| Check | Asserts |
|---|---|
| `E1-schema` | remote D1's columns == the union of `mailbot/migrations/*.sql` |
| `E1-version` | the deployed Worker version is one a probe round-tripped under |
| `E2-roundtrip` | a synthetic mail went routing → worker → DeepSeek → reply → D1 |
| `E3-deepseek` | the DeepSeek key is accepted and the balance is above the floor |
| `E4-sync` | `lumen-mail-sync.timer` fired recently and `mail.db` tracks D1 |
| `E5-recall` | the messenger's `email_recall` path still executes |
| `E6-suppressed` | no burst of auth failures / canary trips in the last hour |
| `S1-public` | `pokemon.lumennode.uk/events.json` is fresh, not merely 200 |
| `S2-unit` | `lumen-pokemon-live` is active *and* making progress |
| `S3-tunnel` | cloudflared is up and still has the pokemon ingress rule |
| `S4-local` | `:8871` is serving a fresh feed on loopback |

Two of those distinctions were forced by live evidence rather than guesswork:

- `lumen-lpp-brb.service` serves a static splash on `:8871` whenever the live
  agent is down, so an HTTP 200 is exactly what a dead stream looks like.
  Freshness comes from each event's `t` stamp (`pokemon/src/stream.ts:408`).
- `mail.db`'s mtime is not a health signal. A sync with nothing new writes
  nothing; on 2026-09-02 the file was 7.7h old while the timer had fired four
  minutes earlier. `E4` reads the timer's `LastTriggerUSec` instead.

## What happens when a check goes red

1. `ntfy-send -p urgent` to Frank's phone, naming the check id. Re-alerts every
   15 cycles, not every pass.
2. The check's heal ladder advances one rung per cycle. Every heal command is
   checked against `HEAL_ALLOWLIST` in `guardian.py` before it runs — an
   unlisted command raises rather than executing.
3. Ladder exhausted and still red → a `fleet-fires` ticket pointing at
   `foreman/LUMEN_PUBLIC_RUNBOOK.md`. That lane has `requires_approval: false`
   (`dispatcher/config.json`), so the dispatcher spawns a worker on its next
   pass without waiting for anyone. That worker is the one focused on Lumen.
4. Recovery emits a `RECOVERED <id>` alert and resets the ladder.

A check that *cannot run* (missing credential) reports `UNCONFIGURED`, never
`ok`, and nags once a day. Silent disablement is the failure mode this whole
service exists to prevent.

A rejected Workers-read token (HTTP 401/403 from Cloudflare) is also
`UNCONFIGURED`, and the detail names the token file to replace. A masked or
deleted `lumen-pokemon-live.service` drops `S1`/`S2`/`S4` from the pass (logged
each run, re-armed the moment the unit loads again) — a retired stream is an
operator decision, and restarting a masked unit cannot heal it. `S3-tunnel`
keeps running: cloudflared carries every public hostname.

## Who watches the watchdog

The unit stamps `/home/lumen/logs/.stamp.lumen-public-guardian.service` on every
successful pass (`ExecStartPost=-/home/lumen/bin/lumen-cron-stamp %n`).
`lumen-cron-deadman` (wq#1184) alerts when a stamp is older than 2× its timer
cadence — 4 minutes here, detected on the deadman's next 10-minute sweep.

## Install

```
sudo /home/lumen/bin/lumen-run --root /home/lumen/foreman/ops/wq1252-lumen-public-guardian/install.sh
```

Installs to `/home/lumen/services/lumen-public-guardian/`, generates the probe
HMAC secret if absent, writes a two-line `sudoers.d` grant for exactly the two
stream heals, and enables the 2-minute timer.

## Tests

```
python3 -m pytest tests/ -q            # 26 offline unit tests
python3 tests/failure_drills.py        # the ticket's test-to-failure set
python3 guardian.py selftest           # config/dependency sanity, no side effects
python3 guardian.py run --dry-run --force   # a real pass that changes nothing
```

`failure_drills.py` breaks things for real where that is safe: a pre-0002 sqlite
copy for the schema drill, an invalid key against the live DeepSeek API, a
closed port for the stream drill. It never takes a public surface down to prove
a check works.

## Mailbot dependency

`E2` needs the mailbot to answer its prober, which the self-domain suppression
otherwise blocks. Applied in `/home/lumen/mailbot`:

- `wrangler.toml` — `PROBE_SENDER = "probe@lumennode.uk"` (empty = feature off).
- `src/worker.js` — `isGuardianProbe` skips the `automated` suppression and the
  daily cap. It requires an exact address match **and** a `[LPG-PROBE <token>]`
  subject, and the SPF/DKIM check still runs afterwards, so spoofing the
  address from outside the domain gains nothing.

That repo has no remote (fleet ISSUE-LOG #16) and had wq#1250's work in flight
when this landed, so the change lives in its working tree rather than in a
branch here. If a merge ever loses it, `E2` reports "no inbound at all" and the
runbook names this as the first thing to check.
