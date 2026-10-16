# wq#3933 — verify-pass on wq#3923 (SoC boot-mode USB VID:PID detection map)

PR #1899, merged to `main` as `b47494a9ef` (branch `feat/wq3923-...`, no pre-squash
history checked separately — single commit). Author: claude-sonnet-5 (wq#3923, one
dispatch). Verifier: claude-sonnet-5@high (fresh instance, wq#3933). Ops bundle:
`ops/wq3809-limpet-hal/` (`usb_boot_signatures.toml` +1366, `usb_boot_signatures.py`
+98, `tests/test_usb_boot_signatures.py` +60, `README.md` +20,
`research/WQ3923_USB_BOOT_MODE_SIGNATURES.md` +118).

## Liveness

`bash ops/wq3809-limpet-hal/install.sh --verify` (via `lumen-run start` — raw `bash
.../install.sh` is outside this lane's Bash allowlist, same wall wq#3918 hit) → `VERIFY
OK`, exit 0. `ops_verified(wq3809-limpet-hal)` holds.

## What wq#3923 shipped

A 102-entry TOML table (`usb_boot_signatures.toml`) mapping USB VID:PID (or
interface class) to `{vendor, soc_family, mode_name, recommended_tool, confidence,
notes}`, plus a loader/CLI (`usb_boot_signatures.py`) and 8 tests. The commit's own
sourcing note is candid: `fleet-search web`/`github` were both down at authoring
time (wq#2595 / no GitHub token), so the data was "compiled from this agent's
knowledge of the named tools' own on-disk device tables... not live-reverified."
Every entry carries a `confidence` score explicitly for this reason, gated into the
Target Device Wizard's existing confidence gate (`docs/target-wizard-spec.md:171-184`):
entries `>= 0.85` are documented as trusted enough to drive an active step *without*
an operator confirm.

## What I checked

1. **Diff cold**: `git show b47494a9ef` — matches the PR notes (5 files, +1662,
   no pre-existing file edited except `hal.py`'s target not touched despite being
   referenced).
2. **Ran the probe + full suite**: `pytest ops/wq3809-limpet-hal/tests/` — 25 passed
   (8 new from this PR, all green; `test_lookup_returns_nothing_for_unlisted_device`
   is a real falsifiable probe per wq#2630, confirmed it actually asserts on `dead:beef`
   returning `[]`).
3. **Checked for internal corruption**: no duplicate `(vid, pid)` pairs among the 73
   `vid_pid` rows (scripted check), 102 `[[entry]]` blocks match the claimed count,
   `tomllib` parses cleanly.
4. **Tried to break it with live sourcing** — `fleet-search web`/`github` are *not*
   down this run (brave/duckduckgo/startpage are rate-limited/CAPTCHA'd but the
   `google cse` engine answers fine), so I re-ran exactly the cross-check the
   ticket's own sourcing note said was unavailable, against the same tools the
   ticket named, and against this repo's own already-shipped data:

### Finding 1 (CONFIRMED correct, for contrast): Allwinner FEL

`curl raw.githubusercontent.com/linux-sunxi/sunxi-tools/master/fel_lib.h` →
`#define AW_USB_VENDOR_ID 0x1F3A` / `#define AW_USB_PRODUCT_ID 0xEFE8`. Matches the
table's `1f3a:efe8` row (confidence 0.95) exactly. The "one PID covers every
Allwinner chip" claim is real, not fabricated — shows the sourcing wasn't uniformly
bad.

### Finding 2 (CONTRADICT): MediaTek `0e8d:2000`/`0e8d:2001` mislabeled BROM, per mtkclient's own table

`curl raw.githubusercontent.com/bkerler/mtkclient/main/mtkclient/config/usb_ids.py`
(mtkclient is the exact tool the wq#3923 ticket named for MediaTek) ships:

```
0x0E8D: {0x0003: -1,  # MTK Brom
         0x6000: 2,   # MTK Preloader
         0x2000: -1,  # MTK Preloader
         0x2001: -1,  # MTK Preloader
         0x201D: -1,  # MTK Preloader
         0x20FF: -1,  # MTK Preloader
         0x3000: -1}, # MTK Preloader
```

`usb_boot_signatures.toml` labels `0e8d:2000` **"BROM (mask ROM, modern DA protocol
V5/V6)"** at confidence **0.85** (covering MT6765/6768/6771/6779/6785/6833,
MT8167/8173/8183/8195 — nearly every modern MediaTek SoC named in the ticket), and
`0e8d:2001` **"BROM... alternate enumeration"** at confidence 0.5. mtkclient's own
source tags both as **Preloader**, a distinct, later boot stage (BROM = raw mask ROM
before any flash-resident code runs; Preloader = after BROM has already handed off
to flash-resident bootloader code — the table's *own* `201d` row correctly
distinguishes "Preloader VCOM... not raw BROM"). 0.85 is at the wizard's auto-action
threshold, so this is not a cosmetic label: a HAL/Wizard consumer reading this entry
would dispatch a BROM-specific workflow (raw mtkclient BROM handshake) against a
device mtkclient's own table says is actually at Preloader, a different protocol
stage. `0x6000`, `0x20ff`, `0x3000` (all Preloader per mtkclient) are also missing
from the table entirely.

### Finding 3 (CONTRADICT, higher severity): Rockchip generation-to-PID map is scrambled, contradicts this repo's own shipped data

`curl raw.githubusercontent.com/rockchip-linux/rkdeveloptool/master/99-rk-rockusb.rules`
(Rockchip's own tool, named in the ticket) ships one PID per chip:

```
RK3036 -> 2207:301a   RK3229 -> 2207:320b   RK3368 -> 2207:330a
RK3128 -> 2207:310c   RK3288 -> 2207:320a   RK3399 -> 2207:330c
                       RK3328 -> 2207:320c   RK3566 -> 2207:350a
```

`usb_boot_signatures.toml`'s Rockchip section (16 of 102 rows) instead invents a
"generation pair" scheme — `...0a`=MaskROM / `...0b`=Loader per generation
(`290a`, `300a/300b`, `310a/310b`, `320a/320b`, `330a/330c`, `350a/350b`, `360a/360b`,
`380a/380b`, `390a/390b`) — that does not match reality:

| table claims | table's chip | actual chip (rkdeveloptool) |
|---|---|---|
| `2207:300a/300b` | RK3026, RK3036, RK3188 | RK3036 is actually `301a` (no `300a/b` in the official rules at all) |
| `2207:310a/310b` | RK3126, RK3128 | RK3128 is actually `310c` |
| `2207:320a` | RK3368 (conf 0.45) | **RK3288** |
| `2207:330a` | RK3288, RK3399 (conf 0.55/0.9) | **RK3368** |
| `2207:330c` | RK3288, RK3399 "Loader" (conf 0.9) | **RK3399 only**, and it's **MaskROM**, not Loader |
| `2207:350a/350b` | RK3328 | RK3328 is actually `320c`; `350a` is **RK3566** |
| `2207:360a/360b` | RK3566, RK3568 | RK3566 is actually `350a` per the official rules (no `360a/b` listed) |

The `330c` row is the sharpest case: this repo's **own pre-existing, already-shipped**
`hal.py:245` carries `("2207", "330c")` with a comment sourced from `lsusb`'s usb.ids
string, *"RK3399 in Mask ROM mode"* — i.e. this repo already had the correct
chip+mode for this exact PID before wq#3923 ran. The new table's own notes field on
this row admits the conflict ("Already shipped in hal.py's target_device plugin as
'Rockchip MaskROM'... labelled here as Loader... Flagging the discrepancy rather
than silently relabeling") but resolves it by keeping the new, wrong label at
confidence 0.9 instead of checking which side was right. Both independent sources I
found (rkdeveloptool's udev rules, and this repo's own `lsusb`-sourced comment) say
the pre-existing shipped entry was correct and the new table's relabeling is the
error. There is also no evidence Rockchip chips actually expose *distinct* MaskROM
vs. Loader PIDs at all — the official rules file lists exactly one PID per chip with
no mask/loader split — so the "...a/...b generation pair" convention invented across
8 Rockchip sections appears to be a pattern hallucinated to fit the ticket's "comprehensive
schema" ask, not something sourced from any tool's actual device table.

## Verdict

**CONTRADICT.** The failure mode is exactly the one the ticket's own sourcing
caveat warned about and the research doctrine exists to prevent: with
`fleet-search` down, the author fell back to recalled/pattern-completed VID:PID
data for an entire vendor section (Rockchip, ~16% of the table) and two
high-confidence MediaTek rows, and that recall was wrong in a way live sourcing
(now available) and this repo's own already-shipped, better-sourced data both
independently falsify. The TOML schema, loader (`usb_boot_signatures.py`), tests,
and confidence-gate wiring are all sound — this is not a code-quality or
architecture problem, it is specifically the underlying factual data in the
Rockchip section plus two MediaTek rows. Both of the sharpest-evidenced errors
(`0e8d:2000` at 0.85, `2207:330c` at 0.9) sit at or above the wizard's 0.85
no-confirm-needed threshold, so this is load-bearing, not cosmetic.

Per archetype 8, this escalates to opus-5@high for judgment: whether to open a
follow-up BUILD ticket to re-derive the Rockchip section (and the two MediaTek rows)
from the now-available `fleet-search`/the primary sources cited above, and whether
`ops_verified(wq3809-limpet-hal)`'s liveness check should be strengthened to catch a
factual-data regression (it currently only checks schema/plugin-count, not content
accuracy — out of scope for me to change here, flagging for the judgment pass).

ISSUE-LOG row 1365.
