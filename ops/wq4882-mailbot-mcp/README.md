# mailbot-mcp (wq#4882)

A thin stdio MCP adapter over mailbot's local mirror `/home/lumen/mailbot/mail.db`. It offers
read/search and drafts of task answers. There is no send tool, and the process holds no
credential. The investigation and decision record is `research/WQ4882_MAILBOT_MCP_ADAPTER.md`.

| File | Role |
|---|---|
| `mailstore.py` | Queries (mirror opened `mode=ro`), the drafts store (`drafts.db`, 0600), and the human CLI `drafts` / `approve <id>` / `discard <id>` |
| `server.py` | FastMCP wrappers, 8 tools |
| `launcher.sh` | Runs the server behind `lumen_mcp_guard` (allowlist, mask, untrusted wrap). This is the command to register. |
| `smoke.py` | Live check through the launcher. It prints no mail bodies. |
| `tests/` | `python3 -m pytest ops/wq4882-mailbot-mcp/tests -q` |

Sending needs Frank's approval passphrase (wq#5175). A TTY check alone was bypassable:
`script -qec "... approve <id>" /dev/null` gives any shell a pty. Only a scrypt verifier is stored,
at `/home/lumen/.config/mailbot-approve/verifier.json` (0600). The path is fixed in code, not taken from env.

One-time setup (rotation asks for the current passphrase):
`ssh -t lumen@100.117.66.102 'python3 /home/lumen/foreman/ops/wq4882-mailbot-mcp/mailstore.py set-passphrase'`

To send a draft:
`ssh -t lumen@100.117.66.102 'python3 /home/lumen/foreman/ops/wq4882-mailbot-mcp/mailstore.py approve <draft_id>'`
The mailbot cron delivers it within 15 minutes. Until a passphrase is set, approve refuses and sends nothing.

Boundary: the lumen account can read the D1-Edit token and has sudo, so deliberate tampering is outside
this gate. The messenger Bash guard (`messenger/guards/pretooluse_guard.py`, MAIL_SEND) refuses those
paths by name for the brain and for interactive sessions in /home/lumen/messenger.
