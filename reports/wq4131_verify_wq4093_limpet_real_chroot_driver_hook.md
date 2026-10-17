# wq#4131 — verify pass on wq#4093 (LIMPET: first real-chroot run of driver_hook)

Author: claude-sonnet-5. Verifier: claude-sonnet-5@high (this session). Archetype 8
adversarial verify. PR: lumen/claude-foreman#2074 (merged as 82a6fcbd, merge b55dfeae;
forge PR lookup 404'd — repo/PR already merged into main history, verified from git
directly, which is authoritative).

## Verdict: CONFIRM

## What wq#4093 claims
1. Catalog bug: `10-network-adapters.toml`'s RTL8812BU entry was wrong — cataloged as
   needing an out-of-tree DKMS build (`rtl8812bu`, dead `dkms_source` repo URL). Fix:
   it's in-kernel via `rtw88_8822bu`, which claims USB ID `0bda:b812`; only its firmware
   blob (via `linux-firmware`) is needed. `dkms-sources.pin` gets a `skip` line.
2. First real (non-mocked) run of `driver_hook.py install --run-chroot` as root, inside
   an assembled chroot, against the real pinned `xubuntu-26.04-desktop-amd64.iso` —
   previously this path only ran against synthetic fixtures with chroot faked.
3. Honestly flags that the catalog fix leaves **zero** `dkms = true` devices anywhere in
   `hardware.d/*.toml`, so the real chroot run could not and did not exercise the DKMS
   build branch (`git clone`/`dkms add`/`dkms install`) in `chroot_script()` — that logic
   is only covered by `test_driver_hook.py`'s mocked unit tests.
4. Base ISO stays pinned at 26.04 rather than a hypothetical 26.04.1, because all search
   engines were down for that session (confirmed independently this session — same
   SearXNG 429/CAPTCHA failure reproduced live, see below).

## What I tried to break
- **USB-ID-to-driver claim** (the load-bearing fact): is `0bda:b812` really served by the
  in-kernel `rtw88_8822bu`/`rtw8822bu.c` module and not, say, `rtw8812au.c` (RTL8812AU,
  a different chip generation) or nothing at all? Fetched the actual upstream kernel
  source (git.kernel.org, since GitHub raw/jsdelivr were rate-limited/403 from this host):
  - `drivers/net/wireless/realtek/rtw88/rtw8822bu.c`: `rtw_8822bu_id_table[]` lists
    `USB_DEVICE_AND_INTERFACE_INFO(RTW_USB_VENDOR_ID_REALTEK, 0xb812, ...)` →
    `rtw8822b_hw_spec`. Confirmed.
  - `Makefile`: `obj-$(CONFIG_RTW88_8822BU) += rtw88_8822bu.o` / `rtw88_8822bu-objs :=
    rtw8822bu.o` — the module name is literally `rtw88_8822bu`, matching the catalog's
    `linux_driver` field exactly.
  - `rtw8812au.c` (the chip the old dkms_source URL's name suggested) claims `0bda:8812`
    and `07b8:8812` — not `b812`. No overlap/ambiguity with the 8822bu table.
  - Conclusion: the central technical correction is factually correct, not guessed.
- **Firmware claim**: `/home/lumen/kb/ISSUE-LOG.md` row 1390 independently documents a
  real `dpkg -L linux-firmware-realtek` inside the stage1b chroot showing
  `/usr/lib/firmware/rtw88/rtw8822b_fw.bin.zst`, resolved — corroborates the README's
  firmware claim with a second, separately-logged piece of evidence (not just the PR's
  own prose).
- **Did the "real chroot run" claim quietly overreach?** No — README.md explicitly adds
  a "Still untested" line naming the exact branch (DKMS build) the catalog fix made
  unreachable. This is the first thing I went looking to contradict (a claimed
  "run end to end" that actually skipped the interesting part) and the PR already
  discloses it. No overclaim found.
- **Test coverage after the fix**: `test_build_iso_drivers.py`'s
  `test_dkms_targets_only_include_dkms_true_devices` now asserts `targets == []` (catalog
  has zero dkms=true devices) instead of asserting `"rtl8812bu" in modules`. Confirmed via
  `grep -rn "dkms = true" limpet/hardware.d/*.toml` → no matches anywhere in the catalog.
  The assertion is accurate to current state, not weakened to dodge a failure; the DKMS
  build *logic* itself (not catalog-driven) is still separately covered by
  `test_driver_hook.py`'s mocked tests (`test_pinned_dkms_script_is_hash_checked_and_valid_bash`
  etc.), so this isn't a coverage hole introduced by the PR.
- **Search-engine-down claim**: ran `/home/lumen/bin/fleet-search web "..."` live this
  session — reproduced the same failure mode (brave/google-cse suspended, duckduckgo/
  startpage CAPTCHA, no `BRAVE_API_KEY`), so the claimed reason for deferring the 26.04.1
  decision is real, not a fabricated excuse.
- **Fabrication check (instinct 8)**: `build.sh` really defines `stage1_fetch_base_iso`,
  `stage1b_assemble_chroot`, `stage7_bundle_driver_bundle`, and reads
  `LIMPET_DRIVER_HOOK_RUN` — all grepped directly, not invented by this PR's prose.

## Probe run
`pytest limpet/iso-builder -q` → 67 passed (includes the 21 in
`hardware.d/test_build_iso_drivers.py` + `driver_hook/test_driver_hook.py`).
`python3 -c "... bid.load_catalog(); bid.dkms_targets(devices)"` → `[]`, 43 devices total,
confirming the catalog state matches both the test assertion and the README prose.

## Minor (non-blocking) observation
The device's catalog `name` field is still "Realtek RTL8812BU" even though its real
silicon (per the verified kernel source) is the 8822B family (`rtw8822bu.c`,
`rtw8822b_hw_spec`) — a naming mismatch that predates wq#4093 and is outside its scope
(driver/DKMS classification, not product naming). The new catalog comment already makes
this explicit for the next reader, so it doesn't mislead; flagging only in case a future
ticket wants to rename the entry to match its chip family.

No contradiction found. Not escalating to opus-5@high.
