# Target Device Wizard

The Target Device Wizard is the front door to an investigation. You plug in a device
(or point Limpet at an image on disk), and the wizard carries you from "something is
attached" to "a workspace is open and the first analysis step has run" — without
needing you to already know what the device is.

## Three ways to start

The wizard is one engine reachable three ways; all three run the same state machine:

| You do this | Mode | How it starts |
|---|---|---|
| Plug in a USB device | reactive | The HAL sees the new device and *offers* onboarding — a toast in the GUI, a line in `target list --pending` headless |
| Run **New Target** | interactive | The GUI button, or `limpet target new` / the `target_new` MCP tool |
| Supply an image or dump | interactive | `limpet target new --artifact <path>` — no physical device; it skips the hardware steps and starts on the file |

!!! note "Hotplug is an offer, not an auto-run"
    Plugging a device in never creates a workspace or powers anything on its own. It
    surfaces a *pending* onboarding that you choose to accept. Nothing energizes the
    device until you say so.

## What it does, step by step

1. **Read the inventory.** The wizard does not re-scan hardware itself — it reads the
   HAL inventory, the single source of truth for what is attached.
2. **Identify the device.** It matches USB vendor/product IDs against the
   [device profile database](../device-profiles.md). A VID:PID hit is high
   confidence. When there is no match, it can fall back to the vision pipeline
   (photograph the chip markings → OCR/VLM → part lookup).
3. **Show its confidence and evidence.** Every identification carries a confidence
   level *and* the evidence behind it. A VID:PID match and an OCR guess on a chip
   marking are not treated the same.
4. **Open a workspace.** It creates an isolated [workspace](workspaces.md) for this
   target, applying artifact hygiene from the first moment: the device image or dump
   lives under `target/`, tools are run against it by path, and nothing is executed
   from inside the artifact directory.
5. **Run the first step.** It recommends and (with your go-ahead) runs the first
   analysis action — for a flash chip, a read; for a firmware image, extraction.

## The confidence gate

No irreversible step — flashing, powering a probe — runs on a **low-confidence**
identification without an explicit confirm from you. This is deliberate: a wrong
guess that energizes the wrong pins can brick a board. When the wizard is sure
(a clean VID:PID match), it proceeds; when it is guessing, it asks first and shows you
why it is unsure.

## Headless use

Everything above works over SSH. The wizard is a headless state machine with a thin
GUI on top — the GUI holds no logic the command line lacks:

```sh
limpet target new                      # start onboarding for attached hardware
limpet target new --artifact fw.bin    # start from a dropped image
limpet target list --pending           # see devices awaiting onboarding
```
