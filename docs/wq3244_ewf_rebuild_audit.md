# wq#3244 — e-waste-flasher v1 rebuild: audit record

Frank directive 2026-09-30: rebuild `/home/lumen/build/e-waste-flasher` from scratch (drop the
mock sidecar), typed Rust commands over `rkdeveloptool`/`dd`/`gzip`, dark React UI with a
backup-before-flash gate, release bundle shipped to Maksoor for an on-device test.

This ticket is the work-queue audit/episode anchor for that rebuild. The implementation landed
directly in the `e-waste-flasher` repo (a separate repo from this one, not a submodule of
`foreman`), in an interactive orchestrator session (`orch-ewf-rebuild-c0ae0e`), not through this
fworker's branch — hence this doc rather than a diff against `e-waste-flasher` itself.

## What shipped

- Rust/Tauri backend, no mock sidecar. Every `rkdeveloptool`/`dd`/`gzip` invocation is a
  server-side typed Tauri command with validated arguments — the webview never passes a raw
  command string:
  - `src-tauri/src/lib.rs:1-3` (module doc, states the no-raw-command-string invariant),
    `src-tauri/src/lib.rs:81-134` (`run_streaming`: spawns `rkdeveloptool`, streams
    stdout/stderr, parses `(NN%)` progress), `src-tauri/src/lib.rs:243-305`
    (`backup_device`/`backup_inner`: two backends — USB mass storage via `dd | gzip`
    (`src-tauri/src/lib.rs:276-286`), and rockusb Loader-mode reads via `rkdeveloptool rl`
    (`src-tauri/src/lib.rs:288-300+`)).
  - `src-tauri/src/rk.rs:1-7` documents the `rkdeveloptool` 1.32 (pine64 fork) output formats
    the parser was verified against; `src-tauri/src/rk.rs:14-19` locates the binary in
    `/usr/local/sbin`, `/usr/local/bin`, `/usr/bin`, `/usr/sbin`, `PATH`, or `EWF_RKDEVELOPTOOL`.
  - Input/output validation lives in `src-tauri/src/lib.rs:138-195`
    (`validate_output`/`validate_input_file`/`validate_partition`): absolute-path checks, refuses
    writes under `/dev`, `/proc`, `/sys`, and restricts partition targets to a GPT name or a
    numeric LBA.
- React/TypeScript dark UI: `src/App.tsx:1-19` wires device scan/select, a `backedUp` map
  (`src/App.tsx:14`) that gates flashing on a prior backup for the selected device, a live job
  terminal drawer, and progress/output event streams from the Tauri backend.
- Device profiles (`profiles/`, schema at `profiles/SCHEMA.md` per `README.md:16-18`) — one YAML
  per board: USB identities per mode, loader, partition layout, mode-entry steps, known images.

## Build, test, ship evidence (from wq#3244 ticket notes)

- `2026-10-01 01:14:37` — built and committed to `e-waste-flasher` main at `70570e6`, tagged
  `v0.1.0-rc1`; `cargo test` 9/9 passing; `.deb` and AppImage staged at
  `/home/lumen/build/ewf-dist`; ship watcher `lumen-job-ewf-ship-1790817206` armed (Maksoor was
  offline at dispatch time).
- `2026-10-01 06:28:41` — dispatch-time advisory `dc2c0540`: `/home/lumen/build/e-waste-flasher`
  is not under `allow.read_dirs` in `fleet-exec-policy.yaml`; recorded, not held (a fleet-policy
  PR to widen that is still open separately — this is why this worker read that repo via the
  `Read` tool rather than `Bash`/`find`, which are directory-scoped).
- `2026-10-01 10:22:18` — `burn-3-fd1058` confirmed the bundle landed on Maksoor at `~/ewf` and
  is sha256-identical to the Lumen-side `ewf-dist` build (AppImage `2d4f81d6…`, deb
  `129cb4ff…`). `SHA256SUMS` was rewritten to relative paths on both sides (absolute-path
  original kept as `SHA256SUMS.abs.bak`) so `sha256sum -c` verifies on Maksoor as-is. The ship
  watcher unit is gone because delivery already completed. `rkdeveloptool` is present at
  `/usr/local/sbin` on Maksoor; `rkdeveloptool ld` reports no device attached.

## What's left

The rebuild, its test suite, and the ship-to-Maksoor step (byte-identical, checksummed) are
done. The one remaining step — plugging the Rockchip board into Maksoor in Maskrom/Loader mode
and running the shipped AppImage against it over the RDP session — is a physical action nobody
remote can perform. Per the no-physical-gate rule, that step is tracked as its own queued
physical ticket (`frank-hw` lane) rather than left as a close-blocking condition on this one;
see that ticket for the live status of the on-device run.
