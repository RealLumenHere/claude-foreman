# Device quirks — Spectrio S905X3 player

## Identity

- SoC: Amlogic **S905X3**, SM1 family (`compatible = "amlogic,sm1"` in the
  board DTB). Confirmed against the fleet's own LIMPET hardware-ID catalog
  (`limpet/target-devices.d/amlogic.toml`: USB VID:PID `1b8e:c003` covers
  "GXBB/GXL/GXM/G12A/G12B/SM1", `soc_model` list includes S905X3).
- Board DTB used as the working base: `meson-sm1-spectrio-tf1.dtb`, derived
  from a stock SM1 board file whose DTS carries
  `model = "AMedia X96 Max+"` — i.e. the Spectrio hardware enumerates close
  enough to a known commodity SM1 TV-box reference design that starting
  from that board's DTB and patching only the Ethernet section was viable,
  rather than needing a from-scratch board file.
- `boot-g12.py` (pyamlboot's G12A/G12B/SM1 BootROM-stage tool) is the right
  low-level tool for this SoC family, **not** `boot.py` (GXL/GXM/GXBB only)
  — and per pyamlboot's own README it can only push `u-boot.bin`, not chain
  straight to a Linux+rootfs boot; see `FLASH_PROCEDURE.md`. (Verified in
  this worktree: `docs/wq3253_ewf_amlogic_allwinner_backends.md`, §1.3,
  "Restricted, G12A/G12B/SM1 family" row.)

## The Ethernet PHY mismatch (the actual quirk)

The board's Ethernet PHY is a **PEF7071** — a MaxLinear/Intel "XWAY" family
part (same family as GPY111/PHY11G), driven by the Linux `intel-xway` PHY
driver (`drivers/net/phy/intel-xway.c`), not the generic Realtek/Motorcomm
PHY driver most stock Amlogic TV-box DTBs assume. This is a real,
upstream-documented part family — see the LKML thread proposing a
100BASE-TX link-up workaround for the whole family: Andrew Lunn,
"[PATCH net v2] net: phy: intel-xway: workaround 100BASE-TX Link-Up issue"
(lkml.org/lkml/2026/9/22/1668), which explicitly lists
"GPY111/PEF7071/PHY11G" together as sharing the same diagnostic-mode
quirk.

Stock ophub `s905x3` board DTBs are built for the PHYs that ship on the
commodity boards ophub targets (commonly Realtek RTL8211), wired at a
different MDIO address and usually with an RGMII RX delay applied in the
PHY driver. Neither assumption holds for this board. The working DTB's
Ethernet node (`ethernet@ff3f0000`, read back from the fleet's golden copy
of `meson-sm1-spectrio-tf1.dts`) is:

```dts
ethernet@ff3f0000 {
    compatible = "amlogic,meson-g12a-dwmac", "snps,dwmac-3.70a", "snps,dwmac";
    reg = <0x0 0xff3f0000 0x0 0x10000 0x0 0xff634540 0x0 0x08>;
    interrupts = <0x0 0x08 0x04>;
    interrupt-names = "macirq";
    clocks = <...stmmaceth clkin0 clkin1 timing-adjustment...>;
    rx-fifo-depth = <0x1000>;
    tx-fifo-depth = <0x1000>;
    status = "okay";
    phy-handle = <&phy_handle>;
    phy-mode = "rgmii";
    amlogic,tx-delay-ns = <0x2>;
    /* no amlogic,rx-delay-ns property: the PEF7071 side supplies RX delay
       itself, so letting the SoC side also delay RX double-corrects and
       breaks the link — this is the fix. */
    snps,aal;
    snps,rxpbl = <0x8>;
    snps,txpbl = <0x8>;
    nvmem-cells = <&eth_mac>;
    nvmem-cell-names = "eth_mac";

    mdio {
        compatible = "snps,dwmac-mdio";
        #address-cells = <0x1>;
        #size-cells = <0x0>;
        /* ethernet-phy@5: PEF7071 at MDIO address 5 — the board-specific
           fixup. See full source for the complete phy node; reproduced
           here only as prose per the fleet's own annotation (provision.sh
           and CENSUS_METHODOLOGY.md both independently describe it as
           "PEF7071 @ MDIO addr 5, rgmii, no rx-delay") pending a verified
           pull of the complete .dts across (see README "Status"). */
    };
};
```

The two load-bearing facts — **MDIO address 5** and **no RX delay on the
SoC side** — are corroborated independently in two places in the fleet's
internal copy of this project: the inline comment in `provision.sh`
(`cp ... meson-sm1-spectrio-tf1.dtb ... # PEF7071 @ MDIO 5, rgmii, no
rx-delay`) and the prose in `CENSUS_METHODOLOGY.md` ("DTB: ...(PEF7071 PHY
@ MDIO addr 5, rgmii, no rx-delay — the TF1-specific ethernet fix)"). The
exact `ethernet-phy@5` node body (compatible string, reg, any
`phy-mode`/timing overrides specific to the PHY node itself rather than the
MAC node) is in the full `.dts`, which — per wq#4322's search — is not on
the RE workstation (maksoor) but in a lumen-local scratch tree needing a
`dir()` grant; see README's "Status" section and wq#4326 rather than
guessed at here.

## Memory

The reserved-memory layout baked into the board DTB reserves a fixed
secure-monitor region plus a 256 MiB CMA pool:

```dts
reserved-memory {
    secmon@5000000  { reg = <0x0 0x5000000 0x0 0x300000>;  /* 3 MiB */ };
    secmon@5300000  { reg = <0x0 0x5300000 0x0 0x2000000>; /* 32 MiB */ };
    linux,cma { size = <0x0 0x10000000>; /* 256 MiB */ reusable; linux,cma-default; };
};
```

Per the ticket, the working boot args include an explicit `mem=3552M` cap.
That number isn't independently re-derived here — it's carried over as-is
from the ticket's own description of the bring-up — but it is at least
consistent in shape with a board that needs to subtract secure-monitor +
CMA reservations from whatever the vendor BL2/U-Boot reports as total DRAM
before the kernel sees a clean, non-colliding map. The precise root-cause
narrative (what the kernel saw without the cap, and why) is one of the
items pending the maksoor pull noted in `README.md`.
