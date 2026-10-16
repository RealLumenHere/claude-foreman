# wq#4060 — LIMPET RESEARCH: end-to-end deployment UX across Kali / Tails / Parrot / AttifyOS

**Recommendation in one line: Limpet ships as a *live-first* image (Kali's three-image
model, not Tails' amnesia), uses the vendor-neutral Etcher/`dd` USB path (no custom
creator), boots straight to a desktop with a one-shot first-run *provisioner* instead of
a wizard, surfaces tools through a searchable agent index rather than a 600-entry menu,
runs exactly one RE-specific daemon (the meta-server), and updates by `apt` rolling
release with an offline delta bundle.**

This is a **research/design** ticket. It specifies the deployment surface that the
provisioning ticket (`design/wq3806_limpet_architecture.md:308-321`, §7 build order) will
build; it installs nothing and changes no running service. It slots deployment UX onto the
hooks the architecture already defines — the tool registry (`:50-121`, §1), the MCP
meta-server as sole router (`:123-170`, §2), plugin lifecycle (`:172-190`, §3), workspace
isolation (`:233-257`, §5), and the agent core (`:258-307`, §6) — and does not restate
them.

## 0. Prior art and method (read first)

Live-search doctrine applies (this is a "how do other people solve X" ticket). Fleet-search
(SearXNG, wq#999) ran 2026-10-16 against the vendor docs below; `brave`/`duckduckgo`/
`startpage` were rate-limited or CAPTCHA'd (the standing wq#2595 condition), `google cse`
answered. This worker has **no `WebFetch` grant** (same posture as
`design/wq4059_sentinel_agent_eval.md:0`), so page bodies were not fetched; the account is
grounded in the returned result snippets, the primary vendor documentation URLs they point
at, and offline knowledge to the Jan-2026 cutoff. Where a number is version-specific it is
marked *as of* a date and should be re-confirmed at build time rather than trusted as
frozen.

Primary sources identified (for the build ticket to fetch and pin):
- Kali image types: `kali.org/docs/introduction/what-image-to-download/`
- Kali USB on Windows: `kali.org/docs/usb/live-usb-install-with-windows/` (recommends Etcher, Rufus as alt)
- Parrot USB: `parrotsec.org/docs/usb/how-to-create-a-parrot-usb-drive/` (Etcher or `dd`)
- AttifyOS: `github.com/adi0x90/attifyos` (Ubuntu-based IoT RE distro; see §9)
- Tails persistence/greeter: Tails Persistent Storage + Welcome Screen docs

## 1. Comparison matrix — distro UX patterns

| Dimension | Kali | Tails | Parrot (Security) | AttifyOS |
|---|---|---|---|---|
| **Base** | Debian testing (rolling) | Debian stable | Debian stable | Ubuntu LTS |
| **ISO distribution** | Direct HTTP + torrent + global mirror CDN; checksummed | HTTPS + mirrors, signed, verified in-browser | Direct + torrent | GitHub release / OVA |
| **Image variants** | Installer, NetInstaller, Live, VM (VMware/VBox/Hyper-V/QEMU), WSL, ARM, cloud, Docker, Everything | Single live USB image only | Full, Security, Home, minimal, VM | Single OVA (VM-first), build script |
| **Image size** | Live ~3–4 GB; Everything ~9–11 GB; NetInstaller ~500 MB *(as of 2026)* | ~1.5 GB | Full ~4–5 GB | OVA ~6–8 GB |
| **USB creator** | Recommends **Etcher**; Rufus (DD mode) alt; `dd` on Linux/mac | Ships **Tails installer / GNOME Disks**; dd-style clone | **Etcher** or `dd` | Flash OVA via hypervisor; `dd` for bare metal |
| **Boot menu** | GRUB: Live / Live (persistence) / Live (encrypted persistence) / Install / Graphical install / memtest | GRUB/syslinux: Tails / Tails (Troubleshooting) | GRUB: Live / Install / RAM / failsafe | Standard Ubuntu/GRUB |
| **Installer** | **Calamares** (GUI) + Debian d-i (advanced) | None — live-only by design | **Calamares** + Debian d-i | Ubiquity (Ubuntu) or run build script on a base install |
| **First boot** | Drop straight to XFCE desktop (user set at install) | **Welcome Screen greeter**: language, admin pw, persistence unlock, options | First-run dialog (updates/anon posture) | Ubuntu desktop; tools pre-placed |
| **Tool organization** | `kali-menu` categories (01-Info-Gathering … 13-Reversing …) in XFCE; `kali-tweaks`; no agent | Minimal curated app set (anonymity-focused) | Parrot menu categories; AnonSurf applet | Flat "Attify tools" folder / README index; no menu taxonomy |
| **RE-specific daemon** | None (tools are on-demand CLIs/GUIs) | Tor daemon (core identity), not RE | AnonSurf / Tor (optional) | None |
| **Update mechanism** | **Rolling `apt`** (`apt update && apt full-upgrade`) | **Atomic signed image** upgrades (auto, incremental delta or full) | Rolling-ish `apt` (parrot-upgrade) | `apt` + manual `git pull` per tool |
| **Live persistence** | Optional **LUKS-encrypted persistence partition** on the USB (opt-in at boot) | Optional **LUKS Persistent Storage**, feature-gated per data type | USB persistence supported | N/A (VM-first; state lives in the VM disk) |

## 2. The ten required findings, condensed

1. **ISO distribution.** Kali's model is the one to copy: direct HTTPS off a mirror CDN
   *and* torrent, every image checksummed and GPG-signed, with a documented "which image"
   page. The key lesson is **variant segmentation**: a small NetInstaller, a bootable
   Live, pre-built VM images per hypervisor, and a Docker image are different front doors
   to the same system. Tails' single-image simplicity is only possible because it refuses
   to be installed — not an option for Limpet, which is a working appliance.

2. **USB creation.** Every distro converged on the same answer and so should Limpet:
   **recommend balenaEtcher** (cross-platform, validates the write, hides the raw-device
   footgun) and document `dd` for Linux/mac power users; mention Rufus (DD mode) as the
   Windows alternative. **Nobody ships a bespoke USB creator except Tails**, and Tails only
   does so to enforce its clone-and-upgrade model. Writing a custom creator is cost with no
   payoff — do not.

3. **Boot menu.** GRUB with a flat, labelled list. Kali's useful entries: Live, Live
   (persistence), Live (encrypted persistence), Install, Graphical install, memtest. The
   persistence entries are the interesting part for Limpet (see §10).

4. **Installer.** Calamares is the de-facto GUI installer for Debian-derived security
   distros (Kali, Parrot). It is branded via a theme/config package and a `modules`
   sequence; customization is declarative, not forked code. AttifyOS leans on the Ubuntu
   installer plus a post-install build script — simpler to produce but a worse first-run.
   Tails has no installer by design.

5. **First boot.** Three live models exist: Kali (drop to desktop, identity was set at
   install), Tails (a mandatory **greeter** that gates language/admin-password/persistence
   before the desktop appears), and Parrot (a lightweight first-run dialog). The greeter
   pattern is the right primitive for anything that needs a decision *before* the session
   is usable; a plain drop-to-desktop is right when there is nothing to decide.

6. **Tool access.** Kali's `kali-menu` sorts ~600 tools into ~14 numbered categories in the
   XFCE menu, plus `kali-tweaks` for meta-config. This does **not** scale as a discovery
   surface — a numbered menu of 600 entries is a filing cabinet, not search. **No mainstream
   distro ships an agent that surfaces tools by task.** That gap is exactly Limpet's
   differentiator: the agent core + meta-server (`design/wq3806_limpet_architecture.md:258-307`,
   `:123-170`) can answer "what do I use to unpack this SPI dump?" which `kali-menu` cannot.

7. **Service architecture.** Kali runs essentially a stock Debian desktop service set and
   **no RE daemon** — tools are invoked on demand. Tails' one defining daemon is Tor. The
   lesson: a security distro should run the *minimum* privileged surface. Limpet's one
   justified always-on daemon is the MCP meta-server as the single tool router
   (`:123-170`), and — per `design/wq4059_sentinel_agent_eval.md` — the energize-confirm
   sentinel as a second, LLM-free, separately-privileged daemon. Everything else stays
   on-demand.

8. **Update mechanism.** Two philosophies. Kali: rolling `apt`, continuous, online-assumed,
   occasionally breaks (acceptable for a lab tool, the user can roll a snapshot). Tails:
   atomic signed-image upgrades applied as an incremental delta with a full-image fallback,
   so a half-applied update can never leave a broken system — the price is you cannot
   `apt install` arbitrary packages persistently. For **offline** updates Kali relies on a
   local mirror/`apt-offline`; Tails ships the whole signed image. Limpet should default to
   `apt` rolling but publish a **signed offline delta bundle** for air-gapped RE benches
   (firmware work is frequently done disconnected).

9. **AttifyOS specifically** (closest analogue — a firmware/IoT RE distro). It is
   **Ubuntu-based, VM-first (OVA), and curated-not-categorized**: a focused set of IoT/
   firmware tools (binwalk, firmware-mod-kit, firmadyne, radare2, GDB-multiarch, QEMU,
   MQTT/CoAP/BLE utilities, JTAG/UART helpers) assembled largely by a post-install build
   script and presented as a flat "Attify" tool folder with a README index rather than a
   Kali-style taxonomy. Versus Kali it is **narrower, heavier per tool, and lower-polish**:
   no Calamares-grade installer, no menu taxonomy, update is `apt` + per-tool `git pull`.
   Its strength (a firmware-RE-shaped tool set) and its weakness (no discovery layer, no
   trust gate on the tools it pulls) together define Limpet's opportunity: ship AttifyOS's
   tool *focus* with Kali's *distribution polish* and Limpet's *agent discovery + trust
   gate* (`TOOL_REGISTRY.md:1-9` already puts unvetted packages on SECURITY HOLD — AttifyOS
   has no equivalent).

10. **Live USB persistence.** Both Tails and Kali implement it as an **opt-in LUKS-encrypted
    partition** on the same USB. Tails goes further, feature-gating *what* persists (dotfiles,
    GPG keys, additional software) so amnesia stays the default. This matters for Limpet:
    a firmware analyst may want to boot a trusted appliance on an untrusted laptop and keep
    case artifacts across reboots without installing. Limpet should support encrypted
    persistence, scoped to the workspace tree (`design/wq3806_limpet_architecture.md:233-257`,
    §5) so persisted state inherits the isolation model rather than becoming a second,
    unscoped store.

## 3. Recommendations for Limpet

**3.1 Distribution.** Ship three front doors from one build: a **Live image** (primary —
"try before you install"), a **pre-built VM image** (VMware/VBox/QEMU — matches how RE is
usually done, and how AttifyOS is consumed), and a **NetInstaller** for permanent benches.
Add a Docker image only if the meta-server can run headless there (it is the natural CI/
batch form). All images GPG-signed and torrent-seeded. Do **not** replicate Kali's
"Everything" 11 GB image — Limpet's catalog is curated and trust-gated, not maximal.

**3.2 USB creation.** Document Etcher (recommended) + `dd` (power user) + Rufus-DD
(Windows). **Build no custom creator.**

**3.3 First boot — a provisioner, not a wizard.** Boot to the desktop like Kali, but run a
**one-shot first-run provisioner** (closer to Parrot's dialog than Tails' blocking greeter)
that does only what cannot be defaulted: set/confirm the operator approval identity
(`design/wq2059_per_operator_approval_keys.md`), unlock or create encrypted persistence if
the user booted the persistence entry, and seed the tool catalog (`:90-121`, §1.3). It must
run **non-interactively** given a config file, because Limpet also boots headless/batch
(`design/wq4059_sentinel_agent_eval.md`) — a blocking greeter would wedge the batch path, so
a Tails-style mandatory greeter is explicitly rejected.

**3.4 Tool organization — index, not menu.** Keep a thin `kali-menu`-style category fallback
for browsers, but make the **agent + meta-server the primary discovery surface**: task-phrased
queries resolved against the registry (`:50-121`), returning the trust tier (`:71-89`) with
each result so the user sees *at the point of discovery* whether a tool is vetted, held, or
community. This is the single thing no existing distro does and is Limpet's reason to exist.

**3.5 Service architecture.** Exactly two RE daemons at boot: the **meta-server** (sole tool
router, `:123-170`) and the **energize-confirm sentinel** (LLM-free, separately privileged,
`design/wq4059_sentinel_agent_eval.md`). Everything else on-demand. No tool runs as a daemon
unless a specific plugin's lifecycle (`:172-190`, §3) requires it, and then under workspace
isolation (§5).

**3.6 Update mechanism.** Default **rolling `apt`**; publish a **signed offline delta
bundle** for air-gapped firmware benches; snapshot before `full-upgrade` so a broken roll is
revertible (Kali's weakness, cheaply fixed). Plugin/tool updates route through the
TOOL_REGISTRY trust gate (`TOOL_REGISTRY.md:1-9`), never a blind per-tool `git pull` the way
AttifyOS does.

**3.7 Persistence.** Support opt-in **LUKS-encrypted persistence** on the Live USB, scoped to
the workspace tree (§5) so persisted artifacts inherit isolation. Amnesic (no-persistence)
Live boot stays the default for untrusted-host use.

## 4. What this ticket does NOT decide

Role change (Limpet from "secondary control/worker" to RE appliance) remains the
provisioning ticket's call — see `design/wq3806_limpet_architecture.md:16-33` §0.1. Exact
image sizes, the Calamares theme, and the offline-bundle signing key are build-time inputs,
not fixed here. Numbers marked *as of 2026* must be re-confirmed against the vendor docs in
§0 at build time; this doc fabricates no tool counts or sizes beyond what the sources
returned.
