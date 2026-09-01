# wq#1157 — ui-drift: qbit.lumennode.uk ORIGIN_DOWN

Latest report (`/home/lumen/maint/ui-drift/latest.md`, 2026-09-01T11:15:36Z):

```
[ORIGIN_DOWN] qbit.lumennode.uk (http://localhost:8108) — curl: (56) Recv failure:
Connection reset by peer  (streak 2)
```

## Both of the ticket's usual fixes are already correct — this is not drift

- `/etc/cloudflared/config.yml` already has `qbit.lumennode.uk -> http://localhost:8108`,
  matching the probe target exactly. No ingress correction needed.
- `/home/lumen/glance/glance.yml` already lists qBittorrent under the Media group
  (`https://qbit.lumennode.uk`), inside the `ui-inventory`-generated block. No dashboard
  regeneration needed.

## Root cause: qbittorrent itself is unresponsive on thoth, not a local misconfig

`localhost:8108` on this host is not a local service — it's one leg of a long-running
`autossh` tunnel to `thoth` (192.168.8.218):

```
/usr/lib/autossh/autossh -N -i /home/lumen/.ssh/id_ed25519 ... \
  -L 127.0.0.1:8108:127.0.0.1:8080 -L 127.0.0.1:32400:127.0.0.1:32400 \
  -L 127.0.0.1:8989:127.0.0.1:8989 -L 127.0.0.1:7878:127.0.0.1:7878 \
  -L 127.0.0.1:9696:127.0.0.1:9696 thoth@192.168.8.218
```

i.e. `qbit` is `thoth`'s own `127.0.0.1:8080`, tunneled in. `curl localhost:8108`
reproduces the probe's exact failure (TCP connects, then reset on the HTTP request). The
tunnel process itself is healthy — every *other* port it forwards from the same `thoth`
leg answers normally: plex (32400) → 401, sonarr (8989) → 200, radarr (7878) → 200,
prowlarr (9696) → 200. That isolates the failure to the qbittorrent process/port on
`thoth`, not the tunnel, not DNS, not cloudflared.

## Why this worker can't apply the fix

The actual remediation is "restart qbittorrent on thoth" — this build worker has no path
to do that:

- `ssh ... thoth@192.168.8.218 ...` is not on the per-command Bash allowlist
  (`harness/worker-settings.json`) and isn't reachable from a headless session (the
  approval prompt has no one to answer it).
- `thoth` is a remote fleet node, not a unit in this host's
  `/home/lumen/agent/healer_watch_table.yaml` — the self-healer only restarts local
  systemd/docker units, so it doesn't cover this either.

No config change is needed or staged here (unlike wq#1099's `vip` case) — there's nothing
wrong to patch. This is a live-service outage on `thoth` and needs whoever has SSH/console
access there to check and restart qbittorrent (docker container or systemd unit — not
determined from this side) directly.

## Verification once fixed

`curl http://localhost:8108/` from this host (or `/home/lumen/bin/ui-drift`) should
return the qBittorrent WebUI instead of a connection reset.
