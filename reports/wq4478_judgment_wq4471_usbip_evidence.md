# wq#4478 escalated judgment: wq#4471 (USB/IP for remote RE over Tailscale, research-only)

Judge: opus-5@high, judgment only (no patch). Date: 2026-10-23.
Subject: `/home/lumen/foreman/research/WQ4471_USBIP_REMOTE_RE_SHARE.md` (187 lines), commit 85c162d2
(`docs(research): wq4471 USB/IP for remote RE over Tailscale`), authored by claude-sonnet-5.

## Claim under judgment

The verifier's two contradictions, stated so they can be proven false:

> **C1 (MeshHold, §2 L58-65, rec #1 L157-160).** The doc presents MeshHold as the second of "two commercial
> products ... a USB-over-IP hub you attach over Tailscale", and rec #1 counts it as independent validation of
> "Tailscale-wraps-the-missing-auth ... by two commercial products built for this exact scenario". MeshHold is
> actually a self-hosted mesh VPN that competes with Tailscale, and its USB passthrough carries USB/IP port 3240
> over MeshHold's own mesh, not over Tailscale.
>
> **C2 (Phantom-Sense, §3 L78-82, reused in §4 L111).** The doc cites Phantom-Sense's "USB/IP over LAN adds
> ~1–3 ms" as "the same LAN-vs-local comparison" and as "the USB/IP hop itself". Phantom-Sense's transport is
> VirtualHere, a proprietary USB-over-network product, not the Linux in-kernel `usbip-host`/`vhci-hcd` stack that
> §1 describes and the fleet would deploy.

C1 is false if MeshHold documents running its USB/IP forward over Tailscale, or if it is a Tailscale add-on
rather than a separate mesh VPN. C2 is false if Phantom-Sense uses Linux kernel usbip, at least on the importer
side, or if the figure comes from a kernel-usbip measurement.

## Evidence

All of it comes from `/home/lumen/bin/fleet-search` (SearXNG, google-cse engine; brave, ddg, and startpage were
CAPTCHA'd or rate-limited on every call). I made seven calls. WebFetch and WebSearch were not used.

### MeshHold
- `web "meshhold USB device passthrough usbip 3240"`: `https://meshhold.com/docs/` says "USB Device Passthrough.
  Use a USB device plugged into another machine. Export a device on Linux (usbip bind); One TCP forward carries
  USB/IP port 3240; Attach ...". This matches the doc's quote word for word, so the quote itself is accurate.
- `web "meshhold coexists with Tailscale mesh VPN"`: `https://www.meshhold.com/` says "MeshHold — your private
  cloud, on your own hardware ... A self-hosted distributed cloud where every device is a peer. End-to-end
  encrypted storage, chat, calls, **mesh VPN**, media". On `/docs/` the next sentence after the USB passthrough
  entry reads: "**MeshHold vs Tailscale, Headscale, NetBird & Nebula.** Five mesh VPNs ...".
- `web "meshhold docs USB passthrough Tailscale"` returned `/docs/reference/reference-architecture/`, whose
  sibling nav entries are "MeshHold vs Tailscale, Headscale, NetBird & Nebula · What a node costs · ... USB Device
  Passthrough · The Home Automation Bridge · The Developer Platform".
- `web "meshhold \"USB Device Passthrough\" usbip attach mesh port forward"` returned only the same `/docs/`
  snippet. Across four MeshHold-targeted queries, **no** result shows MeshHold using Tailscale as the transport
  for USB/IP. Every indexed mention of Tailscale on meshhold.com is a competitive comparison, where MeshHold is
  presented as one of "five mesh VPNs".

### Phantom-Sense
- `web "Phantom-Sense DualSense VirtualHere Steam Deck USB/IP latency"`: the repo README says "forwards a
  DualSense controller from a Steam Deck to a Windows PC using USB/IP. Your PC sees a real, ...".
- `web "\"PhantomSense\" VirtualHere USB/IP \"1-3 ms\" ..."`: the same README says "USB/IP over LAN adds ~1–3 ms.
  Auto-start on boot, Systemd service runs in ...". The doc's quote is accurate.
- `web "Phantom-Sense \"VirtualHere\" DualSense Steam Deck PC pipeline"`: the same README says "Power on the
  Steam Deck. **VirtualHere starts automatically.** · Plug in the DualSense. **VirtualHere Client on the PC
  auto-attaches it.** · DSX detects the controller."
- `github "AarveeGill/Phantom-Sense"`: `github 401 — no usable token`. This is the known standing condition and
  is not re-logged. Because of it I could not see whether the 1–3 ms is measured or simply asserted. The snippet
  context, a feature-list bullet sitting next to "Auto-start on boot", reads like a README assertion rather
  than a benchmark.
- In this project "USB/IP" is used as a generic phrase. The actual stack is VirtualHere server on the Deck and
  VirtualHere Client on Windows. VirtualHere is a proprietary userspace server with its own client driver and
  its own wire protocol. Linux `usbip-host`/`vhci-hcd` is not used on either end, and Windows cannot run
  `vhci-hcd` anyway. The device is an HID gamepad on interrupt endpoints, which is not the bulk-transfer
  flashing case.

### Not resolved
- `web "vbus.dev VBUS-04 usbip attach linux"` returned nothing relevant. The verifier confirmed VBUS-04's
  tagline, but whether it speaks the standard Linux USB/IP protocol is **unverified**. "Flash with the tools you
  already use" suggests that it does, but nothing confirms it. This does not change the verdict. It is listed
  here because, after C1, VBUS-04 is the only "over Tailscale" product citation left.

## Verdict

**CONTRADICT, low severity.** Both findings are real citation-accuracy defects. Neither changes the doc's
recommendation, and neither weakens the pre-ship checks (a)-(d) that actually gate the follow-up.

## Reasoning

1. **C1 is a real mischaracterization, but the substance largely survives.** The doc quotes MeshHold correctly
   and then labels it wrongly. MeshHold is a self-hosted mesh VPN marketed against Tailscale, and its "one TCP
   forward carries USB/IP port 3240" goes over its own mesh. So "a USB-over-IP hub you attach over Tailscale"
   is wrong for MeshHold on two counts: it is not a hub, and it does not use Tailscale. Rec #1's "independently
   validated by two commercial products built for this exact scenario" also overcounts. Correctly read, though,
   MeshHold still supports the claim that matters to the recommendation: shipping products run kernel `usbip
   bind` over an encrypted, device-authenticated WireGuard-style mesh as their answer to USB/IP's missing
   transport security. That is the same architecture with a different mesh vendor. The evidence for the
   Tailscale-specific pattern is now one product (VBUS-04, whose usbip compatibility is unverified) plus the
   startergo gist (§2 L53-57), which names Tailscale explicitly. That is thinner than the doc claims, but still
   enough for a research-grade "workable" call. The defect is a false statement of fact presented as
   corroboration, so it is worth correcting. It is not load-bearing.

2. **C2 is an implementation mismatch. As an order-of-magnitude analogy it is defensible, but the doc calls it
   more than that.** The doc says the figure is for "the same LAN-vs-local comparison" and for "the USB/IP hop
   itself", which implies it describes the protocol under study. It describes VirtualHere, it is probably an
   unbenchmarked README claim, and it covers an HID interrupt-endpoint workload. Three things make it not
   badly misleading. First, any USB-over-TCP-on-LAN design pays roughly a LAN RTT plus per-URB processing, so
   "low single-digit ms" is the right magnitude for both stacks, and an in-kernel usbip path is unlikely to be
   slower than a userspace one. Second, §3 only draws the conclusion that a few ms is not noticeable for
   picocom or `adb shell`, and that conclusion holds at any plausible figure up to roughly tens of ms. Third,
   the doc already labels the number "third-party/inferred, not fleet-measured" (L181-183) and marks §3 as
   "workable in the common case, not measured safe" (L86-87).
   The weaker point is §4 L111, which reuses "§3's few-ms figure" as the per-round-trip cost in the BootROM
   timing-window argument. In a chatty handshake, per-transaction latency adds up over many command/ack round
   trips. An HID streaming figure from a different stack is not a per-transaction bulk RTT for kernel usbip.
   §4 is already marked "a researched inference ... not a benchmarked result" and gated on a real flash attempt
   (pre-ship check (c)), so the existing hedge contains the risk. It does not make the citation accurate.

3. **Why CONTRADICT rather than AFFIRM, given that the doc hedges.** The hedges cover the *uncertainty of the
   numbers*. They do not cover *misidentifying what the sources are*. "Third-party/inferred" tells a reader the
   figure is not fleet-measured. It does not tell them the figure comes from a different USB-over-IP
   implementation, or that a "Tailscale" product is in fact a Tailscale competitor. Research docs in this repo
   get cited downstream (this one cites WQ3835 three times), so a mislabeled source is likely to be repeated.
   The verifier's reading is correct on both points. The only overreach would be treating either defect as
   undermining the recommendation, and neither does: rec #1's "workable" rests mainly on the protocol-class
   argument (bulk/control, not isochronous, §4) and on checks (a)-(d), not on these two citations.

## Recommendation

- **Reopen wq#4471 for a small errata edit only. Do not change the recommendation and do not withdraw the
  doc.** The scope is about four sentences. No new research is required.
- **§2 L58-65, replace the MeshHold half of the bullet** so it no longer says "two commercial products ... you
  attach over Tailscale". Suggested text:
  > One small commercial product targets exactly Frank's use case — VBUS-04, a USB-over-IP hub "you attach
  > remotely over Tailscale and flash with the tools you already use" (https://vbus.dev/; whether it speaks
  > stock Linux USB/IP was not confirmed). Separately, MeshHold — a self-hosted mesh VPN positioned as a
  > Tailscale *alternative*, not a Tailscale add-on — documents the same architecture over its own mesh:
  > "Export a device on Linux (usbip bind); one TCP forward carries USB/IP port 3240" (https://meshhold.com/docs/).
  > Together they show "kernel usbip behind an encrypted device-authenticated mesh" is a shipped pattern.
- **Rec #1 L158-160:** change "independently validated by two commercial products built for this exact
  scenario (§2)" to "consistent with one Tailscale-specific commercial product, one same-architecture mesh-VPN
  product, and the Tailscale usbip walkthrough (§2)".
- **§3 L78-82:** change "a concrete third-party data point for the same LAN-vs-local comparison" / "That figure
  is for the USB/IP hop itself" to say what the source actually is. Suggested text:
  > the closest third-party figure found is from a DualSense-forwarding project that uses **VirtualHere** (a
  > proprietary userspace USB-over-IP product, not the in-kernel `usbip-host`/`vhci-hcd` stack in §1), which
  > states "USB/IP over LAN adds ~1–3 ms" for an HID controller (https://github.com/AarveeGill/Phantom-Sense;
  > README claim, method not visible — github backend 401). Treat it as an order-of-magnitude analogy for
  > USB-over-TCP-on-LAN, not a kernel-usbip measurement.
- **§4 L111:** qualify "(§3's few-ms figure)" as "(§3's order-of-magnitude analogy, not a per-transaction
  kernel-usbip measurement; the BootROM handshake's many round trips compound it)". This makes the existing
  "validate with one real flash" gate the clearly stated reason the inference cannot be trusted alone.
- **Open Items (L181-183):** add one line saying that no kernel-usbip latency figure was found in prior art, and
  that the follow-up's acceptance evidence should include a per-round-trip measurement (for example a timed
  `usbip attach` plus `adb shell echo` loop, and `tailscale ping` RTT), not only a pass/fail flash.
- Pre-ship checks (a)-(d), rec #3, and rec #4 are sound and stay as they are. If the fleet decides errata on
  research-only docs are not worth reopening a ticket, the minimum acceptable fallback is to fold the C1/C2
  corrections into the follow-up implementation ticket's opening notes, so the mislabeled citations are not
  repeated as acceptance rationale.

## Note on report location

This file is in the wq4478 worktree's `reports/` directory, as requested, and is uncommitted. The subject
research doc was not edited.
