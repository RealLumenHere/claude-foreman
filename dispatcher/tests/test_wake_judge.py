"""wq#3650: wake_judge.py's three steps (wakeonlan, poll ssh, dispatch), against the real
registry schema (research/inference_nodes.yaml), isolated with a scratch copy the same way
test_node_power.py isolates node_power.py.
"""
import os
import sys

import pytest

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
DISPATCHER_DIR = os.path.dirname(TESTS_DIR)
REPO = os.path.dirname(DISPATCHER_DIR)
for p in (DISPATCHER_DIR, os.path.join(REPO, "harness")):
    sys.path.insert(0, p)
import wake_judge  # noqa: E402

NODE = "isis"
MAC = "00:3e:e1:c8:27:97"


@pytest.fixture
def registry(tmp_path):
    """A scratch copy of the real registry, untouched: isis is still `wake.method: plug-ac` here,
    same as live — the happy-path tests opt into `wol` explicitly via the `wol_registry` fixture."""
    src = os.path.join(REPO, "research", "inference_nodes.yaml")
    text = open(src).read()
    assert f'mac: "{MAC}"' in text        # the field this ticket added for isis
    dst = tmp_path / "inference_nodes.yaml"
    dst.write_text(text)
    return str(dst)


@pytest.fixture
def wol_registry(registry, tmp_path):
    """`registry`, with isis's wake.method flipped to wol (its mac is already live-registry data)."""
    text = open(registry).read()
    old = f'method: plug-ac\n      mac: "{MAC}"'
    new = f'method: wol\n      mac: "{MAC}"'
    assert old in text
    dst = tmp_path / "inference_nodes_wol.yaml"
    dst.write_text(text.replace(old, new, 1))
    return str(dst)


class FakePorts:
    def __init__(self, ssh_after=0):
        self.calls = []
        self.wol_rc = 0
        self.ssh_after = ssh_after    # how many "true" polls fail before one succeeds
        self._polls = 0

    def wol(self, mac):
        self.calls.append(("wol", mac))
        return self.wol_rc, ""

    def ssh(self, node, cmd, timeout=120):
        self.calls.append(("ssh", node, cmd))
        self._polls += 1
        return (0, "") if self._polls > self.ssh_after else (255, "")

    def start_unit(self, unit):
        self.calls.append(("start_unit", unit))
        return 0, ""


def test_wol_node_sends_packet_polls_then_dispatches(wol_registry):
    ports = FakePorts(ssh_after=2)
    sleeps = []
    rc, msg = wake_judge.wake(NODE, ports, registry_path=wol_registry,
                              dispatch_unit="lumen-foreman-dispatch.service", sleep=sleeps.append)
    assert rc == 0 and "up after" in msg
    assert ports.calls[0] == ("wol", MAC)
    assert ports.calls.count(("ssh", NODE, "true")) == 3
    assert ("start_unit", "lumen-foreman-dispatch.service") in ports.calls
    assert len(sleeps) == 2   # two failed polls before the one that succeeded


def test_send_only_skips_the_poll_and_the_dispatch(wol_registry):
    ports = FakePorts(ssh_after=10**6)
    rc, msg = wake_judge.wake(NODE, ports, send_only=True, registry_path=wol_registry,
                              dispatch_unit="lumen-foreman-dispatch.service")
    assert rc == 0 and "magic packet sent" in msg
    assert ports.calls == [("wol", MAC)]


def test_wol_send_failure_is_rc_2_and_never_polls(wol_registry):
    ports = FakePorts()
    ports.wol_rc = 1
    rc, msg = wake_judge.wake(NODE, ports, registry_path=wol_registry)
    assert rc == 2 and "failed" in msg
    assert ports.calls == [("wol", MAC)]


def test_ssh_timeout_is_rc_3(wol_registry):
    ports = FakePorts(ssh_after=10**6)
    rc, msg = wake_judge.wake(NODE, ports, timeout=0.01, registry_path=wol_registry, sleep=lambda s: None)
    assert rc == 3 and "did not answer" in msg


def test_a_plug_ac_node_is_refused_rc_1(registry):
    ports = FakePorts()
    rc, msg = wake_judge.wake("hathor", ports, registry_path=registry)   # hathor stays wake.method: plug-ac
    assert rc == 1 and "not 'wol'" in msg
    assert ports.calls == []


def test_mac_missing_is_refused_rc_1(wol_registry, tmp_path):
    text = open(wol_registry).read().replace(f'mac: "{MAC}"', "mac: null", 1)
    patched = tmp_path / "patched.yaml"
    patched.write_text(text)
    ports = FakePorts()
    rc, msg = wake_judge.wake(NODE, ports, registry_path=str(patched))
    assert rc == 1 and "mac is unset" in msg
    assert ports.calls == []


def test_unknown_node_raises(registry):
    with pytest.raises(SystemExit):
        wake_judge.wake("not-a-real-node", FakePorts(), registry_path=registry)
