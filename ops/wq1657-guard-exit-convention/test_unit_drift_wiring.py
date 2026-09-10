#!/usr/bin/env python3
"""Integration test (wq#1657): staged/unit-drift's guard_lint() finds
guard_exit_lint.py next to itself and lints a fixture SYS dir."""
import importlib.util, os, tempfile, unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(HERE, "staged", "unit-drift")


def load_unit_drift():
    spec = importlib.util.spec_from_loader("unit_drift", loader=None)
    mod = importlib.util.module_from_spec(spec)
    mod.__file__ = SCRIPT  # guard_lint() resolves the module next to the script
    exec(compile(open(SCRIPT).read(), SCRIPT, "exec"), mod.__dict__)
    return mod


class Wiring(unittest.TestCase):
    def test_guard_lint_flags_untagged_known_guard(self):
        mod = load_unit_drift()
        d = tempfile.mkdtemp()
        with open(os.path.join(d, "wq-drift-guard.service"), "w") as f:
            f.write("[Service]\nExecStart=/bin/true\n")
        mod.SYS = d
        v = mod.guard_lint()
        self.assertEqual(len(v), 1)
        self.assertIn("GEL2 wq-drift-guard.service", v[0])

    def test_guard_lint_clean_on_tagged_unit(self):
        mod = load_unit_drift()
        d = tempfile.mkdtemp()
        with open(os.path.join(d, "wq-drift-guard.service"), "w") as f:
            f.write("[Unit]\nOnFailure=ntfy-onfailure@%n.service\n"
                    "[Service]\nX-Lumen-Guard=finding-exit\nExecStart=/bin/true\n")
        mod.SYS = d
        self.assertEqual(mod.guard_lint(), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
