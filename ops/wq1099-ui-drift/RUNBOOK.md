# wq#1099 — ui-drift: ask.lumennode.uk / vip.lumennode.uk

Two independent findings, two root causes. `ask` was fixed live in this ticket.
`vip` is a retirement whose cloudflared-side cleanup is staged here for the
orchestrator/Commander to apply — this worker does not touch the live
cloudflared config or reload cloudflared.

## 1. ask.lumennode.uk — INVISIBLE (FIXED)

`localhost:3000` is the `vane` docker container
(`itzcrazykns1337/vane:slim-latest`, up 31h at diagnosis time), a live
AI-chat/search surface (`<title>Chat - Vane</title>`, HTTP 200, `X-Powered-By:
Next.js`). It has a real cloudflared ingress
(`ask.lumennode.uk -> http://localhost:3000`) and was never linked from any
dashboard — a live surface, not a retirement.

Fix applied: `ui-inventory --write-glance` (no hand classification needed —
`ask.lumennode.uk` is not in `INFRA_HOSTS`/`RETIRED_HOSTS`, so the generator's
default behavior lands it under the "Services" group). Glance hot-reloaded
the new config with no restart needed.

Verified: `ui-drift` run before the fix showed `[INVISIBLE] ask.lumennode.uk
... (streak 5)`; run after the fix shows only the `vip` finding —
`ask.lumennode.uk` is gone from the findings list (`findings=1`).

Backup of the pre-change config: `/home/lumen/glance/glance.yml.bak.20260830_010710`.

## 2. vip.lumennode.uk — ORIGIN_DOWN (retired, staged for Commander)

`localhost:8099` has no listener, no docker container, and no systemd unit.
This is `lumen-vip-api.service` (+ its sibling `lumen-vip-watcher.service`),
part of an 8-unit "research mill" prompt-generation subsystem:

```
lumen-evaluator.service   lumen-orchestrator.service   lumen-prompt-engine.service
lumen-generator.service   lumen-persona-engine.service lumen-vip-api.service
lumen-planner.service                                  lumen-vip-watcher.service
```

All 8 unit files were moved out of `/etc/systemd/system/` into
`/etc/systemd/system/DISABLED-research-mill.bak/` together (dir mtime
2026-08-28 06:43 UTC) — a coherent, deliberate group action, not a crash.
`/home/lumen/agent/logs/prompt_engine.log` shows the mill's mandate change
minutes earlier, at 06:38-06:42 UTC the same day: `"=== Lumen Prompt Engine
started — NEW MANDATE: self-improvement only ==="`. `vip-api`/`vip-watcher`
served the mill's "VIP"-tier prompt queue (`claude_memory.md` references "52
VIP prompts processed", `intel/gap/vip/persona` prompt banks) — retiring the
mill retired them with it. No wq ticket or memory entry records the
decommission explicitly; this reconstruction is from unit-file placement +
log timestamps, both internally consistent.

**This is a retirement, not an incident** — three call sites never got
updated when the mill was disabled:

1. `/etc/cloudflared/config.yml` — still has the `vip.lumennode.uk ->
   http://localhost:8099` ingress. **Staged, not applied**:
   `cloudflared-remove-vip.patch` in this directory (`patch -p1 --directory=/
   < cloudflared-remove-vip.patch`, then reload cloudflared via the sanctioned
   detached `systemd-run` path — never foreground, per house policy).
2. DNS — `vip.lumennode.uk` is Cloudflare-proxied (`dig` returns the
   anycast A pair `172.67.217.208`/`104.21.94.10`, not the real CNAME —
   proxied records hide the origin at the resolver). The tunnel is
   `85e6c90f-af20-4458-9eb8-84783828b145` (from `config.yml`); every other
   hostname on this tunnel resolves through
   `85e6c90f-af20-4458-9eb8-84783828b145.cfargotunnel.com`, so `vip`'s CNAME
   almost certainly matches that pattern. **Confirm the exact record in the
   Cloudflare dashboard (DNS tab, `lumennode.uk` zone) before deleting** —
   this worker has no Cloudflare API/dashboard access to verify directly.
   Delete the CNAME for `vip.lumennode.uk` there, or via
   `cloudflared tunnel route dns` tooling if Commander prefers the CLI.
3. `/home/lumen/agent/healer_watch_table.yaml` — added `lumen-vip-api` and
   `lumen-vip-watcher` as self-healer restart targets on 2026-08-27
   (wq#969-971), one day *before* the mill retirement on 2026-08-28. It's
   stale (self-healer will `systemctl restart` a unit that no longer exists
   and get no result — no crash, but dead weight in the table). `lumen-state`
   docs (`python3 /home/lumen/minerva/lumen_state.py current` →
   `lumen-self-healer.watch_list`) carry the same stale entries. Also
   stale in the same table: `lumen-prompt-engine` (another of the 8 disabled
   units) — out of this ticket's scope (not a ui-drift finding) but the same
   root cause; worth folding into whichever ticket applies this patch, or a
   fast-follow. **Not touched by this worker** — it's a live automation
   config outside the ui-drift dashboard surface this ticket owns.

Verification path once applied: `/home/lumen/bin/ui-drift` — `vip.lumennode.uk`
drops out of `findings` once the ingress is gone (no origin to probe = no
`ORIGIN_DOWN`). Confirmed *before* the patch: `ui-drift` streak 6 as of
2026-08-30T01:07Z.

## Files

```
ops/wq1099-ui-drift/cloudflared-remove-vip.patch   staged ingress removal, NOT applied
ops/wq1099-ui-drift/RUNBOOK.md                     this file
```
