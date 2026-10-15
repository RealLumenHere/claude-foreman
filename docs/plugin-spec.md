# LIMPET plugin system — spec (wq#3945 refinement of wq#3810)

Status: as-built spec for the shipped plugin system under `limpet/`, refined
against ten RE/security plugin ecosystems surveyed in
[`design/wq3932_limpet_plugin_ecosystems.md`](../design/wq3932_limpet_plugin_ecosystems.md).
The lifecycle spec and reference implementation are
[`design/wq3810_limpet_plugin_system.md`](../design/wq3810_limpet_plugin_system.md)
and `limpet/`; this document layers the research findings on top and is the
one to read for "what should a plugin author or an index/signing effort build
against today."

**Relationship to `design/limpet/plugin-manifest.schema.json`:** that schema
(and `design/wq3806_limpet_architecture.md`) sketch an earlier, heavier vision
— a required `trust` tier (core/vetted/community), `/opt/limpet/plugins.d`, a
signed `registry.lock.json`. wq#3810 deliberately shipped a simpler model
first (no trust tiers, `/etc/limpet/plugins.d`, plain `unified.json`,
signing explicitly deferred — wq#3810 "Future work") and that simpler model is
what `limpet/manifest.py` actually parses and validates. Treat this document
and the shipped code as authoritative for the current manifest shape; §5
below reconciles where the wq#3806 trust-tier idea reappears as a forward
path rather than a requirement.

## 1. What a plugin is

One `limpet-plugin.toml`, either `kind = "tool"`, `"hardware"`, or (new in
wq#3945) `"data"` — a data-only plugin (signature set, device-ID table,
symbol map) that ships no runtime command, following IDA Lumina's and
Volatility's ISF lesson that RE plugins are often a data layer, not just code
(`limpet/manifest.py:19`, wq#3932 §3.6).

## 2. Manifest format (unchanged shape, two new fields)

Declarative TOML, parsed by stdlib `tomllib`, no code execution
(`limpet/manifest.py`). wq#3932's cross-ecosystem survey confirmed this is the
right call — it matches Binary Ninja's `plugin.json`, the most mature
non-marketplace prior art, and is the only way to validate/index a plugin
*before* trusting it (wq#3932 §2 "Manifest format").

New in wq#3945, both optional and backward compatible with every existing
`limpet-plugin.toml` in `limpet/examples/`:

- **`[plugin].category`** — one of a closed enum
  (`limpet/manifest.py:27-30`: disassembler, decompiler, debugger, fuzzer,
  firmware, sdr, flash-programmer, network, data, other), validated
  fail-closed like every other field (`limpet/manifest.py:146-148`). Every
  mature ecosystem surveyed selects by category (NSE categories, Binary
  Ninja/ClawHub marketplace categories — wq#3932 §3.4); empty/omitted means
  not-yet-categorized rather than a forced bad guess.
- **`[plugin].conflicts`** — a list of other plugin names this one cannot be
  active alongside, e.g. two hardware plugins claiming the same physical
  device (`limpet/manifest.py:150-156`, wq#3932 §3.5 "soft deps + conflicts").
  Declaring the conflict on either side is enough: `resolve()` mirrors it
  symmetrically (`limpet/registry_core.py:78-88`) so authors of two
  independently-maintained plugins don't have to coordinate editing both
  manifests to get the exclusion right.

Worked example: `limpet/examples/hackrf.toml` declares
`conflicts = ["hackrf-portapack"]` against the new
`limpet/examples/hackrf-portapack.toml` (a HackRF running PortaPack firmware
enumerates as the same device) — install/enable both and the registry keeps
exactly one `active`, blocking the other with a reason instead of racing for
the USB device at runtime (`limpet/registry_core.py:136-143`).

## 3. Discovery — current state and the recommended next step

**Today:** GitHub-repo-per-plugin, no index, discovery by word of mouth or
browsing `limpet/examples/`. wq#3932 §2 "Discovery mechanism" names this the
single biggest gap relative to the field — every mature ecosystem with a
central index (Binary Ninja's `vector35/community-plugins` → `plugins.json`,
r2pm's package-db repo, ClawHub) wins on UX over GitHub-search ecosystems
(Ghidra, IDA, Volatility, YARA).

**Implemented now (local, in-repo):** `limpet list --category <name>` filters
the installed set by the new category field (`limpet/cli.py:54-70`,
`limpet/cli.py:141-143`) — the in-repo discovery primitive the category
taxonomy exists to serve.

**Recommended, not yet built (needs a separate `limpet-plugins` index repo,
out of this ticket's scope):** a curated index aggregating each plugin's
`limpet-plugin.toml` metadata into one `index.json`, backing `limpet install
<name>` / `limpet search <term>` (wq#3932 §3.1). This is the highest-leverage
item left on the list; it needs real infrastructure (a hosted repo, a CI
gate) rather than a code change to `limpet/`.

## 4. Dependency resolution and conflicts

Unchanged and still ahead of the field (wq#3932 §2 "Dependency resolution":
none of the ten surveyed ecosystems does transitive resolution with cycle
detection). `limpet/registry_core.py` resolves `[dependencies]` constraints,
detects cycles, and computes a topological `start_order`
(`design/wq3810_limpet_plugin_system.md:68-78`). wq#3945 adds the one
affordance the survey flagged as missing: mutual exclusion via `conflicts`
(§2 above), checked after dependency satisfaction and before the health check
in the same effective-state ladder (`limpet/registry_core.py:128-146`):

```
disabled → cycle → blocked (missing/disabled/version-mismatched dep,
           OR an active conflicting plugin) → unhealthy → active
```

**Also new in wq#3945:** `[optional_dependencies]` — a soft dep that
*improves* a plugin but never blocks it if absent, unsatisfied, or disabled
(`limpet/manifest.py` `optional_dependencies` field; resolved in a second
pass after the main ladder, `limpet/registry_core.py:161-175`, since it can
never block it doesn't need the first pass's dependency-first ordering). Per
entry the unified view reports `optional_dependencies_satisfied`: installed,
version-matching, AND active — the same bar a hard dependency enforces, just
advisory. Worked example: `limpet/examples/ghidra-mcp.toml` declares
`llm4decompile = "*"` as optional — ghidra-mcp runs fine without it, but the
registry surfaces whether LLM-assisted decompilation is actually available.

## 5. Trust / signing model

**Designed (wq#4056, not yet implemented):**
[`design/wq4056_limpet_plugin_supply_chain_trust_gate.md`](../design/wq4056_limpet_plugin_supply_chain_trust_gate.md)
specifies the full supply-chain gate — `[integrity]` pinned tree hash verified at
install and load, `[provenance]` (source + commit + build origin), `[signature]`
(minisign Ed25519, maintainer or org key, TOFU-pinned), a lockfile, a new
`unverified` ladder rung, and a visible CLI/GUI warning with per-plugin explicit
opt-in for unverified community plugins (`tampered` is never loadable). Evidence
driver: the trojaned fake REA 6.3.0 release (2026-10-14, 172 downloads).

**Shipped today: none**, matching every ecosystem in the survey except that `[source]
git`/`rev` already pins provenance by revision (`limpet/manifest.py`
`source` field). wq#3932 §2 "Trust / signing" calls this the field-wide hole
and the one place Limpet can lead rather than follow.

**Recommended design (not implemented in wq#3945 — needs the index repo from
§3 to attach signatures to):** the index carries a publisher identity and a
signature over each entry's `(name, git, rev)` tuple; trust-on-first-use for
a new publisher, pin thereafter (wq#3932 §3.2). This is a lighter-weight
version of `design/wq3806_limpet_architecture.md`'s required `trust` tier +
signed `registry.lock.json` — same goal (don't run an unreviewed community
plugin silently), cheaper mechanism (sign a pointer, not a trust label baked
into every manifest).

## 6. Sandboxing and privilege separation

### 6.1 Today

**Today:** health and runtime (`start`/`stop`) commands run out-of-process —
health via a plain subprocess (`limpet/health.py:_shell_runner`), runtime
commands via whatever the manifest's `start`/`stop` strings invoke (the
`ghidra-mcp.toml` example uses `systemctl --user start/stop`). No sandboxing
is applied by the registry itself.

**Recommended, not implemented in wq#3945:** harden the `systemd --user`
invocation with `NoNewPrivileges=yes`, `ProtectSystem=strict`,
`ProtectHome=read-only`, `PrivateTmp=yes`, a scoped `ReadWritePaths`
(wq#3932 §3.3). wq#3932 is right that no other RE tool sandboxes plugins at
all and Limpet is one hardening step from the only real isolation story in
the field — but that step touches the deployed systemd unit files
(`limpet/registry/systemd/limpet-registry.service` is a *different*
subsystem's unit, wq#3807's tool-registry daemon, not wq#3810's plugin
registry, which has no shipped unit file to harden yet). Scoping it correctly
needs its own ticket once the plugin registry daemon has a real systemd unit.

**Recommended lifecycle pattern (wq#3963, from ReVa):** a plugin that spawns a
headless analysis backend (Ghidra headless being the first case) should own
that backend's project state for the life of one session and delete it on
`stop`, the way ReVa (`cyberkaida/reverse-engineering-assistant`) runs headless
mode: "ReVa manages starting Ghidra and projects for you. Projects in headless
mode are ephemeral (session-scoped) and automatically cleaned up" (README,
fetched 2026-10-15). For Limpet that means: the throwaway Ghidra project goes
under a per-session temp dir, analysis results the agent needs are written to
the workspace before `stop`, and `stop` removes the temp project so no stale
`.gpr`/`.rep` lock state survives into the next session. Not implemented; the
`ghidra-mcp.toml` example's `start`/`stop` strings are where it would land.

### 6.2 v2 design: privilege separation and the hardware broker (wq#3961)

**Scope note.** This subsection is the wq#3961 design review of the upstream
LIMPET repo's Plugin Development Guide (`docs/plugin-spec.md` in the repo the
tracked README points at — `docs/limpet_readme.md:316`). That guide's install
flow runs a plugin's `install.sh` as root via `sudo` when the plugin is
flagged needs-root (the same all-or-nothing shape visible in the tracked
README: `sudo ./install.sh`, `docs/limpet_readme.md:99`, and the
`apt-get install` plugin `install.sh` template, `docs/limpet_readme.md:296-303`),
with **nothing between unprivileged and root**. The plugin system there is
still pre-implementation, so this belongs in its future-v2 registry
discussion, not a v1 rewrite. It is recorded here — in the authoritative
manifest spec — because the manifest shape below is a `limpet-plugin.toml`
change, and this document is where that schema lives.

The design (from the 2026-10-15 thor-docs research digest §D, independent
ecosystem research across VS Code, Binary Ninja, Nix, Homebrew, and Burp —
every one of which runs extensions/builds unprivileged, and Homebrew refuses
root outright):

1. **Unprivileged workers by default.** Plugin processes — install hooks,
   health probes, `start`/`stop`, tool invocations — run as a dedicated
   unprivileged user. `escalates = true` in the shipped `[mutation]` boundary
   (`limpet/manifest.py:73`) stays the explicit, auditable opt-out, but in v2
   it buys a *scoped* grant (a specific systemd unit with a specific
   capability set), never a blanket root shell.

2. **A hardware broker owning USB/JTAG/serial.** A separate, narrowly-scoped
   service is the only component with device-node access (udev-granted ACLs /
   `plugdev`+`dialout` group membership). Plugins never open `/dev/*`
   directly; they request an operation on a device handle over a local
   socket, and the broker checks the request against the manifest's declared
   capabilities before touching hardware. This keeps root out of the loop
   entirely for ordinary hardware access — the gap today is not that root is
   used, it's that root is the *only* step above unprivileged.

3. **Per-capability authorization for destructive ops.** Flash erase/write
   and bootloader changes require explicit per-operation authorization at the
   broker, over and above the manifest declaration. This generalizes the
   already-mandatory energize-confirm gate
   (`design/wq3806_limpet_architecture.md:214-226`): read-only probing and
   passive capture stay fully autonomous; `flash-erase`, `flash-write`, and
   `bootloader` verbs are refused unless both declared in the manifest *and*
   confirmed per invocation.

**Manifest shape** — additive to the existing `limpet-plugin.toml` schema, no
rewrite (it sits alongside `[mutation]`, `[dependencies]`, `[health]` exactly
as `[optional_dependencies]` did in wq#3945):

```toml
[capabilities]
network = false          # default-deny, mirrors [mutation].network
root    = false          # default-deny, mirrors [mutation].escalates

[capabilities.udev]
subsystems  = ["usb", "tty"]
match       = [{ vendor = "0x0483", product = "0x3748" }]   # ST-Link v2
destructive = ["flash-erase", "flash-write", "bootloader"]  # broker-gated verbs
```

`[capabilities]` is the v2 superset of the shipped `[mutation]` boundary
(`limpet/manifest.py:62-75`): `root` maps to `escalates`, `network` to
`network`, and `[capabilities.udev]` carries what `[mutation]` cannot — which
physical devices a hardware plugin may ask the broker for, and which verbs on
them are destructive. The broker uses `match` to bind a plugin to its
declared VID/PID set (the same udev-driven inventory the HAL detection path
already specifies, `design/wq3806_limpet_architecture.md:197`), so a plugin
cannot reach a device it never declared. Migration from `[mutation]` is
mechanical; `[mutation]` remains valid for v1 manifests.

### 6.3 v2 design: three-tier version identity, hash pinning, lockfile (wq#3961, secondary)

The upstream guide's versioning today is `git pull` to update, `HEAD~1` to
roll back, and `pin` meaning only skip-autoupdate — no artifact-hash pinning
anywhere, so a force-pushed rev or a tampered checkout is invisible. The v2
registry should separate three identities that the current single version
string conflates:

1. **Plugin identity** — the name plus its canonical source
   (`[source].canonical`, already shipped: `limpet/manifest.py:108-114`),
   which is what the index's anti-impersonation check binds to.
2. **Plugin release** — the manifest `version` + git `rev`, what
   `[dependencies]` constraints resolve against.
3. **Underlying tool release** — the version of the wrapped tool itself
   (OpenOCD 0.12, Ghidra 11.x), which can change under a plugin release when
   `install.sh` does an unpinned `apt-get install`
   (`docs/limpet_readme.md:296-303`) — exactly the hole tier 3 exists to name.

Pin by **artifact hash**: a `limpet.lock` lockfile records, per plugin,
`(name, rev, content hash of the plugin directory, underlying tool version)`,
and the loader fails closed on mismatch. This is the same mechanism
`design/wq3806_limpet_architecture.md:86-88` already sketches as
`registry.lock.json` (resolved version + content hash + trust tier per
plugin) and the same lesson as Nix (store-path hashes) and modern package
lockfiles: `rev` says what you asked for, the hash proves what you got.
Rollback becomes "restore the previous lock entry" instead of `HEAD~1`, so it
survives a rewritten upstream branch. Depends on the §3 index/registry work;
lower priority than §6.2.

## 7. Update mechanism

**Today:** re-clone/re-fetch the plugin's git repo at a new `rev`, overwrite
the drop-in. No version pinning enforcement beyond the manifest's own
`[dependencies]` constraints; no `limpet update` command.

**Recommended:** once §3's index exists, `limpet index update` refreshes
`index.json` the way Binary Ninja's extension manager refreshes
`plugins.json` (wq#3932 §4 step 4) — pure follow-on of the index work, not a
separate mechanism.

## 8. Community contribution workflow

Recommended process from wq#3932 §4 (not yet operational — it assumes the
§3 index repo exists):

1. **Author** writes `limpet-plugin.toml` in their own repo, tags a release,
   runs `limpet validate` locally (`limpet/cli.py` `validate` subcommand,
   exit 2 on failure — already shipped and usable today even without an
   index).
2. **Submit** a PR to the `limpet-plugins` index repo adding one entry
   (name, kind, category, git, rev, summary, publisher key id) — no plugin
   code ever merges into Limpet's core.
3. **CI gate** (Limpet's analogue of Metasploit's `msftidy`) clones at the
   pinned `rev`, runs `limpet validate`, checks the filename-stem ==
   `[plugin].name` invariant (`limpet/registry.py:100-104`), verifies the
   publisher signature (§5), rejects an unresolvable dependency.
4. **Merge** regenerates `index.json`.

This is the middle model from wq#3932's three-way split (monorepo-PR,
index-entry-PR, open-dump-repo) and fits because a Limpet plugin is already a
standalone git repo with the manifest at its root
(`design/wq3810_limpet_plugin_system.md:80-86`).

## 9. What wq#3945 actually shipped vs. what's still future work

| Area | Status after wq#3945 |
|---|---|
| Category taxonomy | **Shipped.** `limpet/manifest.py` validates it; `limpet list --category` filters on it. |
| Conflicts / mutual exclusion | **Shipped.** `limpet/registry_core.py` resolves it symmetrically; worked example in `limpet/examples/`. |
| `data` plugin kind | **Shipped.** Third `KINDS` value, no runtime command required. |
| Central index + `limpet install/search` | Not shipped — needs a new repo, out of this ticket's code scope. |
| Signing | Not shipped — depends on the index existing first. |
| Sandboxing hardening | Not shipped — depends on the plugin registry having its own systemd unit. |
| Hardware broker + `[capabilities]` manifest (§6.2) | Designed (wq#3961), not shipped — v2 registry work; pre-implementation upstream. |
| 3-tier version identity + `limpet.lock` hash pinning (§6.3) | Designed (wq#3961), not shipped — depends on the §3 index. |
| Soft/optional dependencies | **Shipped.** `[optional_dependencies]` + per-entry `optional_dependencies_satisfied`, worked example in `limpet/examples/ghidra-mcp.toml`. |
| Contribution workflow (§8) | Documented, not operational — depends on the index. |

Tests: `limpet/test_limpet_manifest.py` (category/conflicts/optional-deps
validation), `limpet/test_limpet_registry_core.py` (conflict resolution,
one-sided declaration mirrored, disabled-conflict doesn't block, optional-dep
satisfaction for the active/missing/version-mismatched/disabled cases),
`limpet/test_limpet_registry.py` and `limpet/test_limpet_cli.py` (end-to-end
through `Registry.reconcile()` and `limpet list --category`) all pass — 135
tests across `limpet/` (see ISSUE-LOG #1334 for the pre-existing, unrelated
`registry.py`/`registry/` import-collision quirk hit while running this
suite, and the file-path-load workaround used to verify it here).
