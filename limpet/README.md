# LIMPET plugin system

Hot-swappable **tool**, **hardware**, and **data** modules for LIMPET. Drop a
`limpet-plugin.toml` into `/etc/limpet/plugins.d/`, and the registry daemon
validates it, resolves its dependencies and conflicts, health-checks it, and
publishes a unified view every other tool reads. Full spec:
[`design/wq3810_limpet_plugin_system.md`](../design/wq3810_limpet_plugin_system.md),
refined by ecosystem research in
[`docs/plugin-spec.md`](../docs/plugin-spec.md).

## Write a plugin in 60 seconds

Create `my-thing.toml` (filename stem must equal `name`):

```toml
[plugin]
name = "my-thing"
version = "1.0.0"
kind = "tool"          # or "hardware" / "data"
category = "fuzzer"    # optional: see manifest.py CATEGORIES for the full enum
summary = "what it does"
conflicts = ["other-plugin-name"]   # optional: can't both be active

[dependencies]          # optional: other plugins it needs (missing/mismatched -> blocked)
some-dep = ">=2.0"

[optional_dependencies]   # optional: other plugins that help but are never required
nice-to-have = "*"

[health]                # optional: how it proves it's alive (exit 0 = healthy)
command = "my-thing --health"
interval_seconds = 60
timeout_seconds = 10
```

Validate, install (drop into the dir), and inspect:

```sh
python3 limpet/cli.py validate my-thing.toml
cp my-thing.toml /etc/limpet/plugins.d/
python3 limpet/cli.py list          # effective state of every plugin
python3 limpet/cli.py list --category sdr   # filter by category
python3 limpet/cli.py disable my-thing   # toggle off without uninstalling
```

Share it as a git repo with the `.toml` at its root (see `examples/`).

## Discover and install from the registry (wq#4094)

`limpet-tools` is the registry front door — federated git-repo indexes,
offline-first search, content-bound trust (`docs/plugin-registry-spec.md`
in the limpet project repo):

```sh
python3 limpet/limpet_tools.py index add <git-url> --name canonical --ceiling core
python3 limpet/limpet_tools.py search "bluetooth sniffer"   # local query; pulls if online
python3 limpet/limpet_tools.py info ubertooth
python3 limpet/limpet_tools.py install ubertooth            # pin rev, verify sig, install
python3 limpet/limpet_tools.py install <git-url> --name x   # direct install, unlisted tier
python3 limpet/limpet_tools.py verify ubertooth             # re-verify content now
python3 limpet/limpet_tools.py elevate flashrom             # per-plugin privilege grant
```

Trust is a minisign signature over `(name, git, rev)` plus a tree hash of the
pinned checkout, re-verified on **every registry scan** (not just install) —
a plugin tampered with on disk fails to load. Trust roots live in
`/etc/limpet/trust/` (`LIMPET_TRUST_DIR` to override): `core.pub`,
`vetted-*.pub` reviewer keys, `index-*.pub` operator-trusted index keys.
Community/unlisted plugins that declare `[mutation].escalates = true` are
refused until explicitly elevated (plugin-spec.md §6.2 tier policy).

## Run the daemon

```sh
python3 limpet/daemon.py --interval 30      # reconcile loop; run detached
```

Set `LIMPET_PLUGINS_DIR` / `LIMPET_STATE_DIR` to override the `/etc` and `/var`
defaults (the tests pin both to a tmp tree).

## Tests

```sh
python3 -m pytest limpet/ -q
```
