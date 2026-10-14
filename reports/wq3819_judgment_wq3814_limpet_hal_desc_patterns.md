# wq#3819 — judgment on wq#3814's contradiction of wq#3809 (LIMPET HAL `desc_patterns`)

- **Ticket judged:** wq#3814 (verify, sonnet-5@high) vs wq#3809 (LIMPET hardware
  abstraction layer, PR#1802, merged `ab4bd811`)
- **Judge:** claude-opus-5@high, fresh instance, worktree `wq3819-a1`, 2026-10-14
- **Ruling: CONTRADICTION UPHELD — defect real, severity MEDIUM (latent), discharged by
  follow-up fix in this ticket's PR. wq#3809 is NOT reopened.**

## 1. Is the contradiction real? Yes — and it is broader than reported

wq#3814's two reproductions replay exactly against the merged copy, and a third plugin
it flagged only "to a lesser extent" is a false positive too:

```
$ python3 -c "... import hal; [hal.detect(hal.list_usb_devices(line)) for line in ...]"
['sdr_receiver']    0bda:8153 Realtek Semiconductor Corp. RTL8153 Gigabit Ethernet Adapter
['target_device']   05c6:a40c Qualcomm, Inc. Mobile Broadband Modem
['logic_analyzer']  04b4:0001 Cypress Semiconductor Corp. mouse      ← not in wq#3814
```

A mouse reporting `logic_capture`/`pulseview` is the same defect class as the NIC
reporting `rtl_sdr`: `HardwarePlugin.matches` (hal.py:139-143) treats `desc_patterns` as a
substring test over the whole lsusb descriptor, and three plugins' patterns were bare
*vendor* names, which lsusb prints for every product that vendor ships.

Two further findings from checking what lsusb actually renders (via `fleet-search web`,
since the local `usb.ids` is outside this worker's allowed paths — ISSUE-LOG #1288):

- `"allwinner"` never matched its intended device at all. lsusb renders Allwinner FEL
  (`1f3a:efe8`) as `Onda (unverified) V972 tablet in flashing mode` — no "Allwinner"
  anywhere. The pattern was pure liability: zero true positives, unbounded false ones.
- `"rockchip"` cannot be narrowed: a MaskROM board prints
  `Fuzhou Rockchip Electronics Company RK3399 in Mask ROM mode`, so the *mode* string is
  the only product-specific part; the vendor name matches any Rockchip board in any mode.

So the verifier's reading of the purpose conflict (README.md:14-17 — the tool exists so the
agent does not guess) holds: the detector answered confidently and wrongly for three of the
most common USB vendor IDs on any laptop or dock.

## 2. Severity and disposition

**MEDIUM, latent.** Confirmed independently that nothing consumes it: no file outside the
bundle references `hal.py` or `wq3809-limpet-hal` (wq#3808's MCP meta-server included), so
no agent has yet acted on a wrong capability list. That is why this is not HIGH — but it is
not LOW either, because the failure mode is an agent reaching for `edl_flash` tooling
against a modem, and the whole point of shipping the HAL was to be trusted without probing.

**wq#3809 is not reopened.** Its deliverable landed, its parsing/resolution/CLI work is
correct (wq#3814 checked each and they held), and the merged bundle is byte-identical to
the reviewed branch. Reopening would relitigate a correct close over one wrong table of
strings. The honest unit of work is a follow-up edit, which this ticket's PR carries:

- `hal.py` — `sdr_receiver` `("realtek semiconductor",)` → `("rtl2832", "rtl2838")`;
  `target_device` `("qualcomm, inc.", "rockchip", "allwinner")` →
  `("qdl mode", "mask rom mode", "in flashing mode")`; `logic_analyzer` drops
  `"cypress semiconductor"` (its FX2 clones are already covered by `04b4:8613` in
  `usb_ids`); `coral_tpu` drops the vendor-level `"global unichip"` for the same reason.
  Every surviving pattern is a product or USB-mode string taken from a rendered `lsusb`
  line found in the wild, not from memory. The module docstring and the
  `desc_patterns` field now state the product-or-mode rule so the next plugin author
  does not reintroduce it.
- `tests/test_hal.py` — `ORDINARY_LSUSB_SAMPLE`: nine ordinary consumer devices (Realtek
  NIC/WiFi/card reader, Qualcomm modem + phone in MTP mode, Cypress mouse, Rockchip and
  Allwinner boards in normal mode, a Global Unichip ASIC), all with PIDs in no plugin's
  `usb_ids`, asserted to detect as nothing. This is the wq#2630 probe the shipped suite
  lacked: drawn from outside the population the fallback was designed to match, and it
  fails on the pre-fix copy. Two tests also pin that the narrowing did not cost the
  fallback its purpose — an unlisted-PID RTL2832 clone still reports `sdr_receiver`, and
  all three bootrom-mode strings still report `target_device`.
- `install.sh --verify` + `verify_no_false_positive.py` — the same probe against the
  *deployed* copy, loading the live `hal.py` by path (not whatever `import hal` finds) and
  MISSing with the offending `usb_id → hw_type` pair. It fails on the unfixed copy, so the
  deploy check can fail:

```
$ python3 -I ops/wq3809-limpet-hal/verify_no_false_positive.py ops/wq3809-limpet-hal/hal.py
OK 3 ordinary-vendor devices reported as no RE hardware          (rc=0, fixed)
$ python3 -I ops/wq3809-limpet-hal/verify_no_false_positive.py /home/lumen/foreman/ops/wq3809-limpet-hal/hal.py
false positive: 04b4:0060 (... mouse) reported as logic_analyzer with logic_capture,...
false positive: 05c6:a40c (... Mobile Broadband Modem) reported as target_device with adb,edl_flash,...
false positive: 0bda:8153 (... RTL8153 Gigabit Ethernet Adapter) reported as sdr_receiver with rf_capture,...
                                                                  (rc=1, pre-merge live copy)
```

## 3. `_apex_node_for` — removed, not deferred, and no follow-up ticket

wq#3814 proposed accepting it as a documented single-device assumption. Ruling: stronger
than that — the resolver could never return a correct answer, so documenting it would have
documented a bug. `detect()` only ever iterates `lsusb` devices; a PCIe/M.2 Coral is not on
the USB bus at all, and there is no busnum/devnum correlation available from
`/sys/class/apex/*`. The only reachable path was "USB Accelerator attached *and* an apex
node exists" — i.e. report the PCIe card's node for the USB device. It is deleted, the
`coral_tpu` row's device-node column corrected to the USB bus path, and the PCIe
non-detection written up under README "Known gaps" (covering it needs an `lspci` scan that
is a feature, not a fix, and nothing consumes the HAL to want it yet). No ticket filed;
the gap is now stated where a reader of the tool will see it.

## Evidence (read-only, re-runnable)

```
python3 -m pytest /home/lumen/foreman/ops/wq3809-limpet-hal/tests/test_hal.py -q
python3 -m pytest /home/lumen/foreman/ops/wq3809-limpet-hal/test_verify_handler.py -q
python3 -I /home/lumen/foreman/ops/wq3809-limpet-hal/verify_no_false_positive.py \
    /home/lumen/foreman/ops/wq3809-limpet-hal/hal.py
python3 -c "import sys; sys.path.insert(0, '/home/lumen/foreman/ops/wq3809-limpet-hal'); import hal; print(hal.detect(hal.list_usb_devices('Bus 001 Device 003: ID 0bda:8153 Realtek Semiconductor Corp. RTL8153 Gigabit Ethernet Adapter\n')))"
```

The last command is the ticket's own liveness probe: `[]` on the fixed tree, the
`sdr_receiver` false positive before it.
