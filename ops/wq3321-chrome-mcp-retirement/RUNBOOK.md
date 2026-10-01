# wq#3321 — ui-drift: chrome-mcp.lumennode.uk ORIGIN_DOWN (persistent, 2 cycles)

## Finding

```
[ORIGIN_DOWN] chrome-mcp.lumennode.uk (http://localhost:3103): curl: (7) Failed
to connect to localhost port 3103 after 0 ms: Could not connect to server
```

`ui-drift` (`/home/lumen/bin/ui-drift`) raises ORIGIN_DOWN when a cloudflared
ingress entry's local origin fails to answer (ui-drift:195-207). The check
runs unconditionally over every ingress entry — it does **not** skip hosts
marked `infra` or `retired` in `ui-inventory`'s classification tables
(ui-drift:195 starts the loop with no `if e["infra"] or e["retired"]: continue`
guard, unlike the INVISIBLE check seven lines above it at ui-drift:152). So
neither re-running `ui-inventory --write-glance` nor hand-classifying the
host in `RETIRED_HOSTS` silences this finding by itself — the only thing
that does is removing or fixing the live cloudflared ingress entry, per the
ticket's own framing.

## Root cause

`chrome-mcp.service` is stopped and disabled, not crashed:

- `systemctl status chrome-mcp` → `Loaded: ... disabled; preset: enabled`,
  `Active: inactive (dead)`.
- `ss -tlnp` shows no listener on `127.0.0.1:3103` (compare `127.0.0.1:3104`
  memory-mcp, which is up).
- `journalctl -u chrome-mcp` shows a clean deliberate stop at
  `2026-09-29 20:12:55 UTC`: `Stopping chrome-mcp.service ...` →
  `Deactivated successfully` → `Stopped chrome-mcp.service`. No crash loop,
  no OOM, no restart attempt since (2+ days as of this ticket).

No commit or ISSUE-LOG row records the stop/disable explicitly. Reconstructed
from timing: ISSUE-LOG row 961 (wq#2768) shipped the chrome-mcp Access JWT
guard SHADOW→ENFORCE flip earlier the same day (`09:36Z` per `flip.sh`
evidence, 35 valid / 0 would-deny verdicts) after row 838/909 (wq#2751/2768)
found chrome-mcp :3103 served anonymous `use_browser` — `file://` nav + `eval`
— to the whole tailnet. The stop at `20:12:55Z` reads as a belt-and-suspenders
follow-up: turning the service off entirely rather than relying solely on the
Access guard for an RCE-shaped surface. Same reconstruction pattern as the
`vip.lumennode.uk` "research mill" retirement in `ops/wq1099-ui-drift/
RUNBOOK.md` (also undocumented, also reconstructed from unit-file + journal
timestamps).

## Why this worker didn't apply the fix directly

This worker (`wq3321-a1`, a build-type session) has no read access to the
live `/etc/cloudflared/config.yml` (`sudo -n cat` is blocked by the house
deny-list — `sudo` is a denied verb regardless of target) and no `--add-dir`
grant covering `/etc/cloudflared/`. Per house doctrine ("never guess a path,
config value, or root cause — verify it"), a hand-written line-numbered patch
against content this worker cannot read and verify is not safe to stage as
a ready-to-apply diff (unlike `ops/wq1099-ui-drift/cloudflared-remove-vip.patch`,
written by a session that evidently did have that read access).

Instead: `remove_chrome_mcp_ingress.py` in this directory removes the entry
by hostname match against whatever the live file actually contains (no line
numbers assumed), backs up the original with a timestamp suffix, writes the
result, and reloads cloudflared via a detached `systemd-run --collect` unit
— never foreground, per house policy on cloudflared/vault/Commander changes.
It needs root and a grant on `/etc/cloudflared/`, neither of which this
worker holds.

## Apply (needs root + cloudflared config access)

```
python3 ops/wq3321-chrome-mcp-retirement/remove_chrome_mcp_ingress.py --dry-run
python3 ops/wq3321-chrome-mcp-retirement/remove_chrome_mcp_ingress.py --apply
```

## Follow-up (out of this ticket's scope, same repo as the finding — not this branch)

`/home/lumen/bin/ui-inventory` (`RETIRED_HOSTS`, line ~45) should gain:

```python
"chrome-mcp.lumennode.uk": "stopped+disabled 2026-09-29, wq#2751/2768 RCE-surface shutdown",
```

Not applied here: `/home/lumen/bin` is a separate live repo on `main`
(confirmed: `git -C /home/lumen/bin branch --show-current` → `main`), outside
this ticket's worktree/branch — editing it directly would be a hand-deploy
straight to main, exactly the anti-pattern ISSUE-LOG row 960 (wq#2973) flagged.
Needs its own PR through that repo's normal flow.

This classification alone would **not** clear the ui-drift finding (see
"Finding" above — ORIGIN_DOWN ignores `retired`) but keeps the dashboard
generator from ever treating chrome-mcp.lumennode.uk as a live Services
entry if cloudflared ever re-adds a route to it.

## Verification path once applied

`/home/lumen/bin/ui-drift` — the `chrome-mcp.lumennode.uk` ORIGIN_DOWN
finding drops out of `findings` (mirrors the verification note in
`ops/wq1099-ui-drift/RUNBOOK.md` for the `vip.lumennode.uk` fix).
