# wq#3882 — LIMPET: flashrom MCP server

FastMCP wrapper around `flashrom` (catalogued at `/usr/sbin/flashrom` on the maksoor RE
host, `TOOL_REGISTRY.md:21`) — the usual first step of a firmware-extraction workflow:
find the programmer, probe the chip, dump it before touching anything, then
write/verify/erase against a backed-up image.

## Tools

| tool | flashrom action | notes |
|---|---|---|
| `identify_programmer` | — | delegates to the LIMPET HAL (`ops/wq3809-limpet-hal/hal.py detect`, filtered to `hw_type="flash_programmer"`) instead of re-deriving USB VID:PID signatures here |
| `detect_chip(programmer, chip="")` | none (flashrom's default: probe only) | run first; parses `Found <vendor> flash chip "<name>" (<N> kB, <bus>)` lines out of stdout |
| `read_flash(programmer, output_path, chip="", region="", layout_file="")` | `-r` (`-l`/`-i` for `region`) | creates `output_path`'s parent dir; `region` requires `layout_file` (flashrom rejects `-i` without a layout source — raises `FlashromError` locally otherwise); always do this before write/erase |
| `write_flash(programmer, input_path, chip="", verify=True)` | `-w` (`-n` if `verify=False`) | requires `input_path` to already exist |
| `verify_flash(programmer, input_path, chip="")` | `-v` | requires `input_path` to already exist |
| `erase_chip(programmer, confirm=False, chip="")` | `-E` | **refuses unless `confirm=True`** — destructive, no undo without a prior `read_flash` |

Every call is `subprocess.run([...], shell=False)` with an argv list — a `programmer` or
path string can't break out of its argument position. A nonzero flashrom exit is
returned as `{"ok": false, "returncode": ..., "stdout": ..., "stderr": ...}`, not raised,
so the operator/agent sees flashrom's own error text; only a missing binary or a timeout
raises `FlashromError`.

## Layout

- `flashrom_ops.py` — all the logic (argv construction, subprocess handling, output
  parsing), no `mcp` import. Split out because `mcp` isn't pip-installed in this build
  lane — same reason `ops/wq1151-lumen-kb` splits `kb_paths.py`/`kb_distill.py` out of
  its `server.py` (see that dir's `requirements.txt`).
- `server.py` — `FastMCP("limpet-flashrom")`, one `@mcp.tool()` per `flashrom_ops`
  function, `mcp.run(transport="stdio")`. Pattern follows
  `ops/wq1151-lumen-kb/server.py`, the FastMCP reference implementation in this repo.

## Registering as a meta-server child

Add to `ops/wq3808-limpet-mcp-meta-server-a1`'s manifest:

```json
{
  "name": "flashrom",
  "transport": "stdio",
  "command": ["python3", "/path/to/ops/wq3882-limpet-flashrom-mcp-a1/server.py"],
  "enabled": true
}
```

## Tests

```sh
python3 -m pytest ops/wq3882-limpet-flashrom-mcp-a1/tests -q
```

No real flashrom binary or hardware is exercised — `subprocess.run` is monkeypatched
throughout, including a fake `hal.py` path for `identify_programmer`.
