"""wq#1445 / wq#1447: sobek was dropped from `copy_dirs` while its hostname did not
resolve from Lumen (missing from fleet-keymesh's NODES list). wq#1447 added it to
fleet-keymesh and re-ran the mesh, so `ssh sobek -- true` exits 0 and the copy grant
is back, scoped to its lumen-drop like every other node. never.power/never.restart
are deny-lists unrelated to reachability and must list sobek regardless.
"""
import os

import yaml

HARNESS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
POLICY_PATH = os.path.join(HARNESS_DIR, "fleet-exec-policy.yaml")


def _load_policy():
    with open(POLICY_PATH, "rb") as f:
        return yaml.safe_load(f.read())


def test_sobek_copy_dirs_is_its_lumen_drop_only():
    policy = _load_policy()
    assert policy["copy_dirs"]["sobek"] == ["/home/sobek/lumen-drop"]


def test_sobek_never_lists_untouched():
    policy = _load_policy()
    assert "sobek" in policy["never"]["power"]
    assert "sobek" in policy["never"]["restart"]
