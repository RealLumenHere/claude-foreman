# limpet-plugin-hackrf-mcp

HackRF One plugin for Limpet, wrapping
[thehappydinoa/hackrf-mcp](https://github.com/thehappydinoa/hackrf-mcp) (MIT,
pinned commit `d9fa2ee6bf58acd28e50a1cad98a4458e0e2ef67` — no tags, no
releases, not on PyPI, so the git SHA is the only pin available). 10
registered tools over device info, RX spectrum sweep, a single RX/TX IQ
transfer tool, clock/Opera-Cake/bias-tee configuration, and four offline IQ
file-analysis tools.

## RX-only v1 — TX is refused, not just ungated

`hackrf_transfer` takes `direction="rx"|"tx"`. Upstream annotates it `CONFIG`
(`destructiveHint=False`) and relies only on its docstring to warn about
transmit (`server.py:160,188-195`). Per
`reports/wq4145_limpet_verify_hackrf_mcp_eval_buspirate_mcp.md` section 1
finding 1, an energize-confirm gate alone is not enough for RF transmission —
it is a distinct risk class (regulated, affects third parties, unbounded with
`repeat_tx`) and its policy is wq#4147's decision, not this plugin's.

So this plugin's `[mcp]` command does not point at `hackrf-mcp` directly. It
points at `mcp/hackrf_tx_guard.py`, a stdio proxy that sits between the MCP
client and the real upstream process:

- every `tools/call` for `hackrf_transfer` with `arguments.direction == "tx"`
  is answered locally with an `isError` result and **never reaches
  upstream**;
- every other request (including `direction="rx"`, every other tool, and
  non-JSON-RPC lines) is forwarded unmodified in both directions over a
  background reader thread, so a notification that gets no reply (e.g.
  `notifications/initialized`) can't stall the proxy.

See `mcp/hackrf_tx_guard.py:is_blocked_tx_call` / `run_proxy` and
`tests/test_hackrf_tx_guard.py` for the logic and an integration test that
drives the real proxy loop against a fake upstream subprocess. Re-enabling TX
means changing the guard, not flipping a `tools_energize` flag.

Three firmware-touching tools (`hackrf_debug`, `hackrf_cpldjtag`,
`hackrf_spiflash`) are commented out upstream and never registered at all
(`server.py:710-845`) — there is nothing for this plugin to refuse there.

## Energize gates

Per the report's section 1 table, gated in `[mcp.tools_energize]`:
`hackrf_transfer` (also TX-refused as above), `hackrf_clock` (drives a signal
out of an SMA on `clkout_enable`), `hackrf_operacake` (antenna-switch config),
`hackrf_biast` (applies DC 3.3-5V to the antenna port). Everything else —
`hackrf_info`, `hackrf_sweep`, and the four offline `hackrf_iq_*` tools — is a
read or an offline file operation and stays ungated.

## Device sharing with gnuradio-mcp

HackRF One (`1d50:6089` operating / `1d50:cc15` DFU,
`limpet/hardware.d/09-sdr.toml`, confidence=high) is the same physical device
`limpet-plugin-gnuradio-mcp`'s `GNURADIO_HARDWARE=hackrf` backend drives.
Upstream hackrf-mcp itself says only one `hackrf_*` command at a time
(`server.py:25-26`) — libhackrf/libusb does not arbitrate concurrent opens.
`limpet-plugin.toml` declares `[dependencies].conflicts = ["gnuradio-mcp"]` so
the two are never active together.

## Install

`install.sh` installs the `hackrf` system package (for `hackrf_info` and the
other CLI binaries the MCP server wraps via subprocess with no shell —
`cli.py:31,42-51`), clones upstream pinned to
`d9fa2ee6bf58acd28e50a1cad98a4458e0e2ef67` into a dedicated venv under
`/opt/limpet/plugins/hackrf-mcp/`, and copies this plugin's own
`mcp/hackrf_tx_guard.py` into the install dir so `[mcp].command` can launch
it in front of the real server.
