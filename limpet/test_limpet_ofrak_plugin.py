"""wq#3967: the OFRAK plugin manifest (limpet/examples/ofrak.toml) loads
cleanly through the real plugin registry, the same Registry.scan() path any
other drop-in plugin goes through (see test_limpet_registry.py). Exercises
the actual committed file, not a copy pasted into the test, so a future edit
to ofrak.toml that breaks the schema fails here.

Loads limpet/registry.py by file path rather than `from registry import
Registry`: that bare import resolves to the limpet/registry/ PACKAGE
(wq#3807's tool-catalog subsystem, which has no `Registry` class) instead of
the limpet/registry.py MODULE (wq#3810's plugin registry) whenever both sit
in the same sys.path dir -- a name collision that pre-dates this ticket and
currently breaks test_limpet_registry.py and test_limpet_daemon.py the same
way (ISSUE-LOG.md). Fixing that collision is out of scope here; this file
just doesn't trip over it."""
import importlib.util
import shutil
import sys
from pathlib import Path

_LIMPET_DIR = str(Path(__file__).resolve().parent)
if _LIMPET_DIR not in sys.path:
    sys.path.insert(0, _LIMPET_DIR)  # so registry.py's own `from health import ...` etc. resolve

_REGISTRY_PATH = Path(__file__).resolve().parent / "registry.py"
_spec = importlib.util.spec_from_file_location("limpet_plugin_registry", _REGISTRY_PATH)
_registry_module = importlib.util.module_from_spec(_spec)
sys.modules["limpet_plugin_registry"] = _registry_module
_spec.loader.exec_module(_registry_module)
Registry = _registry_module.Registry

EXAMPLE = Path(__file__).resolve().parent / "examples" / "ofrak.toml"


def test_ofrak_example_manifest_loads_in_registry(tmp_path):
    plugins = tmp_path / "plugins.d"
    state = tmp_path / "state"
    plugins.mkdir()
    shutil.copyfile(EXAMPLE, plugins / "ofrak.toml")

    reg = Registry(plugins_dir=str(plugins), state_dir=str(state),
                    health_runner=lambda cmd, t: (0, "ok"), clock=lambda: 1000.0)
    report = reg.scan()

    assert report.rejected == {}
    assert "ofrak" in report.loaded
    manifest = report.loaded["ofrak"]
    assert manifest.kind == "tool"
    assert manifest.category == "firmware"
