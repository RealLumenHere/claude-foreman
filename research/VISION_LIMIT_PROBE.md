# wq#894 — Vision-token vs text-token subscription-limit burn

**Status: NOT MEASURED.** No deltas exist yet. The probe is built and dry-run tested; the spend step
could not run from the BUILD lane. Verdict on pxpipe / Headroom image-mode stays open.

## Why it did not run

`claude -p` is not on `harness/worker-settings.json`'s per-command allowlist (only `python3 *` and
`curl *` are; `claude setup-token*` is denied). The refusal was
`This Bash command contains multiple operations. The following part requires approval: ... claude -p ...`.
Wrapping the call in `python3 subprocess` would sidestep the gate, so it was not done.
Frank's spend approval is recorded on wq#894 (2026-09-19 00:55 PDT); the block is tooling, not consent.

## Probe

`research/vision_limit_probe.py` (`plan` = no spend, `run` = one text arm + one image arm):

- Text arm: ~N random short words (~1 token each, no cacheable repetition).
- Image arm: noisy PNG sized to `ceil(w/28) * ceil(h/28)` = N visual tokens (default 1456 = pxpipe's
  1568x728 page; long edge <= 1568 so no downscale).
- Same trailing prompt, no tools, no session persistence, stream-json in and out.
- Before and after each arm it records `state.json` limits (session, weekly_all) and the result-frame
  `usage`, plus any `rate_limit_event` frames. It waits 100 s after each arm for a governor poll.

## Measurement hazards a runner must handle

1. **Integer percent.** `state.json` (from `/api/oauth/usage`) reports whole percent. One arm at
   N~1.5k tokens is likely far below 1%, so both deltas may read 0. Raise `--tokens` (bounded by the
   image cap: 1568 tokens on standard models, 4784 on 4.7+) or read `rate_limit_event.utilization`
   from the stream frames, which may be finer. Check that field first.
2. **Shared subscription.** Every fleet worker moves the same counters. Run with the dispatcher
   quiet, or the delta is noise. Frank said no repeats, so a noisy single pair is inconclusive.
3. **429 on `/api/oauth/usage`.** `state.json` `reused_cache: true` means the "after" value can be stale.
   Compare `updated_at` before and after.
4. **Cache.** Fresh random payloads avoid cache reads, which bill differently from fresh input.

## Decision rule

- Image-arm session delta clearly below text-arm at equal N: vision compression pays; probe pxpipe and Headroom canaries.
- Deltas equal (both 0 counts as inconclusive, not equal): image tokens are billed like text. Compression
  then depends on the pxpipe 3.1x chars-per-token ratio alone (PXPIPE_CANARY.md §1) and the recall
  blocker in §3 decides it.
- Image-arm higher: vision context is a net loss for subscription users.

## Next step

Run `python3 research/vision_limit_probe.py run --tokens 4000` on a lane that can call `claude -p`
(or add `Bash(/home/lumen/.npm-global/bin/claude -p*)` to the allowlist), fleet quiet, then fill in
the table below.

| arm | N | session % before -> after | weekly_all % before -> after | result.usage |
|-----|---|---------------------------|------------------------------|--------------|
| text | | | | |
| image | | | | |
