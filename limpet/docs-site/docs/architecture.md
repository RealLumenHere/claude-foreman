# Architecture

This page is for contributors — how Limpet is put together, and the constraints that
shaped each decision. It is a map, not the territory: the authoritative designs live
in the repository under `design/` (notably `design/wq3806_limpet_architecture.md` and
`design/wq3810_limpet_plugin_system.md`) and in the `docs/*-spec.md` files, and those
win where this summary is thinner.

## The shape of the box

Limpet targets a small-form-factor PC (reference hardware: an M70q Gen 5 — i5-14500,
32 GB DDR5, Intel iGPU, no discrete GPU). That single fact drives much of the design:
a 9–14B resident model plus Ghidra and QEMU backends already sit near the memory
ceiling, so every component is built to be lean. The chat frontend is a thin renderer,
not a second heavyweight service; the agent is one process, not a swarm.

## The five components

```
          ┌─────────────────────────────────────────────┐
          │                 Agent core                   │
          │  single MCP client · owns the tool loop ·    │
          │  trust boundary · local-LLM inference        │
          └───────────────┬───────────────┬─────────────┘
                          │               │
              ┌───────────▼──────┐   ┌────▼──────────┐
              │  MCP meta-server │   │  LLM gateway  │
              │  one endpoint    │   │  (litellm)    │
              │  over all tools; │   └───────────────┘
              │  trust gate      │
              └───────┬──────────┘
                      │ reads the manifest
          ┌───────────▼───────────┐     ┌───────────────────┐
          │       Registry        │◄────┤   HAL (udev)      │
          │  validates + health-  │     │  USB inventory ·  │
          │  checks plugins/tools │     │  hotplug events   │
          └───────────────────────┘     └───────────────────┘
```

### HAL — hardware abstraction layer

Watches the USB bus through udev, keeps the inventory of attached probes and target
devices, and emits hotplug events. It is the **single source of truth** for what is
connected — the Target Device Wizard reads this inventory rather than opening `/dev`
itself. It enriches raw USB identifiers against the
[device profile database](device-profiles.md).

### MCP meta-server

One MCP endpoint in front of every installed tool. It ships with every child tool
server **disabled** and enables only what the installed tool set declares. Every tool
call passes its **trust gate**: a `community`-tier call is refused with a structured
"promotion required" error, destructive calls require an energize confirm, and tool
output is size-capped and written to the workspace by reference so nothing inlines
megabytes or leaks a credential.

### Registry

Validates each `limpet-plugin.toml`, resolves dependencies and conflicts, runs the
health checks, and publishes the unified view the meta-server and the status line
read. The registry daemon is what reconciles the manifest the meta-server enables tools
from. See [Plugin Development](plugin-development.md).

### LLM gateway

A local gateway (litellm) in front of the on-device model. The agent reads its live
`/v1/models`, so pointing at an external gateway or pulling another model is an
operator choice once the box has a network — never a first-boot requirement. The
gateway is **optional**: the agent degrades gracefully and comes up even if no model
is present.

### Agent core

The single MCP client to the meta-server. It owns the tool loop, the trust boundary,
and inference. The [chat interface](user-guide/agent-chat.md) is a view onto an agent
session — it does not open its own MCP connections, call tools directly, or make its
own trust decisions.

## Cross-cutting constraints

These hold everywhere, and a change that violates one is almost certainly wrong:

- **Headless first.** The appliance has no attached console in normal use; it is driven
  over SSH/MCP. Every surface works headless, with the GUI as a thin opt-in layer that
  holds no logic the command line lacks.
- **Untrusted targets.** Devices and images are hostile until proven otherwise.
  [Workspaces](user-guide/workspaces.md) enforce artifact hygiene from creation: the
  artifact lives under `target/`, tools run against it by path, nothing executes from
  inside it.
- **One source of truth per concern.** Hardware detection belongs to the HAL; tool
  availability to the registry; trust to the meta-server. Components read those
  sources; they do not re-implement them.
- **Lean by necessity.** The memory ceiling is real and already contended. New
  components justify their resident footprint.

## Where to read next

- `design/wq3806_limpet_architecture.md` — the full architecture (meta-server, HAL,
  workspace isolation, agent core).
- `design/wq3810_limpet_plugin_system.md` — the plugin lifecycle.
- `docs/*-spec.md` — per-surface specs (desktop, agent chat, target wizard, settings,
  deployment).
- The [Tool Reference](tool-reference.md) and its generator,
  `limpet/docs-site/gen_tool_reference.py`.
