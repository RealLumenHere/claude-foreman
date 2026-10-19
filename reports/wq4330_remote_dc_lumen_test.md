# wq#4330 — Remote Desktop Commander on Lumen: direct claude.ai path, bypassing Horus

**Date:** 2026-10-19 · **Worker:** BUILD a1 · **Verdict: WORKS — paired, read/write/exec on Lumen proven over the hosted relay. Latency is the catch: ~1s per tool call, likely *slower* than the Horus hop. Keep Horus as fallback (per ticket); Frank has one 2-minute step left (claude.ai connector add).**

## What was done (all headless, no browser)

1. **Device agent installed & running on Lumen** — `npx @wonderwhy-er/desktop-commander@latest remote`, detached via `ops/wq4330-remote-dc/launch_agent.py` (pid file + log at `/tmp/fworker-wq4330/rdc/`). Status: `✅ Device verified`, device ID `9df52288-cbbd-46ef-96f8-20d58f627967`, name `lumen`.
2. **Fleet-owned account minted** — `lumen@lumennode.uk` (NOT Frank's gmail) on desktopcommander.app. The GUI-only OAuth device flow was reversed to plain REST (`ops/wq4330-remote-dc/dc_auth.py`):
   - `POST auth.desktopcommander.app/auth/backend/signup` (`auth_type: "device"`)
   - confirmation link pulled from fleet mail (`mail_recall.py recall desktopcommander`), tokens captured from the Supabase 303 redirect fragment
   - `POST /auth/backend/session/create` → 30-day session cookie, domain `.desktopcommander.app`
   - `POST mcp.desktopcommander.app/device/verify` with `Authorization: Bearer <supabase access_token>` + `{"user_code": ...}` → device authorized
3. **MCP client test against the relay** (`ops/wq4330-remote-dc/mcp_test.py`, raw numbers in `results.json`): the Supabase access token is accepted directly as the MCP bearer on `https://mcp.desktopcommander.app/mcp` — no separate PKCE dance needed for API clients.

## Acceptance answers

**Can an agent read/write files on Lumen directly, no SSH wrapping?** YES.

| Operation via relay | Result | Latency |
|---|---|---|
| `initialize` / `tools/list` (31 tools) | ok | 314 / 387 ms |
| `write_file` → worktree probe file | ok, content round-trip verified | 1291 ms |
| `read_file` probe file ×10 | ok | min 852 / **median 973** / max 1043 ms |
| `read_file /home/lumen/kb/ISSUE-LOG.md` (outside worktree — probe that could fail) | ok | 1093 ms |
| `start_process` `hostname && id -un` | ok, executed on Lumen | 955 ms |

**Latency vs. the Horus hop:** the relay path costs **~1s per tool call** (every call transits mcp.desktopcommander.app). Current-path components measured from Lumen: CF edge round-trip to `mcpw.lumennode.uk` = 89 ms (401 reject, lower bound, no exec); Lumen↔Horus LAN TCP RTT = 0.4 ms. End-to-end Horus-path timing wasn't measurable from this sandbox (CF Access gates mcpw; `ssh` not on the worker allowlist), but even with a generous ~300 ms for ssh exec, the Horus hop's infra overhead sums to ~0.4 s — **the hosted relay is roughly 2–3× slower per call**. It wins on simplicity (outbound-only agent; no cloudflared, mcp-proxy, CF Access, or DNS to maintain), not speed.

## Security & trust notes

- The relay is a **closed-source hosted beta**; whoever holds the `lumen@lumennode.uk` DC account gets full file/shell on Lumen while the agent runs. Mitigations: account is fleet-owned, password is machine-generated 24-char, recoverable only via fleet mail (`forgot-password` → `lumen@lumennode.uk`), devices revocable at mcp.desktopcommander.app dashboard; stopping the agent kills all access instantly.
- Credentials (`account.json`, `tokens.json`, `cookies.json`, all 0600) sit at `/tmp/fworker-wq4330/rdc/` — the proper home `/home/lumen/.secrets/` is deny-listed for BUILD workers. **Frank/unrestricted session: move them.** Loss is non-fatal (password reset via fleet mail). No secret appears in this repo or report.
- Access token expires hourly; `refresh_token` + password re-auth both work. The device agent holds its own long-lived device credential independent of these.

## Remaining steps (Frank, ~2 min, his claude.ai account — queued in done-notes)

1. claude.ai → Settings → Connectors → Add custom connector → `https://mcp.desktopcommander.app/mcp`
2. OAuth prompt → sign in with `lumen@lumennode.uk` + password from `/tmp/fworker-wq4330/rdc/account.json` on Lumen (or do a password reset to fleet mail first).
3. Ask claude.ai to list a directory on Lumen; compare felt latency against the mcpw path before deciding anything.

## Operational notes

- Agent survives this session (detached, session leader) but **not a reboot**. Restart: `python3 /home/lumen/foreman/ops/wq4330-remote-dc/launch_agent.py` then re-verify is NOT needed (device credential persists) — it reconnects as `lumen`.
- Productionization if adopted: systemd user unit for the agent + creds in `.secrets` + decide `allowedDirectories` (currently the agent exposes all of `/home/lumen` — the kb read above proves it).
- **Do not tear down mcpw.lumennode.uk / Horus** — fallback until Frank's side is proven (ticket requirement).
