# wq#4882 — mailbot internal API vs gsuite-mcp / gmail-mcp

2026-11-18. Verdict: do not acquire gsuite-mcp or a Gmail MCP for mailbot. Mailbot already has a
stable data API (D1, mirrored locally as `mail.db`). A thin adapter over it is built in
`ops/wq4882-mailbot-mcp/` (read/search plus drafts, no send). It is not registered with any client yet.

## What mailbot actually is

- A Cloudflare Email Worker on the bot address, not a Gmail client. It forwards every raw inbound
  message to Frank's real inbox first (`mailbot/src/worker.js:775-781`, `env.FORWARD_TO`), replies
  through the Cloudflare `SEND_EMAIL` binding, and stores everything in D1 `lumen-mail`
  (`mailbot/wrangler.toml:20-38`). No Google OAuth token exists anywhere in mailbot.
- D1 tables (`mailbot/migrations/0001`–`0009`, all additive): `threads`, `messages`,
  `contact_notes`, `attachments`, `tasks`.
- There is no HTTP API, by design: DESIGN_v2 option C, "No new endpoint on the worker"
  (`mailbot/DESIGN_v2.md:32-34`). Lumen reads D1 over the Cloudflare REST API with a D1-Read token
  and mirrors it into `/home/lumen/mailbot/mail.db`:
  - `mail_recall.py sync|recall|today|probes|notes|needs-frank`
  - `mail_tasks.py sync|pending|show|answer`
- There is one owner-initiated send path. `mail_tasks.py answer` writes `tasks.answer_body` with
  `status='ready'` (D1-Edit token, `mail_tasks.py:103-111`). The worker cron then sends it, threaded
  onto the original message (`worker.js:740-770`). Mailbot has no delete, forward-on-demand or
  calendar function.

Stability: this API is a schema and two CLIs, not a service. The guardian's E4/E5 checks
(`ops/wq1252-lumen-public-guardian/checks_email.py`) already rely on it. The adapter reads
`mail.db` directly instead of parsing CLI output, and the tests pin the mirror schema.

## Why not gsuite-mcp / personal-gmail-mcp

Both servers work on a Gmail account. Mailbot's mail never lives in Gmail as a mailbox the bot owns:
Gmail only holds Frank's forwarded copies. A Gmail MCP would solve a different problem, an agent
reading Frank's personal inbox. It would also bring the OAuth token the ticket wants kept away
from agents. That is a separate decision for Frank, and nothing here needs it.

If that decision is ever made, the leads below are search snippets only (fleet-search, 2026-11-18).
They are unverified because WebFetch is denied to workers:
- Google lists an official Gmail remote MCP server with user-delegated OAuth client IDs:
  `developers.google.com/workspace/gmail/api/guides/configure-mcp-server`.
- `github.com/klodr/gmail-mcp` describes a read-only token mode that hides write tools, and an
  allowlist for draft recipients.

Scope floor: `gmail.readonly` plus `gmail.compose`, token in a 0600 file, behind `lumen_mcp_guard`.

## The adapter (`ops/wq4882-mailbot-mcp/`)

| Ticket requirement | How it is met | Evidence |
|---|---|---|
| Thin adapter, no new mail subsystem | stdio FastMCP over `mail.db`. No listener, no new D1 tables, no worker change. | `server.py`, `mailstore.py` |
| Read/search | `mail_status`, `mail_search`, `mail_thread`, `mail_contact`, `mail_needs_frank`, `mail_pending_tasks` | tests 1-2 |
| Draft creation | `mail_draft_create(task_id, body)` writes `drafts.db` (0600). Never written to `mail.db` or D1. | test 4 |
| Approval for send/delete/forward/calendar | None of these tools exist. The guard `--allow` list is the whole surface (`mail_send` → "Unknown tool"). Sending = `mailstore.py approve <id>`, which needs Frank's approval passphrase (scrypt verifier, fixed path). wq#5175 replaced the original TTY + typed-back-id gate, which `script(1)` bypassed. | tests 6, 8; smoke |
| OAuth/credentials kept from agents | The MCP process opens no token file. The D1-Read token stays with sync; the D1-Edit token is read only by `approve`, after the passphrase check. | test 7 |
| Mail bodies are third-party text | The launcher runs the server behind `lumen_mcp_guard --untrusted-output --mask-output mask-secrets --max-result-bytes`. | smoke output: `UNTRUSTED DATA ... <<<untrusted-…` |

Drafts answer pending tasks only. Mailbot has no path for sending a free-form reply on an
arbitrary thread, and inventing one would be the new mail subsystem the ticket rules out.

**Correction (wq#5175).** This section first said the approve TTY gate stops a headless agent's Bash
call. That was wrong. The wq#5094 verify showed `script -qec "python3 mailstore.py approve <id>" /dev/null`
passes `isatty()`, and the typed-back draft id can be fed through the same pty. A TTY is not proof
that a human is there. The gate is now a secret only Frank holds: an approval passphrase checked against
a scrypt verifier at a fixed path. The body sent is the copy Frank reviewed. A test drives the exact
script(1) bypass and checks that it sends nothing (`test_script1_pty_bypass_is_refused`).
Residual: lumen can read the D1-Edit token, edit the code, and sudo. Deliberate tampering is the same
trust level as `mail_tasks.py answer`. The messenger Bash guard refuses those paths by name.

### Verification (2026-11-18)

- `python3 -m pytest ops/wq4882-mailbot-mcp/tests -q` → `8 passed`.
- Each probe was checked by running the suite against a mutated copy. Each of these produced exactly one failure:
  - opening the mirror `mode=rwc`
  - adding a `.cf_d1_token` read
  - adding a `mail_send` tool
- Live smoke on the real `mail.db` through the guard:
  `python3 ops/wq4882-mailbot-mcp/smoke.py --guard <main's lumen_mcp_guard.py>` → 5/5 PASS. The tool
  list equals the allowlist. `mail_draft_create` on an unknown task is refused, `mail_send` is
  unknown to the guard, and no `drafts.db` was created.
- With the deployed guard (`~/.local/share/mcp/_guard`) the launcher exits with
  `unrecognized arguments: --max-result-bytes 524288 --untrusted-output`. That guard predates foreman
  2283ca1b (2026-11-13), so the launcher fails closed until `ops/wq4514-mcp-install-batch/install.sh`
  redeploys the guard.

### Registering it (not done in this run)

Use project or local scope for the one brain that should read mail (messenger/Minerva). Never use
`-s user`, which would give every general-purpose agent access to Frank's correspondents. The form
install.sh uses:
`claude mcp add-json -s local mailbot '{"type":"stdio","command":"/home/lumen/foreman/ops/wq4882-mailbot-mcp/launcher.sh","args":[],"env":{}}'`.
This also covers wq#1197 (needs-frank for the messenger) without a grant into the messenger repo.

## Findings outside the adapter

Follow-ups:
- wq#5090: findings 1-2
- wq#5091: guard redeploy
- wq#5092: findings 3-4
- wq#5093: registration, which supersedes wq#1197
- ISSUE-LOG #1667, #1668

1. `lumen-mail-sync.timer` is not keeping `mail.db` current. A manual `mail_recall.py sync` on
   2026-11-18 pulled an outbound row stamped `2026-10-19T03:32:11Z`, 11 s past the stored cursor.
   A 5-minute timer would have pulled it within minutes. The unit could not be inspected:
   `systemctl --user status lumen-mail-sync.timer --no-pager` is refused by the worker allowlist.
2. The guardian status (`/home/lumen/logs/lumen-public-guardian-status.json`) was last generated
   `2026-10-08T07:10:31Z`, so its E4-sync "ok" is 41 days old.
3. `mail_tasks.py sync` uses a `created_at` cursor, so task status changes (`ready`, `sent`) never
   reach the mirror. The adapter works around this with its own approval record
   (`mailstore._approved_tasks`). The proper fix is in mailbot.
4. `mail.db` is mode 0644, so mail bodies are readable by every local user. 0600 matches the token
   files beside it.
5. D1 holds zero `tasks` rows to date (`mail_tasks.py sync` → `synced 0 tasks`), so draft creation
   has nothing to target until the task router classifies a real email.
