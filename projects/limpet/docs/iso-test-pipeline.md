# Limpet ISO Test Pipeline — QEMU shared VNC + agent boot observer

> Status: DESIGN (wq#4417)
> Author: Lumen BUILD worker, 2026-10-19
> Scope: interactive boot/install testing of the Limpet ISO under QEMU, with Frank
> and the agent sharing one running VM — Frank watches and can take the keyboard,
> the agent observes every frame + the serial console, classifies the boot phase,
> and may inject keystrokes only through the energize-confirm gate.

## 1. Why this exists

`limpet/iso-builder/build.sh:557` already runs a **non-interactive** QEMU smoke test
(`stage12_qemu_test`): boot the ISO `-nographic` with `-serial file:…`, then
`grep -qi 'login:'` the serial log. That is a pass/fail gate on *one* outcome — "did
it reach a login prompt in 180s" — and it is blind to everything in between. It cannot
tell a GRUB hang from a kernel panic from an installer waiting on a keypress, it cannot
drive an interactive installer, and Frank cannot watch it.

This pipeline is the interactive counterpart. One QEMU VM boots the ISO with a **shared**
VNC display and a serial console exposed as a live stream. Two consumers attach to that
one VM at the same time:

- **Frank** — a human VNC viewer on `localhost:5901`, for eyes-on observation and manual
  takeover when he wants it.
- **The agent** — attached through `qemu-mcp` (QMP + VNC-framebuffer + serial), taking
  screenshots, reading the serial console, classifying the boot phase, and — gated —
  sending keystrokes.

Neither blocks the other. The VM does not know or care how many observers are attached.

## 2. Prior art (searched before designing — doctrine)

The problem "drive a VM through its OS install over VNC, recognize which screen you are
on, type the right keys, and treat the serial console as a first-class signal" is solved
in the wild by **SUSE's openQA / os-autoinst** (https://open.qa/docs/). os-autoinst boots
a QEMU VM, connects to its VNC display, periodically grabs the framebuffer, and matches
**needles** (hand-fingerprinted pixel regions) to decide what screen is showing, then
sends VNC/QMP keystrokes to advance. It also consumes the serial console as a parallel
channel and records a video of the whole run. HashiCorp **Packer**'s QEMU builder
(`boot_command`) does the same keystroke-injection trick for unattended installs, driving
input over the QMP/VNC channel against a boot timeout.

The QEMU primitives both of them stand on are the ones we use directly:

- **`screendump`** (HMP) / **`screendump` via QMP** — write the current guest display to a
  PPM/PNG file. This is the screenshot sensor.
- **`send-key`** (QMP) — inject a list of `QKeyCode`s as a single chord. This is the
  keystroke actuator. (`sendkey` is the HMP spelling.)
- **`-vnc <display>,share=allowed`** — let more than one viewer attach to the same display
  (QEMU VNC shared-flag semantics, `interop/qemu-qmp-ref.html`).
- **`-serial`** — route the guest serial port to a host sink (file, pty, TCP, unix socket).

**Where we diverge from openQA.** openQA classifies screens by *pixel needle matching* —
precise, but every screen needs a hand-authored needle and needles rot when the theme
changes. Limpet already ships an agent with vision; we classify the boot phase by handing
the screenshot to the agent and letting it label the phase (`bios_menu`, `grub_menu`,
`installer_progress`, `login_prompt`, `crash_screen`, …). That removes the needle-authoring
burden and tolerates a rebranded GRUB/Plymouth theme (the Limpet ISO rebrands both —
`iso-builder/build.sh` stage9), at the cost of a classifier that is probabilistic rather
than exact. §6 handles that cost: the serial console is the deterministic cross-check on
the agent's visual guess, never the other way around.

## 3. The VM: one launch, two observers

`qemu-system-x86_64` launch (BIOS path; UEFI/OVMF path is the same plus
`-drive if=pflash,…OVMF_CODE.fd` / `…OVMF_VARS.fd`):

```
qemu-system-x86_64 \
  -machine q35 -m 4096 -smp 2 \
  -cdrom "$ISO" -boot d -no-reboot \
  -vnc :1,share=allowed \
  -serial file:/tmp/limpet-iso-test/serial.log \
  -qmp unix:/tmp/limpet-iso-test/qmp.sock,server=on,wait=off \
  -display none
```

Line by line, and why each is load-bearing:

- **`-vnc :1,share=allowed`** → VNC on host port **5901** (`:1` = 5900+1). `share=allowed`
  is the shared-mode flag: Frank's viewer and the agent's framebuffer grabber attach to
  the one display concurrently, neither evicting the other. (`share=force-shared` is the
  harder guarantee if a client ever negotiates exclusive; `allowed` is the default-friendly
  choice and what the ticket's `shared` maps to.)
- **`-serial file:/tmp/limpet-iso-test/serial.log`** → the guest's first serial port is
  appended to a host file the agent tails. This keeps parity with the existing
  `stage12_qemu_test` serial contract so the same boot markers (`login:`) still mean the
  same thing. §6 upgrades the *sink* (file → also a unix socket) for live interactive use
  without changing the markers.
- **`-qmp unix:…/qmp.sock,server=on,wait=off`** → the QEMU Machine Protocol control socket.
  `send-key`, `screendump`, `query-status`, and `system_powerdown` all ride this. A **unix
  socket, not TCP** — same fail-closed posture as `limpet-hald`'s `/run/limpet/hal.sock`
  (hal-spec §9.4 item 9): no network bind, gated by filesystem permissions. `wait=off`
  lets QEMU boot immediately instead of blocking for a QMP client.
- **`-display none`** → QEMU opens no local GUI window (there is no local console on these
  nodes — instinct 1). The display still exists and is served over VNC; `-display none`
  only suppresses the *local* SDL/GTK window.
- **`-no-reboot`** → a guest reboot exits QEMU instead of looping, so a boot-loop is an
  observable terminal state rather than an infinite spinner.

The run directory `/tmp/limpet-iso-test/` holds `serial.log`, `qmp.sock`, screenshots
(`frame-<seq>.png`), and the control lease file (§7). It is per-run and disposable.

## 4. qemu-mcp — the agent's attachment surface

`qemu-mcp` is the MCP server the agent talks to; it owns the QMP socket and the VNC
framebuffer client and exposes a small, typed tool surface. It is the Limpet MCP
meta-server pattern (mcp-meta-spec) applied to a QEMU VM instead of a HAL device, and it
carries the same `action_class` manifest axis (hal-spec §9.2b) so the energize-confirm
gate (§8) applies unchanged.

Tools (each with its `action_class`):

| tool | action_class | what it does |
|---|---|---|
| `qemu_screenshot` | `read` | `screendump` over QMP → PNG → return path (+ the bytes for the agent's vision) |
| `qemu_read_serial` | `read` | return new bytes from `serial.log` since the last offset (tail, not re-read) |
| `qemu_status` | `read` | `query-status` — `running` / `paused` / `shutdown` / `guest-panicked` |
| `qemu_classify_phase` | `read` | screenshot + recent serial → boot-phase label (§5) |
| `qemu_send_keys` | `caution`/`destructive` | `send-key` a chord — **gated** (§8), lease-held (§7) |
| `qemu_acquire_lease` / `qemu_release_lease` | `read` | input arbitration (§7) |
| `qemu_powerdown` | `destructive` | `system_powerdown` (ACPI) / `quit` — gated |

`action_class` defaults to the fail-closed value (`energizing`/`destructive`) if a tool's
manifest is missing or ambiguous — same rule as hal-spec §9.2a. A read tool is autonomous;
anything that puts bytes into the guest or changes VM power state is gated.

Why read-only sensors are autonomous but input is gated: screenshots and serial reads
**observe** the VM and cannot change the guest or any attached target. Keystrokes **drive**
it — and inside an OS installer a keystroke can confirm a partition-wipe. That is exactly
the energize-confirm distinction (§8), applied at the VM boundary.

## 5. Boot-phase classification

The agent's job each tick: grab a screenshot + the serial delta, emit one phase label, and
decide the next action. `qemu_classify_phase` returns one of a fixed, closed set so callers
can branch deterministically:

| phase | visual cue | serial cue | agent default action |
|---|---|---|---|
| `bios_menu` | SeaBIOS/OVMF splash, boot-device list | firmware banner | wait, or `send-key` to pick boot device (gated) |
| `grub_menu` | GRUB menu (Limpet-rebranded, stage9) | `GNU GRUB` | wait for timeout, or select entry (gated) |
| `kernel_boot` | Plymouth/console scroll | kernel ring buffer, `systemd` units | wait; watch for stall |
| `installer_progress` | installer TUI/GUI, progress bar | installer log lines | wait; **do not** confirm destructive steps without gate |
| `installer_prompt` | installer waiting on input (partition/confirm) | prompt string on serial | **STOP** — needs operator confirm (§8) |
| `login_prompt` | `login:` / display-manager greeter | `login:` on serial | success terminal state (matches stage12 contract) |
| `crash_screen` | kernel panic trace, Xorg failure, black-with-cursor-forever | `Kernel panic`, `Oops`, `BUG:` | **STOP** — capture evidence, classify as failure |
| `unknown` | none of the above | — | hold, re-tick; N consecutive `unknown` → escalate |

**Serial is the tie-breaker, not the agent's eyes (§6).** The visual label is a hypothesis;
the serial console confirms or overrides it. A black screen that the vision classifier might
read as `unknown` is unambiguously `crash_screen` if serial carries `Kernel panic`, and
unambiguously `kernel_boot` if serial is actively scrolling systemd units. When vision and
serial disagree, serial wins for the phases serial can see (`login_prompt`, `crash_screen`,
`kernel_boot`); vision wins only for the purely graphical phases serial is silent on
(`grub_menu` graphics, GUI installer screens).

A **stall detector** runs across ticks: if the screenshot is pixel-stable AND the serial
offset has not advanced for `stall_timeout` (default 120s) while phase ∉
{`installer_prompt`, `login_prompt`}, the phase is reclassified `unknown`→ escalate — this
is what catches a silent GRUB hang that the old `grep login:` only caught as a 180s timeout
with no diagnosis.

## 6. Serial console as a first-class sensor

The old smoke test treated serial as the *only* sensor; this pipeline treats it as the
*authoritative* one, co-equal with and arbiter over vision (§5).

- **Sink.** Keep `-serial file:…/serial.log` for parity with `stage12_qemu_test`'s markers,
  AND expose it live. The clean way is `-chardev`-backed multiplexing: route serial to a
  unix socket the agent reads incrementally while QEMU also logs to the file, e.g.
  `-chardev socket,id=ser0,path=…/serial.sock,server=on,wait=off,logfile=…/serial.log`
  `-serial chardev:ser0`. The agent reads the socket (push, no polling-reread — respects
  the "don't re-read an unchanged file" discipline); the file is the durable evidence
  artifact.
- **Markers.** `login:` stays the success marker (stage12 contract preserved). Failure
  markers: `Kernel panic`, `Oops:`, `BUG:`, `Call Trace:`, `Entering emergency mode`,
  `You are in rescue mode`. These are deterministic; the agent does not need vision to fire
  them, which is why §5 lets serial override the visual label.
- **As evidence.** `serial.log` is the per-run artifact cited in the evidence chain
  (hal-spec §9.4 item 6 audit-trail pattern): a boot failure is reported with the serial
  tail + the `crash_screen` screenshot, not with a prose claim.

## 7. Control lease — input arbitration

Frank (VNC keyboard) and the agent (`qemu_send_keys`) can both type into the one shared
display. Two typists on one keyboard corrupt each other's input — openQA sidesteps this by
being the *only* input source; we cannot, because manual takeover is the whole point. So
input is arbitrated by a **single-writer control lease**, advisory and cooperative:

- One lease file, `…/control.lease`, holds `{holder, acquired_at, ttl, reason}`. Holder is
  `agent` or `frank`.
- **Default holder is `frank`** (fail-safe toward the human): on VM launch the lease is
  unheld, and `qemu_send_keys` refuses unless the agent explicitly holds it. The agent must
  `qemu_acquire_lease` before any keystroke and `qemu_release_lease` after its action
  completes. Holding is scoped and short — acquire, send the chord, release — never a
  session-long grab.
- **TTL + heartbeat.** A lease auto-expires after `ttl` (default 30s) so a crashed agent
  cannot wedge the keyboard held-forever; the agent renews only while actively driving.
- **Frank always preempts.** A manual-takeover signal (a hotkey, or Frank setting the lease
  to `frank`) immediately revokes the agent's lease; the agent's next `qemu_send_keys` fails
  the lease check and the agent falls back to observe-only. The human is never blocked
  waiting on the agent to release. Reads (screenshot/serial) are never leased — both parties
  observe freely at all times.
- The lease is **arbitration, not authorization.** Holding the lease lets the agent *attempt*
  a keystroke; whether that keystroke is *allowed* is the separate energize-confirm decision
  (§8). An agent can hold the lease and still be refused a destructive keystroke.

## 8. Keystrokes through the energize-confirm gate

Keystrokes are where observation becomes action, so they ride the **existing** energize-confirm
gate (hal-spec §9) rather than a new one — there is exactly one confirm gate and one confirm
tool in Limpet, `limpet_confirm_destructive` / `confirmation_id` (hal-spec §9.4 item 8); this
pipeline reuses it, it does not add a parallel mechanism.

Classification of a keystroke request:

- **`read`-equivalent navigation** that only moves a selection without committing —
  arrow keys in a menu, scrolling — is `caution`. The agent may send these autonomously
  while holding the lease, because they energize nothing: no byte reaches disk, no
  installer step commits. (This keeps routine "page down the GRUB menu" from needing a
  human on every arrow key.)
- **Energizing keystrokes** — any key that *commits* an action that writes the target disk
  or launches the install: `Enter` on an installer "Erase disk and install" / partition-commit
  screen, a confirm of a bootloader step, typing into a destructive prompt. These are
  `destructive` and **require a per-invocation `confirmation_id`** — the agent calls
  `limpet_confirm_destructive`, a human approves *that specific keystroke*, and the
  `confirmation_id` is attached to the `qemu_send_keys` call. No token → `qemu_send_keys`
  refuses (fail closed). Confirmation does not pre-authorize the next destructive keystroke
  (hal-spec §9.4 item 4).
- **`installer_prompt` is the trip-wire.** When §5 classifies `installer_prompt`, the agent
  STOPS and does not advance it autonomously even with the lease — advancing an installer
  prompt is presumptively energizing. It surfaces the screenshot + serial to the operator
  and waits for a confirm (or for Frank to take the keyboard via the lease, §7).

Why the gate belongs here and not only at the HAL: in an **install** test the QEMU guest is
the thing being energized — its virtual disk is written, exactly as a physical target's flash
would be. The agent autonomously driving an installer to "confirm wipe" is precisely the
autonomy the gate exists to stop (hal-spec §9.1). That it is a VM, not physical hardware, does
not change the classification; it changes only the blast radius.

Headless/batch behavior matches hal-spec §9.4 item 5: if no interactive operator is attached,
destructive keystrokes **queue and wait** — they never auto-proceed. A fully autonomous ISO
boot test therefore validates everything up to the first energizing keystroke (BIOS → GRUB →
kernel → installer-reached, or → login on a live/non-install boot) without a human, and blocks
at the first commit screen for a confirm — which is the correct safety posture, not a
limitation.

## 9. Pipeline modes

Two modes over the same machinery:

1. **Autonomous boot smoke test** (CI / `build.sh` successor to `stage12`). No operator, no
   VNC viewer needed. Boot → tick the classifier → assert the run reaches `login_prompt`
   (live boot) or `installer_prompt` (install media) within a budget, with no `crash_screen`
   and no stall. On failure, emit the serial tail + the failing-phase screenshot as evidence.
   This strictly dominates the current `grep login:` test: same success marker, far richer
   diagnosis, same no-human requirement (it stops cleanly at the first energize point).
2. **Interactive install test** (Frank in the loop). Same VM, Frank on `localhost:5901`,
   agent observing and driving the non-destructive phases, every destructive keystroke
   gated through §8, Frank able to seize the keyboard any time via the lease (§7).

Both produce the same evidence artifacts in `/tmp/limpet-iso-test/`: `serial.log`, the frame
sequence, the phase timeline, and the lease/confirm audit log.

## 10. Relationship to existing build.sh

`stage12_qemu_test` (`iso-builder/build.sh:557`) is not deleted — it is the degenerate
`-nographic`/grep form of mode 1. The intended path is for mode 1 to **replace** stage12 as
the ISO's boot gate (same success contract, richer evidence), with stage12's current
invariant (`login:` within 180s) preserved as mode 1's baseline assertion. The interactive
mode 2 is net-new and depends on `qemu-mcp` (§4), which is the only new component this design
requires — everything else is QEMU stock flags plus the already-specified energize-confirm
gate.

## 11. Open items (for implementation tickets, not this design)

- `qemu-mcp` server itself: QMP client, VNC framebuffer grabber, serial socket reader, the
  tool manifest with `action_class` values above. (new component)
- Phase-classifier prompt/schema: the closed label set in §5 is the contract; the
  vision+serial fusion rule (§5/§6) is the logic.
- Lease hotkey for Frank's manual-takeover signal (§7) — a VNC-side binding or a tiny
  control endpoint.
- UEFI/OVMF launch variant pflash paths on the target KVM node (§3).
- Node placement: these runs need a KVM-capable host the worker can reach; ISSUE-LOG row
  1393 records the earlier artifact/`--add-dir` and `qemu-system-x86_64` allowlist gaps that
  a boot-test worker hit — the implementation ticket must grant the run dir and the qemu
  binary, or ssh to a KVM node.
