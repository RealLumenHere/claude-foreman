# wq#4471: USB/IP for remote RE — share Maksoor's USB devices with Lumen agents over Tailscale

Goal: answer whether running `usbipd`/`usbip-host` on Maksoor (the RE bench, tailnet
`100.81.204.74`, `TOOL_REGISTRY.md:9`) and attaching from Lumen (tailnet `100.117.66.102`
per this run's own SSH-wrap instinct) would let agents run `mtkclient`/`edl`
(`TOOL_REGISTRY.md:24`) and ADB/`picocom` against devices physically plugged into Maksoor,
without SSH-wrapping every command onto Maksoor. Research only — no code changes.

## Prior-art search run (doctrine: search before reasoning)

`fleet-search web/reddit` queries run this session (SearXNG-backed, wq#999): USB/IP +
Tailscale latency, vhci-hcd/usbip-core kernel module requirements, USB/IP flashing
reliability, usbip security/CVE, usbip-utils install, ADB-over-usbip, port 3240/firewall,
serial/picocom latency, flaky-flashing reports. `fleet-search github` for the one Android-
tooling hit was blocked: `github 401 — no usable token` — already-tracked, not re-logged
(matches the standing "no usable github token" condition noted in
`research/WQ3835_AMD_CPU_TIER_RESEARCH.md:26`). Findings below are cited inline by URL
since this is prior-art research, not a council transcript.

## 1. What USB/IP actually is, and what it needs installed

USB/IP is an in-tree Linux kernel feature, not a userspace network protocol re-implementation:
exporter side loads `usbip-host` (binds a local USB device, which then becomes unusable
locally — it's exclusive export, not a mirror), importer side loads `usbip-core` +
`vhci-hcd` (presents a virtual USB host controller that the kernel's normal driver stack
attaches to, so the exported device shows up to userspace exactly like a local one —
`lsusb`, `/dev/ttyUSB0`, `/dev/bus/usb/...` etc. all just work). Userspace control (`usbip`
client, `usbipd` server) ships either as a `usbip-utils` package or, on Debian/Ubuntu,
inside `linux-tools-<kernel-version>`/`linux-tools-generic`
(https://developer.ridgerun.com/wiki/index.php/How_to_setup_and_use_USB/IP,
https://askubuntu.com/questions/1406205/install-usb-ip-on-ubuntu-22-04-on-windows-subsystem-for-linux).

**Kernel module caveat, confirmed by multiple independent reports, not a one-off**: the
modules are only present if the running kernel was built with `CONFIG_USBIP_CORE` /
`CONFIG_USBIP_VHCI_HCD`. Stock desktop/server Ubuntu kernels normally have them, but several
narrower kernels do not and need a rebuild to get them — AWS EC2 Ubuntu
(https://askubuntu.com/questions/1303403/how-to-install-usbip-vhci-hcd-drivers-on-an-aws-ec2-ubuntu-kernel-version),
NVIDIA Jetson Orin's L4T kernel
(https://forums.developer.nvidia.com/t/how-to-install-the-usbip-vhci-hcd-kernel-module-on-jetson-orin-nx-for-5-10-216-tegra/350286),
and WSL2's custom kernel are all documented cases where `modprobe vhci-hcd` fails outright.
**Action before relying on this**: confirm on both Maksoor and Lumen with
`ssh maksoor 'modprobe -n -v usbip_host'` / `ssh lumen@100.117.66.102 'modprobe -n -v vhci-hcd'`
(dry-run, no state change) rather than assuming a workstation kernel always has it — this
ticket did not have shell access to Maksoor to run that check, so it's an open item, not a
researched fact.

## 2. Tailscale compatibility

USB/IP's wire protocol is a bare TCP service on port 3240 with no transport security of its
own — every guide that covers remote (not same-LAN) use independently converges on "put it
behind a VPN, don't expose 3240 publicly," and several name Tailscale by name as the
preferred way to do that:
- A Windows/Tailscale-specific usbip walkthrough: "Both machines must be able to reach each
  other over TCP port 3240 ... unless using Tailscale or the SSH tunnel workaround ... Do not
  expose port 3240 on a public interface ... Tailscale (good fallback when Tailscale is not
  available [sic — SSH tunnel is the fallback]"
  (https://gist.github.com/startergo/8b2f264e1b68d4f354cb92bb00bb723e).
- Two small commercial products exist for exactly Frank's stated use case — a USB-over-IP
  hub you attach over Tailscale specifically to flash/reset boards remotely: VBUS-04
  ("USB-over-IP hub with per-port power switching. Attach any port remotely over Tailscale
  and flash with the tools you already use.", https://vbus.dev/) and MeshHold ("Use a USB
  device plugged into another machine. Export a device on Linux (usbip bind); one TCP
  forward carries USB/IP port 3240.", https://meshhold.com/docs/). Neither is being proposed
  for purchase here — they're cited as independent confirmation that "usbip host behind
  Tailscale, flash remotely" is a solved, shipped pattern, not a novel risk.

Because Tailscale is a device-authenticated, WireGuard-encrypted mesh, running usbipd bound
to the tailnet interface only (not `0.0.0.0`) gets USB/IP's missing transport security for
free: a device must already be in Frank's tailnet ACL to reach port 3240 at all. The one
thing to verify before depending on this for flashing (which is round-trip-latency
sensitive, see §3) is whether Lumen↔Maksoor get a **direct** WireGuard peer connection or
fall back to a DERP relay — `tailscale ping maksoor` from Lumen reports `direct` vs
relayed-via-derp; a relayed path adds a real extra hop's worth of latency on top of
whatever USB/IP itself adds.

## 3. Latency for interactive serial (picocom/ADB shell)

No fleet-specific measurement exists yet (open item, §6), but a concrete third-party data
point for the same LAN-vs-local comparison: a DualSense-over-network project reports
"USB/IP over LAN adds ~1–3 ms" of latency versus a local connection
(https://github.com/AarveeGill/Phantom-Sense). That figure is for the USB/IP hop itself on a
LAN; Tailscale's own tunnel overhead stacks on top of it. For a UART-over-USB (CDC-ACM)
serial console driven by `picocom`, or an interactive `adb shell`, a few ms of added
round-trip latency per USB transaction is very unlikely to be user-noticeable — both
workloads are byte-stream/command-response, not frame-rate-sensitive. This should read as
"workable in the common case," not "measured safe" — it is the single highest-value thing
to verify empirically with a real device plugged into Maksoor before this moves past
research.

## 4. Reliability for flashing (mtkclient/EDL)

USB/IP forwards the full USB transfer-type taxonomy (control, bulk, interrupt,
isochronous) over TCP, but the protocol class that's documented as unreliable over network
transport is specifically **isochronous** (audio/video streaming, which has no retry budget
and depends on fixed-interval delivery) — e.g. "Verify support for isochronous transfers if
it's audio/video" is flagged as the thing to check before trusting any USB-over-network
tool for that traffic class
(https://forum.babelgum.com/t/any-reliable-alternative-to-usb-over-ethernet-solutions/1923).
`mtkclient`/`edl` (`TOOL_REGISTRY.md:24`) and ADB both talk to their devices over **bulk**
transfers for the actual data path — the class USB/IP handles with ordinary TCP
retransmission semantics, not real-time timing guarantees — so the protocol class itself is
not the risk here.

The actual risk for MTK BootROM (Preloader/BROM) and Qualcomm EDL flashing is narrower and
not something this search surfaced a direct report on either way: these handshakes are
**host-timing-sensitive at enumeration** (the device presents its BootROM USB identity for a
short window right at power-on/reset-strap, and the flashing tool must open and start
talking to it inside that window) — this is a property of the device's own BootROM, true
whether the host talking to it is local or over usbip. What usbip changes is that every
command/ack round-trip in the handshake now carries the extra network hop's latency (§3's
few-ms figure) on top of whatever the BootROM's own timeout tolerance is. For a device whose
BootROM window is generous (most MTK targets mtkclient already supports), this should still
land inside tolerance; for anything with a BootROM window that's already a close call on a
direct USB connection, remote-over-usbip is the wrong tool and a physical connection (or
SSH-wrapped local flash on Maksoor) stays the fallback. **This is a researched inference
from how the protocols are layered, not a benchmarked result** — flag it as the top item to
validate with one real flash attempt before trusting it for anything irreversible (e.g. a
bricked BootROM write) rather than assuming success from the architecture alone.

One usbip-specific reliability constraint worth planning around regardless of device class:
**export is exclusive, single-attach**. A device bound by `usbip bind` on Maksoor stops
being usable locally (host and importer can't share it), and only one importer can `usbip
attach` it at a time — fine for "one agent, one flasher," but it means two agents can't
both watch the same UART or both drive the same flasher concurrently without serializing
through something above usbip (a lock, or routing both through one agent).

## 5. Security implications

- **No transport security or authentication in the USB/IP protocol itself** — this is the
  reason every guide above says "don't expose 3240 publicly" and independently converges on
  a VPN/Tailscale wrapper. Scoping `usbipd` to listen only on Maksoor's tailnet interface
  (not `0.0.0.0`) is the mitigation, consistent with how `rea-maksoor` is already reached
  ssh-stdio over the tailnet rather than a public port (`TOOL_REGISTRY.md:79`).
- **Historical kernel-level attack surface, client side**: CVE-2016-3955 — a malicious or
  compromised usbip *exporter* can send a crafted response that overflows a heap buffer in
  the *importer's* kernel (`usbip_recv_xbuff`, `drivers/usb/usbip/usbip_common.c`), causing
  DoS or worse, on kernels before 4.5.3
  (https://nvd.nist.gov/vuln/detail/cve-2016-3955,
  https://pqsec.org/2016/08/19/uboat-CVE-2016-3955.html). The specific bug is a decade old
  and long patched in any current kernel, but the *shape* of the risk is durable and worth
  carrying forward: usbip is a binary protocol parsed in **kernel space** on the attaching
  side, with no sandboxing, so the importer (Lumen, or whichever agent host attaches)
  is trusting Maksoor's kernel-level USB/IP responses the same way it would trust a local
  USB device's firmware. That trust is already implicit in "plug a possibly-untrusted RE
  target into Maksoor and let mtkclient talk to it," so usbip doesn't introduce a new trust
  boundary between Frank's bench and Lumen's fleet — but it does mean an attached virtual
  device should be treated with the same caution as local USB generally gets per instinct 6
  (never expose a master key in a public-facing container) and the fleet's existing
  ADB-scoping concern (`TOOL_REGISTRY.md:95-100`, scoping Maksoor-side ADB access to one
  lane rather than fleet-wide).
- **Confirm current kernel patch level** before depending on this — `uname -r` on both
  Lumen and Maksoor against the 4.5.3 fix line is a one-line sanity check, not done in this
  research pass since no live shell session to Maksoor was part of this ticket's scope.

## What this means for the fleet (recommendation, not yet implemented)

1. **Workable for the stated use case** (mtkclient/EDL/ADB/picocom against Maksoor-attached
   devices from Lumen agents) based on protocol class (bulk/control, not isochronous) and
   the Tailscale-wraps-the-missing-auth pattern independently validated by two commercial
   products built for this exact scenario (§2).
2. **Before shipping it**: (a) confirm `usbip_host`/`vhci-hcd` modules actually load on
   Maksoor's and Lumen's running kernels (§1) — not assumed; (b) confirm `tailscale ping`
   reports a direct peer path, not DERP-relayed (§2); (c) run one real flash against a
   non-critical MTK/EDL target over usbip before trusting it for anything irreversible
   (§4); (d) bind `usbipd` to the tailnet interface only, never `0.0.0.0` (§5).
3. **Scope ADB/device access the same way `TOOL_REGISTRY.md:95-100` already recommends for
   Maksoor's RE toolchain generally** — a usbip-attached device is a new remote-access path
   into Maksoor's physical bench and should get the same per-lane scoping discipline, not
   a fleet-wide open attach.
4. Follow-up ticket (code, not research) should land: Maksoor-side `usbip-host` setup +
   systemd unit, Lumen-side `vhci-hcd` module load + attach helper script, and the empirical
   latency/flash-reliability check from (2) as its acceptance evidence — this doc is
   research-only, same posture `research/WQ3835_AMD_CPU_TIER_RESEARCH.md:223` took for its
   own follow-up.

## Open items

- No live shell access to Maksoor was in this ticket's scope, so §1's module-presence check
  and §5's kernel-patch-level check are both named but not run — first thing the follow-up
  implementation ticket should do.
- §3/§4's latency and flash-reliability numbers are third-party/inferred, not fleet-measured
  — same caveat pattern as `research/WQ3835_AMD_CPU_TIER_RESEARCH.md:216-219` applied to its
  non-measured tok/s figures.
- `fleet-search github` is still dead (401, no usable token) — one Android-tooling USB/IP
  hit from the web search ("Integrate USB/IP Support for Enhanced Remote Hardware...") could
  not be opened via `github` backend; not re-logged since the 401 condition is already a
  known standing issue, not new information from this ticket.
