# Getting Started

This page takes you from a blank USB drive to a running Limpet appliance: download,
write, boot, install, and first-boot setup. The whole path works with **no network
connection** — the tools, the hardware database, and a local model are all on the
ISO.

## 1. Download the ISO

Limpet ships as a single hybrid ISO, `limpet-<version>-amd64.iso`, that boots on
both BIOS and UEFI machines. From the download page, grab:

- `limpet-<version>-amd64.iso` — the image.
- `SHA256SUMS` — the checksum file.
- `SHA256SUMS.asc` — a signature over the checksums, made with the Limpet release key.

The ISO is large (it carries the full tool set and a local LLM), so the download page
states the exact size up front.

## 2. Verify the download

Always verify before you write. The checksum proves the image arrived intact; the
signature proves it came from the Limpet release key.

=== "Linux"

    ```sh
    sha256sum -c SHA256SUMS
    gpg --verify SHA256SUMS.asc SHA256SUMS
    ```

=== "macOS"

    ```sh
    shasum -a 256 limpet-<version>-amd64.iso
    # compare against the value in SHA256SUMS
    gpg --verify SHA256SUMS.asc SHA256SUMS
    ```

=== "Windows"

    ```powershell
    certutil -hashfile limpet-<version>-amd64.iso SHA256
    # compare against the value in SHA256SUMS
    ```

!!! warning "Verify the key fingerprint out of band"
    The release key fingerprint is printed on the download page. Confirm it against a
    second source (a keyserver, or this repository) — not only from the download page
    itself — before you trust a signature it made.

## 3. Write it to a USB drive

A **16 GB or larger** USB drive is recommended. Writing erases everything on the
drive.

**balenaEtcher is the recommended writer on every OS** — it runs on Windows, macOS,
and Linux, verifies the write, and will not let you pick your system disk by mistake:

1. Open Etcher → **Flash from file** → select the ISO.
2. **Select target** → pick the USB drive (check the size and label).
3. **Flash**, and let it verify.

Any raw-image writer also works: Rufus in **DD Image** mode, Ventoy, GNOME Disks, or
`dd` on Linux. If a writer offers an "ISO vs DD/raw" choice, pick **DD / raw** — the
image is a ready-to-boot hybrid, not a file to unpack.

## 4. Boot the appliance

1. Insert the USB drive into the target machine (a small-form-factor PC works well).
2. Open the firmware boot menu (commonly **F12**, **F10**, **Esc**, or **Del** at
   power-on) and choose the USB device.
3. Limpet boots to a live session. From here you can try it live, or run the
   installer to put it on the internal disk.

!!! tip "Headless by design"
    Limpet is built to be operated over SSH — plug a device into its USB ports and
    drive the agent from your laptop. A screen and keyboard are only needed for the
    install and first-boot steps; after that, everything works headless.

## 5. Install (optional but recommended)

Running from USB is fine for a quick look, but installing to disk is faster and
persistent. Launch the installer from the live session and follow the standard
Xubuntu flow (language, disk, user). Install finishes **without touching the
network** — all packages come from the ISO.

## 6. First boot

On the first boot of an installed system, Limpet brings its stack up in order and
gates each step on a health check rather than a timer, so a slow model load never
fails the boot:

1. **HAL** enumerates the USB bus and writes the hardware inventory.
2. **MCP meta-server** comes up with every tool server disabled, then enables only
   the tools your installed set declares.
3. **Local LLM gateway** confirms the model bundled on the ISO (no network).
4. **Agent core** connects to the meta-server and the model, then runs a tool-health
   pass and reports `n/n✓`.

A non-essential tool that fails is reported as *degraded*, never fatal — and the
agent still comes up even if no model is available, so you can plug one in later.

## 7. First login

The session opens **two stacked terminals**:

- **Top:** the Limpet agent TUI. It greets you, names the hardware it can already see
  ("your probes: ST-Link V2, CH341A"), and reports stack health inline.
- **Bottom:** a plain shell for running `limpet-tools`, `limpet-workspace`, and
  launching GUI tools such as Ghidra by name.

You are ready. Plug in a device and the [Target Device Wizard](user-guide/target-wizard.md)
offers to onboard it, or type a question to the agent. The [User Guide](user-guide/desktop.md)
covers each surface in turn.
