# limpet-plugin-gnuradio-mcp

SDR receive/spectrum-analysis plugin for Limpet (RX-only v1), wrapping
[Pranav-d33/gnuradio-mcp-server](https://github.com/Pranav-d33/gnuradio-mcp-server)
(MIT, PyPI v0.1.1, 13 tools).

## What it does

Bridges an MCP client to a **live GNU Radio flowgraph** over XML-RPC (control)
and ZMQ SUB (IQ data). The server is a pure-Python client process — all SDR
hardware support (HackRF / RTL-SDR / USRP) comes from GNU Radio itself, which
must already be running one of the bundled flowgraphs:

| `GNURADIO_HARDWARE` | Flowgraph | Hardware |
|---|---|---|
| `hackrf` (default) | `hackrf_mcp.grc` | HackRF One (1d50:6089) |
| `rtlsdr` | `rtlsdr_mcp.grc` | RTL2832U dongles (0bda:2838/2832) |
| `usrp` | *(none bundled upstream — user-supplied .grc)* | Ettus USRP B2xx (2500:0020–0023) |
| `simulation` | `simulation_mcp.grc` | none (two test tones) |

The flowgraph must expose an XML-RPC server block (default port 8080) and a
`complex64` ZMQ PUB sink (default port 5555).

## RX-only scope (v1) and the TX risk flag

Upstream exposes **no transmit tool**: all 13 tools (verified against
`gnuradio_mcp/server.py`, not just the README) are receive, tune, or analyse
operations, and the bundled flowgraphs are receive chains. But the RX-only
guarantee lives in the **flowgraph**, not the server — `gnuradio_set_variable`
writes any variable of whatever flowgraph is running, so pointing this plugin
at a TX-capable flowgraph would make it TX-capable.

SDR transmit is a distinct risk class from what `needs_energize` was designed
for (RF emission is regulated, affects third parties, and isn't bounded by the
device under test). **TX is therefore explicitly out of scope for v1 and is a
separate design decision — see wq#4147.** Until that lands: ship/use only the
upstream RX flowgraphs.

## Tools and energize gate

Upstream ships a Read-Only column but no gate annotations; this mapping is this
packaging's own judgment call, checked against source:

- **Gated:** `gnuradio_flowgraph_start`, `gnuradio_flowgraph_stop`,
  `gnuradio_set_center_frequency`, `gnuradio_set_sample_rate`,
  `gnuradio_set_gain`, `gnuradio_set_variable` (the TX-risk vector above),
  and `gnuradio_detect_signals` — upstream labels the sweep read-only, but it
  calls `set_freq` repeatedly against live hardware and restores the original
  frequency only when the sweep ends (`server.py:570,594`), so it is gated here.
- **Ungated:** `gnuradio_flowgraph_status`, `gnuradio_get_variable`,
  `gnuradio_list_variables`, `gnuradio_capture_samples`,
  `gnuradio_get_spectrum_snapshot`, `gnuradio_ping`.

## Installation

```bash
./install.sh
```

Installs `gnuradio-mcp-server==0.1.1` from PyPI (Python ≥3.10) and udev rules
for the three SDR families. GNU Radio itself (`gnuradio`, `gr-osmosdr` for
HackRF/RTL-SDR; `uhd-host` for USRP) is a **system dependency, not bundled**:
it is GPL-3.0, runs as a separate process, and is only talked to over
XML-RPC/ZMQ — no linking, so no copyleft obligation lands on this MIT plugin.
RTL-SDR additionally needs the `dvb_usb_rtl28xxu` kernel driver blacklisted
(see `limpet/hardware.d/09-sdr.toml`).

## Known limitations (upstream)

- Every tool needs the flowgraph running first; `gnuradio_ping` diagnoses
  XML-RPC/ZMQ connectivity.
- RTL-SDR usually needs a flowgraph restart after a sample-rate change.
- ZMQ sink must output `complex64`; anything else raises `GRHardwareError`.
- No USRP flowgraph is bundled upstream (`flowgraphs/` has hackrf, rtlsdr,
  simulation only) — USRP use requires authoring a .grc with the same
  XML-RPC/ZMQ blocks.

## Catalog watch

Alternate candidate: **AetherLink-SDR-MCP** (license unconfirmed, not tested
per Glama at evaluation time — docs/mcp-server-evaluations.md Section 8).
Revisit if this upstream stalls.

## License

MIT (upstream: Pranav-d33/gnuradio-mcp-server). Verified by fetching
`https://raw.githubusercontent.com/Pranav-d33/gnuradio-mcp-server/main/LICENSE`
directly (2026-10-17): standard MIT text, copyright "2026 Pranav Dhiran",
matching the PyPI `license: MIT` field and the repo badge.
