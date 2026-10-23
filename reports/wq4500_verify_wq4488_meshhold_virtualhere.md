# wq#4500 verify: wq#4488 (errata: wq#4471 MeshHold/VirtualHere citations)

Verifier: claude-sonnet-5@high, fresh instance, same model as author (claude-sonnet-5).
Date: 2026-10-23. PR: http://100.121.17.5:3000/lumen/claude-foreman/pulls/2360 (gitea
PR head `fba8475e`, already squash-landed on main as `1d6778a1` — diffed the two, byte-
identical for the subject file, so main already carries exactly what's under review).

Subject: `research/WQ4471_USBIP_REMOTE_RE_SHARE.md`, 40 lines changed (27+/13-).

## What the PR claims to fix

wq#4488 implements the two contradictions opus-5@high confirmed in
`reports/wq4478_judgment_wq4471_usbip_evidence.md`:

- **C1 (MeshHold)**: the doc had called MeshHold a second "USB-over-IP hub you attach
  over Tailscale," counted as independent validation of the Tailscale-wraps-usbip
  pattern. MeshHold is actually a self-hosted mesh VPN marketed *against* Tailscale,
  whose USB passthrough runs over its own mesh, not Tailscale.
- **C2 (Phantom-Sense/VirtualHere)**: the doc had attributed Phantom-Sense's "~1-3 ms"
  figure to "the USB/IP hop itself," implying a kernel-usbip measurement. Phantom-Sense
  uses VirtualHere, a separate proprietary product, on an HID-gamepad workload.

## Verification method

Re-fetched every cited source live rather than trusting the diff's quotes:

1. `curl https://meshhold.com/docs/` (stripped HTML) — confirms:
   - "🔌 USB Device Passthrough ... Export a device on Linux (usbip bind) · One TCP
     forward carries USB/IP port 3240" — the doc's quote is verbatim accurate.
   - "🕸️ MeshHold vs Tailscale, Headscale, NetBird & Nebula. Five mesh VPNs ordered by
     where the control plane lives" — confirms MeshHold is positioned as a Tailscale
     *competitor* in its own docs nav, not a Tailscale add-on. Supports the PR's claim.
   - "🕸 The Flat Mesh LAN ... coexists with Tailscale" — MeshHold's mesh is its own
     transport, separate from Tailscale (consistent with "coexists," not "rides on").
2. `curl https://raw.githubusercontent.com/AarveeGill/Phantom-Sense/main/README.md` —
   confirms:
   - "Moonlight carries the video. VirtualHere carries the controller." and "VirtualHere
     starts automatically ... VirtualHere Client on the PC auto-attaches it" — the PR's
     quote is verbatim accurate, and the transport is unambiguously VirtualHere, not
     Linux-kernel `usbip-host`/`vhci-hcd`.
   - "Low latency | USB/IP over LAN adds ~1–3 ms" — confirms the number itself, in a
     feature-list table, not a benchmark section — supports the PR's "README claim, not
     a usbip measurement" framing. Workload is a DualSense controller (HID, interrupt
     endpoints), matching the PR's "HID gamepad, not bulk-transfer" caveat.
3. `fleet-search web "VirtualHere proprietary protocol different from Linux usbip
   vhci-hcd kernel"` — independent third party (a GitHub issue on a different project)
   lists "usbip (Linux kernel), usbipd-win, VirtualHere, USB Network Gate" as four
   distinct, non-interchangeable products — corroborates that VirtualHere is not the
   Linux kernel usbip stack, from a source neither the author nor wq#4478 cited.
4. Diffed the full post-PR doc (`git show wq4500-pr2360-review:...md`) for any missed
   instance of the old framing — grepped for `meshhold|two commercial|VirtualHere|
   phantom-sense|usb/ip|tailscale|usbip` across all 201 lines. Exactly one edit site per
   claim (§2 L59-68, rec #1 L169, Open Items L198-201); no stale "two commercial
   products" phrasing survives anywhere else in the file.
5. Cross-checked the PR's actual wording against wq#4478's *suggested* replacement text
   — the PR paraphrases rather than copying verbatim, but preserves every substantive
   correction (MeshHold = alternative not add-on, own-mesh not Tailscale; VirtualHere ≠
   kernel usbip; HID not bulk workload; latency figure now flagged as not fleet- or
   kernel-usbip-measured in Open Items).

## Things checked and found to be scope, not defects

- The PR drops the judgment's suggested VBUS-04 caveat ("whether it speaks stock Linux
  USB/IP was not confirmed"). wq#4478 filed that under "Not resolved," explicitly noted
  it "does not change the verdict," and it's outside C1/C2, which is what wq#4488's
  title scopes to ("correct wq4471 MeshHold/VirtualHere citations"). Not a contradiction.
- Rec #1 now cites only VBUS-04 as validation (dropping MeshHold and the startergo gist
  from that specific sentence), which is narrower than wq#4478's suggested wording but
  not inaccurate — VBUS-04 alone does support the sentence's claim.

## Verdict: CONFIRM

Both corrected claims hold up against freshly re-fetched primary sources (not just the
PR's own quotes), a third-party source independent of both the author and wq#4478, and a
full-file grep for missed instances. No factual error, no internal inconsistency, no
unsupported claim introduced by the fix. No escalation to opus-5@high required.
