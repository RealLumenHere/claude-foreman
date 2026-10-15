# wq#3810 — LIMPET plugin system: hot-swappable tool & hardware modules

Status: designed + reference implementation landed (`limpet/`). Author: build worker, 2026-10-14.
Refined by wq#3945 against ten-ecosystem research (wq#3932): manifests gained
a `category` taxonomy and a `conflicts` declaration, and a third `data` kind
for code-free plugins — see
[`docs/plugin-spec.md`](../docs/plugin-spec.md) for the full refinement and
what's still future work (index/discovery, signing, sandboxing hardening).

LIMPET is meant to be extensible: anyone can write a plugin for their hardware or
their tool and drop it in, without patching the core. This doc is the lifecycle
spec; the code under `limpet/` is the reference implementation and the source of
truth for exact behaviour. Prior art surveyed first (doctrine): the drop-in
directory + registry-index + per-plugin git repo shape is the one that keeps
recurring — systemd `*.d/` units, Obsidian community plugins (`manifest.json` +
a community index), DokuWiki/odysseus plugin depots, Cargo, and EdgeCrab's
`plugin.toml` with a persisted enable/disable policy. LIMPET follows it rather
than inventing a new one.

## Design at a glance

A plugin is one `limpet-plugin.toml` describing a **tool** or **hardware** module.
A registry daemon watches a drop-in directory, validates each manifest, resolves
declared dependencies between plugins, runs each plugin's own health check, and
writes a single **unified manifest** (`unified.json`) that every other tool reads
to learn what is installed and actually runnable.

```
author's git repo (limpet-plugin.toml)
        │ install: drop/symlink into
        ▼
/etc/limpet/plugins.d/<name>.toml ──scan+validate──┐
                                                    ▼
      enabled.json (operator toggles) ──►  registry daemon  ──►  /var/lib/limpet/unified.json
      health.json  (last probe results) ──►  (reconcile loop)      {name: {state, reason, deps}, start_order}
```

## The six lifecycle aspects

### 1. Install — drop a manifest into the drop-in dir
Installing is dropping `limpet-plugin.toml` (or a symlink to it) into
`/etc/limpet/plugins.d/` as `<name>.toml`. No core edit, no registration call.
The filename stem must equal `[plugin].name` so the registry can map a plugin to
its drop-in deterministically and reject spoofs (`registry.py:scan`,
`limpet/registry.py:80-119`). The drop-in dir is overridable via
`LIMPET_PLUGINS_DIR` for per-user or test trees.

### 2. Register — the daemon picks it up, validates, unifies
The registry daemon reconciles on a timer (`limpet/daemon.py:run`): it scans the
drop-in dir, parses+validates every manifest, and **rejects bad ones without
crashing** — a malformed drop-in is recorded in `unified.json["rejected"]`, the
rest still register (`registry.py:scan`). Validation is fail-closed: bad name,
non-numeric version, unknown kind, or an unparseable dependency constraint all
reject (`manifest.py:validate_manifest`, `limpet/manifest.py:106-150`). The
resolved view is written atomically (temp file + rename) so a reader never sees a
half-written unified manifest (`registry.py:_atomic_write_json`).

### 3. Health check — each plugin probes itself on a cadence
A plugin declares its own `[health] command` plus `interval_seconds` /
`timeout_seconds`. The registry runs it (`health.py:run_health_check`), exit 0 =
healthy; non-zero, timeout, or exec failure = unhealthy, with a short detail line.
A plugin with no `[health]` table is always-healthy (nothing to probe). This is
how a hardware plugin reports its device is actually attached — `hackrf_info`
exits non-zero with no radio, so the plugin shows `unhealthy` and is not started.

### 4. Enable / disable — toggle without uninstalling
`limpet enable <name>` / `disable <name>` flip a boolean in a persisted
`enabled.json` and reconcile (`registry.py:set_enabled` + `cli.py:_cmd_toggle`).
Install implies enabled; only an explicit disable turns a plugin off, and its
drop-in stays in place, so re-enabling is instant (`registry.py:enabled_names`,
`limpet/registry.py:121-126`). A disabled plugin resolves to state `disabled`.

### 5. Dependencies — plugins declare deps on other plugins
`[dependencies]` is `name = "<constraint>"` (e.g. `ghidra = ">=11.0"`). The
resolver (`registry_core.py:resolve`) treats a dependency as satisfied **only if
the target is installed, version-matching, and itself resolved `active`**, then
computes the effective-state ladder — `disabled`, `blocked`, `cycle`,
`unhealthy`, `active` — recording a reason. Because resolution runs in
dependency-first order (`registry_core.py:_resolution_order`), a block propagates
transitively: disable `ghidra` and `ghidra-mcp` goes `blocked` with reason
"dependency 'ghidra' is disabled". Mutual deps are detected as a `cycle` and
never started (`registry_core.py:_find_cycles`). Active plugins are emitted in a
topological `start_order` so every dependency starts before its dependants.

### 6. Community — plugins shared as git repos
A community plugin is a git repo whose root holds `limpet-plugin.toml`; the
`[source]` table carries `git` / `rev` provenance. Installing is clone + symlink
the manifest into the drop-in dir. Worked examples live in `limpet/examples/`:
`ghidra-mcp.toml` (a tool that depends on `ghidra`, demonstrating aspect 5),
`ghidra.toml` (the dependency), and `hackrf.toml` (a hardware module with USB-id
hints and a device-presence health check).

## Code map

| File | Responsibility |
|---|---|
| `limpet/manifest.py` | `limpet-plugin.toml` schema, parser, version-constraint comparator, fail-closed validator (pure) |
| `limpet/registry_core.py` | dependency resolution, effective-state ladder, cycle detection, topological start order (pure) |
| `limpet/health.py` | run a plugin's health command, classify healthy/unhealthy/timeout (only subprocess module) |
| `limpet/registry.py` | the daemon's reconcile body: scan → health → resolve → write `unified.json`; persisted enable/disable (I/O) |
| `limpet/daemon.py` | periodic reconcile loop (detached: systemd/lumen-run) |
| `limpet/cli.py` | `limpet` CLI — validate / list / enable / disable / health / reconcile / unified; `--json`, scriptable exit codes |
| `limpet/examples/*.toml` | community plugin examples (tool, tool-with-dep, hardware) |
| `limpet/test_limpet_*.py` | unit + integration tests (47), pinned to a non-default plugin tree so the scope boundary can actually fail |

## Design decisions & rationale

- **Drop-in dir over a central registry file.** Matches prior art (systemd `.d/`)
  and means an install never edits a shared file two authors would both touch.
- **Stdlib only.** `tomllib` (3.11+) parses manifests; no `packaging` dependency,
  so the version comparator is a small total grammar (`>= > <= < ==`, `* / any`).
  House rule, and plugin authors in apt/Cargo get by with exactly these operators.
- **Pure core, I/O at the edges.** `manifest.py` and `registry_core.py` have no
  filesystem/subprocess, so the schema and the whole dependency ladder are
  unit-testable without a plugin tree or a daemon.
- **Fail closed everywhere.** Unparseable version/constraint, malformed TOML, a
  dep that is merely enabled-but-not-active — all resolve to reject/blocked, never
  to a silent pass. A broken drop-in degrades to "that one plugin is rejected",
  never "the daemon is down".
- **Atomic unified manifest.** Readers poll `unified.json`; it is swapped via
  rename so a partial write is never observed.

## Future work (not in this ticket)
Signature/trust on community manifests before auto-clone; a `limpet install
<git-url>` fetch+symlink helper; restart-on-unhealthy backoff policy; an
inotify-driven reconcile to complement the timer. (wq#3945 added the
`category` and `conflicts` manifest fields and the `data` kind — see
`docs/plugin-spec.md` §9 for the updated shipped/future split: a curated
index repo, signing, and sandbox hardening are still open.)
