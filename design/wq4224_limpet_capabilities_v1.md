# wq#4224 — LIMPET: versioned capability vocabulary (`limpet.capabilities.v1`)

Status: DESIGN — not implemented. Author: build worker, 2026-10-18.
Source pattern: lampOS evaluation (wq#4209, `/home/lumen/scratch/research/lampOS-evaluation.md`).
Binds to: `docs/plugin-spec.md` §6.2/§6.4 (manifest `[capabilities]`, two-axis gate),
the energize-confirm gate (`design/wq3806_limpet_architecture.md` §4/§6,
`docs/limpet_vision.md:44-48`), and the HAL spec's §9 (energize-confirm, HAL-wide).

> **Repo boundary.** The authoritative HAL spec is `docs/hal-spec.md` in
> `/home/lumen/projects/limpet/` (not a git repo), outside this worktree — the
> same situation `docs/plugin-spec.md` §6.4 records for the plugin-registry
> source doc. This design doc is the git-tracked home of the vocabulary; §7
> below carries the drop-in patch blocks for `hal-spec.md` and the in-repo
> `docs/plugin-spec.md` hook (§6.5, landed with this ticket).

---

## 1. Problem

Limpet HAL manifests declare hardware affordances as free-form booleans. The
spec's own examples (`hal-spec.md` §3.2, §3.4, §4.1) use `capture_video`,
`capture_audio`, `visual_input`, `screen_reading`, `kvm_display`,
`ml_inference`, `flash_read`, `flash_write`, `adb`, `fastboot`,
`exploit_bootrom` — invented per manifest, with no shared registry, no
semantics a second party can rely on, and no safety class attached to the name.

Three consequences:

1. **A tool plugin cannot state what hardware it needs.** The registry
   (`limpet/registry_index.py`, tool-catalog.json) has no capability axis to
   match a tool's requirement against a connected device's declaration, so the
   capability-filtered tool exposure the lampOS eval identified as high-value
   (wq#4209 §3, "filter the tool list the agent sees by capabilities present")
   has nothing to filter on.
2. **Two manifests can spell the same affordance differently** (`visual_input`
   vs `capture_video` vs a future `video_in`) and nothing reconciles them.
3. **Safety class lives on the action, not the capability.** `hal-spec.md` §9
   classifies *actions* `read_only`/`energizing`; a reader of a manifest's
   `[capabilities]` block cannot tell, from the name alone, whether exercising
   it energizes the target.

lampOS solved the shape with a frozen, versioned capability namespace
(`autonomous.capabilities.v1`): names are never removed or repurposed, a body
declares what it has, a skill declares what it needs, and each capability
carries a privacy class and a safety class. This doc defines Limpet's
equivalent, `limpet.capabilities.v1`.

**License note (non-negotiable).** lampOS `hal/` is GPL-3.0 (wq#4209 §0, §6).
This design takes the *pattern* — a frozen versioned namespace with per-name
safety/privacy classes — and nothing else. Every capability name below is
Limpet-specific (RE/hardware-security domain: JTAG, flash, BootROM), not copied
from lampOS's robot vocabulary (audio/vision/motion/expression/companion). No
lampOS code or names are reused.

---

## 2. Namespace and freeze rules

The namespace identifier is the string `limpet.capabilities.v1`. A manifest
declares conformance by that string so a future `v2` can coexist during
migration.

**Freeze rules** (the property that makes the vocabulary a contract, drawn from
how long-lived namespaces stay stable — Linux `CAP_*` constants, Android
permission strings, SPDX license IDs, Wayland stable protocols: all append-only,
none repurposed):

1. **Append-only.** New capabilities are added to `v1`. A new name never
   requires a `v2`.
2. **Never removed.** Once published in `v1`, a name stays in `v1` forever. If a
   capability becomes obsolete it is marked `deprecated` (still listed, still
   parsed), never deleted — a manifest that declares it must not start failing
   validation on upgrade.
3. **Never repurposed.** A name's meaning and its safety class are fixed at
   publication. Tightening semantics in a way that changes which hardware
   matches, or loosening a capability's safety class, is a repurpose and is
   forbidden in `v1`; it requires a new name (or `v2`).
4. **Safety class may only tighten.** A capability published as `read_only` may
   never be reclassified `energizing` (that would silently gate calls that used
   to be autonomous — surprising, but fail-closed, so permitted via deprecation
   of the old name and a new energizing name). A capability published
   `energizing` may **never** be reclassified `read_only` — that weakens the
   gate and is forbidden outright (see §4).
5. **One capability = one affordance.** A name describes a single hardware
   affordance, not a device class or a tool. `video.capture`, not
   `magewell_card`; `flash.write`, not `mtkclient`.

A capability record has four fields: `name` (dotted `domain.affordance`,
lowercase, stable), `class` (`read_only` | `energizing`), `privacy`
(`none` | `content-capture`), and a one-line `semantics` string. `class` and
`privacy` default to the fail-closed value when a manifest omits them (§4).

---

## 3. `limpet.capabilities.v1` vocabulary

Dotted `domain.affordance` names. `class` is the energize classification (§4);
`privacy` flags capabilities that capture data off the target (the lampOS
privacy-class pattern, adapted).

### 3.1 Observation — read-only, no drive into the target

| name | class | privacy | semantics |
|---|---|---|---|
| `usb.enumerate` | read_only | none | Enumerate/identify a USB device: VID:PID, descriptors, USB mode/boot-mode detection. No bus mastering beyond standard enumeration. |
| `video.capture` | read_only | content-capture | Capture video frames from a target's display output (HDMI/UVC capture card). Appliance is a passive sink. |
| `audio.capture` | read_only | content-capture | Capture audio from the target (capture-card audio companion). |
| `vision.screen_read` | read_only | content-capture | Extract text/UI state from captured video (OCR of the target's screen). |
| `vision.component_id` | read_only | content-capture | Photograph and identify PCB components / board features (vision pipeline, wq#3900). |
| `logic.capture` | read_only | none | Passive digital logic / protocol capture (logic analyzer; high-impedance observation). |
| `radio.receive` | read_only | content-capture | SDR receive. RF may carry third-party communications — privacy-flagged. |
| `ml.inference` | read_only | none | Local ML-accelerator inference (Coral/NCS2). No contact with the target under test. |
| `serial.console_read` | read_only | content-capture | Read a UART/serial console the target is already driving. The adapter's TX line stays idle. |
| `adb.pull` | read_only | content-capture | Read files off a target over ADB (`adb pull`). Reads target state; does not modify it. |
| `kvm.switch` | read_only | none | Select a KVM switch's active port. Control-plane only; applies no power or signal to the target under test. |

### 3.2 Target-driving — energizing (confirm-gated, §4)

| name | class | privacy | semantics |
|---|---|---|---|
| `flash.read` | energizing | content-capture | Read flash (SPI/eMMC/NAND) via a programmer that applies probe power and clocks the chip. Applies power to the target → energizing (§4). |
| `flash.write` | energizing | none | Write or erase flash (SPI/eMMC/NAND/NOR). |
| `debug.connect` | energizing | none | Attach JTAG/SWD — drives the debug bus into the target. |
| `debug.memory_read` | energizing | content-capture | Read memory/registers over JTAG/SWD. Requires a driven debug bus → energizing (§4). |
| `debug.memory_write` | energizing | none | Write memory/registers; set breakpoints that modify target execution state. |
| `serial.console_write` | energizing | none | Drive the serial TX line into the target (interactive console input, bootloader commands). |
| `bootloader.unlock` | energizing | none | Bootloader unlock (`fastboot oem unlock`, mtkclient write-DA). |
| `bootloader.flash` | energizing | none | Flash boot/recovery/partition images; fastboot/Odin/rkdeveloptool writes. |
| `exploit.bootrom` | energizing | none | Execute a boot-ROM / boot-vector exploit (kamakiri, checkm8, EDL firehose load) from `limpet/data/soc-exploit-db/`. |
| `power.cycle` | energizing | none | Power-cycle the target, or drive reset/GPIO lines. |
| `input.hid` | energizing | none | Drive keyboard/mouse HID into the target (KVM input side). |
| `adb.shell` | energizing | content-capture | ADB interactive shell / `exec`. Can read or write the target filesystem — fail-closed to energizing. |
| `adb.push` | energizing | none | Write to the target over ADB (`adb push`/`install`). |
| `radio.transmit` | energizing | none | SDR transmit toward a target. **See §6: subject to the open `needs_emission` decision (wq#4147); v1 names the capability but does not resolve its emission policy.** |

### 3.3 Count and growth

24 names at publication (11 read-only, 13 energizing). The set is deliberately
affordance-grained, not device-grained, so a new device reuses existing names
and only genuinely new affordances append (freeze rule 1). This is the same
grain as lampOS's ~15 robot capabilities, re-domained to hardware RE.

---

## 4. Safety class: `read_only` | `energizing`, default energizing

Each capability carries exactly one safety class. The default, when a manifest
omits the class or declares an unknown/ambiguous name, is **`energizing`** —
fail closed, per `hal-spec.md` §9 ("A missing or ambiguous classification
defaults to `energizing`, never `read_only`"). This is the same rule the
energize-confirm gate already applies to actions; the vocabulary pins it to the
*name* so a manifest reader and the broker agree before any call runs.

**The line.** A capability is `read_only` only if exercising it places **no
current and no driven signal into the target under test** — the appliance is a
pure observer (passive capture, standard USB enumeration, local compute, reading
a bus the target itself drives). Any capability that applies probe power, masters
a bus toward the target, drives JTAG/SWD, writes, unlocks, exploits, or toggles
power/reset/GPIO is `energizing`.

### 4.1 Resolving the `hal-spec.md` duplicate-§9 conflict (toward fail-closed)

`hal-spec.md` contains two sections numbered `## 9` (a known doc defect, wq#4209
§3; fix tracked separately). They disagree on bus-touching reads:

- The HAL-wide §9 (authored 2026-10-15) says any action that *"applies probe
  power, drives JTAG/SWD ..."* is energizing.
- The older §9 "9.2 Action Classification" (2026-10-14) lists "Flash/memory dump
  (read SPI, eMMC, NAND)" and "JTAG/SWD register read, memory read" as **READ
  (autonomous)**.

`limpet.capabilities.v1` follows the **later, HAL-wide §9** and classifies
`flash.read`, `debug.connect`, and `debug.memory_read` as **energizing**,
because each applies power or a driven signal to the target. This is the
fail-closed reading the ticket mandates ("defaulting to energizing per HAL
sec 9"). It **strengthens** the gate (a flash dump via an external programmer now
needs a confirm); it does not weaken it. Where an operator wants a specific read
verb to stay autonomous, that is expressed at the verb layer, not by
reclassifying the capability: see §5.

### 4.2 Relationship to the verb-level gate (no weakening)

The capability class does not replace the per-invocation energize-confirm gate;
it feeds it. Per `docs/plugin-spec.md` §6.4 the confirm is **per invocation at
the HAL broker**, never pre-authorized at enable time, and not waivable by trust
tier. The vocabulary adds one invariant on top:

- A verb may be marked autonomous (`action_class = read`,
  `[registry.tool_energize] tool = false`, `hal-spec.md` §9.2 READ list) **only
  if every capability it exercises is `read_only`.** A verb that touches an
  `energizing` capability is energize-gated regardless of its own
  `action_class` — the stricter of (capability class, verb class) wins.

This makes the two layers monotonic: nothing in the vocabulary can downgrade a
verb the gate would have confirmed, and `plugin-spec.md` §6.4's
`[registry.tool_energize]` override "only ever *tightens or relaxes within* the
energize axis; it cannot waive the per-invocation broker confirmation." The
vocabulary is consistent with that — it only ever *raises* a verb's floor.

### 4.3 Privacy class

The second lampOS column, adapted to Limpet's threat surface. `content-capture`
flags capabilities that pull data off the target (its screen, audio, files, RF,
memory contents). It is advisory in v1 — it drives operator-facing labelling and
future data-handling policy (retention, redaction), not the energize gate. It is
recorded now so the name carries it from day one (freeze rule 3 fixes privacy
class at publication, same as safety class).

---

## 5. Mapping existing manifests onto the vocabulary

Every free-form boolean in `hal-spec.md` maps to one or more v1 names. The map
is the migration contract: HAL manifests replace their `[capabilities]` booleans
with declared v1 names, and the loader treats an unmapped legacy boolean as an
undeclared affordance (freeze-closed: no match, not a silent pass).

| legacy boolean (`hal-spec.md`) | `limpet.capabilities.v1` | class |
|---|---|---|
| `capture_video` (§3.2) | `video.capture` | read_only |
| `capture_audio` (§3.2) | `audio.capture` | read_only |
| `visual_input` (§3.2) | `video.capture` | read_only |
| `screen_reading` (§3.2) | `vision.screen_read` | read_only |
| `kvm_display` (§3.2) | `video.capture` + `input.hid` (+ `kvm.switch` if a switch) | mixed — `input.hid` is energizing |
| `ml_inference` (§4.1 coral) | `ml.inference` | read_only |
| `flash_read` (§3.4, §4.1) | `flash.read` | energizing |
| `flash_write` (§3.4) | `flash.write` | energizing |
| `exploit_bootrom` (§3.4, §4.1) | `exploit.bootrom` | energizing |
| `adb` (§3.4) | `adb.pull` (ro) + `adb.shell` + `adb.push` (energizing) | mixed — decompose by verb |
| `fastboot` (§3.4) | `usb.enumerate` (getvar) + `bootloader.unlock` + `bootloader.flash` | mixed |
| debug-probe JTAG/SWD (§5.1 table) | `debug.connect` + `debug.memory_read` + `debug.memory_write` | energizing |
| logic analyzer (§5.3 table) | `logic.capture` | read_only |
| SDR RX (§5.3 RTL-SDR) | `radio.receive` | read_only |
| SDR TX (§5.3 HackRF) | `radio.transmit` | energizing (see §6) |
| UART adapter (§5.4 table) | `serial.console_read` (ro) + `serial.console_write` (energizing) | mixed |

Two migration notes:

- **`adb`/`fastboot`/`kvm_display` were conflated modes, not single
  affordances.** v1 decomposes each into its read and energizing parts, which is
  the whole point: the old single boolean could not say that `adb pull` is safe
  and `adb shell`/`adb push` are not. A manifest declares only the verbs its
  device actually exposes.
- **Informational specs are not capabilities.** `max_resolution`, `max_fps`,
  `formats`, `tpu_tops`, `supported_frameworks` (`hal-spec.md` §3.2, §4.1) stay
  as free-form device metadata; they are not affordances and get no v1 name.

Worked example — the Magewell manifest (`hal-spec.md` §3.2) becomes:

```toml
[capabilities]
vocabulary = "limpet.capabilities.v1"
provides = ["video.capture", "audio.capture", "vision.screen_read"]
# informational specs stay as-is:
max_resolution = "2048x2160"
max_fps = 60
formats = ["MJPEG", "YUV", "NV12"]
```

and the MediaTek BROM target (`hal-spec.md` §3.4) becomes:

```toml
[capabilities]
vocabulary = "limpet.capabilities.v1"
provides = ["usb.enumerate", "flash.read", "flash.write", "exploit.bootrom"]
# every entry here except usb.enumerate is energizing → broker-confirmed per call
```

---

## 6. Open items (deferred, not resolved here)

- **`radio.transmit` and the emission axis (wq#4147).** `plugin-spec.md` §6.4
  carries an undecided open flag: SDR TX risk (third-party RF, regulatory
  exposure, parameter-scaled) is not cleanly the energize axis. v1 *names*
  `radio.transmit` and classes it `energizing` as the conservative default that
  §6.4 already stands on ("every TX-adjacent tool energize-gated as the
  conservative default"), but it does **not** pre-empt that owner's choice
  between overloading `needs_energize` and adding a `needs_emission` axis. If
  `needs_emission` is adopted, `radio.transmit` gains an emission class in a
  future append; the name does not change (freeze rule 2).
- **Duplicate `## 9` in `hal-spec.md`.** Resolved for the vocabulary (§4.1) by
  following the later HAL-wide §9; the underlying doc defect (two sections
  numbered 9) is a separate fix (wq#4209 §3 verdict).
- **Capability-filtered tool exposure (wq#4209 §3 opportunity).** This doc gives
  that work its missing axis; the registry change to filter the agent-visible
  tool list by capabilities currently present is a separate ticket.

---

## 7. Drop-in patches

### 7.1 `docs/plugin-spec.md` §6.5 — landed with this ticket (in-repo)

A new subsection §6.5 adopts `limpet.capabilities.v1` as the vocabulary for the
manifest `[capabilities]` block and points here. See `docs/plugin-spec.md`.

### 7.2 `hal-spec.md` — proposed insert (out-of-repo, not committed here)

`hal-spec.md` lives in `/home/lumen/projects/limpet/` (non-git, outside this
worktree — §0). The following block is ready to drop into `hal-spec.md` §3.2
(manifest schema) and to be cross-referenced from its §9, when that doc is next
edited in place:

> **`[capabilities]` uses `limpet.capabilities.v1`** (see
> `design/wq4224_limpet_capabilities_v1.md` in foreman). Declare
> `vocabulary = "limpet.capabilities.v1"` and list affordances in `provides =
> [...]` using frozen capability names. Free-form booleans are deprecated;
> the loader maps known legacy booleans per §5 of the design doc and treats an
> unknown one as undeclared (fail-closed). Each capability's `read_only` /
> `energizing` class is fixed by the vocabulary, defaulting to `energizing`;
> the §9 energize-confirm gate reads the class from the name, and a verb may be
> autonomous only if every capability it exercises is `read_only`.
