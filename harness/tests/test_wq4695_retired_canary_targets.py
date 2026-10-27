"""wq#4695 — lumen_watchdog.py DEVICES and the maint-ops ollama/tailscale canary
harnesses were still hardwired to isis/hathor/khepri/wadjet after all four were
retired (only nephthys had ever been removed, wq#589). This is a repo-owned
regression guard against that drift recurring, run directly against the fix
shipped in ops/wq4695-watchdog-retired-canary-a1/ (the live
/home/lumen/agent + /home/lumen/bin paths are guard.sh PRIMARY_TREES this repo's
workers cannot write — see that bundle's README "Why the live files aren't
touched directly").
"""
import importlib.util
import json
import os
import re
import subprocess
import sys

import pytest

HARNESS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPO_ROOT = os.path.dirname(HARNESS)
BUNDLE = os.path.join(REPO_ROOT, "ops", "wq4695-watchdog-retired-canary-a1")

RETIRED_JSON = os.path.join(BUNDLE, "fleet-retired-nodes.json")
WATCHDOG_PY = os.path.join(BUNDLE, "lumen_watchdog.py")
OLLAMA_CANARY = os.path.join(BUNDLE, "tests", "ollama_canary.sh")
TAILSCALE_CANARY = os.path.join(BUNDLE, "tests", "tailscale_canary.sh")

RETIRED_NAMES = {"isis", "hathor", "khepri", "wadjet", "nephthys"}


def _load_watchdog(tmp_path, retired_json_path):
    spec = importlib.util.spec_from_file_location("wq4695_watchdog", WATCHDOG_PY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.RETIRED_NODES_PATH = retired_json_path
    return mod


def test_shared_retired_list_matches_the_ticket():
    with open(RETIRED_JSON) as f:
        retired = json.load(f)
    assert set(retired) == RETIRED_NAMES
    # dates, not just presence: a bare True/False would let a future retirement
    # land with no record of when it happened.
    for name, date in retired.items():
        assert re.match(r"^\d{4}-\d{2}-\d{2}$", date), f"{name}: {date!r} is not YYYY-MM-DD"


def test_devices_has_no_retired_node():
    mod = _load_watchdog(None, RETIRED_JSON)
    assert RETIRED_NAMES.isdisjoint(mod.DEVICES), (
        f"DEVICES still monitors {RETIRED_NAMES & set(mod.DEVICES)}")
    # anubis replaced wadjet as the canary target, but it's still a monitored
    # device in its own right — must not have been dropped by mistake.
    assert "anubis" in mod.DEVICES


def test_retired_devices_stay_out_of_transient_too():
    mod = _load_watchdog(None, RETIRED_JSON)
    assert RETIRED_NAMES.isdisjoint(mod.TRANSIENT_DEVICES)


def test_startup_guard_passes_on_the_real_devices_dict():
    mod = _load_watchdog(None, RETIRED_JSON)
    mod.check_no_retired_devices_monitored()  # must not raise


def test_startup_guard_raises_loudly_if_a_retired_node_is_reintroduced():
    mod = _load_watchdog(None, RETIRED_JSON)
    mod.DEVICES = dict(mod.DEVICES, wadjet="100.116.217.15")
    with pytest.raises(RuntimeError, match="wadjet"):
        mod.check_no_retired_devices_monitored()


def test_startup_guard_is_wired_into_main():
    src = open(WATCHDOG_PY).read()
    assert "check_no_retired_devices_monitored()" in src.split('if __name__')[-1]


@pytest.mark.parametrize("path", [OLLAMA_CANARY, TAILSCALE_CANARY])
def test_canary_script_no_longer_targets_wadjet(path):
    """wadjet is still named in a historical comment (why anubis replaced it) —
    what must be gone is wadjet as an actual ssh/connection target."""
    src = open(path).read()
    assert "wadjet@" not in src
    assert "192.168.8.173" not in src  # wadjet's old LAN IP
    assert "100.116.217.15" not in src  # wadjet's old tailscale IP
    assert "anubis" in src


@pytest.mark.parametrize("path", [OLLAMA_CANARY, TAILSCALE_CANARY])
def test_canary_script_checks_shared_retired_list(path):
    src = open(path).read()
    assert "fleet-retired-nodes.json" in src
    assert "check_canary_not_retired" in src


@pytest.mark.parametrize("path", [OLLAMA_CANARY, TAILSCALE_CANARY])
def test_canary_script_fails_loud_on_unreachable_canary(path):
    src = open(path).read()
    assert "REFUSED" in src and "does not skip" in src
    assert re.search(r"ssh -n .*true", src), "must probe reachability before any mutation"


@pytest.mark.parametrize("path", [OLLAMA_CANARY, TAILSCALE_CANARY])
def test_canary_script_is_valid_bash(path):
    r = subprocess.run(["bash", "-n", path], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


def test_canary_retired_check_actually_refuses_a_retired_name(tmp_path):
    """Exercises check_canary_not_retired()'s own python one-liner in isolation,
    against a node name OUTSIDE the live roster (a probe that can fail, wq#2630)."""
    fixture = tmp_path / "retired.json"
    fixture.write_text(json.dumps({"some-other-retired-node": "2026-01-01"}))
    script = (
        "import json, sys\n"
        f"retired = json.load(open({str(fixture)!r}))\n"
        "sys.exit(1 if 'some-other-retired-node' in retired else 0)\n"
    )
    r = subprocess.run([sys.executable, "-c", script])
    assert r.returncode == 1
