# wq#1151 — lumen-kb build-out artifacts

Self-hosted per-topic KB (markdown vault at `/home/lumen/kb`, git-versioned)
exposed to Claude via MCP. Architecture B headless-native. This ops dir is the
**canonical source** for the code deployed to `/home/lumen/projects/lumen-kb-mcp`
(the initial prototype there was built ad hoc in a prior session; this tree
supersedes it and adds the P1 hardening).

| file | what |
|---|---|
| `server.py` | lumen-kb-mcp: FastMCP streamable-http, 127.0.0.1:8781, tools list_topics / search / read_note / write_note / list_attachments, git-commit on write |
| `kb_paths.py` | P1 traversal guards — every path both server and distiller touch |
| `kb_distill.py` | P3 distiller: transcript → LiteLLM summary (+ vision captions) → vault note, manual CLI trigger |
| `lumen-kb-mcp.service` | P1 systemd unit (loopback-only, sandboxed to the vault) |
| `apply.sh` | idempotent host deploy + handshake verify (**privileged session only** — uses sudo) |
| `tests/` | 25 pytest cases: traversal/symlink escapes, distiller write+commit, jsonl extraction |
| `P2_EXPOSURE_REVIEW.md` | mcpw exposure design + go-live checklist (NOT deployed) |

## Deploy (privileged session)

```
bash ops/wq1151-lumen-kb/apply.sh          # copies code, venv, unit, verifies handshake
python3 ops/systemd_desired_state.py capture   # record the new unit as desired state
```

## Phase status

- **P1** — done here: unit file, traversal guards + tests. SDK 2.x migration
  **not** done (2.x layout unverifiable offline from this lane); see
  `requirements.txt` for the migration procedure. server.py imports both layouts.
- **P2** — designed + reviewed on paper, not live. See `P2_EXPOSURE_REVIEW.md`.
- **P3** — distiller built + tested with mocked LLM. First real run (privileged,
  needs the `lumen-kb` LiteLLM service key minted):
  `kb_distill.py --topic google-nest --transcript <session> --dry-run`
- **P4** (search v2, embeddings) — deferred until ripgrep search proves weak.
- **P5** — connector instruction block, paste into the claude.ai connector
  description at P2 go-live:

  > Before answering any question about a topic this KB covers, call
  > `list_topics`, then `search` the relevant topic. Prefer KB facts over
  > memory. After a session that produced durable new facts, `write_note`
  > a short distillation into the matching topic (never secrets).

  Real google-nest seeding from Frank's chats/pics rides on P3's first runs.
