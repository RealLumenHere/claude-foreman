import importlib.util
import os
import sys

# wq#3222: a repo-root `pytest` collects every test_*.py in the tree. The macbench agentic
# suite's repo/ and solution/ dirs are task templates that mb_agentic.py copies into a
# scratch git repo and scores there; their tests are meant to fail until a model under
# benchmark fixes them, and they import modules that only resolve inside that scratch repo.
collect_ignore_glob = ["tools/macbench/agentic/suite/*/repo", "tools/macbench/agentic/suite/*/solution"]

# wq#3222: the no-live-aq/ntfy guard covers every dir a root run collects, not only
# harness/tests. Loaded by file under its own name, so no dir goes onto sys.path, and
# harness/tests/conftest.py's `import live_bin_guard` gets this same module.
if "live_bin_guard" not in sys.modules:
    _spec = importlib.util.spec_from_file_location(
        "live_bin_guard", os.path.join(os.path.dirname(__file__), "harness", "tests", "live_bin_guard.py"))
    sys.modules["live_bin_guard"] = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(sys.modules["live_bin_guard"])
sys.modules["live_bin_guard"].install()


def pytest_sessionfinish(session, exitstatus):
    sys.modules["live_bin_guard"].session_finish(session)
