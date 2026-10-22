"""CLASS 3 — the SHIPPED selfheal.yaml rules (wq#3512). Other tests use synthetic rules, so a config-only
change had no test at all; this loads the real file through the real loader and matcher."""
import os

import pytest
import yaml

import leakwatch

CFG = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "selfheal.yaml")


def _shipped_rules():
    """What selfheal.yaml declares, independent of which rule dirs happen to exist on
    this host -- the shipped contract (wq#4464). load_rules() additionally drops any
    rule whose dir is absent (leakwatch.py:178-184), which is a live-host property, not
    a shipped-config property; asserting through that filter let the langfuse rule
    (selfheal.yaml:237-239) go untested the moment /home/lumen/langfuse stopped existing."""
    with open(CFG) as fh:
        cfg = yaml.safe_load(fh)
    return [{"dir": r["dir"], "globs": list(r["globs"]), "action": r.get("action", "scan")}
            for r in cfg["leakwatch"]["rules"]]


def _loaded_rules():
    with open(CFG) as fh:
        return leakwatch.load_rules(yaml.safe_load(fh))


def test_langfuse_secret_bearing_names_get_the_chmod_action():
    rules = _shipped_rules()
    for name in (".env", ".env.bak.20261001_102546_wq3286", "langfuse-web.env",
                 "langfuse-web.env.bak.20261001_030636_wq3213_rotate", "docker-compose.yml",
                 "docker-compose.yml.bak.20261001_030636_wq3213_rotate",
                 "DEPLOYMENT.md.bak.20260518_132429"):
        rule = leakwatch.rule_for("/home/lumen/langfuse/" + name, rules)
        assert rule and rule["action"] == "chmod", name


def test_unrelated_names_in_the_same_dir_are_not_matched():
    rules = _shipped_rules()
    for name in ("README.md", "DEPLOYMENT.md", "notes.txt", "dump.sql"):
        assert leakwatch.rule_for("/home/lumen/langfuse/" + name, rules) is None, name


def test_every_shipped_rule_is_well_formed_and_the_original_five_survive():
    rules = _shipped_rules()
    assert len(rules) >= 6
    for r in rules:
        assert os.path.isabs(r["dir"]) and r["globs"] and r["action"] in ("scan", "chmod")
    assert leakwatch.rule_for("/home/lumen/claude_memory.md", rules)["action"] == "scan"
    assert leakwatch.rule_for("/home/lumen/agent/.env", rules)["action"] == "chmod"


@pytest.mark.skipif(not os.path.isdir("/home/lumen/langfuse"),
                     reason="load_rules (leakwatch.py:178-184) drops rules whose dir is "
                            "absent; /home/lumen/langfuse doesn't exist on this host "
                            "(wq#4464, ISSUE-LOG row 1501)")
def test_load_rules_keeps_the_langfuse_rule_when_its_dir_exists():
    rule = leakwatch.rule_for("/home/lumen/langfuse/.env", _loaded_rules())
    assert rule and rule["action"] == "chmod"
