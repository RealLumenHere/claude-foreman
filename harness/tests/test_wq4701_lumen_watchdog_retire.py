"""wq#4701: lumen-watchdog is retired, not rescheduled. These tests pin the evidence
for that decision and check the bundle that carries it out
(ops/wq4701-lumen-watchdog-retire/).

The coverage test reads the last shipped watchdog with a rev-pinned `git show`.
That copy is byte-identical to the live /home/lumen/agent/lumen_watchdog.py on
2026-10-27. The test checks it against a committed Gatus snapshot, so it can be
re-run, and it fails if any watchdog target lacks a Gatus endpoint.
"""
import ast
import os
import subprocess

import pytest
import yaml

HARNESS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPO_ROOT = os.path.dirname(HARNESS)
BUNDLE = os.path.join(REPO_ROOT, "ops", "wq4701-lumen-watchdog-retire")
WQ4695 = os.path.join(REPO_ROOT, "ops", "wq4695-watchdog-retired-canary-a1")
RETIRED_YAML = os.path.join(REPO_ROOT, "docs", "fleet", "retired_services.yaml")
GATUS_FIXTURE = os.path.join(BUNDLE, "gatus-endpoints-2026-10-27.txt")
PINNED_WATCHDOG = "7e91411f:ops/wq4695-watchdog-retired-canary-a1/lumen_watchdog.py"

# Watchdog SERVICES names whose Gatus endpoint is named differently.
SERVICE_ALIASES = {"ntfy": "ntfy-self", "beszel": "beszel-hub"}


def _pinned_watchdog_dicts():
    src = subprocess.run(["git", "-C", REPO_ROOT, "show", PINNED_WATCHDOG],
                         capture_output=True, text=True, check=True).stdout
    out = {}
    for node in ast.parse(src).body:
        if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name):
            name = node.targets[0].id
            if name in ("DEVICES", "SERVICES", "OLLAMA_URL"):
                out[name] = ast.literal_eval(node.value)
    return out


def _gatus_keys():
    with open(GATUS_FIXTURE) as f:
        return {l.strip() for l in f if l.strip() and not l.startswith("#")}


def _uncovered(wd, keys):
    # lumen is the host the watchdog itself runs on, so the watchdog could never
    # have reported it down. It is the one target with no Gatus twin.
    missing = {d for d in set(wd["DEVICES"]) - {"lumen"} if f"1-nodes_{d}" not in keys}
    missing |= {s for s in wd["SERVICES"]
                if f"2-lumen-services_{SERVICE_ALIASES.get(s, s)}" not in keys}
    if "2-lumen-services_ollama-local" not in keys:
        missing.add("ollama")
    return missing


def test_gatus_covers_every_watchdog_target():
    wd = _pinned_watchdog_dicts()
    assert len(wd["DEVICES"]) > 1 and wd["SERVICES"], "pinned dicts parsed empty"
    assert "127.0.0.1:11434" in wd["OLLAMA_URL"]
    missing = _uncovered(wd, _gatus_keys())
    assert not missing, f"watchdog targets with no Gatus endpoint: {sorted(missing)}"


def test_coverage_check_fails_on_an_uncovered_target():
    """The coverage probe must be able to fail (wq#2630)."""
    wd = _pinned_watchdog_dicts()
    wd["DEVICES"] = dict(wd["DEVICES"], ghostnode="100.0.0.1")
    wd["SERVICES"] = dict(wd["SERVICES"], ghostsvc=("localhost", 1))
    assert _uncovered(wd, _gatus_keys()) == {"ghostnode", "ghostsvc"}
    assert "ollama" in _uncovered(wd, set())


def test_retired_services_records_both_units():
    entries = {e["name"]: e for e in yaml.safe_load(open(RETIRED_YAML))}
    e = entries["lumen-watchdog"]
    assert set(e["units"]) == {"lumen-watchdog.timer", "lumen-watchdog.service"}
    assert e["retired_on"] == "2026-08-27"
    assert "wq#4701" in e["ticket"]


def test_wq4695_bundle_no_longer_ships_the_watchdog():
    assert not os.path.exists(os.path.join(WQ4695, "lumen_watchdog.py"))
    src = open(os.path.join(WQ4695, "install.sh")).read()
    assert "WATCHDOG_LIVE" not in src and "vfile \"$SRC/lumen_watchdog.py\"" not in src


def test_preserved_units_match_what_remove_units_deletes():
    src = open(os.path.join(BUNDLE, "remove-units.sh")).read()
    for u in ("lumen-watchdog.timer", "lumen-watchdog.service"):
        assert os.path.exists(os.path.join(BUNDLE, "units", u))
        assert f"/etc/systemd/system/{u}" in src
    timer = open(os.path.join(BUNDLE, "units", "lumen-watchdog.timer")).read()
    assert "Unit=lumen-watchdog.service" in timer


@pytest.mark.parametrize("name", ["install.sh", "remove-units.sh"])
def test_bundle_scripts_are_valid_bash(name):
    r = subprocess.run(["bash", "-n", os.path.join(BUNDLE, name)],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


def test_verify_is_read_only_and_reports_units_still_present():
    """Before the root apply, --verify must say MISS rather than OK. It must not
    write anything either; that is checked by grepping for mutating verbs ahead
    of vdone."""
    src = open(os.path.join(BUNDLE, "install.sh")).read()
    verify = src.split('= --verify ]; then', 1)[1].split("vdone", 1)[0]
    for verb in ("rm ", "mv ", "sudo", "systemctl disable", "systemctl daemon-reload", "tee "):
        assert verb not in verify
    r = subprocess.run(["bash", os.path.join(BUNDLE, "install.sh"), "--verify"],
                       capture_output=True, text=True)
    if r.returncode == 0:
        pytest.skip("already applied on this host")
    assert r.returncode == 1, r.stdout + r.stderr
    assert "MISS" in r.stdout
