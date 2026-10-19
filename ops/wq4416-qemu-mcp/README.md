# qemu-mcp (wq#4416)

FastMCP stdio server giving an agent vision + control on a QEMU VM: boot an
ISO, capture the framebuffer, read/wait-on the serial console, and send
keystrokes — built for Limpet ISO install/boot testing per
`projects/limpet/docs/iso-test-pipeline.md` (the wq#4417 design this ticket
implements; see its section 11, which lists "`qemu-mcp` server itself" as
the one open, not-yet-built component). Combined with the shared VNC display
`qemu_boot` opens (`-vnc :N,share=allowed`), Frank can watch the same VM in
an ordinary VNC viewer while the agent drives it through this server — the
VM does not know or care how many observers are attached.

The serial console is the key win the ticket calls out: `qemu_serial` /
`qemu_wait_serial` read the guest's first serial port, which keeps printing
BIOS/GRUB/kernel boot messages long before the framebuffer shows anything
(and shows a kernel panic or GRUB hang the framebuffer might render as an
indistinguishable black screen).

## Prior art (searched before building — doctrine)

Three existing "QEMU + MCP" projects turned up on search, none a match for
this ticket's exact tool surface:

- **nagy/qemu-mcp-server** (Rust, AGPL3+) — closest in shape: connects to an
  already-running QEMU's QMP unix socket and a serial unix socket, exposes a
  generic `execute_qmp` passthrough plus `read_serial`/`write_serial`. No
  dedicated boot/screenshot/type tools, and it assumes QEMU was already
  launched out-of-band — it is a thinner primitive than what the ticket asks
  for, not a wrapper candidate.
- **Neanderthal/mcp-qemu-vm** (Python, 21 stars) — mouse/keyboard/screenshot
  over SSH + xdotool/scrot running *inside* the guest. Wrong fit: it needs
  SSH and a desktop environment already up in the guest, which is exactly
  what is not available during early boot/installer troubleshooting (the
  scenario this ticket exists for).
- **Kevin4562/QEMU-MCP** (`mcp-qemu-lab`, Python) — VM lifecycle + cloud-init
  + SSH + in-guest gdb/core-dump tooling for binary-analysis workflows. No
  framebuffer screenshot or keystroke-injection tools.

The actual primitives this ticket needs (`screendump` for screenshots,
`send-key` for keystrokes, a QMP unix socket for boot/launch control, a
serial sink for the boot-console sensor) are the same ones SUSE's openQA
(os-autoinst) and HashiCorp Packer's QEMU builder (`boot_command`) both
drive directly against QEMU — see
`projects/limpet/docs/iso-test-pipeline.md` section 2 for that comparison in
full. So this is a small, direct QMP client rather than a wrapped
dependency.

## Layout

- `qemu_client.py` — stdlib-only driver (subprocess + socket + JSON). No
  `mcp` import, so it is independently unit-testable without the MCP SDK.
- `server.py` — thin FastMCP wrapper: one `@mcp.tool()` per client method.
- `tests/fake_qemu.py` — a fake `qemu-system-x86_64` binary that opens a real
  AF_UNIX socket at the requested QMP path and speaks the real QMP wire
  protocol (newline-delimited JSON, greeting + `qmp_capabilities`
  handshake), used by the test suite so `boot()`/`quit()` and every tool are
  exercised against real process + socket machinery rather than a mock.
- `tests/test_qemu_client.py` — 14 tests covering the boot/quit lifecycle,
  every tool's happy path, and invalid-input rejection (missing ISO, bad
  memory/smp, not-yet-booted, double boot, unmapped keystroke character,
  negative wait timeout) per wq#2630 (a probe must be able to fail, not just
  confirm the designed-for path) — including `wait_serial`'s timeout branch,
  probed with a pattern that is never written so that branch can actually
  fail.

## Tools

| Tool | Maps to |
|---|---|
| `qemu_boot(iso_path, run_dir, memory_mb, smp, machine, vnc_display)` | launches `qemu-system-x86_64 -cdrom … -vnc :N,share=allowed -serial file:… -qmp unix:…,server=on,wait=off -display none -no-reboot`, opens the QMP socket |
| `qemu_screenshot(out_path, image_format)` | QMP `screendump` — guest framebuffer to a PNG file |
| `qemu_serial(max_chars?)` | tails `run_dir/serial.log` since the last read — never re-reads |
| `qemu_wait_serial(pattern, timeout_s, regex?)` | polls the serial tail until `pattern` appears or the timeout elapses |
| `qemu_type(text, key_delay_s)` | QMP `send-key`, one chord per character (shift+key for uppercase/punctuation) |
| `qemu_quit()` | QMP `quit`, then terminates the QEMU process |

## Running the tests

```
python3 -I -m pytest tests/test_qemu_client.py -q
```

No real QEMU binary needed — `fake_qemu.py` stands in for it. `server.py`
itself is not covered by this suite: the `mcp` package (`requirements.txt`:
`mcp>=1.9,<2`) is not installed in the build sandbox (`pip install` isn't on
the worker Bash allowlist — kb/ISSUE-LOG.md row 1309). The thin wrapper in
server.py has no logic of its own to diverge from `qemu_client.py`'s tested
behavior — each tool is a one-line pass-through.

Actually launching a real `qemu-system-x86_64` boot against a Limpet ISO
(the production workflow this server is for) needs a KVM-capable host and
the run-directory/binary grants wq#4417's pipeline work already tracks
(kb/ISSUE-LOG.md row 1393) — out of scope for this ticket, which builds and
verifies the MCP server itself.
