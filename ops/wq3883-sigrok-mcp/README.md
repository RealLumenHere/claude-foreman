# wq#3883 — sigrok MCP server (logic analyzer capture/decode)

FastMCP wrapper around `sigrok-cli` (already in the RE tool catalog, see
`TOOL_REGISTRY.md`) for logic-analyzer work: list devices, capture, decode
protocols, export. Follows the FastMCP pattern from
`ops/wq1151-lumen-kb/server.py`.

## Pieces

- `sigrok_cli.py` — pure argv-building and output-parsing, no subprocess
  calls. Every command shape and output format it assumes is pinned to a
  verified source (sigrok-cli(1) man page EXAMPLES, or the sigrok wiki's
  `File_format:Protocoldata` / `Protocol_decoder:I2c` pages for decode
  annotation lines), not guessed — see the docstrings and the test fixtures
  in `tests/test_sigrok_cli.py`, which are copied verbatim from those
  sources.
- `runner.py` — the subprocess boundary: `shutil.which` check, then
  `subprocess.run(argv, shell=False, ...)`. Argv lists only, matching the
  house rule in `ops/wq3808-limpet-mcp-meta-server-a1/cli_wrapper.py` (no
  manifest/tool arg is ever interpolated into a shell string).
- `server.py` — the FastMCP tools:
  - `list_devices(driver="", conn="")` — `sigrok-cli --scan`.
  - `device_options(driver, conn="")` — `sigrok-cli --driver <d> --show`,
    returned as raw text (the man page documents `--show` exists but not a
    fixed samplerate-list output format per driver, so this stays raw
    rather than guessing a schema).
  - `configure_samplerate(samplerate)` — normalizes `"1MHz"` / `"500 kHz"` /
    `"1m"` / `1000000` into the Hz value `capture()` needs.
  - `capture(driver, samplerate, output_path, ...)` — one `sigrok-cli`
    acquisition; `samples` or `duration`, not both; defaults to the native
    `.sr` session format that `decode()`/`export()` expect as input.
  - `decode(input_file, decoders, annotations="")` — runs libsigrokdecode
    decoders (`spi:miso=1:mosi=5:clk=3:cs=0`, `uart:rx=0:tx=1`, `i2c`,
    `jtag`, ...) over a captured session and parses the
    `<decoder>-<row>: <text>` annotation lines.
  - `export(input_file, output_path, output_format)` — re-exports a `.sr`
    session to another format (`vcd`, `csv`, ...).

Pairs with Sootty (wq#3878): `export(..., output_format="vcd")` produces the
VCD that Sootty renders as a terminal waveform.

## Running

```
pip install -r requirements.txt
python3 server.py            # streamable-http on 127.0.0.1:8782
```

## Tests

```
pytest ops/wq3883-sigrok-mcp/tests/
```

`tests/` covers `sigrok_cli.py` and `runner.py` only — no `mcp` package or
live hardware required; `server.py`'s FastMCP wiring is exercised by
starting the process, which needs `mcp` installed per `requirements.txt`.
