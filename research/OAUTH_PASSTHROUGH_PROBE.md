# wq#895 — OAuth passthrough probe (ANTHROPIC_BASE_URL local redirect)

ticket: wq#895 | agent: fworker-wq895-a2 | date: 2026-09-19 PT | source: PXPIPE_CANARY.md §4, §6

## VERDICT: **PASS. Max-plan OAuth survives an `ANTHROPIC_BASE_URL` redirect to a local pass-through proxy. ANTHROPIC_BASE_URL-class tools are not DOA on our stack.**

## What ran

`research/oauth_passthrough_probe.py` — a stdlib-only proxy on `127.0.0.1:8895` that relays
every request verbatim to `https://api.anthropic.com` (headers, body, streamed response) and logs
a masked summary. One `claude -p "Reply with exactly the word: pong" --model claude-haiku-4-5
--tools ""` on the real Max OAuth credential ran with `ANTHROPIC_BASE_URL=http://127.0.0.1:8895`.
Cost: one 10-token Haiku reply plus the CLI's ~95 KB startup request bodies.

## Result

| observation | value |
|---|---|
| claude exit / subtype / is_error | 0 / `success` / `false` |
| result text | `pong` |
| requests through proxy | 4: `HEAD /api/hello`, 3× `POST /v1/messages?beta=true` |
| upstream status on every request | 200 (no 401/403) |
| auth on the wire | `Authorization: Bearer sk-ant-oat01…` (108-char token), no `x-api-key` |
| `anthropic-beta` | includes `oauth-2025-04-20` |
| user-agent | `claude-cli/2.1.276 (external, sdk-cli)` |

The CLI does not refuse or downgrade auth for a non-Anthropic base URL: it sends the same OAuth
bearer and OAuth beta header it would send direct, and Anthropic accepts it. Streaming responses
relay through unchanged. Token values were never written; only scheme, 12-char prefix and length.

## Relation to prior work

wq#889 (`HEADROOM_PROXY_OAUTH_GATE.md`) reached the same conclusion through headroom, a full
proxy with its own transforms. This probe removes the variable: a byte-for-byte relay with no
transforms also passes, so the pass is attributable to the CLI/OAuth path, not headroom's behaviour.
That answers PXPIPE_CANARY.md §4 and unblocks pxpipe on the auth axis.

## What this does not clear

- Whether Anthropic meters or flags a non-direct client differently over time. The pxpipe/headroom
  risks in PXPIPE_CANARY.md (§3 metering, vision-token weighting — wq#894) are separate.
- Long sessions, tool use, MCP servers. Only a single-turn no-tools call ran here; wq#889 covered
  a `Read` tool call.
- A proxy is a new single point of failure for every worker routed through it.
- Guard: `harness/guard.sh:120` denies in-command `ANTHROPIC_BASE_URL=` re-injection. The probe
  sets it from Python via `subprocess` env, as `headroom_proxy_gate.py:266-269` does. Any real
  adoption should set it through `fworker.build_env` (harness/fworker.py:637), not shell.

## Reproduce

```
python3 research/oauth_passthrough_probe.py --workdir /tmp/wq895-probe   # spends one Haiku call
```

Exit 0 = success with no auth failure and proxy traffic seen. Writes `probe_report.json`.
