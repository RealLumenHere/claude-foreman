# Limpet

**Limpet is a universal reverse-engineering agent appliance.** You plug a device —
a router, an IoT gadget, a dev board, a phone in download mode, a SPI flash chip on
a clip — into a small box, and point an on-device AI agent at it. The agent
identifies what you attached, picks the right tool, and walks the investigation with
you, from "something is connected" to "here is the firmware, here is what it does."

Everything runs locally. The tools, the hardware detection, and at least one local
LLM all live on the appliance; install and first boot never need the network.

## What's here

<div class="grid cards" markdown>

- :material-rocket-launch: **[Getting Started](getting-started.md)**
  Download the ISO, write it to a USB drive, boot, and finish the first-boot setup.

- :material-book-open-variant: **[User Guide](user-guide/desktop.md)**
  The desktop, the agent chat, the Target Device Wizard, and workspaces.

- :material-tools: **[Tool Reference](tool-reference.md)**
  Every tool the agent can drive, generated from the appliance's own catalog.

- :material-puzzle: **[Plugin Development](plugin-development.md)**
  Write a `limpet-plugin.toml` to add a tool, a device, or an MCP wrapper.

- :material-chip: **[Device Profiles](device-profiles.md)**
  Teach Limpet to recognize a new device from its USB identifiers.

- :material-sitemap: **[Architecture](architecture.md)**
  How the meta-server, HAL, registry, and agent core fit together.

</div>

## How it fits together, in one breath

A **hardware abstraction layer (HAL)** watches the USB bus and keeps an inventory of
what is attached. An **MCP meta-server** exposes every installed tool behind one
endpoint, enforcing a trust gate on each call. A **registry** validates and
health-checks the tools and plugins. The **agent core** is the single client that
talks to the meta-server and the local LLM, and the **chat interface** is your view
onto its work. The **Target Device Wizard** ties it together: it reads the HAL
inventory, identifies the device, opens an isolated **workspace**, and runs the first
analysis step.

Read the [Architecture](architecture.md) page for the full picture.

!!! note "Design status"
    Limpet is built in phases. Some components described in these docs are shipping
    today (the tool registry, catalog, plugin system, HAL device database, and the
    ISO builder); others are specified and in build. Each page flags which is which
    so you always know what you can run right now versus what is on the roadmap.
