# TWN4 MultiTech 3: reflash from the custom OEM app (TPIP) to Elatec Simple Protocol on headless Linux

wq#4690 · researched 2026-10-27 · source prompt `scratch/research_prompts/twn4-multitech3-linux-reflash.txt`

> ## ⚠️ READ FIRST: THIS IRREVERSIBLY DESTROYS THE OEM APP
>
> Flashing any `.bix` replaces both the firmware **and** the App. Elatec states that "Both firmware and
> App cannot be read back from a TWN4" [D1 §2.1.2–2.1.3]. **There is no dump/backup path.** Once TPIP1.15
> is overwritten, you only get it back if the OEM gives you their `.bix`. The OEM's identity is
> **unknown**: the only non-stock descriptor is `iManufacturer = "OEM"`, which tells us nothing. Do not
> flash unless you accept losing TPIP for good.

## 0. Go/no-go summary

| Question | Answer | Confidence / evidence |
|---|---|---|
| Is there an official Linux flasher? | **Not in the DevPack.** DevPack 5.07 ships only Windows binaries (`AppBlaster.exe`, `Tools/flash.exe`). Elatec cites a doc called *"TWN4 Programming with Command Line under Linux DocRev1"* [D3 §8.1], but it is not in the DevPack and I could not find it published anywhere. The tool's name is **unknown**. | Verified from the full DevPack 5.07 inventory (§1.3) |
| Is the unit password-locked (Device Security)? | **Almost certainly not.** Device Security needs boot loader ≥ 1.20 [D5 §5.1], and it first appeared in boot loader 1.21 [D1 §5.2.8]. This unit reports **B1.09**. | Strong: two Elatec statements plus the unit's own version string |
| Can a mute app be forced into the boot loader by software? | **Probably not.** The documented software path is the App calling `StartBootloader()` [D7 §2.3], which Simple Protocol exposes as command `0002` [D6 §1.5.1.2]. TPIP answers nothing, so it most likely ignores this too. flash.exe has its own "Starting boot loader" step, but its mechanism is **unknown**. | Medium; the cheap tests are in §4 |
| Is there a hardware boot loader entry? | **Yes, on the module.** Nano module: pin 25 `BOOT`, "Shortcut against ground during reset will guide firmware directly into boot loader" [D3 pin table]. Core module: short C25 to C28 and power-cycle [D1 §2.5]. The MultiTech 3's external X2 header does **not** bring out BOOT. It only exposes `RESET-` (pin 24) and `GND` (pin 12) [D2 Table 2.2]. The MCU circuitry sits under a shield [D2 §2.1]. | The pin is documented; where to reach it on a MultiTech 3 PCB is **unknown** and needs a physical inspection |
| Can a failed flash brick it? | **No, in principle.** The boot loader is factory-installed and "cannot be installed by the user" [D5 §5.1]. An interrupted flash gives blink code 1 ("no valid firmware… programming must be started from the beginning") [D1 §2.4]. B1.09 has no mirror-image fallback (that arrived in 1.21 [D1 §5.2.8]), so the reader stays dead until a re-flash succeeds. | Medium-high |
| Correct image | `TWN4_xCx507_PRS104_Multi_CDC_Simple_Protocol.bix` from DevPack 5.07. The `x` in the hardware slot means one "Multi" image covers C, N and M hardware. | Verified filename in DevPack 5.07 |
| Better tool for cloning HID Prox to T5577 | **Proxmark3.** | §6 |

**Recommendation:** buy or borrow a Proxmark3 for the HID-clone goal now; it does not depend on any of the
unknowns above. Treat the TWN4 reflash as optional. Try the zero-risk software entry (§4.1–4.2) first.
If it fails, the only remaining path is hardware boot-pin access, which is a physical task for Frank
(§4.3) or a question for Elatec support.

---

## 1. DevPack: where it comes from and what is in it (Q1)

### 1.1 Official download
- Elatec's software page lists **TWN4 DevPack 5.25** as the latest version (INT page, fetched
  2026-10-27). A search-engine snippet of the en-US page showed 5.20, so regional pages lag [S1].
- The "TWN4 DevPack Download" button leads to `https://www.elatec-rfid.com/int/devpack-form`. That is a
  HubSpot form (portalId 8507717, formId 81dcc913-863f-4c89-89d7-19b8566302e9) [S2]. I did not
  inspect which fields it requires (the HubSpot JSON endpoint returned nothing parseable), so whether
  it demands a business email is **unknown**. The page also says: "Please use the contact form below to
  request the latest firmware version" [S1].
- Elatec's LinkedIn post for 5.07 links `https://elatec.me/49jQfHl`, which redirects to the same software
  page (checked with curl 301) [S1].

### 1.2 Public mirror (used for this research)
- `https://codeware-europe.com/rfid-reader-twn4-125khz-13-56mhz-rs232_a_ELTC-TWN4-R.html?tab=downloads`
  is a distributor page. Its "TWN4DevPack.zip" is 57.2 MB with listed MD5 `ecd316046d7daf53eac51b2356e054f6` [S3].
  The file I downloaded on 2026-10-27 **matched that MD5**. It unpacks to `TWN4DevPack507/` (entries dated
  2025-11-14), so it is **DevPack 5.07**.
- A second mirror, `https://dl.heydensecurit.de/Driver/Elatec/TWN4/`, was indexed with
  TWN4DevPack310/401/406/450/480 zips [S5]. It returned 404 on 2026-10-27, but it is useful if you need
  an *older* pack (see §3.3).
- HP's Authentication Suite KB hosts `TWN4DevPack490.zip` and `TWN4DevPack507.zip` for its customers [S4].

Copy-paste download to the reader host (Thor). The file is untrusted data: give it its own empty
directory and only unpack it there.

```bash
ssh thor@100.112.151.100 'mkdir -p ~/twn4/dl && cd ~/twn4/dl && \
  curl -sS -L --http1.1 -A "Mozilla/5.0" -c cj -b cj \
    -e "https://codeware-europe.com/rfid-reader-twn4-125khz-13-56mhz-rs232_a_ELTC-TWN4-R.html?tab=downloads" \
    "https://codeware-europe.com/rfid-reader-twn4-125khz-13-56mhz-rs232_a_ELTC-TWN4-R.html?tab=downloads" -o /dev/null && \
  curl -sS -L --http1.1 -A "Mozilla/5.0" -c cj -b cj \
    -e "https://codeware-europe.com/rfid-reader-twn4-125khz-13-56mhz-rs232_a_ELTC-TWN4-R.html?tab=downloads" \
    "https://codeware-europe.com/rfid-reader-twn4-125khz-13-56mhz-rs232_a_ELTC-TWN4-R.html?fileId=7795&tab=downloads&do=download" \
    -o TWN4DevPack.zip && md5sum TWN4DevPack.zip'
# expect: ecd316046d7daf53eac51b2356e054f6  TWN4DevPack.zip
# The fileId=7795 URL answers with an HTML page unless the cookie+referer pre-fetch above runs first.
ssh thor@100.112.151.100 'cd ~/twn4/dl && unzip -q -o TWN4DevPack.zip && ls TWN4DevPack507/Firmware'
```

### 1.3 What DevPack 5.07 contains (verified inventory, 191 zip entries)
- **Windows GUI/CLI:** `AppBlaster.exe`, `Director.exe`, `Tools/flash.exe` (Win32 CLI flasher),
  `Tools/makeapp.exe`, `Tools/makeosdp.exe`, and the Yagarto 2011 ARM GCC toolchain (`.exe` only) with
  `Tools/sys/libapp.a` and `app.ld` for building Apps.
- **No Linux binaries of any kind. No `.app` files.** Firmware images are `.bix`, and each one already
  bundles firmware plus App [D1 §2.8.1]. App *sources* live under `Apps/Samples/` (for example
  `Apps/Samples/Simple Protocol/App_PRS104_Simple_Protocol.c`).
- **Firmware/**:
  `TWN4_xCx507_PRS104_Multi_CDC_Simple_Protocol.bix` (774 765 B) ←**target**,
  `TWN4_xCx507_STD207_Multi_CDC_Standard.bix`, `TWN4_xCx507_LXT110_Multi_Legic_Transparent.bix`,
  `TWN4_xKx507_STD207_Multi_Keyboard_Standard.bix`, `TWN4_xKx507_STDX207_…Accept_Config_Upgrade_Cards.bix`,
  `TWN4_xKx507_STH207_…HID_Mobile_Access.bix`, `TWN4_xKx507_STHX207_…`, `TWN4_xKx507_DMP234_Multi_Keyboard_Dump.bix`,
  `TWN4_xKx507_TRC234_Multi_Tracer.bix`, `TWN4_xPx507_S1SC161_Multi_CCID_1Slot_Standard.bix`, and BLE `.gbl` images.
- **Tools/HelperImages/**: `…PRB102_Multi_CDC_Simple_Protocol_Binary.bix` and `…BXT206_Multi_BLE_Transparent.bix`.
  The readme says these are helpers for BLE-module programming, "do not modify".
- **Docs/** (PDF): System Overview DocRev48, Simple Protocol DocRev26, API Reference DocRev31, AppBlaster
  DocRev14, Device Security DocRev6, MultiTech 3 DocRev10, MultiTech Nano DocRev13, MultiTech Core DocRev11,
  Upgrade Card DocRev13, PCSC DocRev12, and others. **No Linux programming document.**
- **Driver/**: Windows INF/SYS only (`twncdc.inf` binds 09D8:0320/0420/0427 MI_01 to `usbser`; CCID drivers).

## 2. The Linux CLI path (Q2 / sharpen C)

| Item | Finding |
|---|---|
| Elatec doc "TWN4 Programming with Command Line under Linux DocRev1" | Referenced verbatim in *TWN4 MultiTech Nano DocRev13 §8.1 "Programming Firmware"* [D3]. **Not in DevPack 5.07. Not found online** (exact-phrase SearXNG search, 2026-10-27). |
| The tool's real name, language, arches | **Unknown.** Nothing public confirms a name, path or flags. Do **not** trust any claimed name such as "twn4reflash"; no primary source shows it. |
| How to get it | Ask ELATEC Technical Support (`https://www.elatec-rfid.com/int/customer-service-request-hsform`, linked from the software page) for "TWN4 Programming with Command Line under Linux DocRev1" and the matching tool. Ask whether it ships x86_64 and arm64 builds. |
| Official Windows CLI | `Tools/flash.exe` (Win32 PE). Its usage text and flags are **unknown**: its strings show no usage banner, so run it with no arguments in Windows to see. From the strings: it finds the reader through the Windows registry (`SYSTEM\CurrentControlSet\Enum`, `USB\VID_09D8&PID_0420\`) and opens `\\.\COM%d`. It then prints "Starting boot loader:", "Contacting bootloader:", "Found bootloader V%X.%02X", "Programming Firmware:", "Programming App:". |
| FOSS alternative | **None found** (web/reddit search, 2026-10-27; GitHub API search failed with 401 because the fleet token was missing). The `.bix` payload is encrypted (Elatec: "Encrypted firmware in image (bix) file: Yes, generally supported" [D5 §3.1]; the bytes after the `flashinfo`/`code` headers are high-entropy). A FOSS flasher would therefore have to replay Elatec's boot loader framing; it cannot simply program the MCU. Building one means capturing a successful Windows flash with `usbmon` (§5.4). |

### 2.1 Exact commands available on Linux today

(a) **Detect the reader**:

```bash
ssh thor@100.112.151.100 'uname -m; lsusb -d 09d8:; ls -l /dev/serial/by-id/ 2>/dev/null | grep -i -e elatec -e 09d8 -e TWN4; \
  cat /sys/bus/usb/devices/*/product 2>/dev/null | grep TWN4'
# expected now: 09d8:0420, product "TWN4/B1.09/NCF3.22/TPIP1.15/PI"
```

(b) **Enter the boot loader**: see §4. There is no verified Linux command that forces it on a mute app.

(c) **Program a `.bix`**: there is no verified Linux-native command. Use the Windows VM fallback (§5).

(d) **Program an `.app`**: not applicable. Elatec ships firmware plus App together as `.bix` [D1 §2.8.1],
and DevPack 5.07 contains no `.app` files. To build a custom App you need AppBlaster's source-code
project (Windows), which outputs a `.bix`.

## 3. Exact firmware for this hardware (Q3)

### 3.1 Decoding the unit
`TWN4/B1.09/NCF3.22/TPIP1.15/PI`
- `B1.09` is the boot loader version (§2.6 versioning scheme [D1]; the scheme itself is a figure, but the
  example string `TWN4/B1.03/CCF1.57/PRS1.03/PI` in [D6 §1.5.1.4] has the same layout).
- `NCF3.22`: by the image naming scheme [D1 §2.8.1.1], `N` = Nano Module hardware and `C` = USB CDC
  stack. The trailing letter's meaning is not given in text form. 3.22 is the firmware version.
  **So this MultiTech 3 is built on a TWN4 Nano module.** That inference rests on the naming scheme; it
  is consistent with the "TWN4 Nano" type that flash.exe reports.
- `TPIP1.15` is the App: custom, not an Elatec standard app name (the standard ones are STD/STDC/STDU/STDX/PRS/CFG/DMP/LXT/OSDP/TRC/S1SC [D1 §2.8.1.1]).
- `/PI` are licensed options: **P = HID Prox, I = HID iCLASS** [D9 §1.2]. HID Prox reading needs option P
  [D7 tag table: "LFTAG_HIDPROX … Supported with option P"]. **This unit has it.** Options are separate
  from the firmware image: the Upgrade Card workflow installs a license and then lets you re-flash to any
  firmware, including older ones [D9 §1.5–1.6]. They should therefore survive the reflash. Confirm with
  GetVersionString afterwards (§5.3).

### 3.2 Images (verified names in DevPack 5.07)
- **Simple Protocol (target):** `TWN4DevPack507/Firmware/TWN4_xCx507_PRS104_Multi_CDC_Simple_Protocol.bix`.
  The `x` hardware slot means a Multi image for C/N/M hardware. The older name in the prompt,
  `TWN4_NCx320_PRS104_…_CDC_Simple_Protocol`, uses DevPack 3.20-era hardware-specific naming
  (N = Nano). The current pack has no separate Nano vs MultiTech 2 Simple Protocol image; one Multi image
  covers all of them.
- **Keyboard autoread (read-only, simpler):** `TWN4DevPack507/Firmware/TWN4_xKx507_STD207_Multi_Keyboard_Standard.bix`.
  It enumerates as a USB HID keyboard and types the card ID. Simple Protocol is not available in this mode.
- **No separate Simple Protocol `.app` exists.** It is inside the `.bix`. Source:
  `Apps/Samples/Simple Protocol/App_PRS104_Simple_Protocol.c`.

### 3.3 Compatibility risk: boot loader 1.09 versus firmware 5.07
- Elatec documents only that *newer* boot loaders refuse *older* firmware (BL 1.50 accepts FW ≥ 4.07 …
  BL 2.00 accepts FW ≥ 5.00) [D1 §5.2.10–5.2.15]. **No minimum boot loader is documented for FW 5.07.**
- flash.exe checks compatibility. Its strings include "Device is not compatible!", "Hardware V1 is not
  supported by this firmware!", "This older firmware does not support hardware V2!" and "Wrong booloader
  detected". If it refuses, use an older DevPack (4.x from the mirrors in §1.2) and the matching
  `TWN4_xCx4xx_PRS1xx_…` image.
- HP's KB note that "/B"-less readers need DevPack 4.90 [S4] is about **option B**, which is HID Mobile
  Access over BLE [D1 §2.7]. HP's product depends on it. It is not a general gate for Simple Protocol.

## 4. Boot loader entry on a mute app (Q4 / sharpen A)

**How to tell you are in the boot loader:** flash.exe lists `USB\VID_09D8&PID_000A\` with the message
"Found USB device with running bootloader." That is a **distinct PID: 09d8:000a**. This comes from the
binary's strings and has **not been seen on this hardware**. usb.ids does not list 000a (only
0320/0406/0410/0420 under 09d8 [S7]). The boot loader's USB interface class and Linux driver binding are
**unknown**; capture them with `lsusb -v` the first time it appears.

Watcher (leave it running in a second terminal during every step below):

```bash
ssh thor@100.112.151.100 'timeout 300 udevadm monitor --kernel --subsystem-match=usb --property | grep --line-buffered -E "^(KERNEL|ACTION)|PRODUCT="'
# PRODUCT=9d8/420/... = normal app; PRODUCT=9d8/a/... = boot loader (expected per flash.exe strings)
```

Stop ModemManager from probing `/dev/ttyACM0` (it sends AT commands to new CDC devices). This udev rule
also gives your user access to the VM passthrough (§5):

```bash
ssh thor@100.112.151.100 'printf "%s\n" "SUBSYSTEM==\"usb\", ATTR{idVendor}==\"09d8\", MODE=\"0660\", GROUP=\"plugdev\"" "SUBSYSTEM==\"tty\", ATTRS{idVendor}==\"09d8\", ENV{ID_MM_DEVICE_IGNORE}=\"1\", MODE=\"0660\", GROUP=\"plugdev\"" | sudo tee /etc/udev/rules.d/70-elatec-twn4.rules && sudo udevadm control --reload && sudo udevadm trigger --subsystem-match=usb --attr-match=idVendor=09d8'
```

### 4.1 Zero-risk software attempt #1: Simple Protocol `StartBootloader` (`0002`)
Simple Protocol defines `Reset` = `0001` and `StartBootloader` = `0002` [D6 §1.5.1.1–1.5.1.2]. Lines are
ASCII hex terminated by CR [D6 §1.3]. The default host setting is ASCII, CRC off, 9600 baud [D6]; on USB
CDC the baud rate is nominal. TPIP is presumed to ignore this, but it costs nothing to try. Try both rates:

```bash
ssh thor@100.112.151.100 'D=/dev/ttyACM0; stty -F $D 9600 raw -echo -hupcl; printf "0002\r" > $D; sleep 2; lsusb -d 09d8:'
ssh thor@100.112.151.100 'D=/dev/ttyACM0; stty -F $D 115200 raw -echo -hupcl; printf "0002\r" > $D; sleep 2; lsusb -d 09d8:'
# success = 09d8:0420 disappears and 09d8:000a appears (the watcher shows remove + add)
```

### 4.2 Zero-risk software attempt #2: let Elatec's own tool try (Windows VM, §5)
Run AppBlaster's "Program Firmware Image" (or flash.exe) inside the VM. It runs its own "Starting boot
loader:" sequence. If that prints `Failed` / "Update not possible!" / "No Answer", the app is blocking the
software path. A failure at this stage should leave TPIP intact: the tool's message sequence puts
"Programming Firmware" after "Contacting bootloader … Found bootloader". That ordering is inferred
from flash.exe's strings and has not been observed on this unit.

### 4.3 Hardware boot loader entry (physical task for Frank)
- **Documented pins:** Nano module pin 25 `BOOT`: "Shortcut against ground during reset will guide firmware
  directly into boot loader" [D3 pin table]. Core module: connect C25 and C28 and power-cycle [D1 §2.5];
  C28 is the 3.3 V net and C25 is otherwise marked "Reserved" [D4].
- **On the MultiTech 3:** the X2 2 mm header (24 pins, often unpopulated) exposes `RESET-` (pin 24,
  active-low, internal pull-up), `GND` (pin 12) and `VCC` 3.3 V (pin 19), but **not BOOT** [D2 Table 2.2].
  The MCU and module sit under a shield can [D2 §2.1]. Whether BOOT is reachable (a test pad, a via, or
  only by lifting the shield) is **unknown** until someone opens the reader and photographs both sides
  of the PCB.
- Procedure once the BOOT pad is located (unverified on this board; based on the Nano doc):
  1. Unplug USB. Bridge the Nano `BOOT` pad to GND (a tweezer or wire to X2 pin 12).
  2. Plug USB in, or keep USB connected and pulse X2 pin 24 `RESET-` to pin 12. Keep BOOT grounded through the reset.
  3. Release BOOT after about 1 s. Check: `ssh thor@100.112.151.100 'lsusb -d 09d8:; lsusb -v -d 09d8:000a 2>/dev/null | head -40'`
  4. Pass the device into the Windows VM and flash (§5).
- If opening the reader isn't acceptable, ask Elatec support how to enter the boot loader on a
  MultiTech 3 (and request the Linux CLI doc in the same ticket).

### 4.4 Password-locked boot loader (sharpen B)
- Elatec **does** offer an OEM programming lock: **TWN4 Device Security**, "TWN4 protection against being
  programmed with unwanted firmware images" [D5 §3.1]. It is activated per device from AppBlaster's
  "Manage Security" with a symmetric key (Security Block). Deactivation requires that same key in your
  Security Base [D5 §7.1].
- **It needs boot loader ≥ 1.20, and the boot loader is factory-installed and cannot be updated by
  users** [D5 §5.1]. Device Security first appears in boot loader 1.21 [D1 §5.2.8]. **This unit is
  B1.09, so Device Security cannot be active on it.**
- Detection from Linux: the B-field of the USB product string, already captured as `B1.09`. Re-check before flashing:
  `ssh thor@100.112.151.100 'grep -h TWN4 /sys/bus/usb/devices/*/product'`.
  The authoritative check is AppBlaster → Tools → Manage Security → "Read Security Status" [D5 §7.2]
  (Windows VM). That needs a responsive app, so it may not work on TPIP.
- On a locked unit an unsigned image is rejected; Elatec does not document whether there is a prompt.
  Without the OEM key there is no documented path. Not relevant here, given B1.09.
- **The practical "lock" on this unit is that the app never calls `StartBootloader()`.** The answer to that is §4.3.

## 5. Flashing: Linux host plus minimal Windows VM fallback (sharpen C)

No Linux-native flasher is available (§2), so the reliable path is the official tool inside a Windows VM
with USB passthrough. Every passthrough rule must match **vendor 09d8 with any product ID**, because the
device re-enumerates from 0420 to 000a (boot loader) and back during the flash. A rule pinned to 0420
drops the device mid-flash.

> Thor rule: a VM on Thor is fine. **Do not reboot or suspend Thor.** If Thor turns out to be arm64 or
> has no KVM, move the reader to another x86_64 fleet node with `/dev/kvm` instead of changing Thor.

### 5.1 Check the host
```bash
ssh thor@100.112.151.100 'uname -m; ls -l /dev/kvm; command -v qemu-system-x86_64 virt-install VBoxManage'
# need x86_64 + /dev/kvm. (Windows-on-ARM under arm64 KVM is a separate, heavier path. Avoid it.)
```

### 5.2 Minimal QEMU/KVM VM with vendor-wide USB passthrough
Values a **human must supply**: `WIN_ISO` (a Windows 10/11 installer ISO; there is none on the fleet that
I know of) and a Windows licence if required.

```bash
ssh thor@100.112.151.100 'sudo apt-get install -y qemu-system-x86 qemu-utils && mkdir -p ~/twn4/vm && qemu-img create -f qcow2 ~/twn4/vm/win.qcow2 40G'
# HUMAN: set WIN_ISO to the real ISO path on Thor.
ssh thor@100.112.151.100 'WIN_ISO=/path/to/Win11.iso; qemu-system-x86_64 -enable-kvm -m 4096 -smp 2 -cpu host \
  -drive file=$HOME/twn4/vm/win.qcow2,if=virtio -cdrom "$WIN_ISO" -boot d \
  -device qemu-xhci -device usb-host,vendorid=0x09d8 \
  -vnc 127.0.0.1:1 -daemonize'
# Install Windows over VNC: from your workstation run  ssh -L 5901:127.0.0.1:5901 thor@100.112.151.100  then VNC to localhost:5901.
# usb-host with only vendorid matches ANY product of that vendor, so the 0420 to 000a switch is followed.
```
VirtualBox alternative: `VBoxManage usbfilter add 0 --target "<VM name>" --name elatec --vendorid 09d8`
(no `--productid`, for the same reason).

Inside Windows:
1. Copy `TWN4DevPack507/` into the VM and unzip it into an empty folder.
2. Run `Driver\Install.bat` as admin (installs `usbser` binding for 09D8:0420 and CCID drivers).
3. Run `AppBlaster.exe` → **Program Firmware Image** → **Read Version**. It should list the TWN4 [D8 §6].
4. **Select Image** → `Firmware\TWN4_xCx507_PRS104_Multi_CDC_Simple_Protocol.bix` → **Program Image** [D8 §5].
   - If it fails at "Starting boot loader", do §4.3 (hardware entry), then repeat step 4 while the
     device shows as 09d8:000a.
   - If it reports incompatible firmware or hardware, try an older DevPack image (§3.3).
5. CLI equivalent: `Tools\flash.exe`. Run it with no arguments first to read its real usage. Don't guess flags.
6. Keep USB power stable for the whole flash. An interrupt gives blink code 1; re-run step 4 from the start [D1 §2.4].

### 5.3 Verify on Linux (acceptance)
After the flash, the device re-enumerates as 09d8:0420 with a new product string.

```bash
ssh thor@100.112.151.100 'grep -h TWN4 /sys/bus/usb/devices/*/product'
# expect something like  TWN4/B1.09/NCF5.07/PRS1.04/PI   (exact firmware letters unknown; App field must read PRS…)

# GetVersionString (0004FF): response 00 + length + ASCII hex of the version string [D6 §1.5.1.4]
ssh thor@100.112.151.100 'D=/dev/ttyACM0; stty -F $D 115200 raw -echo -hupcl; (timeout 2 cat $D | od -An -c) & sleep 0.3; printf "0004FF\r" > $D; wait'

# SearchTag (050010): hold the card on the reader; response 00 01 <TagType> <IDBitCount> <len><ID> [D6 §1.5.4.1]
ssh thor@100.112.151.100 'D=/dev/ttyACM0; stty -F $D 115200 raw -echo -hupcl; (timeout 2 cat $D | od -An -c) & sleep 0.3; printf "050010\r" > $D; wait'
# 0000 = no tag in field. For HID Prox expect TagType = LFTAG_HIDPROX (needs option P, which this unit has).
```
Tag write commands for later work [D6]: `AT55_Begin` `1500`, `AT55_ReadBlock` `1501<addr>`,
`AT55_WriteBlock` `1503<addr><4 bytes>` (T5577/ATA5577), and the `MifareClassic_*` and `NTAG_*` families
(§1.5.10, §1.5.26).

### 5.4 Optional: record the protocol for a future FOSS flasher
Capture the Windows flash from the Linux side. This is safe; it only reads traffic.
```bash
ssh thor@100.112.151.100 'sudo modprobe usbmon && B=$(lsusb -d 09d8: | awk "{print \$2+0}" | head -1) && echo bus=$B && sudo timeout 600 cat /sys/kernel/debug/usb/usbmon/${B}u > ~/twn4/flash-usbmon.txt'
```
Start it just before step 4 of §5.2. The capture is the raw material for a Linux-native flasher; the
`.bix` stays encrypted and the boot loader decrypts it.

## 6. Irreversibility (Q5)
- "Both firmware and App cannot be read back from a TWN4… the possibility to clone a device is avoided and
  the intellectual property is protected" [D1 §2.1.2–2.1.3]. Elatec's security table repeats "TWN4 read
  protection of firmware / of App: Yes, generally supported" [D5 §3.1]. **No dump or backup is possible
  through Elatec tooling.** I found no exception and no vendor escrow. Elatec only offers to supply *its
  own* factory-default firmware on request [S1].
- Restoring TPIP needs the OEM's original `.bix` (or AppBlaster project plus Security Block). The OEM is
  unidentified (`iManufacturer "OEM"`, no serial number), so in practice TPIP is gone for good.

## 7. Decision (Q6)

### 7a. HID ProxCard II-style Linear CLM125-H (26-bit H10301) → T5577 clone: **Proxmark3 wins**
| | Proxmark3 (RRG/Iceman firmware) | TWN4 + Simple Protocol (after reflash) |
|---|---|---|
| Read HID Prox | `lf search` / `lf hid read` decodes format, FC and CN [S8] | `SearchTag` returns TagType HIDPROX plus ID bits (needs option P; present) [D6, D7] |
| Clone to T5577 | One command: `lf hid clone -w H10301 --fc <FC> --cn <CN>` or `lf hid clone -r <raw>` [S8]. It writes the T5577 config and data blocks for you. Check with `lf t55xx detect`, wipe with `lf t55xx wipe` [S8]. | Possible only by hand: `AT55_WriteBlock` (`1503`) per block [D6 §1.5.18.4]. You must compute the T5577 block-0 config (FSK2a, RF/50) and the encoded HID data blocks yourself. There is no clone helper. |
| Sim / brute / diagnostics | Yes (`lf hid sim`, `lf hid brute`, raw demod) [S8] | No |
| Prerequisites | Buy hardware; open-source client runs natively on Linux | Reflash first, which depends on the unknowns in §4 |
| HF (MIFARE Classic, NTAG, UIDs) | Yes, plus key recovery | Yes, via a clean host API; good as a desktop reader |

**Verdict:** use a Proxmark3 (an Easy is enough) for read and clone. It needs no reflash and uses a
documented one-liner. Cheap handheld 125 kHz cloners usually copy H10301 to T5577 too, but they give no
visibility into format or verification; I did not research their reliability. Keep the TWN4 for
HF UID/NTAG/MIFARE reading if the reflash succeeds.

### 7b. EMV
EMV payment cards are secure-element smart cards. Their keys never leave the chip, and nothing here
(TWN4, Proxmark3, cloners) can clone or reprogram them. Readers can at most see the public UID and
public application data. This holds whatever firmware the reader runs.

---

## Sources
Primary (Elatec documents inside DevPack 5.07, MD5-verified mirror [S3]; cited by section):
- **[D1]** TWN4 System Overview DocRev48: §2.1.1–2.1.3 boot loader/firmware/App not readable; §2.4 blink codes; §2.5 C25/C28 back door; §2.6 versioning; §2.7 options I/AM/B; §2.8.1 .bix contains firmware plus App; naming scheme C/N/M plus K/C/H/P plus app table; §5.2 boot loader history (1.09 "Support LF and HF versions", 1.21 "Support mirror image / Support TWN4 Device Security", 1.50–2.00 firmware acceptance floors).
- **[D2]** TWN4 MultiTech 3 DocRev10: §2.1 shielded main circuitry; §2.3.2 Table 2.2 X2 pinout (RESET- pin 24, GND 12, VCC 19, no BOOT); §2.3.3 UART X3.
- **[D3]** TWN4 MultiTech Nano DocRev13: pin table "25 BOOT – Shortcut against ground during reset will guide firmware directly into boot loader"; §8.1 cites "TWN4 Programming with Command Line under Linux DocRev1.pdf for Linux".
- **[D4]** TWN4 MultiTech Core DocRev11: C25 "Reserved", C28 VCC 3.3 V.
- **[D5]** TWN4 Device Security DocRev6: §3.1 feature table; §5.1 boot loader ≥ 1.20 required and factory-installed; §7–7.2 activate, deactivate (key required), read status.
- **[D6]** TWN4 Simple Protocol DocRev26: §1.3 CR-terminated ASCII hex, default 9600 ASCII no-CRC; §1.5.1.1 Reset `0001`; §1.5.1.2 StartBootloader `0002`; §1.5.1.4 GetVersionString `0004`; §1.5.4.1 SearchTag `0500`; §1.5.18 AT55 `1500–1503`.
- **[D7]** TWN4 API Reference DocRev31: §2.3 StartBootloader(); tag table (LFTAG_HIDPROX "Supported with option P", LFTAG_AT5555 AT5577); §16 AT55xx operations.
- **[D8]** TWN4 AppBlaster User Guide DocRev14: §5 Program Firmware Image; §6 Read Version; §11 CLI toolchain in `Tools`; §12 settings.
- **[D9]** TWN4 Upgrade Card User Guide DocRev13: §1.2 P = HID Prox, I = HID iCLASS; §1.5–1.6 upgrade then reflash workflow.
- DevPack 5.07 `What's New.txt` (DevPack451 "Support new hardware versions of TWN4 with 3rd gen MCU"; DevPack500 "Support the basic-option"); `Driver/Drivers/twncdc.inf`; `Tools/HelperImages/readme.txt`; static `strings` of `Tools/flash.exe` (PID list including `USB\VID_09D8&PID_000A\` "Found USB device with running bootloader.", compatibility error strings). The binary was not executed.

Web:
- **[S1]** https://www.elatec-rfid.com/int/elatec-software (DevPack 5.25 listed; firmware by contact form) · https://elatec.me/49jQfHl (5.07 LinkedIn link, redirects to S1)
- **[S2]** https://www.elatec-rfid.com/int/devpack-form (HubSpot form, portal 8507717)
- **[S3]** https://codeware-europe.com/rfid-reader-twn4-125khz-13-56mhz-rs232_a_ELTC-TWN4-R.html?tab=downloads (TWN4DevPack.zip 57.2 MB, MD5 ecd316046d7daf53eac51b2356e054f6 = DevPack 5.07)
- **[S4]** https://help.hpauthsuite.com/support/solutions/articles/47001286017 (DevPack490/507 zips; "/B" note)
- **[S5]** https://dl.heydensecurit.de/Driver/Elatec/TWN4/ (older DevPack mirror per search index; 404 on 2026-10-27)
- **[S6]** https://unix.stackexchange.com/questions/747890/how-to-read-a-nfc-card-with-usb-reader (answer rev https://unix.stackexchange.com/revisions/747897/1: AppBlaster is Windows-only; CDC image gives /dev/ttyACMx; CCID image works with pcsclite and libccid ≥ 1.4.28)
- **[S7]** http://www.linux-usb.org/usb.ids (09d8: 0320, 0406, 0410, 0420 only)
- **[S8]** https://github.com/RfidResearchGroup/proxmark3/blob/master/doc/cheatsheet.md (`lf search`, `lf hid read`, `lf hid clone -w H10301 --fc --cn`, `lf t55xx detect`, `lf t55xx wipe`)
- Context: https://alioth-lists-archive.debian.net/pipermail/pcsclite-cvs-commit/2017-June/007911.html (a TWN4 S1SC CCID unit with `iManufacturer: OEM`, 09D8:0428. That suggests "OEM" may be Elatec's own default manufacturer string on some builds, which makes it even weaker as an OEM identifier.)

## Open unknowns and how to close them
1. Linux CLI tool name and binaries: ask Elatec support for "TWN4 Programming with Command Line under Linux DocRev1".
2. flash.exe usage and its boot loader entry mechanism: run it with no args in the VM; capture with usbmon (§5.4).
3. Boot loader USB descriptors (09d8:000a expected): `lsusb -v -d 09d8:000a` on first entry.
4. Where BOOT is reachable on the MultiTech 3 PCB: open the case and photograph both sides (physical task).
5. Whether FW 5.07 accepts BL 1.09 hardware: flash.exe reports this before it writes anything; fall back to an older DevPack.
6. Thor's CPU architecture: `ssh thor@100.112.151.100 'uname -m'`.
