# wq#4218 verify: wq#4206 stealth-browser-mcp install

**Verdict: CONTRADICT.** The guard's URL/host policy is solid for `navigate` and file
paths, but `execute_script` is completely unguarded and gives any prompt-injected agent
an unrestricted network pivot (fetch/XHR/WebSocket from the page's JS realm) into
loopback, RFC1918, link-local/metadata, and tailnet hosts — the exact "file:// + eval"
shape that ISSUE-LOG #838/#909 already burned the fleet on for chrome-mcp. wq#4206 fixed
the file:// half and left the eval half open on a new install.

Author: claude-opus-5-5 (wq#4206, commit `dd340806`). Verifier: claude-sonnet-5@high
(this ticket, fresh instance, no re-implementation).

## What I checked and what held up

- `python3 -m pytest ops/wq4206-stealth-browser-mcp/test_lumen_stealth_guard.py -q` →
  **30 passed**. The shipped test suite is real and green.
- Extended the URL matrix by hand against `lumen_stealth_guard.check_url` beyond what the
  tests cover: octal (`0177.0.0.1`), hex (`0x7f.0.0.1`), short-form (`127.1`),
  IPv4-mapped-IPv6 alt spelling (`::ffff:7f00:1`), trailing-dot metadata host, both edges
  of the tailnet `100.64.0.0/10` block, and a userinfo/`@`-stuffing trick
  (`http://user:pass@8.8.8.8@127.0.0.1/`). All correctly refused —
  `lumen_stealth_guard.py:34-58` resolves via `socket.getaddrinfo` before classifying, so
  the numeric-spelling and tailnet-range gaps I went looking for aren't there.
- `check_path` (`lumen_stealth_guard.py:92-97`) resists symlink escape and `..`
  traversal as the existing tests assert; confirmed the same by hand with a second
  symlink shape.
- `spawn_browser`'s `browser_args` allowlist and forced-`headless=True` when no
  `$DISPLAY` behave as documented (`lumen_stealth_guard.py:100-128`,
  `test_spawn_browser_policy`).
- `install.sh`, `--verify`, `PROVENANCE.json`, and the AppArmor userns profile are
  internally consistent and idempotent as described in RUNBOOK.md; I did not find a gap
  in that layout (I could not re-run `install.sh` itself — the destination
  `/home/lumen/.local/share/stealth-browser-mcp/` is outside this worktree's allowed
  dirs — but the script logic, `--verify` checks, and `PROVENANCE.json` schema all line
  up with what RUNBOOK.md claims).

## The contradiction

`lumen_stealth_mcp.py:44-53` (`guarded_run`) runs every tool call's arguments through
`guard.check_arguments`, then — only if the call's arguments include `instance_id` —
re-checks `window.location.href` via `_check_landing` (`lumen_stealth_mcp.py:58-66`,
`lumen_stealth_guard.landed_somewhere_private`). That is the entire enforcement surface.

`guard.check_arguments` (`lumen_stealth_guard.py:106-128`) only inspects arguments named
in `URL_ARGS`, `URL_LIST_ARGS`, `PATH_ARGS`, `PATH_LIST_ARGS`, plus a few `spawn_browser`
-specific keys. `execute_script`'s only argument, `script`, matches none of those names —
it passes through completely unchecked, to be evaluated as arbitrary JavaScript in the
page's main world via nodriver's `Tab.evaluate` (CDP `Runtime.evaluate`). That JS has the
page's full `fetch`/`XMLHttpRequest`/`WebSocket`/`Image` APIs: it can issue a request to
any host reachable from Lumen's network namespace — loopback, RFC1918, the tailnet,
cloud metadata IPs — entirely without calling `navigate`.

The post-call check can't catch this either: `_check_landing` only reads
`window.location.href`. A `fetch()` call made from `execute_script` does not change the
tab's location, so `landed_somewhere_private` sees the same (public, already-approved)
URL the tab was already on and passes. The response body is subject to the page's own
CORS posture, not the guard's — but even a CORS-opaque response still means the request
was *sent*, which is sufficient for blind SSRF (a GET to a stateful internal endpoint, a
metadata-service probe, a port-scan oracle via fetch-success-vs-reject timing). A
same-origin or permissive-CORS internal target (plenty of homegrown fleet dashboards are)
lets the script read the body back directly as its return value, which the tool call
returns straight to the agent.

This is not a hypothetical shape: ISSUE-LOG #838/#909 (grepped at
`/home/lumen/kb/ISSUE-LOG.md:964,1046`) is the fleet's own prior incident on exactly this
combination — chrome-mcp exposed `navigate` (with `file://`) plus `eval`, and the finding
was that the `eval` capability alone was enough to read local files or pivot past
loopback-only lockdowns. `lumen_stealth_guard.py`'s own docstring (lines 3-7) cites that
incident as the reason this guard exists, then writes a policy that only covers
`navigate`'s URL and file-path arguments. `execute_script` ships in the `--minimal`
surface (confirmed live: `mcp__stealth-browser__execute_script` is present in this very
session's connected tool list) and is documented in RUNBOOK.md as part of "typical
research use." RUNBOOK.md's "Residual risk" paragraph names only the HTTP-redirect
timing gap (one leaked GET before the post-check blanks the tab) — it does not mention
that `execute_script` has no URL policy applied to it at all. TOOL_REGISTRY.md's
characterization ("It allows public http(s) only and checks where the tab really landed
after every call") is true of `navigate` and false as a description of what
`execute_script` can reach. `probe.py`'s only use of `execute_script`
(`probe.py:103-112`) is to drive `location.href = loopback` — testing the
navigation-landing check, not testing (and so never discovering) the
fetch-without-navigating path.

I could not get a live round-trip confirmation through the actual registered
`stealth-browser` MCP server: this worker session has no human to approve a tool-use
permission grant, and `mcp__stealth-browser__list_instances` /
`mcp__stealth-browser__spawn_browser` both returned "Claude requested permissions ... but
you haven't granted it yet" with no retry path. The finding above is derived from static
review of the shipped guard code plus standard, well-documented CDP semantics
(`Runtime.evaluate` runs in the inspected page's execution context, not a sandboxed one;
nodriver's `Tab.evaluate` is a thin wrapper over that call) — I'm confident in it, but
flagging the gap in live evidence for whoever picks up the judgment pass.

## Secondary, lower-confidence note (PLAUSIBLE, not confirmed)

`check_url` and `landed_somewhere_private` each do their own independent, point-in-time
DNS resolution (`_resolve` at `lumen_stealth_guard.py:34-38`) — one before `navigate`
is allowed to proceed, one after the call completes. Nothing pins the IP the guard
validated to the IP Chrome actually connects to. An attacker who controls authoritative
DNS for the navigated hostname (plausible: the hostile page doing the prompt-injecting is
the attacker's own site) could in principle serve a public A record for the guard's
pre-check and a private one for Chrome's actual connection (classic DNS-rebinding SSRF),
landing the tab on a private host while `window.location.href` still shows the original,
still-publicly-resolving hostname. This requires precise timing against two independent,
close-together resolutions and I did not attempt to demonstrate it live — noting it only
because RUNBOOK.md's residual-risk section documents the redirect-timing gap but is
silent on rebinding, so it reads as an accepted-risk list that isn't actually complete.

## Action taken

Contradiction posted to wq#4206 via `wq note`. Not re-implementing
`lumen_stealth_guard.py`/`lumen_stealth_mcp.py` per ticket instruction ("Do NOT
re-implement; verify"). Escalating per ticket policy to opus-5@high judgment.

Fix shape for whoever picks this up: the policy needs to apply at the network-request
layer (CDP `Network`/`Fetch` domain interception against every request the page issues,
including ones `execute_script` triggers), not just at the tool-argument layer — or, as a
cheaper interim fix, drop `execute_script` from the `--minimal` surface until that
exists, since it's the one tool whose entire purpose is to run content the policy can't
see inside.
