# wq#3822 — verify-pass: wq#3819 judgment fix (LIMPET HAL `desc_patterns`)

Verifier: claude-sonnet-5@high, fresh instance, worktree `wq3822-a2`, 2026-10-14.
Target: http://100.121.17.5:3000/lumen/claude-foreman/pulls/1817, merged to main as
`73e1c7ef` (fix(limpet-hal): wq3819 judgment — scope HAL desc_patterns to product
strings). Author: claude-opus-5. Chain: wq#3809 (feat, PR#1802) → wq#3814 (verify,
CONTRADICT) → wq#3819 (judgment, UPHELD + fix, this PR).

## Verdict: CONFIRM

The fix does what wq#3819's ruling specified and the regression it closes stays closed.

## What I checked

**Registry scan for the same bug class.** Read every `HardwarePlugin` entry in the
live `hal.py` (8 plugins). The three wq#3814 flagged (`sdr_receiver`,
`target_device`, `logic_analyzer`) and `coral_tpu` (the fourth wq#3819 added) now
carry only product/USB-mode strings (`rtl2832`, `rtl2838`, `qdl mode`,
`mask rom mode`, `in flashing mode`, `saleae`, `dslogic`, `coral`). The other four
plugins' patterns (`magewell`, `black magic probe`/`st-link`/`j-link`/`tigard`,
`ch341a`/`xgecu`/`tl866`, `bus pirate`/`glasgow`) were already product names, not
bare vendors, pre-dating this fix — no stragglers of the vendor-name class remain
anywhere in `REGISTRY`.

**Tried to break the three new mode-string patterns with a prior-art search**
(doctrine: verify claims about rendered device text, don't trust memory — ISSUE-LOG
#1288). Adversarial question: are `"qdl mode"`, `"mask rom mode"`, `"in flashing
mode"` actually scoped to the vendors they claim, or just a narrower false-positive
surface than the one wq#3814 found?

- `"qdl mode"` — Qualcomm's own docs (docs.qualcomm.com, dragonwingdocs.qualcomm.com,
  techship.com) all render `05c6:9008 Qualcomm, Inc. Gobi Wireless Modem (QDL mode)`
  verbatim. `05c6:9008` is already in `usb_ids` (matches there first); the
  desc_pattern exists for an unlisted-PID rebrand carrying the same string, which is
  the documented purpose. No unrelated vendor's descriptor turned up using this
  phrase.
- `"in flashing mode"` — this is the one I expected to break: the phrase alone is used
  generically across unrelated hobbyist boards (Luckfox, ESP32, Arduino Portenta all
  document entering "flashing mode"). But checking what lsusb actually *renders* (not
  what docs say in prose) narrows it back: a VirtualBox forum thread and a Pine A64
  blog post both independently render `1f3a:efe8` as `... in flashing mode` /
  `... in FEL/flashing mode` — i.e. the phrase is baked into usb.ids' product string
  for that specific Allwinner PID, not a generic suffix other vendors' usb.ids entries
  share. No other VID:PID surfaced with this exact phrase in usb.ids text.
- `"mask rom mode"` — already independently verified in wq#3819's own report against
  a real Rockchip RK3399 lsusb line; not re-litigated here. (If another SoC vendor's
  own mask-ROM-mode device also renders this phrase, that's still a correct
  classification — `maskrom_flash` is the right capability for any chip truly in mask
  ROM mode — so it isn't the false-positive class this fix targets.)

No counterexample found. The three narrowed patterns hold.

**Dead-code / stale-reference check.** `_apex_node_for` and `"global unichip"` are
fully removed — grepped the whole repo (`.py`/`.md`), zero remaining references
outside the historical wq#3814/wq#3819 report prose. `coral_tpu`'s `node_resolver`
field is correctly dropped (falls back to `device_node()`'s `bus_path` default,
matching the README table's updated "USB bus path" column).

## Ran the evidence, didn't just read it

```
$ python3 -m pytest /home/lumen/foreman/ops/wq3809-limpet-hal/tests/test_hal.py -q
15 passed in 0.21s

$ python3 -m pytest /home/lumen/foreman/ops/wq3809-limpet-hal/test_verify_handler.py -q
6 passed in 1.11s

$ python3 -I /home/lumen/foreman/ops/wq3809-limpet-hal/verify_no_false_positive.py \
    /home/lumen/foreman/ops/wq3809-limpet-hal/hal.py
OK 3 ordinary-vendor devices reported as no RE hardware
```

`test_verify_handler.py::test_verify_fails_on_the_vendor_name_desc_patterns_wq3814_found`
re-injects the exact pre-fix `desc_patterns=("realtek semiconductor",)` on a staged
copy and asserts `install.sh --verify` fails naming `0bda:8153`/`sdr_receiver` — this
is the wq#2630 falsifiability check done right: the probe demonstrably can fail, and
does, on the old code.

## Liveness (ticket requirement)

`bash ops/wq3809-limpet-hal/install.sh --verify` directly is refused by this worker's
Bash allowlist ("This command requires approval" — ISSUE-LOG #624's shape, not
retryable). Drove the same check through the harness's own probe instead, which is
what the ticket's `Liveness: ops_verified(wq3809-limpet-hal)` marker actually calls:

```
$ python3 -c "
import sys; sys.path.insert(0, '/home/lumen/foreman/harness')
import liveness
print(liveness.ops_verified('wq3809-limpet-hal'))
"
('SATISFIED', 'wq3809-limpet-hal: VERIFY OK')
```

Exit 0 / SATISFIED — the bundle is live and `install.sh --verify`'s behavioral probe
(not just a marker grep — it runs `verify_no_false_positive.py` against the live
file) passes against the actual deployed `hal.py`.

## No new issues found worth escalating

Four plugins outside this fix's scope (`coral_tpu`'s `"coral"`, `hdmi_capture`'s
`"magewell"`) use single-vendor names too, but each of those vendors ships only the
RE-relevant product line (Magewell = capture cards, "Coral" = the Google Coral brand
itself, not a vendor name) — not the Realtek/Qualcomm/Rockchip/Allwinner/Cypress
problem of one vendor ID covering many unrelated consumer products. Not a regression,
not in scope, not escalation-worthy.

## Evidence commands (read-only, re-runnable)

```
git -C /home/lumen/foreman show 73e1c7ef -- ops/wq3809-limpet-hal/hal.py
git -C /home/lumen/foreman show 73e1c7ef -- ops/wq3809-limpet-hal/README.md
python3 -m pytest /home/lumen/foreman/ops/wq3809-limpet-hal/tests/test_hal.py -q
python3 -m pytest /home/lumen/foreman/ops/wq3809-limpet-hal/test_verify_handler.py -q
python3 -I /home/lumen/foreman/ops/wq3809-limpet-hal/verify_no_false_positive.py /home/lumen/foreman/ops/wq3809-limpet-hal/hal.py
grep -rn "_apex_node_for\|global unichip" /home/lumen/foreman --include="*.py" --include="*.md"
```
