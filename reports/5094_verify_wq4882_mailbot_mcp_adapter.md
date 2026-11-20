# wq#5094: verify-pass of wq#4882 (mailbot MCP adapter, PR #2636 / e238b0c7)

2026-11-19 21:30 PST · verifier: orch-p2r2-1119-1bec4e (claude-opus-5-5) · read cold, probes re-run.

## Verdict: CONFIRMED, with one contradicted claim

The decision holds: don't acquire gsuite-mcp or a Gmail MCP for mailbot, and use the thin adapter over the `mail.db` mirror instead. The adapter does what the ticket asked. One security claim in `research/WQ4882_MAILBOT_MCP_ADAPTER.md` and `mailstore.py` is false, and it is filed as wq#5175.

Note on the ticket title: "P2 DEFER" was wq#4882's triage label, not its verdict. The outcome was "build the adapter, defer any Gmail MCP".

## What still holds (re-checked 2026-11-19, not taken from the PR)

| Claim | Probe | Result |
|---|---|---|
| Mailbot has no Google OAuth; Gmail only receives forwarded copies | `grep -rniE "googleapis\|oauth\|gmail\|refresh_token" mailbot/src mailbot/wrangler.toml` | only `FORWARD_TO = <frank gmail>` in wrangler.toml. No token, no Google API. No mailbot commits since 11-18 |
| Adapter tests | `python3 -m pytest ops/wq4882-mailbot-mcp/tests -q` | 8 passed |
| Guarded surface = allowlist; `mail_send` unknown; drafts never sent | `smoke.py` through the DEPLOYED guard | 5/5 PASS. Results wrapped `UNTRUSTED DATA ... <<<untrusted-<nonce>`; `mail_send` → `McpError: Unknown tool` |
| Registration never at user scope | `~/.claude.json` | `mailbot` absent from user `mcpServers`; present only for local project `/home/lumen/messenger` |
| No credential in the MCP process | code read: `mailstore.py` imports `mail_tasks` only inside `approve()` | holds |

## Conditions changed since 11-18 (all for the better)

- **wq#5091 done.** The deployed guard was redeployed, so the launcher no longer fails closed on `--untrusted-output`. Smoke ran through the default `_guard` path, without the `MAILBOT_MCP_GUARD` override.
- **wq#5093 done.** Registered for the messenger project only, as the research recommended.
- **wq#5090 done.** The sync timer and guardian staleness were fixed.
- **wq#5092 still claimed.** `mail.db` is still `-rw-r--r--` (0644) as of this check. The status-change sync gap is open.

## Contradicted: the `approve` TTY gate is not a boundary against a headless agent

The claims: "approve refuses without a TTY on stdin, so a headless agent's Bash call cannot run it" (`mailstore.py` docstring), and "The approve TTY gate stops a headless agent's Bash call" (research doc).

Probe (scratch drafts DB, nonexistent draft id, so nothing can reach D1):

```
MAILBOT_DRAFTS_DB=/tmp/probe.db python3 mailstore.py approve nonexistent-id </dev/null
  -> approve needs a human at a terminal (stdin is not a TTY) ...
MAILBOT_DRAFTS_DB=/tmp/probe.db script -qec "python3 mailstore.py approve nonexistent-id" /dev/null </dev/null
  -> no open draft nonexistent-id          <- past the isatty() check
```

`script(1)` (util-linux, installed) hands any non-interactive process a pty. The typed-back confirmation is `input()` read from that same pty, so the agent can feed it as well. A Claude Code session in `/home/lumen/messenger` has both the `mailbot` MCP and Bash. It could create a draft with `mail_draft_create` and then send it through `script`, with no human involved.

Severity is bounded. The same session could already call `mail_tasks.py answer` directly, because the D1-Edit token is readable by the `lumen` user, and the research doc says approve is "the same trust level as `mail_tasks.py answer` today". So this does not widen what a lumen-user agent can do. What it changes is the record: the docs present the TTY check as the control behind "require approval for send", and it is not one. The real controls are the MCP allowlist (no send tool) and whatever Bash policy the consuming session runs under.

Fix shape (wq#5175): either correct the docs to say the TTY check is a speed bump, or make approval out-of-band so an agent can't satisfy it. Examples: an energize-confirm style single-use token Frank mints (`foreman/harness/energize_confirm.py`, as serial-maksoor does), or a park card. Also deny `mailstore.py approve` and `mail_tasks.py answer` in the messenger session's Bash policy.

## Not re-tested

- A real `approve` → D1 → cron send was not run: it would send mail. D1 still holds zero `tasks` rows, per the original finding 5.
