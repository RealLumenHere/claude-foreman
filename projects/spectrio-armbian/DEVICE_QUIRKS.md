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
        phandle = <0x14>;   /* parent bus of the mdio-multiplexer below */
    };
};
```

The PHY itself is not under that `mdio` node. As on every G12A/SM1 board,
it hangs off the SoC's MDIO multiplexer, on the external-PHY leg
(`mdio@0`). The complete node, verbatim from the shipped
`meson-sm1-spectrio-tf1.dts` (lines 1922-1948):

```dts
mdio-multiplexer@4c000 {
    compatible = "amlogic,g12a-mdio-mux";
    reg = <0x00 0x4c000 0x00 0xa4>;
    clocks = <0x02 0x13 0x0b 0x02 0xb1>;
    clock-names = "pclk", "clkin0", "clkin1";
    mdio-parent-bus = <0x14>;
    #address-cells = <0x01>;
    #size-cells = <0x00>;
    phandle = <0xe4>;

    mdio@0 {
        reg = <0x00>;
        #address-cells = <0x01>;
        #size-cells = <0x00>;
        phandle = <0xe5>;

        ethernet-phy@0 {
            reg = <0x05>;               /* the fix: PEF7071 answers at MDIO 5 */
            max-speed = <0x3e8>;        /* 1000 */
            reset-assert-us = <0x7530>;
            reset-deassert-us = <0x13880>;
            reset-gpios = <0x15 0x0f 0x07>;
            interrupt-parent = <0x16>;
            interrupts = <0x1a 0x08>;
            phandle = <0x09>;           /* = the MAC's phy-handle */
        };
    };
    /* mdio@1 / ethernet-phy@8: the SoC's internal 10/100 PHY, unused */
};
```

Things that only show up in the real node:

- **The fix is a single `reg` change.** The node keeps its stock X96 Max+
  name `ethernet-phy@0`. Only `reg` moved from `0x00` to `0x05`. The unit
  address in the name no longer matches `reg`. That's cosmetic: the kernel
  finds the PHY through the MAC's `phy-handle` and addresses it by `reg`,
  and the board runs 1 Gb/s full duplex with this exact blob. This is what the
  fleet's patch script does: it decompiles `meson-sm1-x96-max-plus.dtb`
  and runs `sed` on `reg` inside the `ethernet-phy@0 {` block only.
- **There is no `compatible` string.** The kernel reads the PHY ID over
  MDIO and picks `intel-xway` on its own.
- **The X96 Max+ reset/IRQ properties are still there.** The reset GPIO,
  its assert/deassert timings and the PHY interrupt were written for that
  board's RTL8211F. One intermediate fleet revision stripped them and
  renamed the node `ethernet-phy@5`. The golden DTB does neither, and it is
  the one proven on hardware. Leave them in.
- **No RX delay on the MAC side.** The MAC node has
  `amlogic,tx-delay-ns = <0x2>` and nothing for RX: no `amlogic,rx-delay-ns`
  and no `rx-internal-delay-ps`. An earlier patch revision added
  `rx-internal-delay-ps = <0x7d0>` (2 ns). The golden copy dropped it,
  because the PEF7071 adds the RX delay itself.

Integrity: `meson-sm1-spectrio-tf1.dtb` sha256 `0f1f9b3a…2a88ef`, which is
the same hash the fleet's read-only inspection of the working community
image recorded. The `.dts` is that same DTB decompiled. Its MAC node, PHY
node, `/memory` and `reserved-memory` match a direct parse of the binary
property for property.

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

Those two `secmon` regions sit low in DRAM (0x05000000-0x072fffff). That
is the whole list. Nothing near the top of the 4 GiB part is declared, and
`/memory@0` is the X96 Max+ placeholder `reg = <0x0 0x0 0x0 0xffffffff>`,
which the bootloader is supposed to fix up with the real size and carve-outs.
This board's 2015-era vendor U-Boot never declares the TrustZone/BL31
memory at the top of DRAM. The kernel therefore maps it as ordinary RAM,
touches it during `paging_init`, and dies with an Asynchronous SError
Interrupt.

The fix is the boot arg `mem=3552M` (0xDE000000). It caps the kernel's view
of RAM below the secure region, giving up the top ~544 MiB (usable RAM
shows as ~3.4 GB). See `BOOT_ARGS.md`. Making the DTB declare that region
would be the cleaner fix. It hasn't been done because nobody has measured
the exact BL31/TEE bounds on this board.

## Provenance re-check (wq#4322)

The golden `meson-sm1-spectrio-tf1.dts`/`.dtb` and the root-cause writeups
above already landed in this repo (wq#4326, commit `a3acf112`) before this
ticket was dispatched. wq#4322 re-opened the question of whether maksoor
holds additional per-unit flash transcripts beyond what's here. A direct
`ssh`/`fleet-exec run maksoor` check of the home directory (`~`, `~/Documents`,
`~/Downloads`) found no Spectrio/S905X3/TF1 artifacts — that box's current
working set is unrelated hardware (RK3399, Aruba AP-303, SkillFishOS). The
DTS/DTB/boot-arg/eMMC content in this repo remains the authoritative,
already-verified copy; there was nothing further to pull.
