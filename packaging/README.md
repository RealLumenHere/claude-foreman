# Limpet .deb packaging (wq#3947)

Debian source packages for the Limpet components that have real, running code in
this tree today. Build each with `dpkg-buildpackage -us -uc -b` from inside its
directory, or run `packaging/build-all.sh` to build every package below in one pass.

## Packaged now (3 of 11)

| package | wraps | source |
|---|---|---|
| `limpet-hald` | `hal.py` USB RE-hardware detector + target-device catalog | `ops/wq3809-limpet-hal/`, `limpet/target_devices.py`, `limpet/target-devices.d/` |
| `limpet-mcp-meta` | MCP meta-server (manifest-driven child-server multiplexer) | `ops/wq3808-limpet-mcp-meta-server-a1/` |
| `limpet-tools` | `limpet` CLI + registry reconcile daemon (plugin lifecycle) | `limpet/cli.py`, `limpet/daemon.py`, `limpet/registry.py`, `limpet/registry_core.py`, `limpet/manifest.py`, `limpet/health.py` |

Each debian/rules stages its sources with `cp`, out of the monorepo paths above, into
the package build tree (`$(CURDIR)/../../...` relative to `packaging/<pkg>/`) rather
than duplicating the files on disk — the package always builds from whatever is
currently checked in at those paths.

## Not packaged yet (8 of 11) — no implementation to package

The ticket's component list (`limpet-installer`, `limpet-agent`, `limpet-chat`,
`limpet-startbar`, `limpet-precheck`, `limpet-greeter`, `limpet-workspace`,
`limpet-config`) assumed finalized specs for all 11 components. Checked against this
tree (2026-10-15): design specs exist for most of these, but every one of them is
explicitly design-only and ships no code yet, and `limpet-installer`/`limpet-startbar`/
`limpet-precheck`/`limpet-greeter` have no spec document under that name at all.
Packaging a `.deb` for a component with zero source would mean shipping an empty or
fabricated binary — a `postinst`/systemd unit pointing at a GTK app, Plymouth hook, or
urwid wizard that was never written. That is the fabricated-evidence failure mode
(fleet instinct 8), not packaging work, so this ticket packages what exists and
records the rest as blocked here instead of inventing stand-ins.

| package | blocked on | evidence |
|---|---|---|
| `limpet-installer` | no spec or source under this name anywhere in the tree (`grep -r urwid` / `installer` across `docs/`, `design/`: no hits) | — |
| `limpet-agent` | design-only: `docs/agent-chat-spec.md:5` — "design-only ticket — nothing here installs a UI framework, opens a port, or registers an MCP server" | `docs/agent-chat-spec.md:5` |
| `limpet-chat` | same design-only doc as `limpet-agent` | `docs/agent-chat-spec.md:5` |
| `limpet-startbar` | no spec or source under this name (`docs/desktop-spec.md` covers the settings launcher, not an XFCE panel/genmon bundle) | — |
| `limpet-precheck` | no spec or source under this name anywhere in the tree | — |
| `limpet-greeter` | no spec or source under this name anywhere in the tree | — |
| `limpet-workspace` | `design/wq3806_limpet_architecture.md:130` lists it as an in-process MCP tool group inside the agent, not a standalone CLI binary — nothing to package separately yet | `design/wq3806_limpet_architecture.md:130` |
| `limpet-config` | `docs/settings-spec.md` specifies the CLI contract in detail but no `limpet-config` entry point exists in the tree yet (only the backing store, `limpet/registry.py`, which `limpet-tools` already packages) | `docs/settings-spec.md:102` |

Each of these becomes a straightforward addition to this `packaging/` directory,
following the `limpet-hald`/`limpet-mcp-meta`/`limpet-tools` layout, once its upstream
ticket lands real source.

## wq#4392 (duplicate of wq#3947, bug fix + gap audit)

wq#4392 re-asked for `.deb` packaging of these same three components, with an
expanded description (`limpet-hald` TOML+udev, `limpet-mcp-meta` "Sentinel +
ResultStore", `limpet-tools` "firstboot wizard + XFCE layout + panel scripts",
packages "baked into ISO squashfs"). The `.deb` packaging itself was already
done by wq#3947 (this file, above); re-auditing against that expanded wording
found one real bug and three more fabricated-evidence traps:

- **Bug, fixed**: `packaging/limpet-hald/debian/rules` staged `hal.py` and
  `usb_boot_signatures.{py,toml}` but never installed `hal_daemon.py`, even
  though `systemd/limpet-hald.service`'s `ExecStart` points at
  `/usr/lib/limpet/hald/hal_daemon.py`. The built `.deb` would have shipped a
  systemd unit that fails on start with "No such file or directory".
  `hal_daemon.py` only imports its sibling `hal` module via a
  `sys.path.insert(0, os.path.dirname(...))` (`ops/wq3809-limpet-hal/hal_daemon.py:35-36`),
  so installing it alongside `hal.py` in the same directory is sufficient; added.
- **"Sentinel + ResultStore"** (MCP meta-server): no such symbol exists in
  `ops/wq3808-limpet-mcp-meta-server-a1/` or in `design/wq3806_limpet_architecture.md`
  — not a narrower design-only case like the 8 components above, but no mention
  anywhere at all. Not packaged; nothing to package.
- **"firstboot wizard"** (limpet-tools): `design/wq4060_limpet_deployment_distro_ux.md:144-151`
  explicitly rejects this shape — "a provisioner, not a wizard ... a Tails-style
  mandatory greeter is explicitly rejected." The only `*wizard*` artifact in the
  tree is `limpet-wizard.desktop`, which launches the unrelated Target Device
  Wizard (`docs/target-wizard-spec.md`), already noted under `limpet-installer`
  above. Packaging a first-boot wizard binary would contradict the component's
  own design doc, not fill a gap.
- **"XFCE layout + panel scripts"**: these exist and are real —
  `limpet/iso-builder/overlay/etc/skel/.config/xfce4/xfconf/xfce-perchannel-xml/xfce4-panel.xml`
  and `limpet/iso-builder/overlay/opt/limpet/panel/genmon-{workspace,toolhealth,hardware}.sh`
  — but they ship as `/etc/skel` + `/opt` files copied straight into the ISO
  overlay (`limpet/iso-builder/build.sh:191-192`), not through `dpkg`. Folding
  them into the `limpet-tools` `.deb` would mean carrying a second, divergent
  copy; left on the overlay path they already use.
- **"Packages baked into ISO squashfs"**: the component *code* already lands in
  the squashfs, but via `stage3_inject_limpet_packages` raw-copying the source
  modules (`limpet/iso-builder/build.sh:196-205`), not via `dpkg -i` of this
  directory's `.deb`s — the two delivery mechanisms have diverged. Reconciling
  them (building these three `.deb`s on the build host and `dpkg -i`-ing them
  into the chroot instead of the raw `cp`) is a real follow-up, but it changes a
  working ISO stage that needs a real build-host run (root, loop devices, ~15 GB)
  to validate, which this worktree cannot do — tracked here rather than
  guessed at.

## APT repo hosting

Hosting the built `.deb`s on a Limpet APT repo is a deploy/infra step against a
physical Limpet target box, not a file this branch can carry — it needs `reprepro` (or
equivalent) running on that host. Queued as a follow-up physical task rather than
blocking this ticket on it (fleet instinct 3): once a Limpet node is reachable, `cd`
into each package dir, build, then `reprepro includedeb <codename> *.deb` on the repo
host.
