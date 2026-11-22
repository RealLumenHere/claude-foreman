#!/usr/bin/env python3
"""wq#4899 — ops/wq858-trivy-weekly-scan is the single source for the LIVE weekly scan.

install.sh --verify must pass on the real box (the bundle's scan.sh + units are the live
files byte for byte) and must FAIL on each way the live scan can drift: a one-byte edit
of the live script or a unit, a bundle edit without re-pinning SHA256SUMS, the dead-man
drop-in gone, a trivy binary without the flags scan.sh uses, or the retired root variant
reappearing.

Fixture cases point WQ4899_SYS / WQ4899_SCAN_DIR / WQ4899_TRIVY / WQ4899_RETIRED_DIR at
temp paths; nothing outside the temp dir is written. The timer checks (enabled, active,
scheduled) always query real systemd, so the module is skipped off-Lumen.

Run: python3 -m pytest ops/tests/test_wq4899_trivy_bundle.py -q
"""
import hashlib
import os
import shutil
import stat
import subprocess

import pytest

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir, os.pardir))
BUNDLE = os.path.join(REPO, "ops", "wq858-trivy-weekly-scan")
PAYLOAD = ["scan.sh", "lumen-trivy-weekly.service", "lumen-trivy-weekly.timer"]
DROPIN_BODY = "[Service]\nExecStartPost=-/home/lumen/bin/lumen-cron-stamp %n\n"
FAKE_TRIVY = "#!/bin/sh\necho 'Flags: --scanners strings --skip-dirs strings --timeout duration'\n"


def _timer_enabled():
    r = subprocess.run(["systemctl", "is-enabled", "lumen-trivy-weekly.timer"],
                       capture_output=True, text=True)
    return r.stdout.strip() == "enabled"


pytestmark = pytest.mark.skipif(not _timer_enabled(),
                                reason="lumen-trivy-weekly.timer not on this host")


def _run(install, env_extra=None, *args):
    env = dict(os.environ)
    env.update(env_extra or {})
    return subprocess.run(["bash", install, *args], capture_output=True, text=True,
                          env=env, timeout=120)


@pytest.fixture
def fx(tmp_path):
    """A copy of the bundle (with ../_verify.sh) and a good set of live targets."""
    ops = tmp_path / "ops"
    shutil.copytree(BUNDLE, ops / "wq858-trivy-weekly-scan")
    shutil.copy(os.path.join(REPO, "ops", "_verify.sh"), ops / "_verify.sh")
    sysd = tmp_path / "sys"
    (sysd / "lumen-trivy-weekly.service.d").mkdir(parents=True)
    for u in PAYLOAD[1:]:
        shutil.copy(os.path.join(BUNDLE, u), sysd / u)
    (sysd / "lumen-trivy-weekly.service.d" / "99-cron-stamp.conf").write_text(DROPIN_BODY)
    scan = tmp_path / "scan"
    scan.mkdir()
    shutil.copy(os.path.join(BUNDLE, "scan.sh"), scan / "scan.sh")
    os.chmod(scan / "scan.sh", 0o755)
    trivy = tmp_path / "trivy"
    trivy.write_text(FAKE_TRIVY)
    os.chmod(trivy, 0o755)
    env = {"WQ4899_SYS": str(sysd), "WQ4899_SCAN_DIR": str(scan),
           "WQ4899_TRIVY": str(trivy), "WQ4899_RETIRED_DIR": str(tmp_path / "trivy-scan")}
    install = str(ops / "wq858-trivy-weekly-scan" / "install.sh")
    return dict(tmp=tmp_path, sys=sysd, scan=scan, trivy=trivy, env=env, install=install,
                bundle=ops / "wq858-trivy-weekly-scan")


def _verify(fx):
    return _run(fx["install"], fx["env"], "--verify")


def _flip_last_byte(path):
    data = bytearray(open(path, "rb").read())
    data[-1] = ord("\n") if data[-1] != ord("\n") else ord(" ")
    open(path, "wb").write(bytes(data))


# --- the bundle itself ---------------------------------------------------------------

def test_sha256sums_pins_exactly_the_committed_payload():
    pins = {}
    for line in open(os.path.join(BUNDLE, "SHA256SUMS")):
        digest, name = line.split()
        pins[name] = digest
    assert sorted(pins) == sorted(PAYLOAD)
    for name in PAYLOAD:
        got = hashlib.sha256(open(os.path.join(BUNDLE, name), "rb").read()).hexdigest()
        assert got == pins[name], name


def test_root_variant_is_retired_from_the_bundle():
    for gone in ["trivy-scan.sh", "trivy-scan.service", "trivy-scan.timer",
                 "trivy_report.py", "test_trivy_report.py"]:
        assert not os.path.exists(os.path.join(BUNDLE, gone)), gone
    unit = open(os.path.join(BUNDLE, "lumen-trivy-weekly.service")).read()
    assert "User=lumen" in unit and "User=root" not in unit
    assert "ExecStart=/home/lumen/services/trivy-weekly/scan.sh" in unit
    assert "lumen-cron-stamp: opt-out" not in unit  # dead-man drop-in must keep applying


def test_scan_sh_is_executable_in_the_bundle():
    assert os.stat(os.path.join(BUNDLE, "scan.sh")).st_mode & stat.S_IXUSR


# --- the real box ----------------------------------------------------------------------

def test_verify_passes_against_the_live_scan():
    r = _run(os.path.join(BUNDLE, "install.sh"), None, "--verify")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "VERIFY OK" in r.stdout


def test_install_half_refuses_without_root():
    if os.geteuid() == 0:
        pytest.skip("running as root")
    r = _run(os.path.join(BUNDLE, "install.sh"))
    assert r.returncode == 2
    assert "refuse" in r.stderr


# --- fixture: good, then each drift must fail -----------------------------------------

def test_fixture_good_state_passes(fx):
    r = _verify(fx)
    assert r.returncode == 0, r.stdout + r.stderr


def test_live_scan_one_byte_edit_fails(fx):
    _flip_last_byte(fx["scan"] / "scan.sh")
    r = _verify(fx)
    assert r.returncode == 1
    assert f"sha256 differs: {fx['scan'] / 'scan.sh'}" in r.stdout


@pytest.mark.parametrize("unit", PAYLOAD[1:])
def test_live_unit_edit_fails(fx, unit):
    _flip_last_byte(fx["sys"] / unit)
    r = _verify(fx)
    assert r.returncode == 1
    assert f"sha256 differs: {fx['sys'] / unit}" in r.stdout


def test_bundle_edit_without_repin_fails(fx):
    _flip_last_byte(fx["bundle"] / "scan.sh")
    r = _verify(fx)
    assert r.returncode == 1
    assert "edited without re-pinning SHA256SUMS" in r.stdout


def test_live_scan_absent_fails(fx):
    os.remove(fx["scan"] / "scan.sh")
    r = _verify(fx)
    assert r.returncode == 1
    assert f"absent: {fx['scan'] / 'scan.sh'}" in r.stdout


def test_live_scan_not_executable_fails(fx):
    os.chmod(fx["scan"] / "scan.sh", 0o644)
    r = _verify(fx)
    assert r.returncode == 1
    assert "not executable" in r.stdout


def test_dead_man_dropin_missing_fails(fx):
    os.remove(fx["sys"] / "lumen-trivy-weekly.service.d" / "99-cron-stamp.conf")
    r = _verify(fx)
    assert r.returncode == 1
    assert "99-cron-stamp.conf" in r.stdout


def test_trivy_without_skip_dirs_fails(fx):
    fx["trivy"].write_text("#!/bin/sh\necho 'Flags: --scanners --timeout'\n")
    r = _verify(fx)
    assert r.returncode == 1
    assert "lacks --skip-dirs" in r.stdout


def test_trivy_absent_fails(fx):
    os.remove(fx["trivy"])
    r = _verify(fx)
    assert r.returncode == 1
    assert "absent/not executable" in r.stdout


@pytest.mark.parametrize("unit", ["trivy-scan.service", "trivy-scan.timer"])
def test_retired_root_unit_reappearing_fails(fx, unit):
    (fx["sys"] / unit).write_text("[Unit]\n")
    r = _verify(fx)
    assert r.returncode == 1
    assert "retired unit present" in r.stdout


def test_retired_root_payload_dir_reappearing_fails(fx):
    (fx["tmp"] / "trivy-scan").mkdir()
    r = _verify(fx)
    assert r.returncode == 1
    assert "retired payload dir present" in r.stdout


def test_missing_pin_is_an_error_not_a_pass(fx):
    (fx["bundle"] / "SHA256SUMS").write_text("")
    r = _verify(fx)
    assert r.returncode == 2
    assert "ERR no pin" in r.stdout
