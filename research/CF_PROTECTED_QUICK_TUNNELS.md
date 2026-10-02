# wq#3420 — Cloudflare Protected Quick Tunnels (`--allowed-mail`) triage

Source: https://developers.cloudflare.com/changelog/post/2026-10-02-protected-quick-tunnels/
(filed by cf-changelog-watch, wq#3335). Blog: https://blog.cloudflare.com/protected-quick-tunnels/

## What changed
- `cloudflared tunnel --url http://localhost:8080 --allowed-mail alice@example.com` puts an
  email one-time-PIN gate in front of a `*.trycloudflare.com` Quick Tunnel. No account needed.
- Requires **cloudflared >= 2026.9.3** (per the blog). Older binaries won't recognize the flag.

## Fleet exposure
- Prod ingress (lumennode.uk) runs on a **named** tunnel behind Access. This feature doesn't affect it.
- The only recorded Quick Tunnel use is ad-hoc spikes. Example: wq#1804, an SSE edge proof
  (`despag/spike/CLOUDFLARE_EDGE_PROOF.md`) that ran briefly with a public URL and was then killed.
- No Quick Tunnel is in a script, unit, or bin tool. A grep of bin/, foreman/ and kb/ for
  `trycloudflare|tunnel --url` finds only the spike doc and ISSUE-LOG row 359.

## Verdict: adopt as a convention, no code change
Any future throwaway Quick Tunnel that serves something other than synthetic test data should
add `--allowed-mail <frank's address>`. Keep the ISSUE-LOG #359 isolation, too:

    ssh lumen@<lumen> 'cloudflared --version'   # must be >= 2026.9.3
    ssh lumen@<lumen> 'cloudflared --config /dev/null tunnel --url http://127.0.0.1:<port> --allowed-mail <addr>'

Without `--config /dev/null`, cloudflared reads `~/.cloudflared/config.yml` and attaches to the
prod named-tunnel identity instead (#359). The email gate doesn't change that.

Caveat: the OTP gate stops casual URL discovery, but it doesn't harden the origin. An exposed
origin still needs its own auth if it can do anything privileged.

Unverified here: the installed cloudflared version on Lumen. `cloudflared --version` and
`dpkg -s` aren't on the worker Bash allowlist, so check the version before first use.
