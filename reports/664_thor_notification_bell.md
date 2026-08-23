# wq#664 — Thor's notification bell: RCA and click-path

**Verdict: the bell was never dead. The log was never missing.** Both earlier
conclusions came from checking a path that xfce4-notifyd 0.9.x does not use.
Verified end-to-end live on Thor 2026-08-23 (uptime 2d, session started 08-21 10:51).

## Root cause of the false alarm

xfce4-notifyd **0.9.x replaced the plain-text log with SQLite**. Orch checked
`~/.cache/xfce4/notifyd/log`, which 0.9.7 never writes, concluded logging was broken,
and chased xfconf properties that were already correct.

The real store is `~/.cache/xfce4/notifyd/log.sqlite`:

| fact | value |
|---|---|
| birth time | **2026-05-27** — logging predates orch's 08-15 "fix" by three months |
| rows at investigation | 1000 (the cap), 975 unread |
| span | 2026-08-20 00:34 → 2026-08-23 11:48 |
| schema | `notifications(id TEXT PK, timestamp INTEGER, tz_identifier, app_id, app_name, icon_id, summary, body, actions BLOB, expire_timeout, is_read)` STRICT |
| timestamp unit | **microseconds** since epoch, not seconds (`datetime(timestamp/1000000,'unixepoch','localtime')`) |

The panel plugin does not read that file. The daemon serves it over D-Bus —
`org.xfce.Notifyd.Log` with `List / Get / HasUnread / GetAppIdCounts / MarkRead /
Delete / Truncate / Clear` plus `RowAdded` / `RowChanged` signals. All confirmed
answering correctly.

## The three suspicions in the ticket, resolved

1. **Rival notification daemon?** No. `org.freedesktop.Notifications` is owned by
   `/usr/lib/x86_64-linux-gnu/xfce4/notifyd/xfce4-notifyd`, `GetServerInformation` →
   `Xfce Notify Daemon / Xfce / 0.9.7 / spec 1.2`. Runs as the `xfce4-notifyd.service`
   user unit; both `org.freedesktop.Notifications` and `org.xfce.Notifyd` are D-Bus
   activatable, so killing it can never strand the bell.
2. **Wrong log path?** Yes — see above. That was the whole bug.
3. **Do sentinel/ntfy alerts reach xfce4-notifyd?** Yes, by two independent proofs.
   `~/.config/ntfy/client.yml` runs `ntfy subscribe --from-config` under
   `ntfy-client.service` with a `default-command` of
   `notify-send -a "Lumen/$NTFY_TOPIC" …`, subscribed to lumen-alerts, lumen-updates,
   lumen-briefing, lumen-fleet. And the log itself contains them:
   lumen-fleet 597, lumen-updates 231, lumen-alerts 164, lumen-briefing 1.

## End-to-end proof

`notify-send` on Thor → new row in `log.sqlite` within 3s → clicked the panel icon →
the popup menu showed that notification at the top, above real history (SENTINEL
CRITICAL: probe-pokemon-liveness, Lumen ECC: 1 new correctable CE, dispatcher PSI
halt, Gatus 5-probes, DGM daily run), each with full summary **and** body text. Run
twice, including once after `systemctl --user restart xfce4-notifyd`. All test rows
were deleted afterwards via `org.xfce.Notifyd.Log.Delete`; Frank's real history is
untouched (994 rows).

## Click-path for Frank

**The icon is a megaphone, not a bell.** That is very likely why it read as "nothing
there" — it does not look like a notification icon.

1. Top-right of the panel, in the tray at x≈1815–1842 (27×27 px): the white
   **megaphone**, between the red YouTube icon and the blue Bluetooth icon.
2. A **red dot** on the megaphone means unread notifications are waiting. No dot
   means everything has been read — not that history is empty.
3. **Left-click it.** The menu opens with a "Do not disturb" toggle at the top, then
   the history, newest first, each entry showing summary, body and full timestamp.
   Scroll for older entries; it lazy-loads.
4. Bottom of the menu: **Clear log**, **Mark all read**, **Notification settings…**.

## Retention: the one real defect found

The log is capped at **1000 entries**, and `Lumen/*` topics generate ~285/day, so the
bell only ever held **~3.4 days** — and 60% of that is `lumen-fleet` chatter crowding
out CRITICALs.

`/log-max-size` exists as a property ("The maximum number of entries to be retained in
the log", UI range 1–100000) but **is inert on 0.9.7-2build1**: set to 10000 and then
to 990, live and after a daemon restart, the row count stayed pinned at exactly 1000
across new inserts. The only knob that works is the enable flag.

Applied on Thor: `xfconf-query -c xfce4-notifyd -p /log-max-size-enabled -s false`
(proven: row count went 1000 → 1002 on the next two notifications). `/log-max-size`
left at 10000 so a future fixed build picks up a sane value if Frank re-enables it.

- Growth: ~134 KB/day, ~4 MB/month in `~/.cache`. "Clear log" in the bell menu resets it.
- Revert: `xfconf-query -c xfce4-notifyd -p /log-max-size-enabled -s true`.

Popup readability is unchanged and unfixable from here: every `Lumen/*` notification
arrives with `expire_timeout=10000` (10s), set by the sender. The bell is the answer
to "I could not read that in time".

## Code fix: `dispatcher/notify_sinks.py`

The dispatcher's second dead-man's-switch sink was a direct `INSERT` into
`log.sqlite` — against a schema that does not exist:

```
INSERT INTO notifications (app_name, app_icon, summary, body, time)
```

0.9.x has no `app_icon` and no `time` column, and `id` / `timestamp` /
`tz_identifier` are NOT NULL with no defaults. That statement could only ever raise
`sqlite3.Error` and return False. It was unreachable in practice
(`FOREMAN_XFCE_NOTIFYD_DB` defaulted to empty), so the circuit breaker has been
shipping with one working sink while documenting two.

Writing to that file directly is also wrong even with a corrected schema: the daemon
owns it and pushes `RowAdded` to the panel plugin, so a row inserted behind its back
is invisible to the bell, and the docstring's suggested NFS mount would put a live
SQLite database Frank actually reads behind a network filesystem.

Replaced with `desktop_notify()`: `notify-send` over SSH to `DESKTOP_SSH_HOST`
(default `thor`), `shlex.quote`d, `BatchMode=yes ConnectTimeout=5`, same
never-raises/returns-bool contract. It is genuinely independent of ntfy — different
transport, different failure mode — and it lands in the bell like every other alert.
Verified live: `desktop_notify(...) → True`, row present with
`app_id=Lumen/foreman-dispatcher` and shell metacharacters intact.

`notify_critical`'s result key is now `desktop`, not `xfce_notifyd`. 161/161 dispatcher
tests pass.
