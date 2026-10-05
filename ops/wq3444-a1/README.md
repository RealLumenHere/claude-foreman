# wq#3444 — Station P1 non-Pro #3 (Set) bring-up: IP-KVM host (Magewell + PiKVM)

## Status at prep time (2026-10-05)

**BLOCKED ON PHYSICAL ARRIVAL.** No `set` node in `lumen_state` and none on the
tailnet (checked this session via `lumen_state.py current`). The ticket's own text
already records the block ("arriving in days") — this is a shipment in transit, not a
Frank decision or purchase waiting to be queued, so there is no new physical ticket to
file (unlike wq#3319, which needed Frank to *decide and execute a purchase*). Everything
below is what ships without the hardware existing yet.

## What's already decided (from the ticket)

- **Role:** KVM-over-IP host — Magewell USB Capture HDMI Gen 2 (video capture) +
  PiKVM-style software stack (video streaming + keyboard/mouse emulation) for remote
  console access to fleet nodes that otherwise have none.
- **Name:** Set.
- **This is the 3rd Station P1 non-Pro unit.** `lumen_state` already has two in service
  as independent fleet monitors: `station-p1.node_provisioning` (asfar@100.108.107.111)
  and `station-p1-1.node_provisioning` (borto2an@100.76.194.58), both "StationPC P1,
  Armbian 26.11 trixie, RK3399". Set is presumed the same SKU bought in the same batch —
  **verify on arrival, don't assume** (could be a different board revision).

## Reconciling with the 2026-09-19 "not buying IP-KVM" decision

`lumen_state fleet-hardware.incoming` (since 2026-09-19) reads: "Frank buying more smart
switches 2026-09-19 for fleet nodes lacking remote power control ... NOT buying IP-KVM
(wq#391 retired). Smart-switch-per-node is the remote-power-recovery standard." That
decision retired wq#391's GL-RM1 *appliance* purchase for remote **power-cycle
recovery**. This ticket is a different thing: a DIY KVM-over-IP build (capture dongle +
software) on hardware arriving for other reasons, aimed at remote **console/video**
access, not power-cycle recovery. Not a contradiction — flagged explicitly so the two
decisions aren't conflated later by someone skimming `fleet-hardware.incoming`.

## Prior art (fleet-search, this session — mandatory first step, not skipped)

Three queries run via `/home/lumen/bin/fleet-search web`: PiKVM+Magewell setup, PiKVM
supported capture devices, PiKVM on non-Raspberry-Pi/Armbian/RK3399. Search engine
coverage was thin this run (Brave rate-limited, DuckDuckGo/Startpage CAPTCHA'd), but
what resolved is enough to fix the architecture:

- PiKVM's official OS/`kvmd` images are **Raspberry Pi (CM4/4)-specific**, keyed to the
  RPi's `dwc2` USB-OTG gadget controller for HID (keyboard/mouse) emulation
  (`github.com/pikvm/ustreamer` setup docs reference `/boot/firmware/usercfg.txt`,
  an RPi-only boot config path).
- The Magewell USB Capture HDMI Gen 2 needs **no vendor driver**: it's a native USB 3.0
  UVC/UAC device ("turns any HDMI 1.4a feed into a native USB 3.0 UVC/UAC source... no
  drivers" — ForeFront Imaging / avgear product listings, cross-checked against two
  independent retailers). It will show up as a plain `/dev/video*` node via the stock
  `uvcvideo` driver on any Linux board, PiKVM-specific or not. **Needs USB 3.0** — note
  for the port-survey step below.

**Conclusion for this board:** flashing the stock PiKVM OS image is not an option here
(it targets RPi hardware; Set is RK3399/Armbian per the station-p1 precedent). The
realistic path is installing `ustreamer` + `kvmd` manually on top of Set's existing
Armbian base — video capture (Magewell → `uvcvideo` → `ustreamer`) will work on any
board, but the HID half only works if Set's board exposes a USB-OTG-gadget-capable
port. **That is unverified and is the single open risk this runbook cannot close before
the hardware exists** — RK3399 boards vary on whether any port supports OTG/gadget mode.

## On-arrival checklist (run as a follow-up ticket with real ssh access)

1. Bring up like the other two non-Pro units: `fleet-provision-ssh`, static LAN IP,
   ingest to `lumen_state` with hostname `set` (same shape as the
   `station-p1`/`station-p1-1.node_provisioning` keys). `/home/lumen/fleet-units/
   p1-monitor/` is the source cited for the existing two units' base image — it's
   outside this worktree's `--add-dir` set, so confirm its contents from a session with
   that access rather than guessing its layout here.
2. Verify hardware, don't assume (same discipline as Neith's bring-up,
   `ops/wq3296-neith-bringup/README.md:29-33`): confirm SoC/board model, RAM, and
   **specifically which USB ports exist and whether any is OTG-gadget-capable** — the
   load-bearing unknown for the HID half of this role.
3. Plug in the Magewell USB Capture HDMI Gen 2 into a USB 3.0 port, confirm it
   enumerates: `lsusb` (Magewell vendor ID) and `v4l2-ctl --list-devices` (expect a
   `/dev/video*` node via the stock `uvcvideo` driver — no vendor driver install
   expected per the prior-art check above).
4. If an OTG-capable port exists: package/build `ustreamer` + `kvmd` for Armbian
   (Debian-based) rather than assuming PiKVM's Arch-Linux-ARM packages apply directly —
   this is new porting work; scope it as its own ticket once the board/port facts are in
   hand rather than guessing a package list here.
5. If no OTG-capable port exists: the HID (keyboard/mouse) half of "PiKVM" isn't
   buildable on this board as shipped; fall back to video-only capture (`ustreamer` +
   Magewell, read-only remote console) and flag the keyboard/mouse gap back to Frank as
   a hardware decision (e.g. a USB HID-emulation dongle), not a software one.
6. Record the outcome in `lumen_state` (`set.role`, `set.node_provisioning`,
   `set.hardware`) following the `station-p1-1.node_provisioning` entry as the format
   model.

## Why this ticket has no acceptance probe

`[NO-PROBE: physical: hardware not yet shipped/arrived]` — there is no `set` node to
probe; nothing on this fleet can assert against hardware that doesn't exist yet. AC1 is
this README: the real, reviewable artifact this session produced given the block.
