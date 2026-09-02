# Lumen public-surface runbook (wq#1252)

You are reading this because a `fleet-fires` ticket named a `lumen-public-guardian`
check. Lumen is publicly reachable two ways, and both are in scope here:

- **Email** — `lumen@lumennode.uk`: Cloudflare Email Routing → Worker
  `lumen-mailbot` → DeepSeek → reply → D1 `lumen-mail`, mirrored to
  `/home/lumen/mailbot/mail.db` by `lumen-mail-sync.timer`, read back by the
  messenger's `email_recall` tool.
- **Stream** — `pokemon.lumennode.uk`: `lumen-pokemon-live.service` → `:8871`
  → cloudflared → the public hostname.

Guardian source: `/home/lumen/foreman/ops/wq1252-lumen-public-guardian/`
(installed at `/home/lumen/services/lumen-public-guardian/`).
State: `/home/lumen/logs/lumen-public-guardian-state.json`.
Status: `python3 /home/lumen/services/lumen-public-guardian/guardian.py status`.

## Ground rules for this fire

1. **Never reboot.** Nothing on this runbook needs a reboot. Thor
   (100.112.151.100) is never restarted at all.
2. **Back up before any schema change.** The guardian's own migration heal
   exports D1 first; if you apply a migration by hand, export first too.
3. **Only the heals listed below are sanctioned.** The guardian enforces this
   in code (`HEAL_ALLOWLIST` in `guardian.py`). If the fix you want is not
   here, do not improvise on a live public surface: research it, write up what
   you found, and add the heal to both the allowlist and this file in a PR.
4. Start by reproducing the finding: `guardian.py run --dry-run --force --only <ID>`.
   Dry-run alerts nobody, heals nothing, and writes no state.

## Email checks

### E1-schema — remote D1 does not match the migrations

**Means:** `mailbot/migrations/*.sql` promise a table or column that remote D1
does not have. `storeMessage()` catches and logs D1 errors
(`mailbot/src/worker.js`), so every affected write fails **silently** — the
worker keeps replying and stores nothing. This is the 2026-09-02 incident:
`messages.dodge_style` was missing for ~7.5h after a deploy without migrations.

**Heal (automatic):** export D1, then `wrangler d1 migrations apply --remote`.
Needs `/home/lumen/mailbot/.cf_deploy_token`.

**By hand:**
```
cd /home/lumen/mailbot
export CLOUDFLARE_API_TOKEN=$(cat .cf_deploy_token)
npx wrangler d1 export lumen-mail --remote --output backups/lumen-mail-$(date -u +%Y%m%dT%H%M%SZ).sql
npx wrangler d1 migrations apply lumen-mail --remote
```
**Root cause to fix, not just heal:** `deploy.sh` must run the migrations
before `wrangler deploy`. If it still does not, that is the ticket.

### E1-version — a deploy has not proved itself

**Means:** the live Worker version is not the one that last passed an E2 round
trip, and it has been that way past the grace window (30 min). Someone
deployed and the probe has not confirmed the new code works end to end.

**Do:** check E2. If E2 is green the state promotes itself on the next pass. If
E2 is red, treat this as the E2 fire and consider the rollback below.

### E2-roundtrip — the synthetic probe did not complete

**Means:** `lumen-mail-probe` mails `lumen@` every 30 min with an HMAC token;
the guardian looks for the inbound message and its reply in D1 within 5 min.
A miss means one of: the probe worker's cron, Email Routing, the mailbot's
suppression rules, DeepSeek, `message.reply()`, or D1 storage.

**Narrow it down:**
- inbound present, no outbound → worker/DeepSeek/D1. Check E3 and E1-schema,
  then `npx wrangler tail lumen-mailbot`.
- no inbound at all → probe worker or routing. `npx wrangler tail lumen-mail-probe`,
  and confirm `PROBE_SENDER` in `mailbot/wrangler.toml` still matches the
  probe's `PROBE_FROM`. If the mailbot logs `suppress: automated/self/noreply
  sender probe@lumennode.uk`, the allowlist in `worker.js` was lost in a merge
  — that is the fix.

**Heal (automatic, second consecutive miss):** roll back to the last version a
probe actually round-tripped under.
```
cd /home/lumen/mailbot
export CLOUDFLARE_API_TOKEN=$(cat .cf_deploy_token)
npx wrangler rollback <version-id> --message "E2 rollback (wq#1252)"
```
The version ids are in
`/home/lumen/logs/lumen-public-guardian-mailbot-version.json`
(`validated_version` = last good, `previous_validated` = the one before).

### E3-deepseek — key invalid or out of balance

**Means:** `https://api.deepseek.com/user/balance` rejected the key (401) or
reports a balance at or under the floor. Every reply the mailbot generates is
about to fail into `safeFallback`.

**No automatic heal** — this needs money or a new key, both Frank's account.
Page Frank, then top up or rotate `DEEPSEEK_API_KEY` in `/home/lumen/agent/.env`
and re-push it: `echo "$KEY" | npx wrangler secret put DEEPSEEK_API_KEY`.

### E4-sync — the local mirror is not tracking D1

**Means:** either `lumen-mail-sync.timer` has not fired within 20 min, or
`mail.db`'s newest message is more than 10 min behind D1's. The mailbot itself
is fine; the messenger's memory of it is not.

**Heal (automatic):** `python3 /home/lumen/mailbot/mail_recall.py sync`.
If that fails, read the error — a `D1 error` here usually means the read token
at `/home/lumen/mailbot/.cf_d1_token` expired.

Note: `mail.db`'s mtime is **not** a health signal. A sync with nothing new to
pull writes nothing, so on a quiet inbox the file is legitimately hours old.

### E5-recall — the messenger's email tool is broken

**Means:** `mail_recall.py today` exited non-zero, or `email_recall` is no
longer registered in `agent_bridge.TOOLS`. Frank asks "did you talk to X" and
gets an error.

**Do:** run it by hand for the real message; a corrupt `mail.db` is the usual
cause (`sqlite3 mail.db "PRAGMA integrity_check"`). Re-syncing rebuilds it.

### E6-suppressed — burst of suppressed inbound

**Means:** ≥5 `auth_fail` or ≥3 canary trips in an hour (wq#1250's `suppressed`
table). Someone is probing the inbox. **Not an outage** — the suppression is
working, that is why you can see it.

**Do:** `python3 /home/lumen/mailbot/mail_recall.py probes --since 1d`, look at
the senders, and decide whether it needs a Cloudflare Email Routing rule. Do
not disable the suppression to make the alert stop.

## Stream checks

### S1-public / S4-local — the stream is not serving fresh frames

**Means:** `/events.json` did not answer, or its newest event is older than
10 minutes. Note that a bare HTTP 200 proves nothing:
`lumen-lpp-brb.service` serves a BRB splash on the same port whenever the live
agent is down, so "200 but not an event feed" is reported as RED on purpose.

`S1` red + `S4` green = the tunnel, not the agent. Go to S3.

### S2-unit — the agent is up but stalled

**Means:** `lumen-pokemon-live.service` is active, its journal has been quiet
past the limit, **and** the local event feed is not advancing either. A quiet
journal alone is normal — the agent logs on save/resume, roughly every 40 min.

### S3-tunnel — cloudflared down or missing the ingress

**Means:** `cloudflared.service` is not active, or `/etc/cloudflared/config.yml`
has no `hostname: pokemon.lumennode.uk` rule. If cloudflared is down, **every**
public `*.lumennode.uk` hostname is down, not just the stream — say so when you
report.

### Stream heal ladder (automatic, in this order)

1. `sudo /usr/bin/systemctl restart lumen-pokemon-live.service`
2. `sudo /usr/bin/systemd-run --collect /usr/bin/systemctl restart cloudflared.service`
   — detached on purpose: the guardian's own route out runs over that tunnel,
   so a foreground restart can kill the process doing the restarting.
3. still red → this fire.

If both restarts ran and the stream is still down, stop restarting. Look at
`journalctl -u lumen-pokemon-live -n 200` for a crash loop (the classic one is
`EADDRINUSE` from the BRB unit squatting `:8871` — `systemctl status
lumen-lpp-brb` will show it running when it should not).

## UNCONFIGURED is not OK

A check that cannot run reports `UNCONFIGURED` and nags once a day. It is
covering nothing. The three that can be in that state, and what each needs:

| Check | Needs | Who |
|---|---|---|
| `E1-version`, migration/rollback heals | Workers-scoped API token at `/home/lumen/mailbot/.cf_deploy_token` (0600) | Frank — minted in his Cloudflare account |
| `E2-roundtrip` | `lumen@lumennode.uk` verified as a Cloudflare destination address, then `cd mail-probe && npx wrangler secret put PROBE_HMAC_SECRET < /home/lumen/.secrets/lpg-probe-hmac && npx wrangler deploy` | Frank for the verification click, anyone for the deploy |
| status board | `/home/lumen/.secrets/lpg-status-push.json` mapping check ids to push URLs on status.lumennode.uk | Frank — the push URLs are credentials |

## When to page Frank

Page (and say which): E3 out of balance, anything needing a credential he must
mint, or a red that survives every sanctioned heal **and** you have no next
step you can take without one of the above. Everything else: fix it, then tell
him what happened.

## Verifying a fix

```
python3 /home/lumen/services/lumen-public-guardian/guardian.py run --dry-run --force
python3 /home/lumen/foreman/ops/wq1252-lumen-public-guardian/tests/failure_drills.py
cd /home/lumen/foreman/ops/wq1252-lumen-public-guardian && python3 -m pytest tests/ -q
```
A fire is closed when the check reports OK on a real (non-dry-run) pass and the
guardian has emitted its `RECOVERED <id>` alert.
