# Desktop layout

Limpet runs an XFCE session, but it is not a general-purpose desktop — it is a thin
surface over the agent and the tools. When you log in, the screen fills with **two
stacked `xfce4-terminal` windows**.

| Position | Runs | What it's for |
|---|---|---|
| **Top** | the `limpet-agent` TUI | Agent chat, the boot greeting, `/tools`, and the Target Device Wizard |
| **Bottom** | a plain login shell | `limpet-tools`, `limpet-workspace`, and launching GUI tools (e.g. `ghidra`) by name |

Together the two windows fill the display. There is no custom start-bar panel to
learn.

## The status line

The agent TUI carries a one-line status area that reads the same live data the rest
of the system does. It surfaces, each as a short label:

- **Active workspace** — which investigation you are acting in.
- **Connected hardware** — the probes and targets the HAL currently sees.
- **Tool health** — a rolling `n/n✓` from the registry's health checks.

When a tool's health check fails, the count drops (for example `Tools: 43/44✓`) and
the agent tells you which one, so a missing dependency is visible rather than silent.

## Running GUI tools

Heavyweight graphical tools — Ghidra, for instance — launch from the bottom terminal
by name. They open as ordinary XFCE windows on top of the two terminals. The agent
can also launch them for you as part of a workflow; either way they run against the
current [workspace](workspaces.md).

!!! info "Why terminals, not a dashboard"
    Limpet's normal operating mode is headless over SSH — an appliance sitting in a
    workshop, driven from a laptop. The two-terminal desktop is deliberately a mirror
    of that SSH experience, not a separate GUI with its own logic. Anything you can do
    at the desktop you can do over SSH, and vice versa.

## Settings

System and Limpet settings are reached with `limpet-settings` from either terminal
(and through the XFCE settings manager for display, keyboard, and network). Settings
is where you point the agent at a different local model or an external LLM gateway
once the box has a network — it is an operator choice, never a first-boot
requirement.
