# wq#4150 — verify-pass of wq#4139 LIMPET: embedded-debugger-mcp plugin (dual backend)

**Verdict: CONFIRMED.** Every externally-checkable claim in the PR — upstream repo identity,
license, pinned tag, the 24-tool MCP surface, and the per-tool energize gating scope — checks
out against the real upstream source, not just the PR's own narration.

PR #2088, merged as `489091c0b8` (`feat(limpet): add embedded-debugger-mcp plugin for
dual-backend debug probes`).

## What wq#4139 claimed

- Wraps `Adancurusul/embedded-debugger-mcp` (MIT, Rust, tag `v0.3.0`), a dual-backend
  (probe-rs native / OpenOCD via GDB RSP) debug-probe MCP server with 24 tools incl.
  `diagnose_fault`/`unwind_exception` crash diagnosis.
- Per-tool energize gate: `halt`, `run`, `reset`, `step`, `write_memory`, `flash_erase`,
  `flash_program`, `run_firmware`, `rtt_write` gated; everything else (incl.
  `diagnose_fault`/`unwind_exception`, explicitly called out as read-only evidence) ungated.
- `install.sh` builds the pinned tag from source via `cargo install --locked --git ... --tag
  v0.3.0 embedded-debugger-mcp`; `health.sh` checks `--help` output.
- Bundled MIT `LICENSE`, udev rules + hardware manifest for 6 probe VID:PIDs ("same set as
  `openocd-mcp`"), and a README section evaluating (not wrapping) the companion
  `serial-mcp-server` for the UART HAL class per the ticket's "also eval" ask.

## What I checked, independently of the diff's own narration

1. **Upstream repo and tag are real, not fabricated** (instinct 8 applies here — a value this
   plugin invented and then "confirmed" against itself would be worthless): `curl
   https://github.com/Adancurusul/embedded-debugger-mcp` → 200;
   `.../releases/tag/v0.3.0` → 200; `.../v0.3.0/LICENSE` → 200 and its body is the standard
   MIT text, copyright "Adancurusul", matching the plugin's bundled `LICENSE` exactly (which
   correctly preserves the original notice and adds a separate 2026 Lumen Industries packaging
   copyright line, not a modification of the original terms).
2. **Tool list is an exact match, not an approximation.** Pulled the upstream README
   (`v0.3.0/README.md`) and counted its "MCP Tool Set" tables: probe mgmt (3) + target control
   (6) + memory/breakpoints (4) + diagnostics (2) + flash (4) + RTT (5) = 24 tools, same 24
   names used in `limpet-plugin.toml`'s `[mcp.tools_energize]` table and the README's tool-group
   prose. No invented or missing tool names.
3. **Energize-gating scope is sound against upstream's own safety model, not just the ticket's
   paraphrase.** Upstream's own "Safety Notes" section: flash erase gated behind
   `allow_flash_erase`, memory writes behind `allow_memory_write` — the plugin gates exactly
   `flash_erase`/`write_memory` (plus the other mutating ops: `halt`, `run`, `reset`, `step`,
   `flash_program`, `run_firmware`, `rtt_write`). `diagnose_fault`/`unwind_exception` are
   explicitly described upstream as "reports evidence; it does not assert a root cause" and
   read-only register/DWARF reads — matches the plugin's explicit exemption rationale verbatim.
   `flash_verify` ("compare... contents", read-only) is correctly left ungated.
4. **Binary/package name matches the build command.** Upstream `Cargo.toml`:
   `[package] name = "embedded-debugger-mcp"`, `[[bin]] name = "embedded-debugger-mcp"`, clap
   4.5 for CLI — confirms `install.sh`'s `cargo install ... embedded-debugger-mcp` and
   `health.sh`'s `embedded-debugger-mcp --help | grep -q 'embedded-debugger-mcp'` target a real,
   correctly-named binary; clap's default usage line prints the binary/package name, so the
   health grep is not a flaky heuristic.
5. **Probe VID:PID set is accurate and matches its sibling plugin's claim.** All 6 entries
   (ST-Link/V2 `0483:3748`, ST-Link/V2-1 `0483:374b`, J-Link `1366:0101`, Black Magic Probe
   `1d50:6018`, FTDI FT2232H `0403:6010`, CMSIS-DAP `0d28:0204`) are correct real-world
   VID:PIDs, and `assets/90-limpet-debug-probe.rules` is byte-for-byte identical (same 6
   entries, same `TAG+="limpet-probe"`) to `limpet-plugin-openocd-mcp/assets/90-limpet-jtag-swd.rules`
   — the README's "same probe-rs/OpenOCD compatible device set as openocd-mcp" claim is
   literally true, not approximate. Two plugins both installing the same udev rules is
   redundant but harmless (idempotent rule content, same tag).
6. **Companion `serial-mcp-server` claims are accurate, not invented.** Pulled its README
   directly: MIT, Rust, and a "JSON Macro DSL" for timed send/delay/expect serial sequences —
   matches the PR's "JSON macro automation and agent skills for repeatable timed workflows"
   description. The ticket's "also eval its own companion Serial MCP Server" is satisfied by
   this documented evaluation-and-explicit-deferral (own ticket, not folded in), not by
   silently dropping it.
7. **Checked for sandbox-verifiable executable evidence.** `cargo` is not on this worker's Bash
   allowlist (same class of gap as wq#3892's `pip` finding, kb/ISSUE-LOG.md row 1309 precedent)
   — cannot build-and-run the binary or exercise `health.sh`/`test_health.sh` end-to-end in this
   sandbox. This is a real, pre-existing sandbox limitation, not an excuse: there is no custom
   protocol/translation code in this PR to unit-test in its absence (unlike wq#3881's
   `openocd_client.py`), since this plugin is a pure install/manifest/health wrapper around an
   upstream binary with zero in-repo logic. The checks above (tool-list cross-reference,
   energize-scope cross-reference, binary-name cross-reference) are the substitute evidence a
   build-and-run would otherwise have produced, and they're pinned to the immutable `v0.3.0` tag
   rather than upstream's mutable default branch.

## Observations noted, not contradictions

- `limpet-plugin.toml` uses `manifest_version`, `category = "hardware-interface"`, and
  `[health].check` — none of which match `limpet/manifest.py`'s `PluginManifest` schema
  (requires `[plugin].kind` in `("tool","hardware","data")`, `category` in a closed enum that
  does not include `"hardware-interface"`, and `[health].command` not `[health].check`).
  Grepped the whole repo: nothing references `manifest_version`, and nothing in
  `limpet/*.py` reads `[paths]`/`[mcp]` from this TOML shape at all — this convention isn't
  wired into the real plugin registry yet. Confirmed this is not a wq#4139 regression:
  `limpet-plugin-flashrom/limpet-plugin.toml` (merged earlier in the same eval batch) uses the
  identical non-conforming shape (`category = "flash-tools"`, `[health].check`, no `kind`).
  Batch-wide convention question for whoever wires `plugins/limpet-plugin-*` into
  `limpet/manifest.py`'s registry — not a defect in this PR specifically.
- `[paths].binary = "/usr/local/bin/embedded-debugger-mcp"` is not guaranteed by `cargo
  install`'s default (`$CARGO_HOME/bin`, typically `~/.cargo/bin`) unless the install
  environment sets `install.root`. Low severity: nothing in the codebase currently reads
  `[paths].binary` (same grep as above), and the actual MCP launch
  (`[mcp].command = "embedded-debugger-mcp"`) is a PATH lookup, which works as long as cargo's
  bin dir is on PATH — the normal rustup/cargo setup.

## What would have broken this verdict (and didn't)

- If the upstream repo, tag, or license didn't exist as described, instinct 8 would apply —
  checked live against GitHub, all three are real.
- If the tool list were padded or trimmed to hit "24" without matching real tool names, the
  independent README table count (3+6+4+2+4+5=24) would have caught it — it didn't; every name
  matches.
- If a mutating tool (e.g. `flash_program` or `write_memory`) were left ungated, or a read-only
  one (e.g. `flash_verify`) were gated without reason, that would contradict upstream's own
  safety-notes framing — checked each of the 24 against the upstream table, no mismatch.
- If the companion-server evaluation were fabricated detail to look thorough, the independent
  README pull would have caught invented capabilities — it didn't; the macro-DSL description is
  accurate.

No escalation to judgment needed — board this as verified.
