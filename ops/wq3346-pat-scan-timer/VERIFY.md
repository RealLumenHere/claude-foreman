# wq#3346 — pat-scan timer missing

Ticket claimed `pat-scan.timer` does not exist on Lumen and the credential
scanner has no schedule. Verified against the live host before touching
anything (doctrine: research first).

## Finding: ticket premise is stale — the timer exists and is running

`systemctl list-unit-files | grep -i pat-scan` on Lumen:

```
lumen-pat-scan.service   disabled   enabled
lumen-pat-scan.timer     enabled    enabled
```

The unit is named `lumen-pat-scan.timer` (the `lumen-` prefix convention used
by every guard unit on this host), not bare `pat-scan.timer` — a literal
search for `pat-scan.timer` finds nothing, which is almost certainly how this
ticket got filed.

`systemctl status lumen-pat-scan.timer`:
- Loaded from `/etc/systemd/system/lumen-pat-scan.timer`, enabled, preset enabled.
- Active (waiting) since 2026-08-31T12:15:02Z (running over a month).
- Next trigger: 2026-10-02T00:07:28Z (~8h out at verification time).

`systemctl status lumen-pat-scan.service`:
- Last invocation exited `0/SUCCESS`, ~1h56m before verification.
- `TriggeredBy: lumen-pat-scan.timer` confirms the timer is the one firing it.
- Drop-ins present: `10-memory.conf`, `50-guard-tag.conf`,
  `60-successexit.conf`, `99-cron-stamp.conf` — this is the unit covered by
  wq#565 (original install) and wq#2572 (SuccessExitStatus Mode C
  classification, see ISSUE-LOG row 711).

## Conclusion

No rebuild or reinstall needed — `lumen-pat-scan.timer` is installed,
enabled, and has been firing the scanner on schedule with clean exits. This
ticket is closed as a non-issue: the guard was never down, just named
differently than the ticket assumed.
