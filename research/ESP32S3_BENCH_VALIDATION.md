# ESP32-S3 / ESP32 Bit Pirate bench validation — wq#4010

Status: **NOT RUN.** This is the validation plan and the catalog/scope work wq#4010
could complete; the actual serprog/SUMP read-write test against the Pico baseline
needs Frank's S3 board physically attached to a reachable host, which it is not this
session (re-confirmed; matches the lsusb sweep of Lumen/Horus/Maksoor in
`research/esp32s3-re-tools.md` ADDENDUM A4). That run is queued as **wq#4036**.
Do not treat this document as a passing validation until wq#4036 appends a Results
section below with real command output and sha256 digests.

## What wq#4010 actually did
- `limpet/hardware.d/03-multiprotocol-tools.toml`: added an "ESP32 Bit Pirate
  (geo-tp firmware, ESP32-S3)" catalog entry, `confidence = "unverified"`, no
  guessed `usb_ids` — see the entry's `proprietary_notes` for the CDC-ambiguity
  reasoning.
- `limpet/target-devices.d/generic-usb.toml` (+ regenerated
  `91-limpet-targets.rules`): added an Espressif `303a:*` wildcard entry,
  `confidence = "low"`, same CDC-ambiguity caveat.
- `limpet/DESIGN-DECISIONS.md`: recorded the decision that a future Bit Pirate
  registry manifest must omit Wi-Fi/BLE/sub-GHz/SDR capabilities.
- Filed **wq#4036** (physical bench task: S3 module ID + this validation) and
  **wq#4037** (needs `repo(/home/lumen/re)`: vendor the pinned firmware, fix
  `re/common/tools/firmware-to-flash/README.md`, validate/document
  `esp32-image-parser` on an S3 image) — both outside what this worktree's
  `--add-dir` grant or available hardware allowed. Logged as
  `kb/ISSUE-LOG.md` row 1352.

## Validation plan for wq#4036 to execute (from `research/esp32s3-re-tools.md` §6)

Rule from fleet bench history: flash rigs lie. The coreswitch campaign showed
noisy rails/fan back-feed, marginal Vcc, and a serprog firmware that failed at
~107 KB (write-buffer negotiation) while another firmware worked at 500 kHz. So:

1. **Identify the module first.** `esptool.py --port /dev/ttyACMx chip_id` and
   `flash_id` (esptool lives in `/opt/re-venv` on Maksoor per the research doc) to
   get the exact S3 variant (N8/N16, PSRAM or not) — this decides which Bit
   Pirate firmware asset applies (`bit_pirate_18_s3-devkit.bin` /
   `-n16-r8.bin` / `-supermini.bin`, sha256 digests pinned in wq#4010's ticket
   notes and `research/esp32s3-re-tools.md` ADDENDUM A2 — re-hash after
   download, do not trust the GitHub-reported digest alone).
2. **Use a SPARE SPI NOR chip with KNOWN content** — explicitly NOT the deployed
   core switch (its U7 flash is live; powering/clipping it risks the fleet core
   switch).
3. Read it **twice** with the S3's `flashrom -p serprog:dev=/dev/ttyACMx:921600,spispeed=<conservative, well below 4M>`.
   `sha256sum` both reads; they must match each other AND a Pico read of the
   same chip.
4. **Write + verify a test image >200 KB** (past the ~107 KB failure point seen
   historically on a different serprog firmware), then read back and compare
   sha256 again.
5. Only after 3-4 pass, raise `spispeed` and repeat the read-compare step.
6. Repeat the same discipline for SUMP: capture a known UART/SPI waveform
   (signal generator, or the Pico driving it) and diff the S3's `sigrok-cli
   --driver=ols` decode against the Pico's decode of the identical capture.
7. Record the firmware commit/version, `spispeed`, and all sha256 digests in
   `lumen_state` (re bench) and append a **Results** section to this file.

## Results

_(wq#4036 appends real output here — exit codes, sha256 digests, firmware
version/commit, and a pass/fail verdict per step above. Until then this
section stays empty; an empty Results section is the honest state, not a
placeholder to silently fill with assumed-good values.)_
