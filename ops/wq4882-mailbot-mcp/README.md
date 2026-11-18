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

To send a draft, Frank runs this from a terminal over `ssh -t`:
`ssh -t lumen@100.117.66.102 'python3 /home/lumen/foreman/ops/wq4882-mailbot-mcp/mailstore.py approve <draft_id>'`
The mailbot cron delivers it within 15 minutes.
