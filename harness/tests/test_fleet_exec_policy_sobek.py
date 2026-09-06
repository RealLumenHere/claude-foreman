"""wq#1445 regression: sobek has a tailscale_ip fact but its hostname does not
resolve from Lumen (missing from fleet-keymesh's NODES list -> no ~/.ssh/fleet_config
entry). `copy_dirs` grants the `copy` verb per node, so sobek must stay out of it
until fleet-keymesh is fixed and re-run (wq#1447) — otherwise `fleet-exec copy sobek`
passes the known-node policy gate and only then dies on DNS (the exact bug this
ticket reports). never.power/never.restart are deny-lists unrelated to reachability
and must still list sobek regardless.
"""
import os

import yaml

HARNESS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
POLICY_PATH = os.path.join(HARNESS_DIR, "fleet-exec-policy.yaml")


def _load_policy():
    with open(POLICY_PATH, "rb") as f:
        return yaml.safe_load(f.read())


def test_sobek_absent_from_copy_dirs_until_ssh_resolves():
    policy = _load_policy()
    assert "sobek" not in policy["copy_dirs"], (
        "sobek is back in copy_dirs — only re-add it once `ssh sobek -- true` exits 0 "
        "(wq#1447 tracks the fleet-keymesh fix)")


def test_sobek_never_lists_untouched():
    policy = _load_policy()
    assert "sobek" in policy["never"]["power"]
    assert "sobek" in policy["never"]["restart"]
