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

Soft/optional dependencies (declare a dep that *improves* a plugin but
doesn't block it if absent) were in the same wq#3932 recommendation but are
not implemented — no current manifest or test exercises a use case for one,
and adding the field without a consumer would be speculative.

## 5. Trust / signing model

**Today: none**, matching every ecosystem in the survey except that `[source]
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

## 6. Sandboxing

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
| Soft/optional dependencies | Not shipped — no concrete use case yet to design against. |
| Contribution workflow (§8) | Documented, not operational — depends on the index. |

Tests: `limpet/test_limpet_manifest.py` (category/conflicts validation),
`limpet/test_limpet_registry_core.py` (conflict resolution, one-sided
declaration mirrored, disabled-conflict doesn't block), `limpet/test_limpet_registry.py`
and `limpet/test_limpet_cli.py` (end-to-end through `Registry.reconcile()` and
`limpet list --category`) all pass — 130 tests across `limpet/` (see
ISSUE-LOG #1334 for the pre-existing, unrelated `registry.py`/`registry/`
import-collision quirk hit while running this suite, and the file-path-load
workaround used to verify it here).
