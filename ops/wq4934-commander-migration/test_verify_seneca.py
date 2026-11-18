"""Tests for verify_seneca.py. The node is a table of canned answers; the file-existence
probe is also run for real against a temp directory."""

import importlib.util
import json
import os
from pathlib import Path

import pytest

_spec = importlib.util.spec_from_file_location(
    "verify_seneca", Path(__file__).with_name("verify_seneca.py"))
vs = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(vs)

CPUINFO = "\n".join(["processor\t: 0",
                     "model name\t: 11th Gen Intel(R) Core(TM) i3-1115G4 @ 3.00GHz"] * 2)


def _absent(path):
    return (1, "", f"stat: cannot statx '{path}': No such file or directory")


def node(**over):
    """Answers of a Seneca that passes Phase A in standby. Override by argv string."""
    answers = {
        "hostname": (0, "seneca", ""),
        "cat /proc/cpuinfo": (0, CPUINFO, ""),
        "ls /sys/block": (0, "loop0\nnvme0n1", ""),
        "tailscale status": (0, "100.83.19.124  seneca  horus@  linux  -", ""),
        f"stat -c %n {vs.RESTORED_FILE}": (0, vs.RESTORED_FILE, ""),
        "systemctl is-active cloudflared.service": (3, "inactive", ""),
        "systemctl is-enabled cloudflared.service": (1, "masked", ""),
        "systemctl is-active dc-gateway.service": (3, "inactive", ""),
        "systemctl is-enabled dc-gateway.service": (1, "masked", ""),
    }
    for marker in vs.MINIMIZED_MARKERS:
        answers[f"stat -c %n {marker}"] = _absent(marker)
    answers.update(over)

    def run(argv):
        return answers[" ".join(argv)]
    return run


def verdict(run, *argv, capsys):
    rc = vs.main([*argv, "--json"], runner=run)
    report = json.loads(capsys.readouterr().out)
    return rc, {c["name"]: c for c in report["checks"]}


def test_phase_a_complete_node_passes(capsys):
    rc, checks = verdict(node(), capsys=capsys)
    assert rc == 0
    assert all(c["ok"] for c in checks.values())
    assert set(checks) == {"node:hostname", "hw:cpu", "hw:nvme", "os:unminimized",
                           "net:tailscale", "carrier:cloudflared.service",
                           "carrier:dc-gateway.service"}


@pytest.mark.parametrize("marker", vs.MINIMIZED_MARKERS)
def test_any_minimized_marker_fails(marker, capsys):
    rc, checks = verdict(node(**{f"stat -c %n {marker}": (0, marker, "")}), capsys=capsys)
    assert rc == 1
    assert checks["os:unminimized"]["detail"] == f"still minimized: {marker}"


def test_markers_gone_but_man_pages_missing_fails(capsys):
    run = node(**{f"stat -c %n {vs.RESTORED_FILE}": _absent(vs.RESTORED_FILE)})
    rc, checks = verdict(run, capsys=capsys)
    assert rc == 1
    assert "packages not reinstalled" in checks["os:unminimized"]["detail"]


def test_a_probe_that_cannot_tell_is_not_read_as_absent(capsys):
    marker = vs.MINIMIZED_MARKERS[0]
    for answer in [(255, "", "ssh: connect to host seneca port 22: Connection timed out"),
                   (1, "", f"stat: cannot statx '{marker}': Permission denied"),
                   (2, "", "DENY [grant:grant_missing_env]: grant file did not verify")]:
        rc, checks = verdict(node(**{f"stat -c %n {marker}": answer}), capsys=capsys)
        assert rc == 1
        assert checks["os:unminimized"]["detail"] == f"probe failed for {marker}"


def test_unreachable_node_fails_with_one_line(capsys):
    run = node(hostname=(255, "", "ssh: connect to host 192.168.8.209 port 22: No route to host"))
    rc, checks = verdict(run, capsys=capsys)
    assert rc == 1
    assert list(checks) == ["node:hostname"]
    assert "No route to host" in checks["node:hostname"]["detail"]


def test_nvme_still_in_the_old_chassis_fails(capsys):
    old = "model name\t: Intel(R) Core(TM) i7-7500U CPU @ 2.70GHz"
    rc, checks = verdict(node(**{"cat /proc/cpuinfo": (0, old, "")}), capsys=capsys)
    assert rc == 1
    assert not checks["hw:cpu"]["ok"] and "i7-7500U" in checks["hw:cpu"]["detail"]
    assert vs.main(["--cpu", "i7-7500U"], runner=node(**{"cat /proc/cpuinfo": (0, old, "")})) == 0


def test_wrong_hostname_and_missing_nvme_fail(capsys):
    run = node(hostname=(0, "horus", ""), **{"ls /sys/block": (0, "loop0\nsda", "")})
    rc, checks = verdict(run, capsys=capsys)
    assert rc == 1
    assert not checks["node:hostname"]["ok"] and not checks["hw:nvme"]["ok"]
    assert checks["hw:cpu"]["ok"]


@pytest.mark.parametrize("answer", [
    (1, "Logged out.", ""),
    (0, "# Health check:\n#     - state=NeedsLogin", ""),
    (1, "", "failed to connect to local tailscaled"),
])
def test_tailscale_not_logged_in_fails(answer, capsys):
    rc, checks = verdict(node(**{"tailscale status": answer}), capsys=capsys)
    assert rc == 1
    assert not checks["net:tailscale"]["ok"]


def test_standby_fails_when_a_carrier_unit_is_running(capsys):
    run = node(**{"systemctl is-active cloudflared.service": (0, "active", "")})
    rc, checks = verdict(run, capsys=capsys)
    assert rc == 1
    assert not checks["carrier:cloudflared.service"]["ok"]
    assert checks["carrier:dc-gateway.service"]["ok"]


def test_standby_needs_an_answer_not_silence(capsys):
    run = node(**{"systemctl is-active dc-gateway.service": (255, "", "ssh: timed out")})
    rc, checks = verdict(run, capsys=capsys)
    assert rc == 1
    assert not checks["carrier:dc-gateway.service"]["ok"]


def test_live_needs_both_units_active_and_enabled(capsys):
    rc, _ = verdict(node(), "--carrier", "live", capsys=capsys)
    assert rc == 1
    live = {f"systemctl {verb} {unit}": (0, word, "")
            for unit in vs.CARRIER_UNITS
            for verb, word in (("is-active", "active"), ("is-enabled", "enabled"))}
    assert verdict(node(**live), "--carrier", "live", capsys=capsys)[0] == 0
    assert verdict(node(**live), "--carrier", "standby", capsys=capsys)[0] == 1
    live["systemctl is-enabled dc-gateway.service"] = (0, "disabled", "")
    assert verdict(node(**live), "--carrier", "live", capsys=capsys)[0] == 1


def test_text_output_marks_each_line(capsys):
    assert vs.main([], runner=node(hostname=(0, "horus", ""))) == 1
    lines = capsys.readouterr().out.splitlines()
    assert lines[0] == "FAIL node:hostname: horus (want seneca)"
    assert all(line.startswith("OK   ") for line in lines[1:])


def test_runners_build_one_argv_per_probe(monkeypatch):
    seen = []
    monkeypatch.setattr(vs, "_exec", lambda cmd: seen.append(cmd) or (0, "", ""))
    vs.ssh_runner("horus-lan")(["stat", "-c", "%n", "/etc/a b"])
    vs.fleet_exec_runner("horus")(["systemctl", "is-active", "dc-gateway.service"])
    assert seen[0] == ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=8", "horus-lan", "--",
                       "stat", "-c", "%n", "'/etc/a b'"]
    assert seen[1] == [vs.FLEET_EXEC, "run", "horus", "--",
                       "systemctl", "is-active", "dc-gateway.service"]


def test_exists_against_real_stat(tmp_path):
    present = tmp_path / "excludes"
    present.write_text("path-exclude=/usr/share/man/*\n")
    locked = tmp_path / "locked"
    locked.mkdir()
    (locked / "inside").write_text("")
    os.chmod(locked, 0)
    try:
        assert vs._exists(vs._exec, str(present)) is True
        assert vs._exists(vs._exec, str(tmp_path / "gone")) is False
        if os.geteuid() != 0:
            assert vs._exists(vs._exec, str(locked / "inside")) is None
    finally:
        os.chmod(locked, 0o700)


def test_unminimized_against_real_files(tmp_path):
    marker = tmp_path / "excludes"
    restored = tmp_path / "ls.1.gz"
    restored.write_text("")
    args = {"markers": (str(marker),), "restored": str(restored)}
    assert vs.check_unminimized(vs._exec, **args)[1] is True
    marker.write_text("")
    assert vs.check_unminimized(vs._exec, **args) == (
        "os:unminimized", False, f"still minimized: {marker}")
    marker.unlink()
    restored.unlink()
    assert vs.check_unminimized(vs._exec, **args)[1] is False


def test_exec_reports_a_missing_binary_as_transport_failure():
    rc, out, err = vs._exec(["/nonexistent/wq4934-binary"])
    assert (rc, out) == (vs.SSH_TRANSPORT_RC, "") and err
