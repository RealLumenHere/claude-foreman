# Agent chat

The agent is where you do the work. You describe what you want in plain language, and
the agent drives the tools — it decides what to run, shows you each call and its
result, and asks before anything irreversible. It is one agent whether you reach it
through the top terminal's TUI or over SSH; the interface is a **view onto an agent
session**, not a second brain.

## Talking to it

Type a goal, not a command:

> *"I plugged in a TP-Link router over its UART header. Dump the flash and tell me
> what bootloader it uses."*

The agent breaks that into steps, picks tools (here, likely a flash read followed by
`binwalk`/`unblob` and a bootloader string scan), and runs them one at a time,
narrating as it goes. You can interrupt, redirect, or ask it to explain any step.

## What you see

For each action the agent takes, the chat shows:

- **The tool call** — which tool, with which arguments.
- **A bounded preview of the result** — never megabytes inline. Large output (a
  flash dump, a decompilation) is written into the workspace and shown *by
  reference*, with a short preview and the path.
- **Trust state** — if a tool needs a higher trust tier than is active, the agent
  surfaces a structured "promotion required" message. It can *propose* a promotion
  but cannot grant one itself; you decide.

!!! warning "The agent never self-promotes or self-approves"
    Every tool call passes the meta-server's trust gate first. The chat will not offer
    a one-click "enable and run" that routes around that gate, and it never prints a
    value that looks like a credential. Destructive steps (flashing, powering a probe)
    require an explicit confirm, and a low-confidence device identification will not
    gate one without you saying so.

## Slash commands

A few commands are typed directly rather than asked in prose:

- `/tools` — list the tools currently available and their health.
- the **Target Device Wizard** — start guided onboarding for whatever is attached
  (see [Target Device Wizard](target-wizard.md)).

## Running without a model

If no local LLM is available — you have not loaded a model, or the gateway is down —
the agent still starts. It tells you it is *"running without AI assistance"* and you
drive the tools yourself from the shell using the [Tool Reference](../tool-reference.md).
Plug in a model or point at a gateway in Settings and the AI layer comes back without
a reboot.
