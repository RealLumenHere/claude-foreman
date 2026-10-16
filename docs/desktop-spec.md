# wq#3902 — LIMPET desktop mode: XFCE start-bar (panel) layout and contents

> **Superseded by Frank's 2026-10-16 decision (wq#4082):** the desktop is two stacked
> `xfce4-terminal` windows, not a panel. See §0a.

Design-only ticket. This specifies the XFCE panel an operator sees when a Limpet box
boots to a desktop session, so a provisioning ticket can lay it down verbatim. It does not
install a desktop, write to `~/.config`, or register any launcher — those inherit the
fleet gates. Companion architecture: [`design/wq3806_limpet_architecture.md`](../design/wq3806_limpet_architecture.md)
(meta-server §2, HAL §4, workspace isolation §5) and the registry in `limpet/`.

## 0a. Superseded: the desktop is two stacked terminals (Frank, 2026-10-16, wq#4082)

Frank decided that the Limpet session opens **two `xfce4-terminal` windows, stacked**:

| Position | Runs | Role |
|---|---|---|
| Top | `limpet-agent` TUI | agent chat, greeting (`docs/deployment-spec.md` §6), `/tools`, Target Wizard |
| Bottom | a plain login shell | `limpet-tools`, `limpet-workspace`, launching GUI tools such as Ghidra by name |

Together the two windows fill the screen. No custom start-bar panel ships, and
nothing below is the default layout any more. Consequences:

- The six items in §0 do not go on a panel. Items 1, 2 and 4 (workspace,
  hardware, tool health) become the agent TUI's status line. It reads the same
  data sources and keeps the §1.1 output contract (one short label per item).
  Items 3 and 5 are the top terminal itself and `/tools` / the Target Wizard
  inside it. Item 6 is `limpet-settings` run from either terminal.
- The session starts both windows from an XDG autostart entry (a script that
  runs `xfce4-terminal` twice, each with its own `--title` and a `--geometry`
  computed from the screen size). The exact flags belong to the iso-builder
  ticket that builds it.
- As built, `limpet/iso-builder/overlay/` still ships this spec's
  `xfce4-panel.xml` (to `/etc/skel`) and the `/opt/limpet/panel/genmon-*.sh`
  scripts. The default session no longer uses them. Removing or repurposing
  them is iso-builder follow-up work; this doc change does not touch the overlay.
- `/home/lumen/projects/limpet/docs/startbar-spec.md` (wq#3806) is superseded the
  same way and carries a matching banner.

Sections 0–7 below are kept as the record of the panel design and of the genmon
data contract that the status line reuses.

## 0. What the panel must surface (ticket acceptance)

Six items, each mapped below to a concrete XFCE plugin and a concrete data source that
already exists in this repo (or is flagged as a build dependency when it does not yet):

| # | Item | XFCE plugin | Data source |
|---|------|-------------|-------------|
| 1 | Active workspace info | `genmon` (poll) | `limpet workspace current --json` *(dep — §5 workspace mgr, not yet built)* |
| 2 | Connected hardware status | `genmon` (poll + udev) | `limpet hal list --json` *(dep — §4 HAL, not yet built)* |
| 3 | Agent chat launcher | `launcher` → `.desktop` | `limpet-agent` TUI/terminal (§6 agent core) |
| 4 | Tool health summary | `genmon` (poll) | `limpet list --json` *(exists: `limpet/cli.py:7`)* |
| 5 | Target device wizard launcher | `launcher` → `.desktop` | `limpet-wizard` (new target → workspace, §5) |
| 6 | Settings | `launcher` → `.desktop` + Whisker menu | `limpet-settings` / `xfce4-settings-manager` |

The two `genmon` items whose CLI does not yet exist (1, 2) name the planned command and
are listed as build dependencies in §7. The spec is complete; those commands are the
contract the workspace-manager / HAL tickets must satisfy, not guesses baked into the panel.

## 1. Why XFCE, and how its panel is configured

XFCE is the right desktop for an i5-14500 / iGPU-only appliance that must keep 32 GB free
for Ghidra/QEMU backends (`design/wq3806_limpet_architecture.md:3`, `:239`): it is light,
and — load-bearing for this ticket — **its panel is fully XML-configured and
file-reproducible**, so the layout below ships as an artifact rather than as click-by-click
setup instructions.

Prior art (fleet-search web, 2026-10-14):

- The panel channel lives at
  `~/.config/xfce4/xfconf/xfce-perchannel-xml/xfce4-panel.xml` (Xfce Docs — xfce4-panel;
  ArchWiki — Xfce). It is a `xfconf` "per-channel" XML file.
- **Caveat that shapes deployment:** `xfconfd` owns that file at runtime and rewrites it,
  so hand-edits "will not take effect" while a session is live and can be clobbered
  (ArchWiki — Xfce). The layout is therefore applied either (a) before first login, or
  (b) via `xfce4-panel-profiles` (formerly `xfpanel-switch`) load of a saved `.tar.bz2`
  profile, or (c) with `xfconf-query` writes followed by `xfce4-panel --restart`. §6 picks
  (a)+(b).
- Dynamic status in an XFCE panel is idiomatically a **Generic Monitor** plugin
  (`xfce4-genmon-plugin`): it runs a command on an interval and renders tagged stdout.
  That is the mechanism for items 1, 2, and 4.

### 1.1 genmon output contract (used by every status item)

`genmon` runs its `Command` every `UpdatePeriod` ms and parses these tags from stdout:

```
<txt>LABEL</txt>        text shown in the panel
<img>/path/icon.png</img>  icon shown left of the text
<bar>0..100</bar>       a small progress/level bar
<tool>hover text</tool> tooltip (multi-line ok) shown on mouse-over
<click>command</click>  command run on left-click
<css>...</css>          per-widget styling (e.g. color the text red when degraded)
```

Every status script below emits `<txt>` + `<img>` + `<tool>` + `<click>`. Scripts are
read-only pollers that shell out **only** to local `limpet` CLI verbs; click actions open a
terminal/window, never mutate state directly (trust boundary, §8).

## 2. Panel geometry

One top panel, full width, locked, 28 px — a classic "start bar". (A second, autohide
bottom panel is optional and deferred; not required by acceptance.)

Left-to-right order (the `plugin-ids` array order is the on-screen order):

```
[Whisker menu ▸] | [Agent Chat] [Target Wizard] ‖ <expand separator> ‖ [WS: <name>] [HW: n✓] [Tools: n/n✓] | [Settings] [clock]
```

- **Left cluster** — launchers the operator reaches for first: Whisker start menu, Agent
  Chat (item 3), Target Wizard (item 5).
- **Expanding separator** — pushes status to the right (standard XFCE idiom).
- **Right cluster** — the three live `genmon` status indicators (items 1, 2, 4), then
  Settings (item 6) and a clock.

## 3. The panel XML (`xfce4-panel.xml`)

Ships to `/etc/skel/.config/xfce4/xfconf/xfce-perchannel-xml/xfce4-panel.xml` (so every new
operator session inherits it) and into the Limpet base image. Plugin ids are stable and
referenced by the `plugin-ids` array.

```xml
<?xml version="1.0" encoding="UTF-8"?>
<channel name="xfce4-panel" version="1.0">
  <property name="configver" type="int" value="2"/>
  <property name="panels" type="array">
    <value type="int" value="1"/>
    <property name="panel-1" type="empty">
      <property name="position" type="string" value="p=6;x=0;y=0"/>  <!-- top, full width -->
      <property name="length" type="uint" value="100"/>
      <property name="position-locked" type="bool" value="true"/>
      <property name="size" type="uint" value="28"/>
      <property name="icon-size" type="uint" value="16"/>
      <property name="plugin-ids" type="array">
        <value type="int" value="1"/>   <!-- whiskermenu       -->
        <value type="int" value="2"/>   <!-- launcher: agent    -->
        <value type="int" value="3"/>   <!-- launcher: wizard   -->
        <value type="int" value="4"/>   <!-- separator (expand) -->
        <value type="int" value="5"/>   <!-- genmon: workspace  -->
        <value type="int" value="6"/>   <!-- genmon: hardware   -->
        <value type="int" value="7"/>   <!-- genmon: tool health-->
        <value type="int" value="8"/>   <!-- launcher: settings -->
        <value type="int" value="9"/>   <!-- clock              -->
      </property>
    </property>
  </property>

  <property name="plugins" type="empty">
    <!-- 1. Start menu -->
    <property name="plugin-1" type="string" value="whiskermenu"/>

    <!-- 3. Agent chat launcher -->
    <property name="plugin-2" type="string" value="launcher">
      <property name="items" type="array">
        <value type="string" value="limpet-agent.desktop"/>
      </property>
    </property>

    <!-- 5. Target device wizard launcher -->
    <property name="plugin-3" type="string" value="launcher">
      <property name="items" type="array">
        <value type="string" value="limpet-wizard.desktop"/>
      </property>
    </property>

    <!-- expanding separator -->
    <property name="plugin-4" type="string" value="separator">
      <property name="expand" type="bool" value="true"/>
      <property name="style" type="uint" value="0"/>   <!-- 0 = transparent -->
    </property>

    <!-- 1. Active workspace info -->
    <property name="plugin-5" type="string" value="genmon">
      <property name="command" type="string" value="/opt/limpet/panel/genmon-workspace.sh"/>
      <property name="use-label" type="bool" value="true"/>
      <property name="update-period" type="uint" value="5000"/>
    </property>

    <!-- 2. Connected hardware status -->
    <property name="plugin-6" type="string" value="genmon">
      <property name="command" type="string" value="/opt/limpet/panel/genmon-hardware.sh"/>
      <property name="use-label" type="bool" value="true"/>
      <property name="update-period" type="uint" value="3000"/>
    </property>

    <!-- 4. Tool health summary -->
    <property name="plugin-7" type="string" value="genmon">
      <property name="command" type="string" value="/opt/limpet/panel/genmon-toolhealth.sh"/>
      <property name="use-label" type="bool" value="true"/>
      <property name="update-period" type="uint" value="15000"/>
    </property>

    <!-- 6. Settings launcher -->
    <property name="plugin-8" type="string" value="launcher">
      <property name="items" type="array">
        <value type="string" value="limpet-settings.desktop"/>
      </property>
    </property>

    <!-- clock -->
    <property name="plugin-9" type="string" value="clock">
      <property name="digital-layout" type="uint" value="3"/>
    </property>
  </property>
</channel>
```

Update periods are staggered (3 s hardware, 5 s workspace, 15 s tool health) so three
`genmon` pollers never fire in lockstep; hardware is fastest because hotplug is the event
an operator most wants reflected immediately (the HAL udev inventory, `design/wq3806_limpet_architecture.md:186-188`).

## 4. The genmon status scripts (`/opt/limpet/panel/*.sh`)

Each is a thin, read-only wrapper over a `--json` CLI verb, parsed with `jq`. They print
exactly one genmon record and exit 0. Sketches (the provisioning ticket commits the final
scripts next to the base image; icon names resolve against the active icon theme):

### 4.1 `genmon-toolhealth.sh` — item 4 (CLI exists today)

Reads `limpet list --json`, whose shape is
`{"plugins": {"<name>": {"state": "...", ...}}, "rejected": {...}}` with the effective-state
ladder `active | unhealthy | blocked | cycle | disabled`
(`limpet/registry_core.py:29`, `limpet/registry_core.py:114-124`; emitted by
`limpet/cli.py:54-61`). Counts `active` vs. total, and colours the label when any plugin is
`unhealthy`/`blocked`/`cycle`:

```sh
#!/bin/sh
j=$(limpet list --json 2>/dev/null) || { echo "<txt>Tools: ?</txt><tool>registry unreachable</tool>"; exit 0; }
ok=$(printf '%s' "$j" | jq '[.plugins[] | select(.state=="active")] | length')
tot=$(printf '%s' "$j" | jq '.plugins | length')
bad=$(printf '%s' "$j" | jq -r '[.plugins | to_entries[] | select(.value.state!="active") | "\(.key): \(.value.state) (\(.value.reason))"] | .[]')
icon="dialog-ok-apply"; [ "$ok" = "$tot" ] || icon="dialog-warning"
echo "<img>$icon</img><txt>Tools: ${ok}/${tot}</txt>"
echo "<tool>Healthy ${ok} of ${tot} plugins${bad:+\n$bad}</tool>"
echo "<click>xfce4-terminal -e 'limpet list'</click>"
```

### 4.2 `genmon-workspace.sh` — item 1 (CLI is a build dependency, §7)

Shows the active investigation workspace (`design/wq3806_limpet_architecture.md:206-218`:
one investigation = one `/var/limpet/workspaces/<target-id>/`). Contract the workspace
manager must provide: `limpet workspace current --json` →
`{"id": "...", "target": "...", "evidence_count": N, "dirty": bool}`.

```sh
#!/bin/sh
j=$(limpet workspace current --json 2>/dev/null)
if [ -z "$j" ] || [ "$(printf '%s' "$j" | jq -r '.id // empty')" = "" ]; then
  echo "<img>folder-open</img><txt>No workspace</txt><click>limpet-wizard</click>"; exit 0
fi
id=$(printf '%s'  "$j" | jq -r '.id')
tgt=$(printf '%s' "$j" | jq -r '.target')
ev=$(printf '%s'  "$j" | jq -r '.evidence_count')
echo "<img>folder-open</img><txt>WS: ${id}</txt>"
echo "<tool>Target: ${tgt}\nEvidence items: ${ev}</tool>"
echo "<click>xfce4-file-manager /var/limpet/workspaces/${id}</click>"
```

### 4.3 `genmon-hardware.sh` — item 2 (CLI is a build dependency, §7)

Shows attached probes/capture devices from the HAL udev inventory
(`design/wq3806_limpet_architecture.md:186-188`). Contract: `limpet hal list --json` → a
list of `{"name": "...", "class": "debug-probe|capture|logic|sdr|programmer", "ready": bool}`.

```sh
#!/bin/sh
j=$(limpet hal list --json 2>/dev/null) || { echo "<txt>HW: ?</txt>"; exit 0; }
n=$(printf '%s' "$j" | jq 'length')
rdy=$(printf '%s' "$j" | jq '[.[] | select(.ready)] | length')
lines=$(printf '%s' "$j" | jq -r '.[] | "\(if .ready then "✓" else "✗" end) \(.name) [\(.class)]"')
icon="network-wired"; [ "$n" -gt 0 ] || icon="network-offline"
echo "<img>$icon</img><txt>HW: ${rdy}/${n}</txt>"
echo "<tool>${lines:-No hardware attached}</tool>"
echo "<click>xfce4-terminal -e 'limpet hal list'</click>"
```

## 5. The launcher `.desktop` files (`/usr/share/applications/`)

Items 3, 5, 6. Each is a standard freedesktop entry referenced by the launcher plugins in
§3 and also surfaced in the Whisker menu (plugin-1) under a "Limpet" category.

```ini
# limpet-agent.desktop  — item 3: agent chat
[Desktop Entry]
Type=Application
Name=Limpet Agent Chat
Comment=Talk to the RE agent about the current target
Icon=utilities-terminal
Exec=xfce4-terminal --title "Limpet Agent" -e "limpet-agent"
Categories=Limpet;System;
```

```ini
# limpet-wizard.desktop  — item 5: target device wizard
[Desktop Entry]
Type=Application
Name=New Target (Wizard)
Comment=Identify a plugged-in device and open a fresh workspace
Icon=document-new
Exec=limpet-wizard
Categories=Limpet;System;
```

```ini
# limpet-settings.desktop  — item 6: settings
[Desktop Entry]
Type=Application
Name=Limpet Settings
Comment=Registry trust tiers, model selection, panel, and XFCE settings
Icon=preferences-system
Exec=limpet-settings
Categories=Limpet;Settings;
```

`limpet-settings` opens the Limpet-specific controls (registry trust tiers per
`design/wq3806_limpet_architecture.md:71-88`, resident-model choice per `:239`) and offers a
button through to `xfce4-settings-manager` for OS-level settings — one entry, both scopes,
so item 6 is a single panel object.

The Target Wizard (`limpet-wizard`) is the GUI front of the "identify → open workspace"
first step of the agent loop (`design/wq3806_limpet_architecture.md:240-245`): it reads the
HAL inventory, lets the operator pick the just-plugged device, names the target, and calls
the workspace manager to create `/var/limpet/workspaces/<target-id>/`, then hands off to the
agent. Its full flow is its own build ticket; this spec only fixes its panel entry point.

## 6. Deployment (how the layout is applied without xfconfd clobbering it)

Because `xfconfd` rewrites the live file (§1), do **not** drop the XML into a running
session. Instead:

1. **Base image / first boot:** ship §3 as `/etc/skel/.config/.../xfce4-panel.xml` and the
   §4 scripts to `/opt/limpet/panel/` and §5 entries to `/usr/share/applications/`. A new
   operator account inherits the panel at account creation, before any session writes it.
2. **Re-apply to an existing session:** export the reference layout once with
   `xfce4-panel-profiles save limpet-default.tar.bz2`, commit that artifact, and load it with
   `xfce4-panel-profiles load limpet-default.tar.bz2` (the tool that the prior-art forum
   thread and ArchWiki name for cloning panels) — it stops the panel, swaps the config, and
   restarts cleanly.
3. **Scripted field tweak:** individual values via
   `xfconf-query -c xfce4-panel -p /plugins/plugin-7/update-period -s 15000` then
   `xfce4-panel --restart`.

Required packages on the base image: `xfce4-panel`, `xfce4-genmon-plugin`,
`xfce4-whiskermenu-plugin`, `xfce4-panel-profiles`, `jq`.

## 7. Build dependencies (contracts this panel assumes)

The panel is complete, but two indicators poll CLI verbs that the companion build tickets
must add. They are specified here as fixed contracts so the panel and the backends land
consistently:

- **`limpet workspace current --json`** → `{"id","target","evidence_count","dirty"}`.
  Owner: workspace-manager ticket (`design/wq3806_limpet_architecture.md` §5). Until it
  exists, `genmon-workspace.sh` renders "No workspace" and links to the wizard (graceful).
- **`limpet hal list --json`** → `[{"name","class","ready"}]`. Owner: HAL ticket
  (`design/wq3806_limpet_architecture.md` §4). Until it exists, `genmon-hardware.sh` renders
  "HW: ?" (graceful).
- **`limpet-agent`, `limpet-wizard`, `limpet-settings`** executables. Owners: agent-core
  (§6), workspace/wizard, and settings tickets respectively.

`limpet list --json` (item 4) needs no new work — it exists at `limpet/cli.py:7` /
`limpet/cli.py:54-61` today.

## 8. Trust & safety notes

- genmon scripts are **read-only pollers**. They invoke only local `limpet … --json` query
  verbs and never a mutating verb (`enable`/`disable`/`reconcile`), so a panel refresh can
  never change registry state. Mutations happen only through the trust-gated meta-server
  (`design/wq3806_limpet_architecture.md:135-139`) reached via the Agent/Wizard apps.
- `<click>` actions open a terminal or file-manager window; they do not pipe panel text into
  a shell, so a crafted plugin `reason` string (which flows into a `<tool>` tooltip) cannot
  reach a command line.
- The panel exposes no credentials and no remote endpoints; everything it shows is derived
  from local state on the box.

## 9. Open items (for the provisioning ticket, not this one)

- Settle whether a second autohide bottom panel (a tasklist/window-buttons dock) is wanted;
  acceptance needs only the top start bar, so it is deferred.
- Pick the Limpet icon set / theme (affects the `<img>` icon names in §4).
- Decide whether `limpet-settings` is a new small GUI or a `yad`/`zenity` form over
  `limpet` + `xfconf-query` (cheaper; recommended for v1).
- Commit the exported `limpet-default.tar.bz2` panel profile (§6.2) once a Limpet box exists
  to export it from.
