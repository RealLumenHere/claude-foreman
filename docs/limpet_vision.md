# LIMPET — The Universal RE Agent Appliance (VISION.md, tracked copy)

> Status: tracked copy — 2026-10-15
> Tickets: wq#4042 (fixed the fabricated "250k/24-target" citation, per wq#4039
> judgment); wq#4016 (closed 2026-10-15 against this copy — the "Why Is It
> Different?" section below, leading with appliance + HAL auto-detection +
> energize-confirm gate + offline-first local LLM + agent-driven workflow
> instead of tool count, is the ticket's implementing change)

This is the git-tracked copy of `/home/lumen/projects/limpet/VISION.md`
(outside any git repo, so edits there have no commit/diff/review trail —
same anti-pattern as ISSUE-LOG row 1328 / wq#3939/#3919, PR #1904). This copy
also carries the correction wq#4039 flagged: the live file previously claimed
"REA alone ships a 250k-tool catalog across a 24-target matrix," cited to
`design/wq3929_competitive_analysis.md:193`. That line describes a
Ghidra-MCP fork ("250 Ghidra tools flat"), not REA, and no "24-target
matrix" or "250k" figure exists anywhere in `wq3929_competitive_analysis.md`.
`wq3929_competitive_analysis.md:57` gives REA's real tool count, ~170. Both
the live file and this copy now cite that figure.

---

# LIMPET — The Universal RE Agent Appliance

## What Is It?
A dedicated appliance for reverse engineering — not an app you install, a
secure RE workstation (M70q Gen 5, i5-14500, 16GB DDR5 — `docs/pcb-vision-spec.md` §7) you plug a target
into. The Hardware Abstraction Layer auto-detects whatever just landed on a
USB port — probe, analyzer, programmer, or the target device itself — and the
agent takes it from there: autonomous analysis, with destructive hardware
actions gated on an explicit operator confirm.

## Why Is It Different?
Tool breadth is commoditized — REA alone already ships ~170 tools, and
AttifyOS already owns the "hardware-RE distro" lane. Limpet doesn't compete
on tool count. It is the only platform that combines:

1. **HAL auto-detection.** Plug in a JTAG probe, a logic analyzer, or an
   unknown target and the agent already knows what it is — no manual
   interface wiring, the step every other hardware-RE distro (AttifyOS
   included) still makes the operator do by hand.
2. **A full appliance, not an app.** Dedicated OS, dedicated box. You don't
   install Limpet onto your workstation; you plug a target into Limpet.
3. **The energize-confirm trust gate.** Read-only work — enumeration,
   identification, passive capture — runs fully autonomous. Anything that
   energizes hardware (probe power, JTAG/SWD, flash writes, GPIO/reset lines,
   exploit vectors) is gated behind an explicit operator confirm. Autonomous
   analysis, human-gated destructive action.
   *Open design flag (wq#4147):* SDR transmit does not fit this framing —
   energize bounds risk to the device under test, while TX emits RF at third
   parties and carries regulatory exposure. Whether TX overloads the energize
   gate or gets its own confirm class is an undecided trust-gate design call;
   see plugin-spec.md §6 ("OPEN DESIGN FLAG — SDR TX").
4. **Offline-first, local LLM.** Runs air-gapped on local inference
   (Ollama/LiteLLM) — no cloud dependency to analyze a device.
5. **Agent-driven, not tool-driven.** The agent suggests the next workflow
   step from what's connected and what's been found so far, instead of
   waiting to be told which of the hundred-plus tools to run.

## Architecture

                    ┌─────────────────────────┐
                    │     AGENT CORE          │
                    │  (Ollama + LiteLLM)     │
                    │  qwen2.5-coder:14b      │
                    └────────────┬────────────┘
                                 │ MCP
                    ┌────────────▼────────────┐
                    │   MCP META-SERVER       │
                    │  (aggregates all tools) │
                    └────────────┬────────────┘
                                 │
          ┌──────────────────────┼──────────────────────┐
          │                      │                      │
    ┌─────▼─────┐         ┌──────▼──────┐        ┌─────▼─────┐
    │ TOOL      │         │ HARDWARE    │        │ WORKSPACE │
    │ REGISTRY  │         │ ABSTRACTION │        │ MANAGER   │
    │           │         │ LAYER       │        │           │
    │100+ tools │         │             │        │ Per-target│
    │ in        │         │ Coral TPU   │        │ isolation │
    │ plugins.d │         │ Magewell    │        │ Git repos │
    │           │         │ JTAG/SWD    │        │ Evidence  │
    │           │         │ Logic Anal  │        │ Artifacts │
    │           │         │ Programmers │        │           │
    └───────────┘         │ SDR         │        └───────────┘
                          │ USB targets │
                          └─────────────┘

## Plugin Manifest Format (limpet-plugin.toml)

    [plugin]
    name = "ghidra"
    version = "12.1.3"
    category = "binary-analysis"
    description = "NSA SRE framework"

    [capabilities]
    disassemble = true
    decompile = true
    script = true
    headless = true
    architectures = ["x86", "ARM", "MIPS", "RISC-V", "AVR", "PIC", "8051"]
    input_formats = ["ELF", "PE", "Mach-O", "raw"]
    output_formats = ["C", "JSON", "XML"]

    [mcp]
    enabled = true
    server = "ghidra-mcp"
    transport = "stdio"

    [paths]
    binary = "/opt/ghidra/ghidraRun"
    headless = "/opt/ghidra/support/analyzeHeadless"

    [health]
    check = "/opt/ghidra/support/analyzeHeadless --version"
    interval = "1h"

    [dependencies]
    requires = ["java-21"]

## Hardware Plugin Format

    [hardware]
    name = "magewell-hdmi"
    type = "video-capture"
    description = "Magewell USB Capture HDMI Gen 2"

    [detection]
    usb_vendor = "2935"
    usb_product = "0001"
    device_path = "/dev/video*"
    driver = "uvcvideo"

    [capabilities]
    capture_video = true
    capture_audio = true
    max_resolution = "2048x2160"
    max_fps = 60
    formats = ["MJPEG", "YUV", "NV12"]

    [agent_use]
    visual_input = true
    screen_reading = true
    kvm_display = true

## Why This Puts Us On The Map
1. Nobody else combines agent-driven operation + an auto-detecting HAL + an
   appliance form factor — AttifyOS has hardware access but isn't
   agent-driven or auto-detecting; REA and the agent frameworks are
   agent-driven but have no hardware awareness at all.
2. The energize-confirm trust gate is the real safety story: autonomous
   analysis, human-gated destructive action. No peer has it.
3. Offline-first with a local LLM means it works air-gapped — a requirement
   the cloud-dependent competitors ignore.
4. The plugin system lets tool breadth grow without us owning it; breadth was
   never the moat.
5. MCP-native means any AI agent can drive it.
6. The M70q form factor is cheap, low-power, and available.
7. Every e-waste reclamation shop could use one.
