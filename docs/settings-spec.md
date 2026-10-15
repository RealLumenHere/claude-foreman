# wq#3904 / wq#3938 — LIMPET settings/preferences panel design

> wq#3938 extension pass (2026-10-15): absorbs the AMD tuning research
> (`docs/hardware-tiers-spec.md` §3.9–§3.10, wq#3919/wq#3939) and the competitive
> analysis (`design/wq3929_competitive_analysis.md`) into this spec, and fills the
> settings the original draft left out: per-role model selection, a model download
> manager, hardware rescan, plugin install/update, evidence-chain and report-template
> preferences, proxy/offline/update-source network keys, and notification/chat display
> prefs. Marked **[wq3938]** inline. Architecture (§2), CLI shape (§3), and the
> Textual-not-GTK4 GUI decision (§5) are unchanged — see
> `limpet/DESIGN-DECISIONS.md` for why GTK4 was considered and rejected.

The config surface an operator uses to set up and tune a Limpet box: LLM provider
routing, hardware tier info, plugin trust/enable state, workspace defaults, network
config, update policy, and appearance. This is a **design-only** ticket — nothing here
writes a config file, opens a port, or ships a package; those steps inherit the fleet
gates. It builds directly on `design/wq3806_limpet_architecture.md` (trust tiers §1.2,
plugin lifecycle §3, workspace isolation §5, LiteLLM routing §6),
`design/wq3810_limpet_plugin_system.md` (the `limpet/` registry — the reference
implementation this design wraps, not duplicates), and `docs/desktop-spec.md` (which
already fixed the GUI's entry point and scope — see §0.1). It does not restate those
docs; it specifies only the settings layer.

## 0. Constraints this design must respect (read first)

Five facts on the ground shape every decision. Ignoring them would be winging it.

1. **The GUI's scope is already fixed by wq#3902, not open to redesign.**
   `docs/desktop-spec.md:274-282` ships a `limpet-settings.desktop` launcher whose own
   `Comment=` line reads *"Registry trust tiers, model selection, panel, and XFCE
   settings"* and states: *"`limpet-settings` opens the Limpet-specific controls
   (registry trust tiers per `design/wq3806_limpet_architecture.md:71-88`,
   resident-model choice per `:239`) and offers a button through to
   `xfce4-settings-manager` for OS-level settings — one entry, both scopes."* This
   design implements that contract (§5) rather than inventing a new one: Limpet-specific
   settings live in `limpet-settings`; OS theme/display/input stay in
   `xfce4-settings-manager`, one button away.

2. **Headless-first, same as every other Limpet surface.** Limpet is operated over SSH
   with no local console (fleet instinct; reaffirmed for this appliance at
   `docs/agent-chat-spec.md:11-16`). The settings surface must be fully usable with no
   display attached, so the **CLI (`limpet-config`) is the primary interface** and the
   GUI (`limpet-settings`) is a thin, optional frontend over the same store — never the
   only way to change a setting.

3. **Plugin management here is a trust-tier-respecting wrapper, not a new gate.**
   `design/wq3806_limpet_architecture.md:71-88` makes `community` plugins visible but
   non-invocable until a human/Minerva promotion decision; `:246-249` says the agent "may
   *propose* promoting a `community` plugin... but cannot self-promote." A settings panel
   that let an operator flip `community → vetted` with a toggle would reopen exactly the
   astroturf-pressure hole §0.3 of that doc was written to close. So the plugin-management
   view here (§4.3) can enable/disable within a plugin's *existing* tier and can *file* a
   promotion request, but has no control that executes a tier change.

4. **No hard-coded model, no plaintext credential in the settings store.** Model lists
   come from the LiteLLM gateway, not a fixed string
   (`design/wq3806_limpet_architecture.md:239`, `docs/agent-chat-spec.md:186-199`). And
   masking discipline applies to the settings store itself: an API key typed into
   `limpet-config` must never be echoed back by `list`/`get` or written in cleartext next
   to ordinary preferences (§4.1, §6).

5. **The box is unprovisioned today.** `research/fleet_hardware.json:417-428` lists
   Limpet as `unprovisioned-unfiled` with `ram_gb: null`; the "i5-14500/32GB" figure used
   throughout the Limpet docs is the *target* spec from the ticket chain, not a verified
   reading. The hardware-tier view (§4.2) must display what is actually known/measured
   and say so plainly — it does not fabricate a tier for a box that hasn't been inventoried.

## 1. Scope — the seven settings domains (ticket acceptance)

| # | Domain | Backing store | Existing code / doc it wraps |
|---|---|---|---|
| 1 | LLM provider config | `llm.*` keys + a 0600 key file | `docs/agent-chat-spec.md:186-216` (routing/picker), `research/models.yaml` (pool taxonomy) |
| 2 | Hardware tier display/tuning | read-only derived view + `tuning.*` keys | `research/fleet_hardware.json`, `design/wq3806_limpet_architecture.md:231-239` |
| 3 | Plugin management UI | **wraps** `limpet/registry.py` state (`enabled.json`, `unified.json`) | `limpet/cli.py` (exists today — this is a frontend, not a reimplementation) |
| 4 | Workspace defaults | `workspace.*` keys | `design/wq3806_limpet_architecture.md:206-230` |
| 5 | Network config | `network.*` keys | `design/wq3806_limpet_architecture.md:224-226` (containment), fleet SSH/tailnet posture |
| 6 | Update preferences | `update.*` keys | fleet deny-list on `systemctl stop/disable/mask` (no silent auto-apply) |
| 7 | Theme/appearance | `appearance.*` keys (Limpet surfaces only) + button to `xfce4-settings-manager` | `docs/desktop-spec.md:274-282`, `docs/agent-chat-spec.md:98-112` (Textual) |

## 2. Architecture — one layered config store, two frontends

The plugin registry already proved a pattern for Limpet state: drop-in/scan +
operator-toggle file + atomically-written resolved view
(`design/wq3810_limpet_plugin_system.md` design-at-a-glance). Settings reuse the same
shape instead of inventing a second one:

```
/etc/limpet/defaults.toml        # shipped with the base image, read-only to operators
/etc/limpet/limpet.toml          # site override (this box)             ─┐ layered,
~/.config/limpet/limpet.toml     # per-operator override                ─┘ last wins
/etc/limpet/secrets/*.key        # 0600, referenced by path only — never inlined (§0.4)
                                  │
                                  ▼
                          limpet-config (CLI)  ──┬── limpet-settings (Textual GUI, §5)
                                                  └── limpet/cli.py (plugin subcommands, §4.3)
```

- **Layering, not a database.** Three TOML files merged key-by-key, later layers
  win — the same `tomllib`-only, stdlib-first discipline as `limpet/manifest.py`
  (`design/wq3810_limpet_plugin_system.md:105-107`). No new dependency.
- **Atomic writes.** Every `limpet-config set` reads the owning layer, applies the one
  key, and writes via temp-file + rename, mirroring `_atomic_write_json`
  (`limpet/registry.py:41`) so a crash mid-write never corrupts the file.
- **Plugin state is not duplicated here.** Trust tier, enabled/disabled, and health
  already live in `enabled.json` / `unified.json` under the registry's `state_dir`
  (`limpet/registry.py:13`). `limpet-config` does not shadow that store; §4.3 shells out
  to the existing `limpet` CLI (`limpet/cli.py:120-151`) and only adds presentation.
- **Secrets never enter the TOML layers.** `llm.api_key_file = "/etc/limpet/secrets/<name>.key"`
  is what `limpet.toml` holds; the key body lives in a 0600 file the config loader reads
  at call time and never serializes back out (§0.4, §6).

## 3. CLI — `limpet-config`

Same conventions as `limpet/cli.py` (`--json` on every subcommand, exit 0 success / 1
usage-or-not-found / 2 validation failure, overridable paths) so an agent or a script can
drive it identically:

```
limpet-config get <key> [--json]
limpet-config set <key> <value> [--layer site|user]   # default layer: site
limpet-config list [--domain <name>] [--json]          # domain = one of §1's 7 names
limpet-config reset <key> [--layer site|user]          # drop the override, fall through
limpet-config export [--json]                          # full merged view, secrets redacted
limpet-config edit [--layer site|user]                 # $EDITOR on that layer's file only
```

- `get`/`list`/`export` redact any key whose name matches the fleet's own secret-shaped
  pattern (`*_key`, `*_token`, `*_secret` — same family the masking tripwire in this
  session's own operating rules watches for) to `<redacted, see <path>>`, never the value.
- `set` on a `*_key_file` key validates the referenced file exists and is mode `0600`;
  it refuses (exit 2) to accept a key whose literal value looks credential-shaped,
  pointing the operator at the file-path pattern instead — the settings CLI must not
  become the thing that teaches an operator to paste a token into a command line.
- Unknown key, out-of-range value (e.g. a `tuning.*` knob outside the hardware's
  measured envelope, §4.2), or a malformed TOML write ⇒ exit 2 with the specific
  violation, fail-closed like `manifest.py:validate_manifest`
  (`design/wq3810_limpet_plugin_system.md:47-49`).

## 4. The seven domains in detail

### 4.1 LLM provider config

- `llm.gateway_url` — the LiteLLM endpoint (default the fleet gateway); overridable for
  an isolated/offline Limpet box.
- `llm.resident_model` — session-default local model, populated from
  `GET <gateway>/v1/models` filtered to what the box's gateway key can call, exactly the
  picker behavior `docs/agent-chat-spec.md:187-199` already specifies — `limpet-config`
  is a second client of that same picker, not a second model list to keep in sync.
- `llm.routing.<step> = <model>` — named per-step overrides (e.g.
  `llm.routing.decompile_review = "sonnet-5-5"`), surfaced per-turn in the agent chat UI
  per `docs/agent-chat-spec.md:192-199`; `limpet-config list --domain llm` shows the
  currently active table.
- `llm.api_key_file` — path to the 0600 key file for any `cheap_cloud`/fleet route that
  needs a distinct credential from the shared gateway key (`research/models.yaml:21-22`
  pool taxonomy). The key body is never a `limpet-config` value (§3, §0.4).
- Pool awareness is read-only here: `limpet-config get llm.resident_model --json` tags
  the answer `"pool": "local"` vs. `"fleet"`/`"cloud"` from `research/models.yaml`'s own
  taxonomy, so a script can refuse to route off-box without reparsing YAML itself.
- **[wq3938] Provider selection.** `llm.provider = "litellm-local" | "litellm-external"
  | "ollama-direct" | "cloud-api"`. Default `litellm-local`: the box's own LiteLLM in
  front of its own Ollama, the architecture's shipping shape
  (`docs/agent-chat-spec.md:188`). `litellm-external` points `llm.gateway_url` at
  another box's gateway (the fleet pattern); `ollama-direct` drops the gateway for a
  minimal install (loses per-step routing — `limpet-config` warns that `llm.routing.*`
  keys become inert); `cloud-api` requires `llm.api_key_file` set and is refused
  outright while `network.offline_mode = true` (§4.5) — the offline differentiator the
  competitive matrix shows most peers lack
  (`design/wq3929_competitive_analysis.md:218`).
- **[wq3938] Per-role model selection.** The fleet already names its roles in the
  LiteLLM alias layer — `agent-fast`, `agent-deep`
  (`ops/wq3849-m80s-llm-deployment/litellm-aliases.yaml:10-14`); Limpet adds
  `agent-code` for code-emitting steps. `llm.role.agent_fast`, `llm.role.agent_deep`,
  `llm.role.agent_code` each name a model from the same `/v1/models` picker as
  `llm.resident_model`. Roles are coarser than `llm.routing.<step>`: a step override
  wins over its role, a role wins over `resident_model` — three layers, same
  last-wins rule as the TOML files themselves (§2). Defaults per hardware tier come
  from the tier's model matrix (`docs/hardware-tiers-spec.md` §7), not hand-typed here.
- **[wq3938] Model download manager.** `limpet-config models list|pull <name>|rm <name>
  [--json]` wraps `ollama list/pull/rm` — presentation and policy only, no second
  download path. Policy it adds over bare Ollama: `pull` refuses (exit 2) when
  `network.offline_mode = true` or when the model's size would leave less than
  `tuning.min_free_disk_gb` free; `rm` refuses to remove a model currently referenced
  by any `llm.role.*`/`llm.routing.*`/`llm.resident_model` key, naming the key. The
  GUI's LLM tab renders `models list` with per-model size and a pull progress line —
  the one long-running operation in the settings surface, so it is cancellable and
  survives the TUI closing (the pull is Ollama's own, not ours).

### 4.2 Hardware tier display and tuning

- **Display is read-only and honest about provisioning state (§0.5).** `limpet-config
  get hardware.tier --json` returns the `research/fleet_hardware.json` row for this host
  verbatim, including `"status": "unprovisioned-unfiled"` and `"ram_gb": null` when that
  is what's on record — it does not backfill the target spec as if measured. Once a
  provisioning ticket records real values, this view reflects them with no code change.
- **Live inventory, not static spec, drives attached-hardware display.** `limpet-config
  get hardware.attached --json` proxies the HAL's `list_hardware` tool
  (`design/wq3806_limpet_architecture.md:186-188`) rather than keeping its own device
  list — one source of truth for "what's physically plugged in."
- **Tuning is a small, bounded set of resource knobs**, not free-form sysctl access:
  `tuning.resident_model_max_params` (gates the 9B-vs-14B choice,
  `design/wq3806_limpet_architecture.md:233-239`), `tuning.backend_concurrency` (how many
  of Ghidra/QEMU may be resident at once — the documented memory-ceiling risk at
  `:140-141`), and `tuning.coral_enabled` (the optional accelerator,
  `design/wq3806_limpet_architecture.md:199-201`, off by default since the agent core
  does not depend on it). Each knob's valid range is derived from the hardware row in
  §4.2's display, not hand-typed, so a 32GB box cannot be tuned past its own ceiling.
- **[wq3938] Re-run hardware scan.** `limpet-config hardware rescan [--json]` invokes
  the HAL detector (`limpet-hald`, `packaging/limpet-hald/bin/limpet-hald:5-6`) plus the
  CPU/memory/GPU probe that fills the hardware-book row
  (`docs/hardware-tiers-spec.md` §3.9 topology detection on AMD, the P-core snippet at
  `:125-132` on Intel), then re-derives the tier and rewrites the cached hardware row
  atomically. Output is a diff against the previous row ("GPU added: …", "tier 3 → 4"),
  so plugging in a Coral or new DIMMs is a rescan away, not a reprovision. Rescan never
  silently changes `tuning.*` overrides the operator set — it re-derives *bounds* and
  flags any override now out of range as `stale` rather than deleting it.
- **[wq3938] Performance tuning overrides** — the full knob set, superseding the three
  knobs above as the complete list. Every knob is a **preference**, applied by the
  provisioning-time service (§4.5's execution rule: `limpet-config` writes, never
  executes), and every default comes from the matched hardware-book TOML
  (`docs/hardware-tiers-spec.md` §3.4), not from this spec:

  | Knob | Key | Default source | Notes |
  |---|---|---|---|
  | Thread count | `tuning.cpu_threads` | `[performance].threads` = physical cores | wizard sweep stores the measured winner (`docs/hardware-tiers-spec.md:221`) |
  | P-core pinning | `tuning.pcore_pinning` | auto on Intel hybrid only (`docs/hardware-tiers-spec.md:108,119`) | **Intel-only**: refused (exit 2, "no P/E split on AMD — see numa_mode") on an AMD vendor string, per the §3.9 warning at `:136-138` |
  | NUMA / CCD mode | `tuning.numa_mode` | §3.9 `needs_pinning_bench` result, else TOML | AMD multi-CCD/EPYC only (`docs/hardware-tiers-spec.md:630`) |
  | Strict CPU affinity | `tuning.cpu_strict` | `true` on every AMD profile (§3.4) | |
  | Swap pressure | `tuning.swappiness` | `vm.swappiness=10` (`docs/hardware-tiers-spec.md:52`) | range 0–100 |
  | CPU governor | `tuning.cpu_governor` | `performance` while a model is loaded | preference-only; the applying service owns the sysfs write |
  | Populated channels | `tuning.require_populated_channels` | EPYC TOML only (§3.10) | |
  | Max resident models | `tuning.max_loaded_models` | RAM-aware matrix (§7) × tier | |
  | AVX-512 status | `tuning.avx512_check` | Ollama `system info` parse (`docs/hardware-tiers-spec.md:230`) | read-only — a check result, not a toggle |
  | ROCm opt-in | `tuning.rocm_experimental` | `false` everywhere (§3.3/§6 matrix) | hard-refused on devices the matrix marks no-toggle (e.g. BC-250, gfx1013) |
  | Min free disk | `tuning.min_free_disk_gb` | tier default | gates `models pull` (§4.1) |

  This table is the settings-side contract for `docs/hardware-tiers-spec.md` §3.10,
  which already names this spec as the owning surface — one knob list, defined here,
  defaults defined there. AMD-vs-Intel gating is intentional: the knobs a given box
  shows are filtered by its vendor/topology row, so a 7950X operator never sees a
  P-core toggle and an i9 operator never sees `numa_mode`.

### 4.3 Plugin management UI

A presentation layer over the registry that already exists — it adds no new state:

- `limpet-config list --domain plugins` is `limpet list --json` (`limpet/cli.py:53-64`)
  reshaped into the settings view's table: name, kind, trust tier, effective state,
  health reason.
- `limpet-config plugins enable|disable <name>` calls through to `limpet enable|disable`
  (`limpet/cli.py:67-81`) — same toggle, same `enabled.json`, same reconcile. No
  duplicate enable/disable path to drift out of sync.
- **Promotion is a request, not a control.** For a `community`-tier plugin the view
  shows "disabled — promotion required" (the meta-server's own refusal reason,
  `design/wq3806_limpet_architecture.md:137-139`) with one action: *file a promotion
  request*, which appends a row to the TOOL_REGISTRY-style review queue — it does not
  write `registry.lock.json` or flip the tier itself (§0.3). This mirrors "the agent may
  propose, never self-promote" (`:246-249`) for the human operator's own UI.
- **[wq3938] Install from URL / git repo.** `limpet-config plugins install <git-url>`
  implements the community-tier flow `design/wq3810_limpet_plugin_system.md` already
  defines (§1 "install: drop/symlink into" the drop-in dir; §6 "plugins shared as git
  repos"): clone into the community plugins dir, run `limpet validate` on its
  `limpet-plugin.toml` (`limpet/cli.py:129-131`), and only then symlink the manifest
  into the drop-in. The result always lands at **community tier, disabled** — install
  is decoupled from enablement, the fail-closed property wq#3929 found no peer
  marketplace has (`design/wq3929_competitive_analysis.md:256`). A plain URL (non-git)
  is accepted only for a bare `limpet-plugin.toml` fetched to a new empty dir, same
  validate-then-link path; and any remote fetch is refused under
  `network.offline_mode = true` (§4.5).
- **[wq3938] Update check.** `limpet-config plugins check-updates [--json]`: for each
  git-installed plugin, `git fetch` its origin and compare the fetched
  `limpet-plugin.toml` version against the installed one. **Notify-only** — output is a
  table of (name, installed, available); applying an update is an explicit
  `plugins install` re-run per plugin, never automatic, matching the update-domain
  posture in §4.6. Honors the proxy keys and offline toggle in §4.5.

### 4.4 Workspace defaults

- `workspace.root` (default `/var/limpet/workspaces`, `design/wq3806_limpet_architecture.md:212`),
  `workspace.container_network` (default `none` — the documented containment default,
  `:224-226`), `workspace.auto_git_commit` (default `true`, so every analysis step is
  committed per `:216`).
- These are exactly the defaults `limpet-wizard` reads when it creates a new workspace
  (`docs/target-wizard-spec.md` — the wizard's own creation step); settings changes here
  take effect on the *next* wizard run, never retroactively rewriting an existing
  workspace's `manifest.json`.
- **[wq3938] Evidence-chain preferences.** `workspace.evidence.redact_reports`
  (default `true` — the mask-once-on-the-IR rule, `limpet/DESIGN-DECISIONS.md`
  wq#3934; `false` only flips the default, `--no-redact` per export still stamps
  `report_meta.redacted=false`), `workspace.evidence.source_date`
  (`commit` default | `none` — the byte-for-byte re-derivation mode the reporting spec
  uses to make a report count as evidence), and `workspace.evidence.require_findings_schema`
  (default `true`: an export with a malformed `evidence/findings/*.json` fails instead
  of skipping the file — fail-closed like the registry). These set workspace-creation
  defaults, same next-run-only rule as above.
- **[wq3938] Report template selection.** `workspace.report_template` (default
  `full-technical`; `exec-summary` also ships — `limpet/report-templates/`). Valid
  values are whatever the three-level template resolution actually finds — workspace
  dir, `/etc/limpet/report-templates`, packaged built-ins
  (`limpet/workspace_report.py:47-50`) — so `limpet-config set` validates against a
  live directory listing, not a hardcoded enum, and a site-installed custom template
  is selectable with no code change.

### 4.5 Network config

- `network.meta_server_bind` (default loopback-only; a tailnet bind is an explicit
  opt-in, never public-facing — instinct 6's "never expose a master key in a
  public-facing container" extends by the same logic to the meta-server's control
  surface).
- `network.emulation_tap_enabled` (default `false` — firmware emulation that needs a tap
  interface opts in explicitly, `design/wq3806_limpet_architecture.md:225-226`).
- No setting here can touch an actual interface (`ip link set`, `systemctl`) directly;
  `limpet-config` writes the preference, a provisioning-time service (outside this
  ticket) applies it — this keeps the settings CLI inside the fleet's deny-list-exempt
  surface rather than needing deny-list carve-outs for itself.
- **[wq3938] Update sources.** `network.update_repo_url` — the apt repo the §4.6 update
  mechanism reads (default the Limpet package repo; a mirror URL for air-gapped sites
  that sync one). Preference-only like everything else here: changing it rewrites a
  sources-list fragment on the next apply, it does not run `apt` itself.
- **[wq3938] Proxy.** `network.proxy.http`, `network.proxy.https`, `network.proxy.no_proxy`
  — honored by every outbound path this spec defines (gateway calls when
  `llm.provider` is external/cloud, `models pull`, `plugins install`/`check-updates`,
  update checks) via the standard env-var names at process spawn. Proxy URLs with
  inline credentials (`http://user:pass@…`) are refused (exit 2) — a credential in a
  TOML layer is exactly what §0.4 forbids; use the 0600 secrets-file pattern or an
  unauthenticated proxy.
- **[wq3938] Offline mode.** `network.offline_mode` (default `false`) — one master
  switch for the appliance's strongest differentiator (every analysis capability is
  local; most peers require cloud, `design/wq3929_competitive_analysis.md:218,261`).
  When `true`: `cloud-api`/`litellm-external` provider routes refuse, `models pull`
  refuses, `plugins install`/`check-updates` refuse remote fetches, update checks stop.
  Everything already on disk — resident models, installed plugins, all RE backends —
  is untouched. The panel surfaces the flag (an "offline" badge) so an operator
  mid-investigation can see at a glance why a cloud route is refusing.

### 4.6 Update preferences

- `update.channel` (`stable` default, `dev` opt-in), `update.policy` (`manual` default —
  an update is an explicit operator action, never silent, consistent with the fleet-wide
  rule that `systemctl stop/disable/mask` and similar are deny-listed and require a human
  in the loop), `update.notify_only` (surface "update available" in the panel's Settings
  item without applying anything, the safe default for an appliance mid-investigation).
- `limpet-config` never itself invokes a package manager or service restart; it only
  records the preference a separate, audited update mechanism reads.

### 4.7 Theme/appearance

- Scope is deliberately narrow, per §0.1: `appearance.tui_theme` (the Textual chat/
  settings color scheme, same palette knob as `docs/agent-chat-spec.md`'s TUI, §3 there),
  `appearance.panel_icon_set` (the icon-set open item noted at
  `docs/desktop-spec.md:349` — this ticket gives it a settings key, not a chosen icon
  set). Window manager theme, fonts, display scaling stay in `xfce4-settings-manager`,
  one button away (§0.1, §5) — `limpet-settings` does not reimplement an XFCE theme
  picker.
- **[wq3938] Theme.** `appearance.theme = "dark" | "light"` (default `dark`) — one knob
  that drives both Textual surfaces (chat + settings) and the HTML report stylesheet,
  so the appliance reads as one product. It does not touch the XFCE GTK theme (§0.1's
  boundary); the desktop launcher ships matching XFCE defaults at provisioning time.
- **[wq3938] Font size.** `appearance.font_size` applies to the *local desktop
  terminal profile* the launchers open (`docs/desktop-spec.md` §4's
  xfce4-terminal-hosted surfaces). Honest limit, stated in the UI: over a remote SSH
  session the font belongs to the operator's own terminal and this key has no effect —
  a TUI cannot set a remote font, and pretending otherwise is the kind of dead toggle
  this spec refuses to ship.
- **[wq3938] Agent chat preferences.** `appearance.chat.show_tool_output`
  (full | collapsed | hidden, default collapsed), `appearance.chat.timestamps`
  (default `false`), `appearance.chat.stream_tokens` (default `true`) — the display
  knobs `docs/agent-chat-spec.md`'s TUI reads; defined here so chat and settings share
  one config loader rather than growing a second prefs file.
- **[wq3938] Notification settings.** `appearance.notify.enabled` (default `true`),
  `appearance.notify.events` (subset of: `analysis-done`, `update-available`,
  `plugin-health-changed`, `scan-hardware-changed`). Delivery is the desktop panel's
  notification area locally and the chat status line over SSH — notify-only surfaces;
  no notification ever carries an action that mutates state, consistent with §4.6.

## 5. GUI — `limpet-settings`

Same framework choice as the chat interface, for the same reasons
(`docs/agent-chat-spec.md:98-112`): **Textual**, not Electron or a bundled webview. A
settings form has no need for a frame/image canvas (the one thing that justified Tauri
as an opt-in there), so `limpet-settings` has no web-surface variant at all — it is pure
TUI, runs over plain SSH, near-zero resident cost on a box already near its memory
ceiling (`design/wq3806_limpet_architecture.md:3`, `:233-235`).

- **Layout.** One tabbed view per §1 domain (LLM / Hardware / Plugins / Workspace /
  Network / Updates / Appearance), each tab a thin form over the matching
  `limpet-config` keys — the GUI calls the same config loader the CLI does, in-process,
  so there is exactly one code path for "read/write a setting," just two entry points.
- **Plugins tab = `limpet list` rendered as a table** with enable/disable toggles and
  the promotion-request action from §4.3 — no separate plugin-state model.
- **The one button out.** A single "OS Settings…" action launches
  `xfce4-settings-manager`, fulfilling the desktop-spec's "button through" requirement
  (`docs/desktop-spec.md:278`) without `limpet-settings` needing any OS-level control of
  its own.
- **Headless fallback.** Launched with no `$DISPLAY`/no pty (e.g. a bare SSH pipe),
  `limpet-settings` degrades to printing the same guidance `limpet-config --help` would
  and exits 1 rather than hanging — consistent with §0.2's "CLI is primary, GUI never the
  only way in."
- **[wq3938] Start-bar access, and why not GTK4 / an XFCE settings-manager plugin.**
  The wq#3938 ticket offered two desktop shapes: XFCE settings-panel integration or a
  standalone GTK4 window. Decision: **neither — keep Textual**, reached from the start
  bar via the `limpet-settings.desktop` launcher the desktop spec already ships
  (`docs/desktop-spec.md:274-282`), which opens the TUI in xfce4-terminal. Rationale
  recorded in `limpet/DESIGN-DECISIONS.md` (wq#3938 entry): a GTK4 window or an
  `xfce4-settings-manager` embed exists only when a display does, so the headless box —
  the primary operating mode — would need a second, parallel settings implementation;
  Textual-in-a-terminal gives the start-bar menu entry the ticket asks for *and* the
  identical surface over SSH, with one code path. The "one button out" above covers the
  OS-settings half of the XFCE-integration option.

## 6. Security

- **Secrets stay out of the merged-view output.** `limpet-config export` and
  `limpet-settings`'s on-screen state both redact `*_key`/`*_token`/`*_secret`-shaped
  values (§3) — the settings surface must not become a second place a credential can leak
  to a shared terminal/log, on top of the fleet's existing `git remote -v`/`env`-style
  tripwires (instinct 4).
- **Network and update domains are preference-only, never executing (§4.5, §4.6).** This
  keeps `limpet-config`/`limpet-settings` off the house PreToolUse deny-list entirely —
  it has no code path that calls `systemctl stop/disable/mask`, `ip link set`, or a
  package manager, so it needs no carve-out from that guard.
- **Plugin promotion is request-only (§0.3, §4.3).** The settings UI can never be the
  mechanism that turns an untrusted community plugin live.

## 7. Build order (for the provisioning ticket, not this one)

1. Layered TOML loader + `limpet-config get/set/list/reset/export` (§2, §3) — nothing
   else works without it.
2. Wire `llm.*` to the real LiteLLM `/v1/models` call (§4.1) — depends on the gateway
   being reachable from Limpet, same dependency `docs/agent-chat-spec.md` §5 already named.
3. Wire `plugins` domain to the existing `limpet/cli.py`/`registry.py` (§4.3) — pure
   wrapper, no registry changes required.
4. Hardware display wired to `research/fleet_hardware.json` + HAL `list_hardware` (§4.2)
   — blocked on the HAL existing (`design/wq3806_limpet_architecture.md` §7 step 4).
5. `limpet-settings` Textual GUI over the CLI's config loader (§5) — last, since every
   tab is read-through to a CLI domain that must work headless first.

## 8. Open items / decisions deferred to provisioning

- Confirm the exact `/etc/limpet/secrets/` layout and which service account owns write
  access to key files (§2, §4.1).
- Pick the real bounds for `tuning.*` knobs (§4.2) once the M70q's actual RAM/CPU are
  recorded in `research/fleet_hardware.json:417` (currently `null`, §0.5) — this design
  fixes the knob names, not their numeric ranges.
- Decide the promotion-request queue's concrete destination (a TOOL_REGISTRY.md row vs. a
  new tracked file) when the plugin-adoption review process (§0.3) is itself formalized
  past the current per-ticket go/no-go.
- Pick the Limpet TUI color palette / panel icon set (§4.7) — same open item
  `docs/desktop-spec.md:349` already deferred; this doc just gives it a settings key.
