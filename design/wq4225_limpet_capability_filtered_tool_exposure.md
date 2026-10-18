# LIMPET design — capability-filtered MCP tool exposure (HAL-driven tool list)

> Status: DESIGN ONLY (wq#4225). No code in this ticket. Source pattern:
> lampOS evaluation `/home/lumen/scratch/research/lampOS-evaluation.md` §2–§3
> (wq#4209), concept-map row "SKILL declares required capabilities; skill loads
> only if body has them" → "Filter the tool list the agent sees by capabilities
> currently present." Directly serves VISION principle 5
> (`docs/limpet_vision.md:56-58`): *the agent suggests the next step from what's
> connected*.
>
> **Hard dependency:** the frozen capability vocabulary `limpet.capabilities.v1`
> designed in wq#4224. Every capability name used by a `requires` field or a
> HAL `provides` field below MUST be a member of that vocabulary. Until v1 is
> frozen the fields specified here are **reserved**, not enforced (status table,
> §8). This doc does not define capability names; it defines how the meta-server
> *uses* them to decide tool visibility.
>
> Prior-art note: the external web/github prior-art search specified by the
> research doctrine was attempted via `fleet-search web|github` and both
> backends were down this session (SearXNG engines CAPTCHA/suspended, no
> Brave/GitHub token — the known wq#2595 hole). wq#4209's lampOS evaluation is
> itself the completed primary prior-art study for this exact pattern and is
> cited throughout.

## 1. Problem

The MCP meta-server (wq#3808/#3821) aggregates every enabled tool from the Tool
Registry into one JSON manifest and serves the whole set to the agent; as
`limpet/DESIGN-DECISIONS.md:234-235` records, *"the meta-server has no
operator-confirm step — any connected agent can call any enabled tool."* Tool
visibility today is a function of *installed + enabled + healthy*, not of *is
the hardware this tool needs actually plugged in right now*.

Consequences:

- The agent sees `openocd` (JTAG/SWD), `flashrom` (SPI flash), `hackrf`
  (SDR-TX), `ubertooth` (BLE) etc. even when no probe, no flasher, no radio is
  attached. It can select and attempt a tool that cannot possibly work, burning
  a turn and producing a confusing hardware-absent error instead of a next-step
  suggestion.
- The only hardware-coupling field that exists,
  `[registry].hardware_required` (`limpet/registry_index.py:208,332`), is a list
  of raw `usb:VID:PID` strings used purely as a *search* filter. It is brittle
  (the MS2109/MS2130 note in `limpet/hardware.d/06-hdmi-capture.toml` shows
  VID:PID varies per rebrand of identical silicon) and it is not consulted when
  the meta-server decides what to expose.
- Tools cannot state *what class of hardware* they need in a way that survives
  a rebrand or a second compatible device, because there is no capability
  vocabulary to state it against — the gap wq#4224 fixes.

lampOS solved the analogous problem for robot skills: a skill declares the
capabilities it requires and *loads only where the body has them*
(`lampOS-evaluation.md:33`). This doc ports that mechanism — not its code (its
`hal/` is GPL-3.0; `lampOS-evaluation.md:88-91`) — to Limpet's MCP tool list.

## 2. Model

Three sets, all computed server-side inside the meta-server; the agent is a
read-only consumer of the result.

- **`R(tool)` — required capabilities.** Declared per tool (§3). A set of
  `limpet.capabilities.v1` names. Empty for pure-software tools (ghidra,
  radare2, unblob — they need no attached hardware and are always exposed).
- **`P` — present capabilities.** The union, over every device the HAL
  currently reports as detected, of that device's declared `provides`
  capabilities (§4), plus a fixed set of always-present host capabilities
  (CPU/RAM/disk — a pure-software tool requires none of these, so in practice
  the host set can be empty and software tools qualify by having `R = ∅`).
  `P` is recomputed on every HAL hotplug event.
- **Exposure rule.** A tool is listed in `tools/list` **iff** `R(tool) ⊆ P`
  **and** every name in `R(tool)` is a known member of `limpet.capabilities.v1`.
  Otherwise it is hidden. (Fail-closed clauses: §5.)

This is the tool-side mirror of lampOS's "skill loads only where the body has
the parts," with Limpet's event-driven HAL supplying `P` at hotplug time
instead of lampOS's declaration-driven one-shot `boards.json` match
(`lampOS-evaluation.md:48-50`).

## 3. Tool-side declaration: `requires` (capabilities, not VID:PID)

Add one optional field to the tool manifest / `tool-catalog.json` tool entry
and the registry-index record:

```toml
# in a tool plugin's limpet-plugin.toml [registry] block, or the catalog entry
requires_capabilities = ["debug.jtag"]     # names from limpet.capabilities.v1
```

- **Distinct from the existing `capabilities` field.** `tool-catalog.json`
  already carries a `capabilities` list (e.g. unblob →
  `["firmware-extraction","format-identification","recursive-unpack"]`): that
  describes *what the tool does*. `requires_capabilities` describes *what
  hardware must be present for it to do anything*. They are different axes and
  both stay; do not overload one for the other. (wq#4224 may split the
  "what-it-does" list into its own namespace; that is its call, not this
  ticket's.)
- **Default empty ⇒ always exposed.** Omitting the field means the tool needs
  no attached hardware. This keeps every existing manifest and every row of
  `tool-catalog.json` valid and visible unchanged — the pure-software majority
  of the catalog is unaffected.
- **Relationship to `hardware_required` (VID:PID).** `hardware_required`
  (`limpet/registry_index.py:332`) stays valid as a *narrower* pin for the rare
  tool that needs one specific silicon, but it is now resolved through the same
  present-set check: a `usb:VID:PID` entry counts as satisfied iff the HAL
  reports that exact device detected. `requires_capabilities` is the forward,
  rebrand-proof field and SHOULD be preferred; a tool may declare both (AND
  semantics — all capabilities present *and* the pinned device detected).
  Migration is mechanical and additive, matching how `[optional_dependencies]`
  and `[conflicts]` were added (`docs/plugin-spec.md:39-55`).
- **Authored in TOML, rendered to JSON.** Per `DESIGN-DECISIONS.md:220-231`
  (wq#3935 D2) the meta-server's served manifest is and stays JSON; `requires`
  is authored in the TOML manifest and emitted into the rendered JSON tool
  record at render time, where validation (every name ∈ v1 vocabulary) is cheap
  and failing is free — exactly where `manifest.py` already fail-closes the
  `category` enum (`limpet/manifest.py:146-148`).

## 4. HAL side: devices declare what they `provides`

The present set `P` is produced by the HAL, never by the agent. Each HAL
hardware descriptor (`limpet/hardware.d/*.toml`) and target-device entry
(`limpet/target-devices.d/*.toml`, wq#3940/#4188) gains an optional
`provides` list of `limpet.capabilities.v1` names:

```toml
[[device]]
name = "Generic UVC HDMI-to-USB capture dongle (MS2109)"
usb_ids = ["534d:2109"]
provides = ["video.capture.hdmi"]        # names from limpet.capabilities.v1
```

- When `limpet-hald` matches a hotplugged device (udev → hal-notify →
  limpet-hald, the event path in `lampOS-evaluation.md:49`), the capabilities
  that device `provides` enter `P`; on unplug they leave `P` (if no other
  detected device still provides them).
- A detected device whose descriptor declares no `provides` contributes
  nothing to `P`. That is correct fail-closed behaviour: an un-mapped device
  cannot silently unlock a tool. It is also a visible backlog signal (the
  device is detected but capability-mute) that a `provides` mapping is owed —
  surfaced, not guessed.
- **Trust boundary.** `P` is computed only from HAL detection state. The agent
  cannot assert "capability X is present" to unlock a tool; there is no
  agent-writable path into `P`. This is the core anti-bypass property — without
  it, capability filtering would be security theatre the model could argue its
  way past, the exact failure lampOS's in-process gate was built to avoid
  (`lampOS-evaluation.md:70-71`).

## 5. Fail-closed behaviour (the required specification)

The filter resolves to **hidden** in every ambiguous or degraded case. Four
distinct cases, three of which hide:

| case | `tools/list`? | reason returned on request (§6) | log |
|---|---|---|---|
| `R ⊆ P`, all names known | **exposed** | n/a | — |
| known capability name, not in `P` (hardware absent) | hidden | `missing: [names] — attach a device that provides them` | debug |
| **unknown requirement** — a name in `R` not in `limpet.capabilities.v1`, or a malformed `requires` field | hidden | `manifest-error: requires unknown capability 'X'` | **warn — manifest defect** |
| HAL state unavailable / meta-server cannot read detection | `P := ∅`; only `R = ∅` tools exposed | `hal-unavailable` | **warn** |

Two fail-closed rules are load-bearing and must not be softened:

1. **Unknown requirement ⇒ hidden, never exposed.** A typo'd or
   not-yet-frozen capability name hides the tool rather than exposing it
   "to be safe for the agent." This matches `manifest.py`'s existing posture —
   an unrecognised enum value is a validation *error*, not a pass-through
   (`limpet/manifest.py:146-148`) — and HAL §9's rule that an unclassified item
   defaults to the restricted class. An unknown requirement is a manifest bug;
   the safe response to a bug is to withhold the tool and log the defect, not to
   show a tool whose preconditions are unverifiable.
2. **Absent HAL ⇒ empty present set, not universal set.** If the meta-server
   cannot read HAL detection state, `P` is empty, so every hardware-requiring
   tool hides and only pure-software tools remain. The degraded mode is "fewer
   tools," never "all tools."

Both rules share one invariant: **the default state of a tool whose
preconditions cannot be positively verified is hidden.**

## 6. "Not available, and here's why" — serving principle 5

Hiding a tool silently would defeat VISION principle 5
(`docs/limpet_vision.md:56-58`): the agent is supposed to *suggest the next
step from what's connected*. The meta-server therefore exposes one read-only
introspection tool (working name `limpet.unavailable_tools`) that is itself
always present and takes no hardware:

- Called with no argument, it returns every currently-hidden tool with its
  structured reason (the §5 table's "reason" column) — including, for a
  `missing`-hardware tool, the capability names it still needs and (resolved
  through the inverse of the §4 `provides` map) an example device class that
  would supply them.
- Called with a tool name, it returns that one tool's reason.
- It returns **descriptions only; it never makes a hidden tool callable.** The
  invariant is that `tools/list` is the sole set of callable tools; this
  endpoint is a window onto the hidden set, not a side door into it.

This is what turns a hidden tool into an actionable next step: the agent can
tell the operator *"flash-write is unavailable because no SPI flasher is
attached; connect a CH341A or an FT2232H-based programmer and it will appear,"*
which is principle 5 applied to the tool surface. It also directly adopts the
lampOS "status table per safety slice" lesson (`lampOS-evaluation.md:60`) —
nothing is hidden without a legible, enumerable reason.

## 7. Dynamic re-exposure on hotplug

The filter is live, not boot-time. On each HAL hotplug/unplug event the
meta-server recomputes `P`, re-evaluates the exposure rule for every tool, and —
if the visible set changed — emits the MCP `notifications/tools/list_changed`
notification so the agent re-fetches `tools/list`. Plug in a JTAG probe and the
on-chip-debug tools appear *in the same session*; unplug it and they go. This
is the event-driven half lampOS lacks (`lampOS-evaluation.md:50`: "lampOS has no
hotplug detection") and is the mechanism that makes "what's connected" a live
input to the agent rather than a one-time snapshot.

Recompute is cheap (a set-subset test per tool over a small `P`) and belongs on
the existing hot-reload path the served JSON manifest already has
(`DESIGN-DECISIONS.md:222-224`), so this adds a trigger, not a new serving
mechanism.

## 8. Interaction with the energize-confirm gate (two orthogonal gates)

This is the subtle part and the ticket's explicit ask. The exposure filter and
the energize-confirm gate answer different questions and are composed in series;
neither may be implemented as the other.

| | exposure filter (this ticket) | energize-confirm gate (`docs/limpet_vision.md:44-48`, `plugin-spec.md §6.4`) |
|---|---|---|
| question | *Can this tool run at all right now — is its hardware present?* | *Should this one invocation drive the target — does the operator confirm?* |
| axis | capability presence (danger = *nothing*; purely a "will it work" test) | energize (danger *to the target*: `plugin-spec.md:284-287`) |
| point | `tools/list` (what the agent can see/select) | per-invocation at the HAL broker (`plugin-spec.md:308-311`) |
| granularity | per tool | per energize verb (`[mcp.tools_energize]`, `[registry.tool_energize]`) |
| fail-closed default | hidden | energize-gated / refused |

Composition rules (all four must hold):

1. **Exposure is upstream of energize.** A tool hidden by the filter is never
   in `tools/list`, so it is never called, so it never reaches the energize
   gate. Hiding is a *visibility* decision made before any invocation; the
   energize gate is an *authorization* decision made at invocation. They stack;
   they do not overlap.
2. **Presence is not consent.** A device appearing in `P` makes its tools
   *visible*; it does **not** pre-authorize energizing it. Plugging in a JTAG
   probe makes `openocd.flash_write` appear, but every `flash_write` invocation
   still requires the per-invocation operator confirm at the broker. This is the
   same non-waiver the trust tier already obeys (`plugin-spec.md:305-311`:
   enabling a plugin never pre-authorizes an energize verb) — extended to say
   *detecting the hardware* never pre-authorizes it either.
3. **The filter is never a substitute for the energize gate, and vice-versa.**
   Read-only tools on present hardware stay fully autonomous (`enumerate`,
   `identify`, `capture`, `classify_access` — `plugin-spec.md:310-311`); the
   exposure filter must not hide them to approximate a confirm step it is not.
   Symmetrically, the energize gate must not be relaxed because a tool "only
   appears when hardware is present" — presence filtering adds no authorization
   and removes none. **This design changes which tools are *listed*; it does not
   touch the energize gate's deny/confirm matrix at all.** (Ticket constraint,
   and the wq#4224 sibling constraint, both honoured: the energize-confirm gate
   is not weakened.)
4. **Both gates fail closed to the same side on unknown.** An unknown
   `requires` capability hides the tool (§5); an unknown/absent energize
   classification energize-gates the verb (`plugin-spec.md:347`, HAL §9). A tool
   must clear *both* independent gates to actually drive hardware: visible
   (hardware present, requirement known) **and** confirmed (operator OK's the
   energize verb). Neither gate satisfies the other — exactly the two-axis
   independence `plugin-spec.md:289-292` establishes for privilege-vs-energize,
   now a three-way: **privilege ⟂ energize ⟂ presence.**

One worked trace: an SPI flasher is unplugged → `flashrom`'s `flash_write` is
hidden (filter) and moot. Operator plugs the flasher in → `P` gains
`flash.spi`, meta-server emits `tools/list_changed`, `flashrom` tools appear.
Agent selects `flash_read` (read-only) → runs autonomously. Agent selects
`flash_write` (energize verb) → broker demands the per-invocation confirm;
the fact that the filter exposed it grants nothing here.

## 9. Status (enforced / reserved) — so the spec cannot overclaim

Per the lampOS "mark each rule enforced vs reserved" lesson
(`lampOS-evaluation.md:60,75`):

| element | status | blocker |
|---|---|---|
| Exposure rule `R ⊆ P` + fail-closed semantics (§2,§5) | **reserved (design)** | needs `limpet.capabilities.v1` frozen (wq#4224) to validate names against |
| `requires_capabilities` tool field (§3) | reserved (design) | wq#4224 vocabulary; then additive to `manifest.py` + `registry_index.py` render |
| HAL `provides` field (§4) | reserved (design) | wq#4224 vocabulary; then additive to `hardware.d`/`target-devices.d` schema |
| `hardware_required` VID:PID pin (§3) | **enforced today** as a *search* filter only (`registry_index.py:208`); re-used by the exposure check when built | — |
| `limpet.unavailable_tools` introspection (§6) | reserved (design) | meta-server endpoint; no code this ticket |
| `tools/list_changed` on hotplug (§7) | reserved (design) | rides the existing hot-reload path |
| Energize-confirm gate (§8) | **enforced** (`[mcp.tools_energize]`, broker confirm) | unchanged by this design |

Nothing in §2–§7 is built by this ticket; this row-by-row status is the guard
against a reader believing the filter is live before wq#4224 lands.

## 10. Non-goals / deferred

- **Does not define capability names.** That is wq#4224.
- **Does not change the energize gate** (§8 rule 3).
- **Does not address SDR-TX / emission risk** — the open `needs_emission`
  question (`plugin-spec.md:370-399`) is orthogonal: emission is an
  authorization axis, presence is a visibility axis. A TX tool is still subject
  to whatever emission decision wq#4147 reaches; the filter only governs whether
  it is *listed* when an SDR is attached.
- **Does not implement a conformance suite** for the `provides`/`requires`
  mappings; that is the wq#4209 "HAL/plugin conformance suite" opportunity
  (`lampOS-evaluation.md:61,95`), a separate ticket.
