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
