"""wq#3834: pytest spells a str param's auto id as the str itself, so `pytest -v` and
`--collect-only` print every credential-shaped fixture whole, built at runtime or not
(wq#3830: `credentials=` + a 35-char tail from test_masking.py paged kv-secret off a
PASSED listing). Such an id is shown masked; pytest suffixes any duplicates.

The repo-root conftest loads this by file and harness/tests/conftest.py imports it, so a
run from either dir gets the hook (a run from harness/tests never loads the root one)."""
import importlib.util
import os
import sys

HARNESS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _masking():
    """harness/masking.py, loaded by file so no dir goes onto sys.path. A test module
    that already imported it shares the same module."""
    for name in ("live_code", "masking"):
        if name not in sys.modules:
            spec = importlib.util.spec_from_file_location(
                name, os.path.join(HARNESS, name + ".py"))
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            sys.modules[name] = mod
    return sys.modules["masking"]


def pytest_make_parametrize_id(config, val, argname):
    if not isinstance(val, str):
        return None
    masked, fired = _masking().mask_text(val)
    return masked if fired else None
