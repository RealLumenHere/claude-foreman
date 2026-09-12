# wq#1804 — S-09 progressive SSE behind Cloudflare (quick tunnel)

Method: `despag/spike/app.py` on `127.0.0.1:45393`, throwaway `cloudflared tunnel --url`
quick tunnel (no account, no config — see gotcha below), suite run from Lumen against the
public `*.trycloudflare.com` URL.

## Result: S-09 PASSES behind the edge, unmodified

`uhp_conformance.cli --base-url <edge> --class core --only S-04,S-09,C-03` (report:
`despag/spike/report_cloudflare.json`):

```
PASS  S-04  sequence_number starts at 0 and has no gaps
PASS  S-09  The stream is progressive, not buffered to the end — events spread over 2.7s
PASS  C-03  A running task can be cancelled and ends non-running — final status=cancelled
3/3 passed
```

No response header was needed — `app.py`'s existing `StreamingResponse(..., media_type=
"text/event-stream")` (app.py:244) streamed through Cloudflare's edge without
`X-Accel-Buffering`, `Cache-Control`, or `Content-Encoding` overrides. Confirmed at the
HTTP level with a raw streamed POST to `/v1/responses` (`stream: true`):

| event index | loopback t | edge t |
|---|---|---|
| response.created (seq 0) | 0.018s | 0.068s |
| response.in_progress (seq 1) | 0.019s | 0.068s |
| response.output_item.added (seq 2) | 1.869s | 1.909s |
| response.output_text.delta (seq 4) | 1.870s | 1.909s |
| response.output_text.done (seq 5) | 2.395s | 2.425s |

Edge response headers seen: `content-type: text/event-stream; charset=utf-8`,
`transfer-encoding: chunked`, `connection: close`, `server: cloudflare`,
`cf-cache-status: DYNAMIC`, `uhp-version: 2026-08-11`. The multi-second gap between the
first two events and the later ones arrives at the same wall-clock offset via the edge as
via loopback (~50ms added latency, no buffering to completion) — proof the edge is not
collecting the stream before forwarding it.

## Gotcha logged: ISSUE-LOG #359

The first two tunnel attempts (`cloudflared tunnel --url ...`, no `--config` flag) silently
loaded `/home/lumen/.cloudflared/config.yml` and its named-tunnel credential file — turning
the intended anonymous quick tunnel into an extra HA connection on the real prod tunnel
identity, which doesn't route `*.trycloudflare.com` and 404s at the edge. Fixed by passing
`--config /dev/null --origincert /dev/null` so the process cannot see `~/.cloudflared` at
all. Prod `cloudflared` systemd unit was not touched or restarted (verified single PID
before and after, `systemctl is-active cloudflared` stayed `active` throughout).

## Teardown

Quick tunnel and spike app processes killed; `pgrep -x cloudflared -a` shows only the prod
systemd unit's PID; prod `cloudflared.service` confirmed still `active`.
