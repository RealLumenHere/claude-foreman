# Hardware Abstraction Layer Specification

> Limpet Project — udev-based device detection, hardware manifests, agent-visible inventory
>
> Status: DRAFT v0.1 — 2026-10-13
> Author: Claude (Limpet Architect)
> Ticket: wq#3809

---

## 1. Overview

The Hardware Abstraction Layer (HAL) is the subsystem that makes a Limpet appliance aware of every piece of hardware connected to its USB ports. It detects plug/unplug events, identifies devices by their USB descriptors, maps them to capabilities, and exposes a clean inventory to the MCP Meta-Server and agents.

An agent should be able to ask "what hardware do I have?" and get an answer like:

```
Connected hardware:
  1. Google Coral USB Accelerator — ML inference (4 TOPS INT8)
  2. Magewell USB Capture HDMI Gen 2 — video capture (4K60 MJPEG)
  3. ST-Link V2 — JTAG/SWD debug probe
  4. MediaTek device (MT8167) in BROM mode — flash read/write target
  5. USB-UART adapter (CP2102) — serial console at /dev/ttyUSB0
```

### Design Principles

- **Zero-config detection.** Plug in a supported device, HAL identifies it automatically. No manual registration.
- **Event-driven.** All detection is via udev events. No polling.
- **Agent-first API.** The HAL exposes capabilities, not raw USB descriptors. An agent does not need to know VID/PID — it sees "JTAG probe" or "MediaTek target in BROM mode."
- **Extensible via hardware plugins.** Community can add support for new devices by dropping a TOML manifest into `/etc/limpet/hardware.d/`.

---

## 2. Device Detection

### 2.1 Architecture

```
USB Bus
  │  plug/unplug
  ▼
udev ──────▶ udev rules (/etc/udev/rules.d/90-limpet.rules)
                   │
                   │ RUN+="limpet-hal-notify"
                   ▼
            HAL daemon (limpet-hald)
                   │
                   ├── Match against hardware manifests
                   │   (/etc/limpet/hardware.d/*.toml)
                   │
                   ├── Run setup commands if needed
                   │
                   ├── Update device inventory
                   │   (/var/lib/limpet/hardware.json)
                   │
                   └── Publish events via Unix socket
                       (/run/limpet/hal.sock)
                              │
                              ▼
                       MCP Meta-Server
                       (hardware-aware routing)
```

### 2.2 udev Rules

`/etc/udev/rules.d/90-limpet.rules`:

```udev
# Limpet HAL — notify daemon of USB device events
# This rule fires for ALL USB devices. The HAL daemon filters by manifest.

# USB device added
ACTION=="add", SUBSYSTEM=="usb", ENV{DEVTYPE}=="usb_device", \
  RUN+="/usr/lib/limpet/limpet-hal-notify add $devpath $attr{idVendor} $attr{idProduct} $attr{manufacturer} $attr{product} $attr{serial}"

# USB device removed
ACTION=="remove", SUBSYSTEM=="usb", ENV{DEVTYPE}=="usb_device", \
  RUN+="/usr/lib/limpet/limpet-hal-notify remove $devpath"

# Serial port added (for UART adapters)
ACTION=="add", SUBSYSTEM=="tty", KERNEL=="ttyUSB*|ttyACM*", \
  RUN+="/usr/lib/limpet/limpet-hal-notify serial_add $devpath $attr{idVendor} $attr{idProduct} $kernel"

# Serial port removed
ACTION=="remove", SUBSYSTEM=="tty", KERNEL=="ttyUSB*|ttyACM*", \
  RUN+="/usr/lib/limpet/limpet-hal-notify serial_remove $devpath $kernel"

# Video device added (for capture cards)
ACTION=="add", SUBSYSTEM=="video4linux", \
  RUN+="/usr/lib/limpet/limpet-hal-notify video_add $devpath $attr{name} $kernel"

# Video device removed
ACTION=="remove", SUBSYSTEM=="video4linux", \
  RUN+="/usr/lib/limpet/limpet-hal-notify video_remove $devpath $kernel"
```

### 2.3 Notification Helper

`/usr/lib/limpet/limpet-hal-notify` is a tiny script that sends events to the HAL daemon via its Unix socket. It runs in the udev process context, so it must be fast (< 100ms) and never block.

```bash
#!/bin/bash
# limpet-hal-notify — relay udev events to limpet-hald
exec socat - UNIX-CONNECT:/run/limpet/hal.sock <<< "$*"
```

### 2.4 HAL Daemon

`limpet-hald` is a Python daemon using `pyudev` as the primary detection mechanism, with the udev rules as a backup notification path. It:

1. Starts a `pyudev.Monitor` watching for USB, serial, and video subsystem events
2. Also listens on `/run/limpet/hal.sock` for `limpet-hal-notify` messages
3. On each event, matches against hardware manifests
4. Updates the hardware inventory
5. Publishes events to subscribers (Meta-Server, CLI)

```python
# Simplified HAL daemon core
import pyudev

context = pyudev.Context()
monitor = pyudev.Monitor.from_netlink(context)
monitor.filter_by(subsystem="usb")
monitor.filter_by(subsystem="tty")
monitor.filter_by(subsystem="video4linux")

for device in iter(monitor.poll, None):
    if device.action == "add":
        match = match_hardware_manifest(device)
        if match:
            register_device(device, match)
            publish_event("device_connected", device, match)
    elif device.action == "remove":
        deregister_device(device)
        publish_event("device_disconnected", device)
```

---

## 3. Hardware Plugin Manifests

### 3.1 Manifest Location

Hardware manifests live in `/etc/limpet/hardware.d/`. Each TOML file describes one class of hardware device. The HAL daemon loads all manifests at startup and reloads on inotify change.

```
/etc/limpet/hardware.d/
  coral-tpu.toml
  magewell-capture.toml
  stlink.toml
  jlink.toml
  ftdi-jtag.toml
  bus-pirate.toml
  glasgow.toml
  cp2102-uart.toml
  mediatek-brom.toml
  qualcomm-edl.toml
  rockchip-maskrom.toml
  android-adb.toml
  ...
```

### 3.2 Manifest Schema

```toml
# ─── Identity ────────────────────────────────────────────────────────
[hardware]
name = "magewell-hdmi"
type = "video-capture"                  # see Section 3.3
description = "Magewell USB Capture HDMI Gen 2"
manufacturer = "Magewell"

# ─── USB Detection ───────────────────────────────────────────────────
[detection]
# Primary match: USB Vendor:Product ID
usb_vendor = "2935"                     # required
usb_product = "0001"                    # optional — if omitted, any product from this vendor matches
# Alternative: match by USB class
# usb_class = "0e"                     # USB Video Class
# usb_subclass = "01"

# Additional match criteria (all optional, AND logic)
usb_manufacturer_string = "Magewell"    # substring match on USB manufacturer string
usb_product_string = "USB Capture HDMI" # substring match on USB product string

# Multiple VID:PID pairs (OR logic)
# [[detection.match]]
# usb_vendor = "2935"
# usb_product = "0001"
# [[detection.match]]
# usb_vendor = "2935"
# usb_product = "0002"

# ─── Device Paths ───────────────────────────────────────────────────
[device]
# Where the kernel exposes this device after driver binding
device_path_pattern = "/dev/video*"     # glob pattern
driver = "uvcvideo"                     # expected kernel driver
additional_paths = ["/dev/snd/pcm*"]    # audio companion device

# ─── Capabilities ───────────────────────────────────────────────────
[capabilities]
# What an agent can do with this hardware
capture_video = true
capture_audio = true
visual_input = true                     # agent can see what the device shows
screen_reading = true                   # agent can read text from captured video
kvm_display = true                      # can be part of a KVM setup

# Performance specs (informational for the agent)
max_resolution = "2048x2160"
max_fps = 60
formats = ["MJPEG", "YUV", "NV12"]

# ─── Setup Commands ──────────────────────────────────────────────────
[setup]
# Commands to run when this device is detected (before it is usable)
on_connect = [
    "modprobe uvcvideo",                # ensure driver is loaded
]
on_disconnect = []

# Permissions: ensure the limpet user can access the device
permissions = { group = "video", mode = "0660" }

# ─── Agent Hints ─────────────────────────────────────────────────────
[agent_hints]
summary = "HDMI capture card: captures video/audio from any HDMI source at up to 4K60."
use_cases = [
    "Capture target device screen output for visual analysis",
    "Record boot sequences for timing analysis",
    "KVM setup for headless device interaction",
    "Read on-screen text and menus via OCR",
]
```

### 3.3 Hardware Type Taxonomy

| Type | Description | Examples |
|---|---|---|
| `video-capture` | Video/audio capture devices | Magewell HDMI, Elgato |
| `ml-accelerator` | ML inference hardware | Coral TPU, Intel NCS2 |
| `debug-probe` | JTAG/SWD debug probes | ST-Link, J-Link, FTDI, Bus Pirate, Glasgow |
| `flash-programmer` | Standalone flash programmers | CH341A, FlashcatUSB |
| `logic-analyzer` | Digital logic analyzers | Saleae, DSLogic, sigrok-compatible |
| `sdr` | Software-defined radios | RTL-SDR, HackRF, YARD Stick |
| `serial-adapter` | USB-UART adapters | CP2102, CH340, FTDI |
| `target-device` | The device being reverse engineered | Phones, tablets, embedded boards |
| `network-adapter` | USB ethernet/WiFi for packet capture | Monitor-mode capable adapters |
| `storage` | USB storage (for firmware dumps) | Flash drives, card readers |
| `kvm-switch` | KVM control | TESmart, ATEN |

### 3.4 Target Device Detection

Target devices (the things being reverse engineered) require special handling because they can appear in multiple USB modes:

```toml
# /etc/limpet/hardware.d/mediatek-target.toml
[hardware]
name = "mediatek-target"
type = "target-device"
description = "MediaTek-based device (phone, tablet, IoT)"
manufacturer = "MediaTek"

# MediaTek devices can appear in different USB modes
[[detection.modes]]
mode = "BROM"
usb_vendor = "0e8d"
usb_product = ["0003"]
description = "BootROM mode — full flash access via mtkclient"
agent_note = "Device is in BROM mode. Use mtk_read_partition to dump flash, mtk_dump_brom to dump BootROM."
available_tools = ["mtkclient"]

[[detection.modes]]
mode = "preloader"
usb_vendor = "0e8d"
usb_product = ["2000", "2001"]
description = "Preloader mode — limited flash access via mtkclient"
agent_note = "Device is in preloader mode. Some partitions may be locked. Try BROM exploit with mtk_exploit_brom."
available_tools = ["mtkclient"]

[[detection.modes]]
mode = "ADB"
usb_vendor = "0e8d"
usb_product = ["201c", "2008"]
# Also match any device with ADB interface
usb_class = "ff"
usb_subclass = "42"
usb_protocol = "01"
description = "Android Debug Bridge — app-level access"
agent_note = "Device is in ADB mode. Use adb shell for inspection. For flash dumps, use adb pull (NEVER adb exec-out dd)."
available_tools = ["adb", "frida", "jadx"]

[[detection.modes]]
mode = "fastboot"
usb_vendor = "0e8d"
usb_product = ["201c"]
usb_class = "ff"
usb_subclass = "42"
usb_protocol = "03"
description = "Fastboot mode — partition-level flash access"
agent_note = "Device is in fastboot mode. Use fastboot to flash partitions. Faster than mtkclient for unlocked devices."
available_tools = ["fastboot"]

[capabilities]
flash_read = true
flash_write = true
adb = true
fastboot = true
exploit_bootrom = true

[setup]
on_connect = []
on_disconnect = []
permissions = { group = "plugdev", mode = "0660" }
```

Similarly for Qualcomm (EDL/Sahara/normal) and Rockchip (Maskrom/Loader/normal):

```toml
# /etc/limpet/hardware.d/qualcomm-target.toml
[hardware]
name = "qualcomm-target"
type = "target-device"
description = "Qualcomm-based device"

[[detection.modes]]
mode = "EDL"
usb_vendor = "05c6"
usb_product = ["9008"]
description = "Emergency Download mode — full flash access via edl/Sahara"
agent_note = "Qualcomm device in EDL/9008 mode. Use edl tool for flash read/write. Requires matching firehose loader for the SoC."
available_tools = ["edl"]

[[detection.modes]]
mode = "ADB"
usb_vendor = "05c6"
usb_product = ["9024", "9025"]
description = "ADB mode"
agent_note = "Qualcomm device in ADB mode."
available_tools = ["adb", "frida", "jadx"]

# ---

# /etc/limpet/hardware.d/rockchip-target.toml
[hardware]
name = "rockchip-target"
type = "target-device"
description = "Rockchip-based device"

[[detection.modes]]
mode = "Maskrom"
usb_vendor = "2207"
usb_product = ["330a", "350a", "350b"]
description = "Maskrom mode — direct flash access via rkdeveloptool"
agent_note = "Rockchip device in Maskrom mode. Use rkdeveloptool for flash read/write. No authentication bypass needed."
available_tools = ["rkdeveloptool"]

[[detection.modes]]
mode = "Loader"
usb_vendor = "2207"
usb_product = ["320a", "320b"]
description = "Loader mode — flash access via rkdeveloptool (may need loader binary)"
agent_note = "Rockchip device in Loader mode. Use rkdeveloptool. Some operations require a DDR init binary."
available_tools = ["rkdeveloptool"]
```

### 3.5 SoC Identification

For target devices, the HAL attempts to identify the specific SoC after mode detection:

1. **USB product string:** Many devices report their SoC in the USB product string (e.g., "MT8167")
2. **BROM identification:** For MediaTek in BROM mode, the first handshake bytes identify the SoC
3. **ADB getprop:** For devices in ADB mode, `adb shell getprop ro.hardware` reveals the chipset
4. **USB PID lookup table:** Known PID-to-SoC mappings maintained in the hardware manifests

The identified SoC is reported to the agent:

```json
{
  "device_id": "usb-3-1",
  "type": "target-device",
  "name": "mediatek-target",
  "mode": "BROM",
  "soc": "MT8167",
  "description": "MediaTek device detected in BROM mode (MT8167)",
  "available_tools": ["mtkclient"],
  "agent_note": "Device is in BROM mode. Use mtk_read_partition to dump flash."
}
```

---

## 4. Agent-Visible Hardware Inventory

### 4.1 Inventory File

The HAL daemon maintains a live inventory at `/var/lib/limpet/hardware.json`:

```json
{
  "version": 1,
  "updated_at": "2026-10-13T22:30:00Z",
  "devices": [
    {
      "device_id": "usb-1-2",
      "devpath": "/devices/pci0000:00/0000:00:14.0/usb1/1-2",
      "type": "ml-accelerator",
      "name": "coral-tpu",
      "description": "Google Coral USB Accelerator",
      "manufacturer": "Google",
      "usb_vid": "1a6e",
      "usb_pid": "089a",
      "serial": "ABC123",
      "driver": "apex",
      "device_paths": ["/dev/apex_0"],
      "capabilities": {
        "ml_inference": true,
        "tpu_tops": 4,
        "supported_frameworks": ["TensorFlow Lite", "PyCoral"]
      },
      "status": "ready",
      "connected_at": "2026-10-13T22:00:00Z"
    },
    {
      "device_id": "usb-1-4",
      "devpath": "/devices/pci0000:00/0000:00:14.0/usb1/1-4",
      "type": "target-device",
      "name": "mediatek-target",
      "description": "MediaTek device in BROM mode (MT8167)",
      "manufacturer": "MediaTek",
      "usb_vid": "0e8d",
      "usb_pid": "0003",
      "mode": "BROM",
      "soc": "MT8167",
      "capabilities": {
        "flash_read": true,
        "flash_write": true,
        "exploit_bootrom": true
      },
      "available_tools": ["mtkclient"],
      "agent_note": "Device is in BROM mode. Use mtk_read_partition to dump flash.",
      "status": "ready",
      "connected_at": "2026-10-13T22:15:00Z"
    }
  ],
  "summary": {
    "total": 5,
    "by_type": {
      "ml-accelerator": 1,
      "video-capture": 1,
      "debug-probe": 1,
      "target-device": 1,
      "serial-adapter": 1
    }
  }
}
```

### 4.2 MCP Tools

The HAL exposes two MCP tools via the Meta-Server:

#### `limpet_hardware_list`

Returns all connected hardware with capabilities.

```json
{
  "name": "limpet_hardware_list",
  "description": "List all hardware devices connected to this Limpet appliance. Shows device type, capabilities, and available RE tools for each device.",
  "inputSchema": {
    "type": "object",
    "properties": {
      "type": {
        "type": "string",
        "description": "Filter by device type (e.g., target-device, debug-probe)",
        "enum": ["video-capture", "ml-accelerator", "debug-probe", "flash-programmer", "logic-analyzer", "sdr", "serial-adapter", "target-device", "network-adapter", "storage", "kvm-switch"]
      }
    }
  }
}
```

#### `limpet_hardware_status`

Detailed status of a specific device.

```json
{
  "name": "limpet_hardware_status",
  "description": "Get detailed information about a specific connected hardware device, including device paths, capabilities, and suggested tools.",
  "inputSchema": {
    "type": "object",
    "properties": {
      "device_id": {
        "type": "string",
        "description": "Device identifier from limpet_hardware_list (e.g., usb-1-4)"
      }
    },
    "required": ["device_id"]
  }
}
```

### 4.3 HAL Events

Events published to `/run/limpet/hal.sock`:

```json
{
  "type": "device_connected",
  "device_id": "usb-1-4",
  "device_type": "target-device",
  "name": "mediatek-target",
  "mode": "BROM",
  "soc": "MT8167",
  "description": "MediaTek device detected in BROM mode (MT8167)",
  "timestamp": "2026-10-13T22:15:00Z"
}
```

| Event | Trigger |
|---|---|
| `device_connected` | USB device plugged in and matched to a manifest |
| `device_disconnected` | Matched USB device unplugged |
| `device_mode_changed` | Target device changed USB mode (e.g., ADB -> fastboot) |
| `device_unknown` | USB device plugged in but no manifest match |
| `device_setup_failed` | on_connect setup commands failed |

---

## 5. Built-in Hardware Profiles

Limpet ships with manifests for common RE hardware. These cover the most likely devices an RE engineer would plug in.

### 5.1 Debug Probes

| Device | VID:PID | Capabilities |
|---|---|---|
| ST-Link V2 | 0483:3748 | JTAG, SWD, ARM Cortex |
| ST-Link V3 | 0483:374e | JTAG, SWD, ARM Cortex, UART bridge |
| J-Link | 1366:* | JTAG, SWD, multi-arch |
| FTDI FT2232H | 0403:6010 | JTAG, SWD, UART, SPI (via OpenOCD) |
| Bus Pirate v3/v4 | 0403:6001 | JTAG, SWD, UART, SPI, I2C |
| Glasgow | 1d50:615b | JTAG, SWD, UART, SPI, I2C, logic analyzer |
| CMSIS-DAP | various | JTAG, SWD (firmware probe standard) |

### 5.2 Flash Programmers

| Device | VID:PID | Capabilities |
|---|---|---|
| CH341A | 1a86:5512 | SPI flash read/write (3.3V ONLY on mod) |
| FlashcatUSB | 1d50:6130 | SPI, I2C, JTAG flash |
| Dediprog SF100/SF600 | 0483:dada | SPI flash (production grade) |

### 5.3 Analyzers

| Device | VID:PID | Capabilities |
|---|---|---|
| Saleae Logic | 21a9:1001 | 8/16-channel digital logic analyzer |
| DSLogic | 2a0e:* | 16-channel logic analyzer (sigrok) |
| RTL-SDR | 0bda:2838 | SDR 24-1766 MHz |
| HackRF One | 1d50:6089 | SDR 1-6000 MHz, TX+RX |

### 5.4 Capture + I/O

| Device | VID:PID | Capabilities |
|---|---|---|
| Magewell HDMI Gen 2 | 2935:0001 | 4K60 HDMI capture, UVC |
| Coral USB TPU | 1a6e:089a | ML inference, 4 TOPS INT8 |
| Intel NCS2 | 03e7:2485 | ML inference, OpenVINO |
| CP2102 UART | 10c4:ea60 | USB-UART 115200 baud |
| CH340 UART | 1a86:7523 | USB-UART |
| FTDI UART | 0403:6001 | USB-UART |

### 5.5 Target Devices

| Vendor | BROM/EDL VID:PID | Normal VID:PID |
|---|---|---|
| MediaTek | 0e8d:0003 (BROM) | 0e8d:201c (ADB) |
| Qualcomm | 05c6:9008 (EDL) | 05c6:9024 (ADB) |
| Rockchip | 2207:330a (Maskrom) | varies (ADB) |
| Amlogic | 1b8e:c003 (USB burn) | varies |
| Samsung | 04e8:685d (Odin) | 04e8:6860 (ADB) |
| AllWinner | 1f3a:efe8 (FEL) | varies |
| STM32 | 0483:df11 (DFU) | N/A |
| ESP32 | 303a:1001 (ROM boot) | varies |

---

## 6. systemd Unit

```ini
[Unit]
Description=Limpet Hardware Abstraction Layer
After=systemd-udevd.service local-fs.target
Before=limpet-registry-daemon.service

[Service]
Type=notify
ExecStart=/usr/bin/limpet-hald
RuntimeDirectory=limpet
StateDirectory=limpet
Restart=on-failure
RestartSec=5s
WatchdogSec=30s

# Needs access to USB devices
SupplementaryGroups=plugdev dialout video

# Security
NoNewPrivileges=yes
ProtectSystem=strict
ReadWritePaths=/var/lib/limpet /run/limpet /dev
ReadOnlyPaths=/etc/limpet /sys
PrivateTmp=yes
ProtectHome=yes

[Install]
WantedBy=multi-user.target
```

---

## 7. Startup and Service Order

```
systemd-udevd.service
        │
        ▼
limpet-hald.service        ←── HAL starts, scans current USB devices, watches for events
        │
        ▼
limpet-registry-daemon.service   ←── Registry starts, reads hardware.json for tool availability
        │
        ▼
limpet-mcp-meta.service ←── Meta-Server starts, connects to both registry + HAL
```

The HAL is the first Limpet service to start because the registry and Meta-Server both depend on hardware state.

---

## 8. Extensibility

### 8.1 Community Hardware Plugins

New hardware support is added by dropping a TOML manifest into `/etc/limpet/hardware.d/`. The HAL daemon picks it up via inotify and starts matching against it immediately.

The `limpet-tools` CLI supports hardware manifest installation:

```
$ limpet-tools install-hardware https://github.com/user/limpet-hardware-myprobe.git
```

This clones the repo, validates the TOML, and copies it to `/etc/limpet/hardware.d/`.

### 8.2 Unknown Device Reporting

When an unrecognized USB device is plugged in, the HAL logs it and makes it available via the `device_unknown` event. The agent can see these:

```
$ limpet-tools hardware --unknown
Unknown USB devices:
  usb-1-6: VID:1234 PID:5678 "Mystery Board v2" (no manifest match)
```

This helps identify devices that need new hardware manifests.

### 8.3 Manual Override

For devices that cannot be auto-detected (no unique USB VID:PID), the HAL supports manual registration:

```
$ limpet-tools hardware-register --name "custom-jtag" --type debug-probe --path /dev/ttyUSB0
```

This creates a persistent entry that survives reboots (stored in `/var/lib/limpet/manual-hardware.json`).

## 9. Safety — the Energize-Confirm Gate (MANDATORY)

> Status: POLICY — Non-negotiable architectural constraint
> Authors: Frank (Limpet Project Owner), 2026-10-14 (structure) and 2026-10-15 (fleet-wide policy rule)
> Applies to: HAL, MCP Meta-Server, Agent Daemon, all tool wrappers, all hardware plugins — present and future, regardless of trust tier

> **Reconciled 2026-10-18 (wq#4227):** this spec previously carried two sections both
> numbered `## 9` — a 2026-10-15 policy note and a 2026-10-14 structured spec. That was a
> duplicate-heading defect, not two competing gates: both describe the same gate from two
> angles (policy rationale vs. implementation contract). Both texts are preserved below,
> merged rather than dropped. §9.6 explains why their two "missing X defaults to Y"
> fail-closed rules (§9.2a vs. §9.4 item 2) do not contradict each other.

### 9.1 Principle

**Limpet's agent may autonomously execute any READ-ONLY action against a connected
target device. It MUST NOT execute any WRITE or DESTRUCTIVE action without explicit
human confirmation.** This is policy, not per-session judgment. It cannot be overridden
by agent configuration, plugin settings, or prompt engineering.

This is the RE equivalent of the fleet rule "never reboot without explicit permission."
Limpet's version: **never write to a target device without explicit permission.**

**Rule (policy, set by Frank, 2026-10-15):** any HAL action that *energizes* a target
device — applies probe power, drives JTAG/SWD, writes flash, flips a GPIO/reset line,
sends a bootloader-unlock command, or executes a boot-vector/exploit action from
`limpet/data/soc-exploit-db/` — requires an explicit operator confirm before it runs,
every time, regardless of the catalog entry's `confidence` rating. Read-only actions
(device enumeration, VID:PID identification, read-only probing, non-destructive dumps,
passive capture) are autonomous and need no confirm.

This is the same "energize-confirm gate" already defined for the Target Device Wizard's
identification flow (`docs/target-wizard-spec.md` §4.3 in foreman: "any step that
energizes the device ... requires an explicit operator confirm before it runs"). That
definition was scoped to the wizard's IDENTIFY stage. This section makes it a HAL-wide
requirement.

This closes the gap identified in `docs/research-digest-2026-10-15-thor-docs.md` §B/§D and
in session review of wq#3921's soc-exploit-db work: a structured SoC→boot-vector→tool
catalog is legitimate RE/security-research data (the same category Kali, routersploit, and
the fleet's own already-installed tools already publish), but an autonomy engine must never
be allowed to autonomously select and fire a flash write, bootloader unlock, or exploit
chain against a real, physically-attached device without a human in the loop for that
specific action. Cataloging is read-only; *acting* on the catalog against hardware is not.

### 9.2a Capability Classification (HAL manifest axis: `read_only` / `energizing`)

Every hardware plugin's capability declaration must classify each capability it exposes
as either `read_only` or `energizing`, and the HAL daemon (§2.4) / meta-server must
refuse to invoke an `energizing` capability without a confirm token attached to the call
— the same way §4.3/§5 of the wizard spec requires `--confirm` for headless operation.
**A missing or ambiguous classification defaults to `energizing` (fail closed), never
`read_only`.**

### 9.2b Action Classification (MCP tool/wrapper manifest axis: `read` / `destructive` / `caution`)

Every MCP tool and wrapper that touches a target device MUST declare an `action_class`
in its TOML manifest:

```toml
[[wrappers]]
name = "dump_flash"
action_class = "read"      # autonomous — no confirmation needed

[[wrappers]]
name = "write_flash"
action_class = "destructive"  # HARD GATE — requires human confirmation
```

#### Classification Rules

**READ (autonomous)** — actions that extract information without modifying target state:
- Flash/memory dump (read SPI, eMMC, NAND)
- Partition table listing
- SoC identification, boot mode detection
- UART/serial console capture (read-only)
- JTAG/SWD register read, memory read
- Firmware extraction and unpacking
- Static analysis (disassembly, decompilation, string extraction)
- Logic analyzer capture
- HDMI video capture
- Component photography / vision pipeline
- Exploit/vulnerability database lookup
- Device enumeration (lsusb, ADB device list, fastboot getvar)

**DESTRUCTIVE (hard gate)** — actions that modify target device state:
- Flash write (SPI, eMMC, NAND, NOR)
- Partition create/delete/resize
- Bootloader unlock (fastboot oem unlock, mtkclient write DA)
- Bootloader modification (write boot/recovery partition)
- Boot ROM exploit execution (kamakiri, checkm8, EDL firehose load)
- Firmware flash/write/update
- Factory reset / data wipe
- Fuse blowing or OTP write
- Debug register write (JTAG/SWD memory write, breakpoint set that modifies target)
- ADB push/install/shell commands that modify target filesystem
- Any SoC download mode command that writes (Odin flash, rkdeveloptool write)

**CAUTION (confirmation recommended, agent may suggest skipping for experienced users):**
- ADB shell (interactive — could be read or write)
- JTAG halt/resume (non-destructive but affects target execution state)
- GPIO toggling (context-dependent)
- Power cycling target device

### 9.3 Enforcement Architecture

**Enforcement surface:** this gate lives in the HAL/meta-server layer (§2.4, §4.2), not in
the agent core's judgment and not in any individual plugin's own code — a plugin cannot
opt out of it by omission, and no per-session or per-agent decision overrides it. See
`design/wq3806_limpet_architecture.md` §4/§6 in foreman for the matching requirement in the
agent core's autonomy boundary.

```
Agent decides to call a tool
        │
        ▼
MCP Meta-Server receives tool invocation
        │
        ├── Check tool's action_class from manifest
        │
        ├── action_class == "read"
        │       └── Execute immediately, return results
        │
        ├── action_class == "caution"
        │       ├── Notify user: "Agent wants to [action]. This may affect
        │       │   target device state. Allow? [Y/n]"
        │       └── Default: require confirmation (configurable per-tool)
        │
        └── action_class == "destructive"
                ├── HARD GATE — cannot be bypassed
                ├── Display to user:
                │   ┌─────────────────────────────────────────────┐
                │   │ ⚠  DESTRUCTIVE ACTION REQUIRES CONFIRMATION │
                │   │                                             │
                │   │ Tool: flashrom --write firmware.bin          │
                │   │ Target: SPI flash on /dev/spidev0.0         │
                │   │ Effect: OVERWRITES entire flash contents     │
                │   │                                             │
                │   │ The agent recommends this action because:    │
                │   │ [agent's reasoning displayed here]            │
                │   │                                             │
                │   │ Type CONFIRM to proceed, or CANCEL to abort │
                │   └─────────────────────────────────────────────┘
                ├── User must type CONFIRM (not just y/Y/enter)
                ├── Confirmation logged to workspace evidence chain
                └── If declined: agent informed, must suggest alternative
```

### 9.4 Implementation Requirements

1. **The gate lives in the MCP Meta-Server**, not in individual tools. Tools do not
   self-gate — the meta-server enforces based on manifest declarations.

2. **Missing action_class defaults to "destructive."** If a tool or wrapper lacks an
   action_class declaration, it is treated as destructive. Fail-closed.

3. **action_class cannot be overridden at runtime.** Not by the agent, not by a plugin,
   not by a configuration file, not by a prompt. The only way to change a tool's
   action_class is to edit its TOML manifest on disk.

4. **Confirmation is per-invocation.** Confirming one flash write does not pre-authorize
   future flash writes, even to the same device in the same session.

5. **Headless mode.** In headless/SSH mode, the confirmation prompt is delivered via the
   SSH session. If no interactive session is available (fully autonomous batch mode),
   destructive actions queue and wait — they do not proceed.

6. **Audit trail.** Every destructive action confirmation (approved or declined) is logged
   to the workspace evidence chain with timestamp, user, tool, arguments, and full
   agent reasoning chain.

7. **Visual indicator.** The start bar hardware status widget shows a red indicator when
   a destructive action confirmation is pending.

8. **Agent-facing name.** The meta-server exposes this gate as one MCP tool,
   `limpet_confirm_destructive` (mcp-meta-spec.md §6.3). The "confirm token" above is
   that flow's `confirmation_id`. "energize-confirm" is the gate, `limpet_confirm_destructive`
   is its tool, and no other confirm tool exists (wq#4080).

9. **Network exposure is fail-closed (wq#4227).** `limpet-hald` never listens on a
   network socket: `/run/limpet/hal.sock` (§2.4, §6) is a Unix domain socket, gated by
   filesystem permissions, not TCP — there is no bind address to configure because there
   is nothing to bind on the network. The MCP Meta-Server's default transport is stdio
   (no network exposure at all, `mcp-meta-spec.md` §2.2); when SSE/HTTP transport is
   enabled, it binds to loopback (`127.0.0.1`, `mcp-meta-spec.md` §9 `sse_host`) by
   default. Binding the Meta-Server to any non-loopback address requires both an explicit
   operator opt-in and a configured authentication credential — if no credential is
   configured, the server MUST refuse to bind to a non-loopback address at all rather than
   serve unauthenticated. Missing credentials narrow what the server will do; they never
   widen it. Wildcard CORS (`Access-Control-Allow-Origin: *`) is prohibited on any
   network-exposed transport. (Counter-example this rules out: the lampOS security audit
   surveyed in wq#4209's `lampOS-evaluation.md` found lampOS's own HAL bound to `0.0.0.0`,
   wildcard CORS, and authentication that silently disabled itself when no key was
   configured — the fail-open shape this item exists to prevent in Limpet.)

### 9.5 What This Enables

With the energize-confirm gate in place, the exploit database (wq#3921) is safe to build
and integrate. The agent can:
- Look up known exploits autonomously (READ)
- Tell the user "this MediaTek MT8167 has a known BROM exploit via mtkclient" (READ)
- Recommend a flash dump procedure (READ)
- Execute the flash dump autonomously (READ)
- Recommend writing modified firmware back (READ — the recommendation)
- **Wait for user CONFIRM before actually writing** (DESTRUCTIVE — gated)

The data is not the threat. Autonomous destructive action is the threat. The gate
separates knowledge from action.

### 9.6 Reconciling §9.2a and §9.4 item 2 (wq#4227)

§9.2a's "a missing or ambiguous classification defaults to `energizing`" and §9.4 item
2's "missing action_class defaults to `destructive`" are not in tension — they are
fail-closed defaults on two different, independent axes, not two rules racing to
classify the same thing:

- §9.2a's axis is **capability classification**, declared on the HAL/hardware-plugin
  manifest (`read_only` vs. `energizing`): what a piece of hardware *can* do.
- §9.2b/§9.4's axis is **action classification**, declared on the MCP tool/wrapper
  manifest (`read` vs. `destructive`, plus `caution`): what a given *tool call* does.

Both default to the more restrictive, confirm-gated option when unspecified, and
`plugin-spec.md` §6.5 already states the rule for combining them: "the stricter of
(capability class, verb class) wins" — a verb may run autonomously only if every
capability it exercises is `read_only`, and a verb touching an `energizing` capability
stays confirm-gated regardless of its own `action_class`. The vocabulary only ever
raises a verb's floor; it never waives the confirm defined in this section.
