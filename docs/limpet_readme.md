# Limpet (README.md, tracked copy)

> Status: tracked copy — 2026-10-15
> Tickets: wq#4042 (fixed the fabricated "250k/24-target" citation, per wq#4039
> judgment); wq#4016 (closed 2026-10-15 against this copy — the "Why Limpet?"
> section below, leading with appliance + HAL auto-detection + energize-confirm
> gate + offline-first local LLM + agent-driven workflow instead of tool count,
> is the ticket's implementing change)

This is the git-tracked copy of `/home/lumen/projects/limpet/README.md`
(outside any git repo, so edits there have no commit/diff/review trail —
same anti-pattern as ISSUE-LOG row 1328 / wq#3939/#3919, PR #1904). This copy
also carries the correction wq#4039 flagged: the live file previously claimed
"REA alone ships a 250k-tool catalog across a 24-target matrix," cited to
`design/wq3929_competitive_analysis.md:193`. That line describes a
Ghidra-MCP fork ("250 Ghidra tools flat"), not REA, and no "24-target
matrix" or "250k" figure exists anywhere in `wq3929_competitive_analysis.md`.
`wq3929_competitive_analysis.md:57` gives REA's real tool count, ~170. Both
the live file and this copy now cite that figure (also consistent with this
same file's own "Supported Tools" table entry, "REA | MCP RE framework (170
tools) |").

---

# Limpet

**The Universal Reverse Engineering Agent Appliance**

Limpet is a dedicated appliance for reverse engineering — not an app, a secure RE workstation you plug a target into. The Hardware Abstraction Layer auto-detects what's connected, the agent suggests the next move, and every destructive hardware action waits on an explicit operator confirm.

Appliance, not app. HAL auto-detection. Energize-confirm trust gate. Offline-first, local LLM. Agent-driven.

```
                    ┌─────────────────────────────┐
                    │        AGENT CORE            │
                    │   (Ollama / LiteLLM /        │
                    │    Claude / any LLM)         │
                    └──────────────┬───────────────┘
                                   │ MCP
                    ┌──────────────▼───────────────┐
                    │      MCP META-SERVER          │
                    │   Single endpoint for all     │
                    │   tools — native MCP proxy    │
                    │   + auto-generated wrappers   │
                    └──────────────┬───────────────┘
                                   │
          ┌────────────────────────┼────────────────────────┐
          │                        │                        │
   ┌──────▼──────┐         ┌──────▼──────┐          ┌──────▼──────┐
   │    TOOL     │         │  HARDWARE   │          │  WORKSPACE  │
   │  REGISTRY   │         │ ABSTRACTION │          │  MANAGER    │
   │             │         │    LAYER    │          │             │
   │ 100+ tools  │         │             │          │ Per-target  │
   │ in TOML     │         │ USB detect  │          │ isolation   │
   │ manifests   │         │ Coral TPU   │          │ Git-tracked │
   │             │         │ JTAG/SWD    │          │ Evidence    │
   │ Plugin      │         │ Capture     │          │ chain       │
   │ system      │         │ Analyzers   │          │             │
   └─────────────┘         └─────────────┘          └─────────────┘
```

## Why Limpet?

**Tool count doesn't differentiate anymore.** REA alone already ships ~170 tools, and AttifyOS already owns the hardware-RE-distro lane. What no one else combines is agent-driven operation + an auto-detecting HAL + an appliance form factor:

- **HAL auto-detection.** Limpet knows about every probe, analyzer, and capture device on its USB ports the moment you plug it in — no manual interface wiring, the step every other hardware-RE distro (AttifyOS included) still makes you do by hand. Plug in a JTAG probe and the agent knows it can debug the target. Plug in a MediaTek phone and it detects BROM mode automatically.
- **A full appliance, not an app.** Dedicated OS, dedicated box, plug-and-go. You don't install Limpet on your daily driver; you plug a target into Limpet.
- **The energize-confirm trust gate.** Read-only work — enumeration, identification, passive capture — runs fully autonomous. Anything that energizes hardware (probe power, JTAG/SWD, flash writes, GPIO/reset lines, exploit vectors) is gated behind an explicit operator confirm. Autonomous analysis, human-gated destructive action.
- **Offline-first.** Runs air-gapped on a local LLM (Ollama/LiteLLM) — no cloud dependency to analyze a device.
- **Agent-driven.** The agent suggests the next workflow step from what's connected and what's been found, instead of making you pick from a tool menu.
- **MCP-native and plugin-extensible.** Every tool — ours or the community's — is a plugin behind a single MCP endpoint, so tool breadth grows without being the point. Any MCP-capable client (Claude, GPT, Ollama, LM Studio) can drive it.
- **Workspace isolation.** Each RE target gets its own git-tracked workspace. Findings link back to the exact tool and command that produced them. Full audit trail.
- **Cheap and reproducible.** Reference hardware is a Lenovo M70q Gen 5 (i5-14500, 32GB DDR5) — under $300 used. Any x86-64 PC with 16GB+ RAM works.

### Who Is This For?

- **E-waste reclamation.** Understand what a device does before recycling or repurposing it.
- **Security researchers.** Automated firmware analysis, vulnerability discovery, and evidence collection.
- **Hardware hackers.** Extract firmware, modify bootloaders, bypass locks, flash custom images.
- **Repair shops.** Diagnose dead devices, read flash, recover data.
- **Education.** Learn reverse engineering with an AI that guides you through the process.

## Quick Start

### Prerequisites

- x86-64 PC with 16GB+ RAM (32GB recommended)
- Ubuntu 24.04 LTS or Debian 12+
- Root access

### Install

```bash
# Clone
git clone https://github.com/lumen-industries/limpet.git
cd limpet

# Install core system
sudo ./install.sh

# Install the default plugin set (100+ tools)
sudo limpet-tools install-defaults

# Verify
limpet-tools health
```

### First Run

```bash
# Start the services
sudo systemctl start limpet-hald limpet-registry-daemon limpet-mcp-meta

# Check what hardware is connected
limpet-tools hardware

# Create a workspace for your target
limpet-workspace create --name "my-device" --description "Unknown IoT gadget"

# Connect your LLM agent via MCP
# (stdio transport — agent spawns the meta-server)
limpet-mcp-meta

# Or via SSE (HTTP transport for remote agents)
limpet-mcp-meta --transport sse --port 3200
```

### Example Session

Plug in a MediaTek tablet in BROM mode. The agent sees:

```
> limpet_hardware_list
Connected devices:
  usb-1-4: MediaTek device in BROM mode (MT8167) — flash read/write via mtkclient

> limpet_suggest_workflow target="MediaTek MT8167 tablet"
1. DUMP:    mtk_read_partition (boot, recovery, system)
2. EXTRACT: binwalk_extract → bang_scan (MT8167-specific)
3. ANALYZE: ghidra_analyze (decompile interesting binaries)
4. REPORT:  limpet_workspace_add_finding (record results)

> mtk_read_partition partition_name="boot" output_file="firmware/boot.img"
Partition boot dumped: 33554432 bytes (32 MB)
SHA-256: a1b2c3d4...

> binwalk_extract input_file="firmware/boot.img"
Extracted 47 files to firmware/extracted/
  - Linux kernel 4.4.146 (ARM)
  - Device tree blob (MT8167)
  - ramdisk (gzip compressed)
```

## Supported Tools

### Firmware Extraction
| Tool | Description |
|---|---|
| binwalk | Signature scanning + extraction |
| unblob | Modern firmware extractor (SquashFS, UBI, ext4, ...) |
| moria | Single-binary extractor with JSON output |
| BANG | SoC-specific parsers (MediaTek, Rockchip, Qualcomm) |

### Binary Analysis
| Tool | Description |
|---|---|
| Ghidra | Disassembly, decompilation, scripting (MCP-native) |
| Rizin | Fast binary triage (radare2 fork) |
| Reko | Multi-arch decompiler (8051, PIC, Z80, AVR, ...) |
| ceasta | MCP-native debugger/decompiler |
| REA | MCP RE framework (170 tools) |

### Emulation
| Tool | Description |
|---|---|
| QEMU | User-mode ARM/MIPS/x86 emulation |
| Qiling | OS-level emulation framework |
| Renode | Full system MCU emulator |

### Flash Tools
| Tool | Description |
|---|---|
| mtkclient | MediaTek BROM exploit + flash |
| edl | Qualcomm EDL mode flash |
| rkdeveloptool | Rockchip Maskrom flash |
| flashrom | SPI/parallel flash programmer |
| dfu-util | USB DFU mode flash |
| OpenOCD | JTAG/SWD debug + flash |

### Android
| Tool | Description |
|---|---|
| ADB/fastboot | Android Debug Bridge |
| JADX | APK/DEX decompiler |
| apktool | APK unpacker |
| Frida | Dynamic instrumentation |

### Network
| Tool | Description |
|---|---|
| bettercap | Network MITM and analysis |
| TShark | Packet capture and decode |

### BIOS/UEFI
| Tool | Description |
|---|---|
| UEFITool | UEFI firmware editor |
| me_cleaner | Intel ME neutralization |
| PSPTool | AMD PSP firmware analysis |

See the full [tool catalog](tool-catalog.json) for all 100+ tools (the catalog's `tools` array is the canonical count; currently 104).

## Supported Hardware

Limpet auto-detects these device types when plugged into USB:

### Debug Probes
ST-Link V2/V3, J-Link, FTDI (FT2232H), Bus Pirate, Glasgow, CMSIS-DAP

### Flash Programmers
CH341A, FlashcatUSB, Dediprog SF100/SF600

### Analyzers
Saleae Logic, DSLogic, RTL-SDR, HackRF One

### Capture + Inference
Magewell USB Capture HDMI, Google Coral TPU, Intel NCS2

### Target Devices (auto-detected modes)
| Vendor | Detected Modes |
|---|---|
| MediaTek | BROM, preloader, ADB, fastboot |
| Qualcomm | EDL (9008), ADB |
| Rockchip | Maskrom, Loader, ADB |
| Samsung | Odin/Download, ADB |
| AllWinner | FEL, ADB |
| STM32 | DFU |
| ESP32 | ROM boot |
| Generic Android | ADB, fastboot |

## Plugin Development

Every tool is a plugin. Creating one is simple:

```bash
mkdir limpet-plugin-mytool && cd limpet-plugin-mytool
git init
```

Create `limpet-plugin.toml`:

```toml
[plugin]
name = "mytool"
version = "1.0.0"
manifest_version = 1
category = "firmware-extraction"
description = "My custom firmware tool"

[capabilities]
extract_firmware = true
headless = true
architectures = ["ARM"]
input_formats = ["raw"]
output_formats = ["JSON"]

[paths]
binary = "/usr/local/bin/mytool"

[health]
check = "mytool --version"
interval = "6h"
timeout = "5s"

[mcp]
enabled = false

[agent_hints]
summary = "Custom firmware extraction for XYZ chipsets."
when_to_use = "Use when working with XYZ chipsets."
complements = ["binwalk"]
workflow_position = "entry"

[[wrappers]]
tool_name = "mytool_extract"
description = "Extract firmware from XYZ chipset dump"
command = "mytool extract {input_file} -o {output_dir}"
parameters = [
  { name = "input_file", type = "string", required = true, description = "Input firmware dump" },
  { name = "output_dir", type = "string", required = false, description = "Output dir", default = "." },
]
output_type = "directory"
output_parser = "extraction_summary"
```

Create `install.sh`:

```bash
#!/bin/bash
set -euo pipefail
# Install your tool here
apt-get install -y mytool
```

Publish and install:

```bash
git add -A && git commit -m "Initial plugin"
git remote add origin https://github.com/you/limpet-plugin-mytool.git
git push -u origin main

# Others can install with:
limpet-tools install https://github.com/you/limpet-plugin-mytool.git
```

See the [Plugin Development Guide](docs/plugin-spec.md) for full documentation.

## Architecture

Limpet is five subsystems:

1. **[Tool Registry](docs/tool-registry-spec.md)** — TOML manifests in `/etc/limpet/plugins.d/`, watched by a registry daemon that builds a unified JSON index.

2. **[MCP Meta-Server](docs/mcp-meta-spec.md)** — single MCP endpoint. Proxies native MCP tools (Ghidra, REA). Auto-generates wrappers for CLI tools. Hardware-aware routing.

3. **[Hardware Abstraction Layer](docs/hal-spec.md)** — udev-based USB detection. Identifies probes, analyzers, capture devices, and target devices (including boot mode detection for MediaTek, Qualcomm, Rockchip).

4. **[Plugin System](docs/plugin-spec.md)** — git-native plugin lifecycle. Install, validate, health-check, enable, disable, update, uninstall.

5. **[Workspace Manager](docs/workspace-spec.md)** — per-target isolation. Git-tracked investigation workspaces with evidence chain (every finding links to the tool and command that produced it).

## Reference Hardware

The reference build is a **Lenovo ThinkCentre M70q Gen 5**:

| Component | Spec |
|---|---|
| CPU | Intel Core i5-14500 (14C/20T) |
| RAM | 32GB DDR5-5600 |
| Storage | 512GB NVMe |
| USB | 4x USB-A 3.2, 1x USB-C 3.2, 2x USB-A 2.0 |
| Network | 1GbE + Wi-Fi 6E |
| Form factor | 1L mini PC |
| Power | ~35W typical |

Any x86-64 PC with 16GB+ RAM and USB ports works. ARM64 support (Rockchip, Raspberry Pi) is planned.

## Why "Limpet"?

A limpet is a marine snail that attaches itself firmly to rocks and does not let go. Like its namesake, this appliance attaches to a target device and methodically examines every surface until the job is done.

Also: **L**umen **I**ndustries **M**CP **P**lugin-based **E**ngineering **T**oolkit.

## Project Status

**Pre-release.** Architecture specs are complete. Implementation is in progress.

- [x] Vision and architecture
- [x] Tool Registry specification
- [x] MCP Meta-Server specification
- [x] Hardware Abstraction Layer specification
- [x] Plugin System specification
- [x] Workspace Manager specification
- [ ] Core implementation (Python)
- [ ] Default plugin set (30 plugins)
- [ ] Hardware manifest library
- [ ] Integration tests
- [ ] Documentation site

## Contributing

Contributions welcome. The fastest way to help:

1. **Write a plugin** for an RE tool you use. See the [Plugin Development Guide](docs/plugin-spec.md).
2. **Add a hardware manifest** for a device you own. See the [HAL spec](docs/hal-spec.md).
3. **Test on your hardware.** We need reports from different x86 PCs and USB devices.
4. **File issues.** Bug reports and feature requests help prioritize work.

## License

MIT

---

*Built by [Lumen Industries](https://lumennode.uk). Making hardware reverse engineering accessible to everyone.*
