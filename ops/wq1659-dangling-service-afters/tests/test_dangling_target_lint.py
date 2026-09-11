#!/usr/bin/env python3
"""wq#1659: staged/unit-drift's dangling_target_lint() flags After=/Wants=/
Requires= targets with no loadable unit, following the same load-as-module
harness as wq#1657's test_unit_drift_wiring.py."""
import importlib.util, os, tempfile, unittest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(HERE, "staged", "unit-drift")


def load_unit_drift():
    spec = importlib.util.spec_from_loader("unit_drift", loader=None)
    mod = importlib.util.module_from_spec(spec)
    mod.__file__ = SCRIPT
    exec(compile(open(SCRIPT).read(), SCRIPT, "exec"), mod.__dict__)
    return mod


class DanglingTargetLint(unittest.TestCase):
    def test_flags_dangling_after_and_wants(self):
        mod = load_unit_drift()
        d = tempfile.mkdtemp()
        with open(os.path.join(d, "lumen-fleet-cerebras.service"), "w") as f:
            f.write("[Unit]\nAfter=litellm.service\nWants=litellm.service\n"
                     "Requires=docker.service\n")
        mod.SYS = d
        mod.SEARCH_PATHS = (d,)
        v = mod.dangling_target_lint()
        self.assertEqual(len(v), 3)  # After=litellm, Wants=litellm, Requires=docker (docker.service absent here too)
        self.assertTrue(any("After=litellm.service" in x for x in v))
        self.assertTrue(any("Wants=litellm.service" in x for x in v))

    def test_clean_when_target_resolves(self):
        mod = load_unit_drift()
        d = tempfile.mkdtemp()
        with open(os.path.join(d, "docker.service"), "w") as f:
            f.write("[Unit]\nDescription=docker\n")
        with open(os.path.join(d, "lumen-fleet-cerebras.service"), "w") as f:
            f.write("[Unit]\nAfter=docker.service\nRequires=docker.service\n")
        mod.SYS = d
        mod.SEARCH_PATHS = (d,)
        self.assertEqual(mod.dangling_target_lint(), [])

    def test_skips_specifier_targets(self):
        mod = load_unit_drift()
        d = tempfile.mkdtemp()
        with open(os.path.join(d, "wq-worker@.service"), "w") as f:
            f.write("[Unit]\nAfter=some-lock@%i.service\n")
        mod.SYS = d
        mod.SEARCH_PATHS = (d,)
        self.assertEqual(mod.dangling_target_lint(), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
