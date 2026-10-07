"""CLASS 3 — the SHIPPED selfheal.yaml rules (wq#3512). Other tests use synthetic rules, so a config-only
change had no test at all; this loads the real file through the real loader and matcher."""
import os

import yaml

import leakwatch

CFG = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "selfheal.yaml")


def _rules():
    with open(CFG) as fh:
        return leakwatch.load_rules(yaml.safe_load(fh))


def test_langfuse_secret_bearing_names_get_the_chmod_action():
    rules = _rules()
    for name in (".env", ".env.bak.20261001_102546_wq3286", "langfuse-web.env",
                 "langfuse-web.env.bak.20261001_030636_wq3213_rotate", "docker-compose.yml",
                 "docker-compose.yml.bak.20261001_030636_wq3213_rotate",
                 "DEPLOYMENT.md.bak.20260518_132429"):
        rule = leakwatch.rule_for("/home/lumen/langfuse/" + name, rules)
        assert rule and rule["action"] == "chmod", name


def test_unrelated_names_in_the_same_dir_are_not_matched():
    rules = _rules()
    for name in ("README.md", "DEPLOYMENT.md", "notes.txt", "dump.sql"):
        assert leakwatch.rule_for("/home/lumen/langfuse/" + name, rules) is None, name


def test_every_shipped_rule_is_well_formed_and_the_original_five_survive():
    rules = _rules()
    assert len(rules) >= 6
    for r in rules:
        assert os.path.isabs(r["dir"]) and r["globs"] and r["action"] in ("scan", "chmod")
    assert leakwatch.rule_for("/home/lumen/claude_memory.md", rules)["action"] == "scan"
    assert leakwatch.rule_for("/home/lumen/agent/.env", rules)["action"] == "chmod"
