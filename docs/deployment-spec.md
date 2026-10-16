# Limpet — End-to-End Deployment Spec

> Status: design — wq#4061
> Scope: the complete user journey, ISO download through daily use. This spec is
> the spine; it references the sibling specs that own each sub-area rather than
> restating them. Where a referenced spec does not yet exist, it is named as a
> companion to be written and this spec carries only the interface it depends on.

Sibling specs this one leans on:

- Install wizard flow → `docs/install-wizard-spec.md` (companion; see §4).
- Target Device Wizard → `docs/target-wizard-spec.md`.
- Agent chat, model picker, greeting → `docs/agent-chat-spec.md`.
- Desktop / start bar → `docs/desktop-spec.md`.
- Settings keys (update, network, LLM) → `docs/settings-spec.md`.
- Plugin + tool registry → `docs/plugin-spec.md`, `limpet/tool-catalog.json`.
- Hardware support matrix → `docs/hardware-tiers-spec.md`.
- Service binaries + units → `packaging/limpet-hald`, `packaging/limpet-mcp-meta`,
  `packaging/limpet-tools`.

Limpet is an appliance, not an app (`docs/limpet_readme.md:29`). The deployment
story must therefore read like unboxing a device, not installing software on a
daily driver: download → write USB → boot → (try live or install) → first boot
brings the stack up → plug in a target → work.

---

## 1. Distribution

**Artifact.** One hybrid ISO (`limpet-<version>-amd64.iso`), BIOS+UEFI bootable,
isohybrid so the same file boots from USB or a VM. x86-64 only — the reference
box is a Lenovo M70q Gen 5, and any x86-64 PC with 16GB+ RAM works
(`docs/limpet_readme.md:73`, `docs/limpet_readme.md:87`).

**Hosting.** Primary: object storage behind the project's own domain (a download
page at `limpet.lumen-industries` / GitHub Releases mirror). The download page
lists, per release: ISO, VM images, every checksum, the signing key, and the
torrent/magnet. No login, no tracker beyond standard web logs.

**Integrity (publish all three, in this order of trust).**

1. `SHA256SUMS` + `SHA512SUMS` text files next to the ISO.
2. `SHA256SUMS.gpg` — a detached signature over the sums file, made with the
   Limpet release key. The key fingerprint is printed on the download page and in
   `docs/limpet_readme.md`; verification instructions assume the user fetches the
   key over a second channel (keyserver / this repo), not only from the download
   page, so a compromised page cannot swap both.
3. Per-file `.sha256` beside each VM image for quick spot-checks.

**Torrent.** A `.torrent` + magnet link for the ISO, web-seeded from the same
object storage so the download works whether or not peers are present. This is
the preferred path for large/offline-mirror sites.

**VM images.** Prebuilt, for evaluating Limpet without hardware (no HAL
auto-detection against real probes, but the full UI + agent + offline LLM work):

- `limpet-<version>.qcow2` (QEMU/KVM, libvirt).
- `limpet-<version>.ova` (VirtualBox / VMware).
- Each ships with USB passthrough notes so an evaluator can still forward a real
  target device into the VM.

**Bundled models.** The ISO ships with at least one local LLM baked in so the box
is useful fully offline on first boot (§5). The download page states the ISO size
and which model(s) are included; a smaller "netinst" ISO without bundled models
is offered for bandwidth-constrained downloads (model pulled on first boot
instead, per §5).

---

## 2. USB creation

The download page carries a per-OS guide. All three paths write the raw hybrid
ISO; none require unpacking.

**Windows — Rufus (recommended) or balenaEtcher.**
1. Download the ISO and `SHA256SUMS`.
2. Verify: `certutil -hashfile limpet-<version>-amd64.iso SHA256` and compare.
3. Rufus: select the USB device, select the ISO, keep "DD Image" mode when
   prompted (hybrid ISO), leave partition scheme on the default, Start.
4. Etcher alternative: Flash from file → select ISO → select drive → Flash.

**macOS — balenaEtcher (recommended).**
1. Verify: `shasum -a 256 limpet-<version>-amd64.iso` and compare.
2. Etcher: Flash from file → select drive → Flash. (Etcher over raw `dd` on
   macOS because it handles unmount/eject and avoids the wrong-disk footgun.)

**Linux — `dd` or balenaEtcher.**
1. Verify: `sha256sum -c SHA256SUMS`.
2. Identify the device with `lsblk` (confirm size; it must be the USB stick, not
   a system disk).
3. `sudo dd if=limpet-<version>-amd64.iso of=/dev/sdX bs=4M status=progress oflag=sync`
   — replace `/dev/sdX` with the whole device, never a partition.
4. `sync` before removing. Etcher alternative for users who want the guardrails.

Every path ends with: boot the target PC, open the one-time boot menu
(F12/F10/Esc depending on vendor), pick the USB device.

---

## 3. Boot

**GRUB menu** (default highlight on "Try Limpet Live", 10s timeout):

```
Limpet <version>

  > Try Limpet Live          (no changes to disk — evaluate first)
    Install Limpet           (guided installer, §4)
    Boot from first hard disk (leave this machine alone)
    Memory test (memtest86+)
    Advanced ▸               (safe graphics, nomodeset, serial console)
```

- **Try Limpet Live** boots the full environment off the USB into a live session:
  the whole stack comes up (§5), HAL auto-detection works against anything plugged
  in, and the user can run a real investigation without touching the internal
  disk. Workspaces live on tmpfs/USB and are lost on reboot unless exported (§11);
  the live session surfaces this in the agent greeting.
- **Install Limpet** launches the wizard (§4). The live session also carries an
  "Install Limpet" desktop launcher so a user who started in Try mode can commit
  without rebooting.
- **Boot from first hard disk** is the safety exit for a user who booted the USB
  by accident.
- **Memtest** is bundled because the reference workload (local LLM + emulation)
  is RAM-heavy and bad DIMMs are a common field failure.
- **Advanced** holds the nomodeset / safe-graphics / serial-console entries so a
  headless or oddball-GPU box can still reach the installer.

Default highlight is deliberately Live, not Install: the appliance pitch is
"plug a target in and see," and a destructive install should be a chosen action,
consistent with the energize-confirm doctrine (`docs/limpet_readme.md:68`).

---

## 4. Install

The installer wizard flow is owned by `docs/install-wizard-spec.md` (companion
spec). This deployment spec depends only on its contract:

- Input: a live/installer session from §3.
- Output on success: an installed system that, on next boot, runs the first-boot
  sequence in §5 and reaches the first-login experience in §6.
- The wizard must record the operator's LLM choice (bundled model vs. pull vs.
  external gateway) so §5's model step is a confirmation, not a fresh decision —
  it writes the `llm.*` settings keys defined in `docs/settings-spec.md:159`.
- The wizard must write the update channel/policy preference
  (`docs/settings-spec.md:338`) so §10 has a source of truth.

If `docs/install-wizard-spec.md` is absent at build time, treat the above four
bullets as the acceptance surface the installer must satisfy.

---

## 5. First-boot sequence

On the first boot of an installed (or live) system, systemd brings the stack up
in dependency order. The real units today are `limpet-hald` (binary at
`packaging/limpet-hald/bin/limpet-hald`, referenced as the HAL detector in
`docs/settings-spec.md:208`), `limpet-mcp-meta.service`
(`packaging/limpet-mcp-meta/debian/limpet-mcp-meta.service`), and
`limpet-registry-daemon.service`
(`packaging/limpet-tools/debian/limpet-registry-daemon.service`). Companion units
still to be authored: `limpet-hald.service`, `litellm.service`,
`limpet-agent.service` (see §9).

**Startup order and gating:**

```
systemd multi-user.target
  └─ limpet-hald.service            (HAL: enumerate USB probes/targets)
       └─ limpet-mcp-meta.service   (single MCP endpoint over all tools)
            └─ litellm.service      (local LLM gateway; optional — see degradation)
                 └─ limpet-agent.service (agent core; connects to meta + LLM)
```

Each step publishes a health check that the next `After=` + a readiness probe
depends on:

1. **limpet-hald** — enumerates connected hardware, writes the inventory the
   start bar and §6 read. Healthy when the detector has completed one full scan.
2. **limpet-mcp-meta** — the manifest-driven multiplexer. It ships with every
   child server disabled (`packaging/limpet-mcp-meta/debian/control:15`) and
   enables only what the installed tool set declares; healthy when it answers
   JSON-RPC `initialize` and the registry daemon has reconciled the manifest.
3. **litellm** — the local LLM gateway. First-boot model step: if the ISO bundled
   a model, this is a one-line confirm; if not (netinst), the agent offers the
   model picker, which reads the gateway's live `/v1/models`
   (`docs/agent-chat-spec.md:195`) rather than a hard-coded list, and pulls the
   selected model. In full offline mode with a bundled model, no network is
   touched.
4. **limpet-agent** — connects to the meta-server and (if up) the LLM gateway,
   then runs tool-health verification: `limpet-tools health`
   (`docs/limpet_readme.md:105`) over the registry, surfacing the `n/n✓` the
   start bar shows (`docs/desktop-spec.md:69`).

**Health checks** are the gate, not a timer: a slow model pull must not fail the
boot. A failed non-essential tool is reported (degraded `Tools: n/m✓`), never
fatal. The agent core comes up even if litellm never does — see §9 graceful
degradation.

---

## 6. First-login experience

On first login the desktop (XFCE start bar, `docs/desktop-spec.md:69`) loads and
the Agent Chat panel opens to a greeting from the agent, not a blank prompt. The
greeting:

- States what Limpet is in one line and names the box's hardware inventory from
  the §5 HAL scan ("I can see no target connected yet; your probes: ST-Link V2,
  CH341A").
- Reports stack health inline: tools `n/n✓`, LLM model in use (or "running
  without AI assistance — plug in a model or gateway in Settings" if litellm is
  down, §9).
- Offers a **guided tour** or **skip**. The tour is a short, dismissible walk:
  the start bar clusters (launchers | status | settings, `docs/desktop-spec.md`
  §2), the Target Wizard, the `/tools` command, and where workspaces live. Skip
  is one click and never nags again (a settings flag).

The hardware inventory is also reachable any time from the start bar `HW: n✓`
indicator and `limpet-tools hardware` (`docs/limpet_readme.md:115`), so the tour
is convenience, not the only path.

Live-session note: the greeting states that workspaces are ephemeral until
exported (§11) and offers the "Install Limpet" launcher.

---

## 7. Day-one workflow

The canonical first session:

1. **Plug in the first target.** `limpet-hald` detects it; the start bar `HW`
   indicator ticks up and the agent proactively notes the new device
   (auto-detection is the differentiator, `docs/limpet_readme.md:66`).
2. **Target Device Wizard launches.** Entry points and flow are owned by
   `docs/target-wizard-spec.md` (entry points §1, flow §2). It walks
   DETECT → IDENTIFY → confidence gate → SUGGEST workflow → WORKSPACE CREATE →
   ANALYZE handoff (`docs/target-wizard-spec.md:109`).
3. **Sample investigation (MediaTek tablet in BROM mode)**, mirroring the README
   example session (`docs/limpet_readme.md:130`):
   - HAL reports `MediaTek device in BROM mode (MT8167)`.
   - Agent suggests the DUMP → EXTRACT → ANALYZE → REPORT workflow.
   - `mtk_read_partition` dumps `boot` — this **energizes the target**, so it
     waits on the energize-confirm gate (`docs/limpet_readme.md:68`); read-only
     enumeration up to here ran autonomously.
   - `binwalk_extract` on the dump; `ghidra_analyze` on the interesting binaries.
   - `limpet_workspace_add_finding` records each result linked to the exact tool
     and command that produced it (audit trail, `docs/limpet_readme.md:72`).
4. **Result:** a git-tracked workspace with firmware, extractions, and findings —
   exportable per §11.

---

## 8. Tool access patterns

Users reach the tool catalog (104 tools; the `tools` array in
`limpet/tool-catalog.json` is canonical, `docs/limpet_readme.md:211`) three ways,
by context:

- **Start bar / Whisker menu** (`docs/desktop-spec.md` §2) — GUI launchers,
  organized by the plugin `category` field (firmware-extraction, binary-analysis,
  emulation, flash, android, network, bios-uefi — the README's section grouping).
  The `Tools: n/n✓` indicator doubles as the discovery entry point: clicking it
  opens the tool list with health state.
- **`/tools` in agent chat** — in-conversation discovery. Lists tools filtered by
  the connected target and the current workflow stage, so the agent surfaces the
  few relevant tools rather than all 104. This is the primary discovery path in
  normal use because the agent already knows the context.
- **`limpet-tools` CLI** (`packaging/limpet-tools/bin/limpet`,
  `limpet-workspace`) — the scriptable/headless path: `limpet-tools health`,
  `limpet-tools hardware`, `limpet-tools install-defaults`
  (`docs/limpet_readme.md:102`). Everything the GUI does is available here for
  automation and remote/SSH use.

Discovery doctrine: breadth (104 tools) is not the pitch — the agent suggesting
the next step is (`docs/limpet_readme.md:64`). `/tools` and the agent's
suggestions are how a user finds the right tool without reading a 104-item menu.

---

## 9. Service architecture

| Unit | Role | After / needs | Restart | Degradation |
|---|---|---|---|---|
| `limpet-hald.service` | HAL device enumeration | `multi-user.target` | on-failure, 5s | if down: no auto-detect; manual `limpet-tools hardware` still works |
| `limpet-mcp-meta.service` | single MCP endpoint over all tools | `limpet-hald`, `network.target` | on-failure, 5s (`…limpet-mcp-meta.service:9`) | children ship disabled (`…/control:15`); one hung child cannot wedge the server |
| `limpet-registry-daemon.service` | reconcile plugin manifest | `local-fs.target` | on-failure, 5s (`…limpet-registry-daemon.service:8`) | stale registry → `Tools: n/m✓` degraded, non-fatal |
| `litellm.service` | local LLM gateway | `limpet-mcp-meta` | on-failure, 5s | **optional**: if down, agent runs without AI assistance |
| `limpet-agent.service` | agent core | `limpet-mcp-meta`; wants `litellm` | on-failure, 5s | starts even if litellm absent |

**Graceful degradation is a first-class requirement.** The agent must work
without the LLM — tool invocation, workspace management, the Target Wizard's
deterministic DETECT/IDENTIFY path, and manual tool runs all function; the box
simply offers no AI suggestions and says so in the greeting (§6) and start bar.
This matches the offline-first posture (`docs/limpet_readme.md:69`): a user with
no model and no network still has a working RE appliance.

`limpet-hald.service`, `litellm.service`, and `limpet-agent.service` are the units
still to be authored; `limpet-mcp-meta.service` and `limpet-registry-daemon.service`
exist in `packaging/` today and are the template for the rest.

---

## 10. Update mechanism

**Online (default).** Limpet packages, tools, and the agent update through an apt
repo whose URL is `network.update_repo_url` (`docs/settings-spec.md:315`).
Policy is operator-controlled and never silent: `update.channel` (`stable`
default, `dev` opt-in) and `update.policy` (`manual` default — an update is an
explicit operator action), with `update.notify_only` surfacing "update available"
in the panel (`docs/settings-spec.md:338`). This is consistent with the
fleet-wide deny-list on silent `systemctl stop/disable/mask`
(`docs/settings-spec.md:77`).

What updates, and how:
- **Limpet packages** (`limpet-hald`, `limpet-mcp-meta`, `limpet-tools`): apt.
- **Tools / plugins**: `limpet-tools check-updates` / `plugins install`, gated by
  the same offline/airgap settings (`docs/settings-spec.md:329`).
- **Exploit DB / signatures**: a data-only refresh channel pulled on the same
  manual policy (never auto, so an air-gapped box stays reproducible).
- **Kernel**: Ubuntu HWE stack via apt, so hardware support tracks the LTS HWE
  cadence without a distro rebuild.

**Offline (USB sideload).** For air-gapped sites: `limpet-update --from-usb
<mountpoint>`. A companion online box runs `limpet-update --export <usb>` to stage
a signed bundle (packages + tool/plugin updates + exploit-DB delta) onto a USB;
the air-gapped box verifies the bundle signature against the release key (§1) and
applies it. The bundle is signed and checksummed the same way the ISO is, so the
offline path has the same integrity guarantee as the online one.

---

## 11. Backup / restore

Three granularities, smallest to largest:

1. **Workspace export** — per-target, the common case. `limpet-workspace export
   <name>` (via `packaging/limpet-tools/bin/limpet-workspace`) produces a single
   archive of one git-tracked workspace: firmware, extractions, findings, and the
   commit history that links each finding to its tool/command (the audit chain,
   `docs/limpet_readme.md:72`). This is also how a live-session user persists work
   before reboot (§3, §6). Restore: `limpet-workspace import <archive>`.
2. **System config backup** — the settings tree (`docs/settings-spec.md`), the
   enabled-plugin manifest, HAL overrides, and the LLM/update preferences. A small
   archive that re-provisions a freshly installed box to a known configuration
   without carrying target data.
3. **Full disk image** — `dd`/`clonezilla`-style whole-disk capture for a
   bit-for-bit appliance clone or bare-metal recovery. Documented as the heavy
   option (large, includes bundled models), recommended before a kernel/HWE jump.

Export/import operate on committed git state, so a workspace round-trips without
losing its evidence chain.

---

## 12. Troubleshooting

**Common issues (download page + in-box help cover these):**
- *USB won't boot* → re-check `SHA256SUMS`, confirm DD/raw-image write mode (§2),
  try the Advanced → safe-graphics / nomodeset GRUB entry (§3).
- *No AI suggestions* → litellm down or no model. Expected degraded mode (§9);
  pick a model in the chat picker (`docs/agent-chat-spec.md:195`) or point
  `llm.provider` at an external gateway (`docs/settings-spec.md:159`).
- *Target not detected* → check `limpet-tools hardware`; the device may need a
  mode switch (e.g. MediaTek BROM vs. ADB, per the detected-modes matrix in
  `docs/limpet_readme.md:230`). Confirm `limpet-hald.service` is active.
- *Tool shows ✗* → `limpet-tools health` names the failing check; reinstall the
  plugin or sideload its update (§10).
- *Update failed / air-gapped* → use `limpet-update --from-usb` (§10).

**Recovery mode.** The installed GRUB carries a recovery entry that boots to a
minimal shell with the stack **not** auto-started, so a wedged service or bad
config can be fixed. From there the operator can `systemctl` individual units,
restore a system-config backup (§11), or re-run `limpet-tools health`. The live
USB is itself a recovery tool: boot it, `limpet-workspace import`/`export` against
the internal disk to rescue work from a non-booting install.

**Getting help.** In-box: the agent answers "how do I…" from the shipped docs;
the start bar links to the local copy of these specs. Out-of-box: the project
repo issue tracker and the download page's support links. Reports should attach
`limpet-tools health` output and the relevant service journal (scrubbed of any
target-identifying data the operator doesn't want to share).

---

## Acceptance

This spec is the deliverable. It covers all twelve sections of the journey,
references each owning sibling spec by path, and grounds the service architecture
(§5, §9) in the real units under `packaging/`. Where a referenced spec does not
yet exist (`docs/install-wizard-spec.md`) or a unit is not yet authored
(`limpet-hald.service`, `litellm.service`, `limpet-agent.service`), the dependency
is named explicitly as a build-time acceptance surface rather than assumed.
