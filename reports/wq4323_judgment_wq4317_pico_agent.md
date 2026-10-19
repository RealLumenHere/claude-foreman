# wq#4323 escalated judgment: wq#4317 / PR #2221 (limpet-plugin-serial-mcp)

Judge: opus-5@high, judgment only (no patch). Date: 2026-10-19.
Subject commit: 316d0ce4 (`feat(limpet): add serial-mcp plugin for UART/serial automation`).

## Claim under judgment

Stated so it can be proven false:

> Calling the ungated MCP tool `serial_open` (pyserial-mcp 0.7.4) on Linux asserts DTR and RTS on the
> physical adapter, with no code path in upstream or in this plugin that suppresses it. That puts it inside
> the plugin's own gating criterion ("drives DTR/RTS/break (can reset or force a target into bootloader)").
> So the manifest's classification of `serial_open` as "pure reads / port config / local-only session and log
> bookkeeping", and the README's line "none of them write a byte to the wire" offered as the reason it is
> safe, are wrong under the plugin's own threat model.

The claim is false if any of these holds: upstream overrides dtr/rts before or after open; pyserial does not
issue line-state ioctls on open; something in the Limpet install (udev, scripts) neutralizes it; or asserting
DTR/RTS on open cannot reset the named class of targets.

## Evidence

### Plugin side (`plugins/limpet-plugin-serial-mcp/` at 316d0ce4)
- `limpet-plugin.toml` L58-70: the gating rule. "Gated = writes bytes to the device, drives DTR/RTS/break
  (can reset or force a target into bootloader), or forcibly reclaims a port... Ungated = pure reads, port
  config, and local-only session/log bookkeeping."
- `limpet-plugin.toml` L78: `serial_set_signals = true  # DTR/RTS — e.g. forces an ESP32 into bootloader/reset`.
- `limpet-plugin.toml` L87-90: `# Pure reads / port config / local-only session and log bookkeeping — ungated`,
  then `serial_open = false`, `serial_close = false`.
- `limpet-plugin.toml` `[agent_hints].when_to_use` (L104) names "Freedom Bot ESP32 serial" and the Spectrio RE
  target as intended targets.
- `README.md` L46-48: "upstream marks `serial_open`, `serial_close`, and `serial_clear_history`
  `readOnlyHint=False` because they mutate server-side session state — none of them write a byte to the wire."
  L57: `serial_open` and `serial_close` listed as Ungated.
- `assets/90-limpet-serial-mcp.rules`: sets only `MODE`, `GROUP`, and `TAG` for five VID:PIDs. Nothing about
  termios, HUPCL, or line state. `install.sh`, `health.sh`, `uninstall.sh`, and `tests/test_health.sh` only
  install, import-check, or remove. **Nothing in the plugin neutralizes DTR/RTS.**

### Upstream source (pinned)
- `python3 -m pip install --no-compile --target /tmp/fworker-wq4323/opus-dl/pkg "pyserial-mcp==0.7.4"` produced
  `pyserial_mcp-0.7.4.dist-info` and `pyserial-3.5.dist-info`.
- `curl` of `raw.githubusercontent.com/alxgmpr/serial-mcp/main/serial_mcp/session.py` is byte-identical to the
  pinned copy (`diff` empty, sha256 prefix `97be7f84ce4f52e372ac` for both).
- `serial_mcp/session.py` L34-41: the only `serial.Serial(...)` construction. It passes port, baudrate,
  bytesize, stopbits, parity, and timeout. There are **no** `dtr`/`rts`/`dsrdtr`/`rtscts` kwargs, and it does not
  use the closed-construct-then-set-`.dtr`-then-`open()` pattern.
- `session.py` L348-354: `set_signals` is the only code that writes `.dtr`/`.rts`. `grep` across `server.py`
  turns up DTR/RTS only in `serial_set_signals` (L975+).
- `server.py` L419-496 (`serial_open`): goes straight into `SerialSession(...)`. If `port` is omitted and exactly
  one adapter is present, it auto-selects that port (L456-461).
- `server.py` L989-991: upstream's own `serial_set_signals` docstring documents
  `serial_set_signals(dtr=False); serial_set_signals(dtr=True)` as "Reset Arduino". In other words, a DTR
  low-to-high edge resets the board, which is the same edge `serial_open` produces.
- `session.py` L504-512 (`close`) calls `self._serial.close()`. `server.py` L56-75 `_session_reaper` auto-closes
  sessions after inactivity (default 900 s), and L45-53 `atexit` closes them all on shutdown. Neither path
  requires a tool call.

### pyserial 3.5 runtime behavior
Script `/tmp/fworker-wq4323/opus-scripts/inspect_pyserial.py`, run as
`python3 -I inspect_pyserial.py /tmp/fworker-wq4323/opus-dl/pkg` against the pyserial copy installed with the
pinned package:
- `SerialBase.__init__`: defaults `rtscts=False, dsrdtr=False`, and the source hardcodes
  `self._rts_state = True` and `self._dtr_state = True`.
- `serialposix.py` `Serial.open` L322: `os.open(port, O_RDWR|O_NOCTTY|O_NONBLOCK)`. L335-338:
  `if not self._dsrdtr: self._update_dtr_state()` / `if not self._rtscts: self._update_rts_state()`.
- `_update_dtr_state` L710-715 and `_update_rts_state` L703-708: `fcntl.ioctl(fd, TIOCMBIS, TIOCM_DTR/RTS)`.
  These are real modem-control ioctls, which usb-serial drivers (ftdi_sio, cp210x, ch341, pl2303) turn into
  vendor control transfers that drive the adapter's DTR#/RTS# pins.
- `_reconfigure_port` L403 only ORs in `CLOCAL | CREAD`. It never clears `HUPCL`.
- `Serial.close` (L529) has no DTR/RTS code of its own. The line drop on close comes from the kernel (below).

### Linux kernel (`drivers/tty/tty_port.c`, torvalds/master via curl)
- L542-545: `if (filp == NULL || (filp->f_flags & O_NONBLOCK)) { if (C_BAUD(tty)) tty_port_raise_dtr_rts(port); ...`.
  The kernel raises DTR and RTS **on any open** of a tty port with nonzero baud, before pyserial's own ioctls
  run. pyserial opens with `O_NONBLOCK`, so this path fires. The usb-serial `init_termios` has a nonzero baud.
- L358-359: `if (tty && C_HUPCL(tty)) tty_port_lower_dtr_rts(port);` lowers both lines on last close. HUPCL is
  set by default and pyserial never clears it.

### Target-circuit facts (Espressif esptool docs, boot-mode-selection, fetched via curl)
> "Some OS and/or drivers may activate RTS and or DTR automatically when opening the serial port ... pulling
> them low together and holding the ESP in reset. If RTS is wired directly to EN then RTS/CTS 'hardware flow
> control' needs to be disabled ... An additional circuitry is implemented in order to avoid this problem - if
> both RTS and DTR are asserted together, this doesn't reset the chip."

No hardware was exercised. I deliberately did not open any `/dev/tty*` on this host, because doing so could reset
whatever is attached.

## Verdict

**CONTRADICT.** The mechanism the verifier described is real, and nothing upstream or in the plugin suppresses
it. One part of the verifier's framing is overstated, though: `serial_open` is not the same capability as
`serial_set_signals`.

## Reasoning

1. **The mechanism holds, and is stronger than the verifier said.** DTR and RTS are asserted on open at two
   layers: the kernel (`tty_port_block_til_ready`) and then pyserial (`TIOCMBIS` x2). Only `set_signals` touches
   the lines afterward, so there is no override to find. Even if upstream added `dtr=False`, the kernel's raise
   on open would still produce a short assertion glitch. Close, the inactivity reaper, and the atexit handler
   all drop both lines via HUPCL. So `serial_close`, which is also ungated, and a close with no tool call at all
   both change line state too.

2. **It falls inside the plugin's own stated gate.** The manifest gates anything that "drives DTR/RTS ... (can
   reset or force a target into bootloader)". `serial_open` drives DTR/RTS. The README's defense, "none of them
   write a byte to the wire", is literally true but beside the point, since the rule it is meant to satisfy also
   covers signal lines. As written, the classification contradicts the plugin's own criterion. That is a
   correctness defect in the safety claim, not a cosmetic nit.

3. **What actually happens depends on how the target is wired.**
   - *Capacitor-coupled DTR to RESET* (Arduino/AVR-style and many clones): **resets on every `serial_open`.**
     This is certain and widely known, and upstream's own docstring L989 relies on it.
   - *Direct RTS-to-EN (and DTR-to-IO0) jumpering on a bare ESP32 module or RE breakout,* a common bench hookup:
     the target is **held in reset for as long as the port is open.** Espressif warns about exactly this case.
   - *ESP32 dev boards with the standard two-transistor auto-program circuit* (DevKitC/NodeMCU style, a likely
     build for a "Freedom Bot ESP32"): when both lines are asserted together, the circuit deliberately does
     nothing. On Linux the kernel raises both in one `dtr_rts` driver call, so **no reset or bootloader entry is
     expected.** `serial_open` cannot produce the asymmetric DTR/RTS sequence needed for ESP32 bootloader entry.
     Only `serial_set_signals` can. The verifier's "functionally the same category" framing overstates the case
     here: open can reset or hold some targets, but it cannot force ESP32 download mode.
   - *TX/RX/GND-only UART console taps* (the typical RE hookup on a target like the Spectrio host): DTR/RTS are
     not connected, so there is no effect.

   The worst realistic outcome is an unintended reset, or a target held in reset, from a tool call the agent and
   operator were told is side-effect-free. That can interrupt a running device, destroy volatile RE state such as
   a halted boot or a live shell, or confuse a capture. It is not a brick, and it is not bootloader entry on the
   named ESP32-class target.

4. **The "picocom does it too" angle.** It is accurate: the kernel raise on open is inherent to opening any tty
   on Linux, so picocom, minicom, screen, or `cat` would do the same. That makes it less novel, but it does not
   rescue the classification. The energize gate exists to control physical-effect actions the agent takes
   autonomously. A human running picocom has chosen to accept the reset; an agent calling an ungated
   `serial_open` (which auto-selects the only attached port when `port` is omitted) has not, and nobody approved
   it. The "inherent to any port open" point argues that the gate cannot remove the effect. It does not argue
   that the gate should describe the effect as absent. Gating `serial_open` is still meaningful: it is a
   once-per-session approval at the moment of first physical contact, and every other wire-touching tool
   already needs an open session. The cost is that a read-only monitoring session would also need one approval.

## Recommendation

- **Reopen wq#4317 for a small manifest/README correction. Do not revert or pull the plugin.** The package
  choice, pin, license, and the rest of the classification are sound. This is a single misclassification plus
  an overclaimed rationale.
- **Required:** correct the safety claim. Remove `serial_open` and `serial_close` from the "pure reads ...
  local-only bookkeeping" group and from the README's "none of them write a byte to the wire" rationale. State
  plainly that opening asserts DTR+RTS (kernel `tty_port.c` and pyserial `serialposix.py` L335-338), and that
  closing, inactivity auto-close (900 s default), and server exit drop them via HUPCL. Explain the consequence:
  this resets capacitor-coupled-DTR boards and holds RTS-to-EN-wired targets in reset, while standard ESP32
  auto-program boards are unaffected.
- **Recommended:** gate `serial_open` (`true`), consistent with the plugin's own rule. `serial_close` can stay
  ungated, with the caveat documented, because blocking close leaves sessions open, and the reaper and exit
  paths close sessions without any tool call anyway. If the owners prefer convenience, the acceptable fallback
  is "ungated, documented caveat". That is only acceptable once the claim of no side effects is gone, and the
  `agent_hints` should say to confirm target wiring (TX/RX/GND only, or no DTR-reset coupling) before opening.
- **Optional, out of scope here:** a wrapper could open with HUPCL cleared and dtr/rts preset False to keep
  the lines mostly still across sessions. The kernel raise on first open still makes a short glitch
  unavoidable, so this would reduce the effect, not fix it. It should not be presented as a fix.
- The hint "Freedom Bot ESP32" should note that bootloader entry needs `serial_set_signals`, which is gated;
  `serial_open` alone cannot trigger it on a standard auto-program board.

## Note on report location

The requested path `/home/lumen/foreman/reports/` was refused by the harness guard (primary repo roots are
read-only for workers, wq#1719/wq#900). This file lives in the wq4323 worktree's `reports/` directory, matching
the convention of commit eb6000a9, and is uncommitted.
