# wq#3064 — verify-pass of wq#2483 (`research/NETWORK_PREMORTEM.md`, PR#1350)

Archetype 8 adversarial verify. Author: claude-opus-5-5. Verifier: claude-sonnet-5@high, fresh instance.

## Verdict: CONFIRM

## What was checked

The PR (http://100.121.17.5:3000/lumen/claude-foreman/pulls/1350) is already merged to
`main`: `f06c3c2d` adds `research/NETWORK_PREMORTEM_TOPTON_PIHOLE.md` (134 lines), `3854d119`
renames it to `research/NETWORK_PREMORTEM.md` — this is the shipped diff, read cold.

1. **Evidence pins.** `foreman@aff449a6` and `bin@52f576b`, both cited at the top of the doc,
   resolve to real commits (`git log -1`), timestamps consistent with same-day authorship.
2. **~20 internal `file:line` citations** across `bin/fleet-search`, `research/FLEET_SERVICE_ATLAS.md`,
   `harness/liveness.py` — spot-checked every one cited for D1-D14, S1-S9, T1-T6 headline
   claims. All line-accurate, no drift, no misquote.
3. **Live port-bind claims** (S1-S9, T1, T5, T6) — re-ran the equivalent of the doc's
   `ss -ltnuH` live probe against the actual host, hours after the doc's own 18:25Z capture.
   Every claimed bind (loopback vs wildcard vs Tailscale IP) matches exactly: LiteLLM
   127.0.0.1+100.117.66.102+fd7a:…:4000, ollama `*:11434`, memoryd 127.0.0.1:8139, ntfy
   0.0.0.0:8080, messenger 0.0.0.0:8765, Gatus `*:8418`, Commander `*:3100`, memory-mcp
   127.0.0.1:3104, Tailscale UDP 41641, SSH 22, resolved-stub 53.
4. **Headline claim §0.1** ("none of the service ports crosses the LAN in cleartext" despite
   several wildcard binds) — cross-checked consumer lists in `FLEET_SERVICE_ATLAS.md` §1.3 and
   grepped the repo for any LAN-IP (`192.168.8.x`) caller of ntfy/messenger. Every named
   consumer is Lumen-local or reaches the service via cloudflared; found no cross-node LAN
   caller. Claim holds.
5. **External citations**, fetched live via `fleet-search read` (same tool the doc used —
   one fetch even hit the fleet-search cache from the author's own 18:25:20Z session):
   - `tailscale.com/kb/1082/firewall-ports` — confirms UDP 41641, TCP 443, UDP 3478, DERP
     relay wording as quoted.
   - `tailscale.com/docs/install/opnsense` — confirms "Hybrid Outbound NAT rule generation"
     + "Check Static Port" verbatim (T3), and NAT-PMP as the documented alternative.
   - `reddit.com/r/pihole/comments/uvzvs5` and `discourse.pi-hole.net/t/42532` — both real;
     the discourse thread reproduces the exact P4 gotcha quoted (all queries arriving from
     the router's IP once a forwarder sits in front of Pi-hole, "no-blocking group didn't
     work at all").
   - Cloudflare tunnel port 7844 TCP+UDP claim (T4/D10) — confirmed true via search (the
     doc's own url does not; see below).

## Findings

**Minor — stale citation URL, content unaffected.** T4/D10 cites
`developers.cloudflare.com/cloudflare-one/connections/connect-networks/deployment-guides/tunnel-with-firewall/`,
which 404s today (`fleet-search read` confirms `http 404`, real title "404 - Page Not Found",
not a JS-rendering artifact). Cloudflare has since restructured this doc under
`cloudflare-one/networks/connectors/cloudflare-tunnel/configure-tunnels/tunnel-with-firewall/`.
The underlying factual claim the doc cites it for — "port 7844 is allowed for both TCP and
UDP protocols (for http2 and quic)" — is independently confirmed true at the new URL via
search snippet. No action needed beyond noting the link rot; does not change any
recommendation in §4.

**Not a defect, but worth a closer look next time this doc is touched:** §0.1's headline
sentence ("Block that flow (UDP 41641, or DERP/STUN egress) and every row of §2 fails
together") reads, on a literal parse, as if blocking UDP 41641 *alone* (with DERP egress
still open) is catastrophic. Tailscale's own docs (cited elsewhere in this same doc) and the
doc's own §0.2 three lines later both say the opposite for that exact case — losing the
direct path degrades to a DERP relay, "traffic still flows, only slower." On a more careful
read, "that flow" plausibly means "whichever one UDP flow is actually carrying this pair's
traffic at a given moment" rather than asserting 41641-alone is fatal — which is defensible,
if awkwardly worded. This does not touch the checklist: §4 correctly asks to pass T1
*and* T2 (41641 + 443 + 3478) as egress, so no operational guidance is wrong. Flagged as
phrasing to tighten, not a contradiction.

## Probe

The doc's own §1.1 acceptance probe ("`fleet-search web` returns `ok:true` with >0 results
from ≥2 engines") was exercised live multiple times during this verify (searches above all
returned `"ok": true` with google-cse plus other engines responding, some suspended —
matching the doc's own fail-soft framing for engine suspensions).

## Conclusion

Every citation checked (internal file:line, live port state, external doc/forum sources)
holds up, including several checked against independently-fetched live sources rather than
trusted at face value. One stale-but-content-correct URL found. No factual, evidentiary, or
recommendation-level defect found. This is a pre-deployment checklist by its own explicit
framing (§"Status") — no live network change was made or claimed, so there is nothing
deployed to re-verify beyond the document's own claims about current state, which check out.

**CONFIRM.**
