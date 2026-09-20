#!/usr/bin/env python3
"""wq#2218: staged/unit-drift advises on masked units that retired_services.yaml
does not record. Runs standalone: python3 test_masked_provenance.py"""
import contextlib, importlib.util, io, os, tempfile, unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(HERE, "staged", "unit-drift")
REAL_YAML = os.path.join(HERE, os.pardir, os.pardir, "docs", "fleet", "retired_services.yaml")


def load_unit_drift():
    spec = importlib.util.spec_from_loader("unit_drift", loader=None)
    mod = importlib.util.module_from_spec(spec)
    mod.__file__ = SCRIPT
    exec(compile(open(SCRIPT).read(), SCRIPT, "exec"), mod.__dict__)
    return mod


MASK = {"kind": "symlink", "target": "/dev/null"}
ALIAS = {"kind": "symlink", "target": "/lib/systemd/system/x.service"}
FILE = {"kind": "file", "sha256": "0"}


class MaskedProvenance(unittest.TestCase):
    def setUp(self):
        self.mod = load_unit_drift()

    def yaml_with(self, text):
        d = tempfile.mkdtemp()
        p = os.path.join(d, "retired.yaml")
        with open(p, "w") as f:
            f.write(text)
        self.mod.RETIRED_YAML = p

    def test_only_dev_null_symlinks_count_as_masked(self):
        units = {"a.service": MASK, "b.service": ALIAS, "c.service": FILE}
        self.assertEqual(self.mod.masked_units(units), ["a.service"])

    def test_unrecorded_mask_is_named(self):
        self.yaml_with("- name: x\n  units: [other.service]\n")
        self.assertEqual(self.mod.unexplained_masks({"a.service": MASK}), ["a.service"])

    def test_recorded_mask_is_silent_with_or_without_suffix(self):
        self.yaml_with("- name: x\n  units: [a.service, b]\n")
        units = {"a.service": MASK, "b.service": MASK}
        self.assertEqual(self.mod.unexplained_masks(units), [])

    def test_unreadable_record_fails_open(self):
        self.mod.RETIRED_YAML = "/nonexistent/retired.yaml"
        self.assertEqual(self.mod.unexplained_masks({"a.service": MASK}), [])

    def test_advisory_points_at_the_record(self):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            self.mod.print_mask_advisory(["a.service"])
        self.assertIn("retired_services.yaml", buf.getvalue())
        self.assertIn("a.service", buf.getvalue())

    def test_no_masks_prints_nothing(self):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            self.mod.print_mask_advisory([])
        self.assertEqual(buf.getvalue(), "")

    def test_committed_record_explains_lumen_pokemon_live(self):
        self.mod.RETIRED_YAML = REAL_YAML
        self.assertEqual(self.mod.unexplained_masks({"lumen-pokemon-live.service": MASK}), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
