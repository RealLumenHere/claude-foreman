# wq#889 — Headroom Proxy OAuth Bearer Passthrough Gate (Max plan)

ticket: wq#889 | agent: fworker-wq889-a1 | date: 2026-08-24 PT | deps: wq#633

## VERDICT: **Gate PASSES on all four criteria. Adoption is still not cleared — the blocker moved from auth to tool-schema deferral.**

headroom-ai 0.36.5 forwards a Max-plan OAuth bearer byte-for-byte, and real `claude -p`
sessions complete through it with no auth error. The reason HEADROOM_EVAL.md §4 held
proxy mode back is now answered. But the live runs surfaced a behavior the eval did not
predict: the proxy rewrites the request's tool list, deferring 23–65 tool schemas per
request. That is 92% of the savings it reports, and it is a change to what the model can
see — a different risk class from the one this ticket was written to close.

## 1. What ran

`research/headroom_proxy_gate.py` — three checks, rerunnable:

| check | what it proves | cost |
|---|---|---|
| `synthetic` | passthrough is byte-exact | none — fake token, local recorder |
| `envleak` | where the ANTHROPIC_BASE_URL trust boundary lands | none |
| `live` (`--live`) | real sessions complete; compression fires | 3 short Haiku sessions |

The synthetic check points headroom's Anthropic upstream at a local HTTP recorder via
`--anthropic-api-url`, then pushes one `/v1/messages` through carrying a fake
`sk-ant-oat01-…` bearer. That gives an exact before/after on the header without putting
the real credential on a wire we terminate.

Note: the CLI verb is `headroom proxy`, not `headroom proxy start` as the ticket wrote it.

## 2. Criteria

**(1) OAuth bearer passes through untouched — PASS.**
The `Authorization` value arriving at the upstream is byte-identical to the one sent
(67 bytes in, 67 out, exact string match). No `x-api-key` is synthesized alongside it.
`anthropic-beta: oauth-2025-04-20` and the `claude-cli/…` user-agent both survive. The
full forwarded header set is `accept, accept-encoding, anthropic-beta, anthropic-version,
authorization, connection, content-length, content-type, host, user-agent, x-app` —
headroom adds no header of its own on the request side.

The proxy does *read* the bearer, in three places: auth-mode classification
(`proxy/auth_policy.py:66`), the rate-limit key, and the subscription tracker
(`subscription/tracker.py:182`). The tracker keeps the token in memory and polls
`https://api.anthropic.com/api/oauth/usage` with it — first-party endpoint, no third
party, and `--no-subscription-tracking` turns it off. Grepping `~/.headroom/*` for the
synthetic token after the run returns nothing: no token is written to disk. Telemetry is
off by default and the banner confirms `Security: loopback-only (no inbound token)`.

**(2) Session completes without auth errors — PASS.**
`claude -p` through `ANTHROPIC_BASE_URL=http://localhost:<port>` on the real Max-plan
credential: `subtype: success`, `is_error: false`, correct result, exit 0. Streaming,
prompt caching (`cache_read_input_tokens` non-zero through the proxy), and tool calls all
work — a second session was made to actually invoke `Read` and it returned the file's
contents. `stderr` was empty; the proxy log contains no 401/403 and no warning.

**(3) Compression metrics are emitted — PASS.**
`GET /stats` reports per-request and aggregate numbers. Synthetic run: one request
compressed, 32.3%, 85,740 → 50,630 body bytes, 9,980 tokens removed. Live run: 4 requests
compressed at 6.1% average, with `transforms_applied` naming each transform per request.

One caveat if we ever quote these numbers: every `summary.cost.*` field reads `$0.00`
under subscription auth, so headroom cannot price its own savings for us. And
`summary.compression.best_detail` ("50,566 → 46,748 tokens") does not reconcile with
`total_tokens_before` in the same block. Treat the token counts as directional.

**(4) ANTHROPIC_BASE_URL does not resurrect the DeepSeek env-leak class — PASS, with the
boundary relocated.**
`fworker.build_env` (harness/fworker.py:637) pops `ANTHROPIC_BASE_URL` and only re-sets it
for local-pool models, paired with the LiteLLM key and never with the OAuth token —
the mutually-exclusive invariant at fworker.py:641. A proxy path sets it to
`http://localhost:<port>`, so the worker-side leak class genuinely does not come back: the
value is a loopback address, the proxy binds 127.0.0.1 only, and the guard's in-command
re-injection deny rule is untouched.

What does change is *where* the redirect can happen. The `envleak` check starts the proxy
with `ANTHROPIC_TARGET_API_URL` in its environment and **no flag**, and the proxy silently
retargets `/v1/messages` to that host — carrying the bearer with it. The scrub fworker
does for the worker's env has no counterpart for the proxy's env. Same for `--http-proxy`
/ `HTTP_PROXY` and `--anthropic-extra-headers`. The one mitigation already present: the
startup banner prints the resolved routing table, so a redirect is visible to anyone who
reads the log.

If we adopt, the proxy unit must scrub its own environment the way `build_env` scrubs the
worker's, pass `--anthropic-api-url https://api.anthropic.com` explicitly, and assert the
banner's routing line at startup rather than trusting the default.

## 3. What the gate did not clear

**Tool-schema deferral is the real finding.** Live `request_logs` show
`transforms_applied: ["anthropic:tool_schema_compaction",
"router:tool_search_deferral:23tools:21748tok", …:40tools…, …:65tools…]`. Headroom pulls
tool definitions out of the request and expects the model to search for them. That
accounted for 114,124 of the 124,000-odd tokens it claims to have saved — the tool-result
compression this was evaluated for is the small half.

Both live sessions still worked, including the one that had to call `Read`. But "worked
on two short Haiku prompts" is not evidence for a worker doing git operations across a
long session with MCP servers loaded. Deferral scaled with toolset size (23 → 65 tools),
so the exposure grows exactly where our workers live.

**Latency is unmeasured.** headroom's own logs report ~2.5s `optimization_latency_ms` per
request, including on a 67-token request, which reads like fixed startup cost rather than
per-request work. Wall-clock supports that: the first session through a cold proxy was
slower than direct (10.5s vs 6.7s), while a later session on the warm proxy was faster
(4.2s). Single samples in both directions — this needs a controlled A/B, not an anecdote.

**Cache interaction is unmeasured.** HEADROOM_EVAL.md §6 established our in-session cache
hit rate is already 89–99%. Rewriting tool schemas per request is exactly the kind of
prefix mutation that costs cache writes. The two runs differ in `cache_creation` vs
`cache_read` split, but they were separate sessions with different cache warmth, so no
conclusion is available from this data.

## 4. Recommendation

Unblock proxy mode as a *technical* option — the OAuth question that gated it is settled.
Do not wire it into fworker on this result. The next ticket should be a controlled A/B on
a real worker payload measuring three things the gate deliberately did not: whether
tool-search deferral changes worker behavior over a full session, the per-request latency
tax against a warm proxy, and the cache-write cost of schema rewriting. Adoption stays
Frank's call per wq#633.

If it is ever wired: `--no-subscription-tracking`, explicit `--anthropic-api-url`, a
scrubbed environment for the proxy unit, and systemd with a restart policy (the proxy is a
new single point of failure for every worker).

## Reproduce

```
python3 -m venv /tmp/hr-venv
/tmp/hr-venv/bin/pip install 'headroom-ai[proxy]'
python3 research/headroom_proxy_gate.py --venv /tmp/hr-venv          # free checks
python3 research/headroom_proxy_gate.py --venv /tmp/hr-venv --live   # spends quota
```

Exit 0 when every check passes. `--workdir` keeps the proxy logs, captured upstream
requests, and `gate_report.json`.
