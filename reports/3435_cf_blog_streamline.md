# wq#3435 triage: CF blog "Streamline: custom video pipelines with Cloudflare Stream and Workers"

Source: https://blog.cloudflare.com/streamline/ (2026-10-02), filed by cf-changelog-watch (wq#3335).
Read via `fleet-search read` on 2026-10-03.

## Verdict: informational, no action required. Close.

The post announces a developer playground and reference architecture. It does not change any
platform behavior. The "Workers" and "Durable Objects" keyword hits describe how the demo is
built; neither product's limits or semantics change. Our Cloudflare footprint (mailbot Worker,
mail-probe, D1 `lumen-mail`, Email Routing, cloudflared tunnel, Access) does not use Stream or
Containers. The LPP stream (`pokemon.lumennode.uk`, `LUMEN_PUBLIC_RUNBOOK.md:10`) is a local
service on `:8871` behind the tunnel. It is not an RTMP/HLS pipeline, and the pokemon repo has
no rtmp/ffmpeg code (grep of `*.ts/*.py/*.md/*.sh`, 2026-10-03).

## What it is (summary)

- **Media Engine** runs in a Cloudflare Container. A Go HTTP controller drives an FFmpeg
  processor. Inputs: RTMPS pull from a Stream Live input, Stream HLS manifests (hosted video),
  or chunks pushed by the app ("webcam" mode). Output: RTMPS to another Stream Live input or a
  new hosted video. Preview goes over an outbound WebSocket to a Durable Object relay.
- **Application** is a Worker. A Durable Object subclass orchestrates the session, the
  Container lifecycle, and the preview relay.
- **Long-running sessions:** the controlling Worker can disconnect and the Container keeps
  processing. The post overrides `onActivityExpired()` so the Container renews its activity
  timeout until the session's max duration, then calls `destroy()`. This pattern applies to
  any long-lived Container job, not only video.
- **API:** `@cloudflare/streamline/client` exposes sessions with `create`, `resume`, `start(config)`,
  `ingest`, `annotation(png)`, `metrics` and `stop`. A pipeline is a JSON config of
  input, ops (`overlay`, `encode`, ...) and output.
- **Open source:** https://github.com/cloudflare/streamline (engine and packages) and
  https://github.com/cloudflare/streamline-demo (Worker, Astro frontend, Access tooling).
  A hosted public playground is also available.

## Possibly useful later (nothing is filed from this ticket)

1. **LPP restream/overlay:** if LPP ever broadcasts to Stream Live, Twitch, or YouTube over
   RTMP, `session.annotation(png)` would put dynamic overlays (party, badges, agent thoughts) on
   the broadcast without a local OBS/FFmpeg box. Today there is no RTMP leg, so there is nothing
   to do.
2. **Container keep-alive pattern:** the `onActivityExpired()` renew-until-deadline approach is
   the reference to reuse if we ever move a long-running fleet job into CF Containers.
