"""wq#4523: the P0 "no working installer" gate (verify_installer.py +
build.sh stage11b).

The validation behind wq#4523 grepped the limpet-20261021.iso casper layers
for ubiquity and found nothing -- but ubiquity is absent from the STOCK
Xubuntu 26.04 ISO too (research/WQ3983_XUBUNTU_26_10_DISSECTION.md section 1:
zero hits across both the 26.04.1 and 26.10 manifests). The real installer is
the ubuntu-desktop-bootstrap snap seeded in the live layer, so that seed is
what the gate checks, on the finished ISO artifact.

Falsifiable both ways (wq#2630): the gate passes on the stock-shaped fixture
ISO AND fails on each broken variant -- no seed snap (the wq#4523 ghost-icon
symptom), seed.yaml seeding a different snap, the live layer deleted, and
install-sources.yaml deleted. The broken variants are derived from the same
committed fixtures, not from live mutable files.
"""
import json
import subprocess
from pathlib import Path

import pytest

from test_layered_remaster import SEED_FILES, _make_base_iso

HERE = Path(__file__).resolve().parent
BUILD = HERE / "build.sh"
VERIFY = HERE / "verify_installer.py"


def _require_tools():
    import shutil
    for tool in ("mksquashfs", "unsquashfs", "xorriso"):
        if not shutil.which(tool):
            pytest.skip(f"{tool} not installed")


def _verify(iso):
    return subprocess.run(
        ["python3", "-I", str(VERIFY), "--iso", str(iso)],
        capture_output=True, text=True,
    )


def _iso_rm(iso, dest, path):
    """A copy of `iso` with `path` deleted -- same boot-record-free synthetic
    layout _make_base_iso produces."""
    subprocess.run(
        ["xorriso", "-indev", str(iso), "-outdev", str(dest), "-rm", path, "--", "-commit"],
        check=True, capture_output=True,
    )
    return dest


def test_gate_passes_on_stock_installer_layout(tmp_path):
    _require_tools()
    r = _verify(_make_base_iso(tmp_path))
    assert r.returncode == 0, r.stdout + r.stderr
    report = json.loads(r.stdout)
    assert report["ok"] is True
    assert report["live_layer"] == "minimal.standard.live"
    assert report["seed_snap_files"] == [
        "var/lib/snapd/seed/snaps/ubuntu-desktop-bootstrap_675.snap"]


def test_gate_fails_without_seeded_installer_snap(tmp_path):
    """The wq#4523 symptom: live layer exists, casper draws the Install icon,
    but there is nothing behind it."""
    _require_tools()
    r = _verify(_make_base_iso(tmp_path, live_extra={}))
    assert r.returncode != 0
    report = json.loads(r.stdout)
    assert report["ok"] is False
    assert any("seed" in e for e in report["errors"])


def test_gate_fails_when_seed_yaml_seeds_a_different_snap(tmp_path):
    _require_tools()
    live = dict(SEED_FILES)
    live["var/lib/snapd/seed/seed.yaml"] = (
        "snaps:\n- name: firmware-updater\n  file: firmware-updater_1.snap\n")
    r = _verify(_make_base_iso(tmp_path, live_extra=live))
    assert r.returncode != 0
    report = json.loads(r.stdout)
    assert any("does not seed 'ubuntu-desktop-bootstrap'" in e for e in report["errors"])


def test_gate_fails_without_live_layer(tmp_path):
    _require_tools()
    iso = _make_base_iso(tmp_path)
    broken = _iso_rm(iso, tmp_path / "no-live.iso", "/casper/minimal.standard.live.squashfs")
    r = _verify(broken)
    assert r.returncode != 0
    report = json.loads(r.stdout)
    assert any("minimal.standard.live.squashfs missing" in e for e in report["errors"])


def test_gate_fails_without_install_sources(tmp_path):
    _require_tools()
    iso = _make_base_iso(tmp_path)
    broken = _iso_rm(iso, tmp_path / "no-sources.iso", "/casper/install-sources.yaml")
    r = _verify(broken)
    assert r.returncode != 0
    report = json.loads(r.stdout)
    assert any("install-sources.yaml missing" in e for e in report["errors"])


def test_stage11b_runs_the_gate_from_build_sh(tmp_path):
    """The stage is wired: build.sh stage11b passes on the good ISO and fails
    the build on the seedless one, via the same entrypoint `all` runs."""
    _require_tools()
    stages = BUILD.read_text()
    order = [s for s in ("stage11_build_hybrid_iso", "stage11b_verify_installer",
                         "stage12_qemu_test")
             if s in stages.split("STAGES=(")[1].split(")")[0]]
    assert order == ["stage11_build_hybrid_iso", "stage11b_verify_installer",
                     "stage12_qemu_test"]

    good = _make_base_iso(tmp_path / "good")
    bad = _make_base_iso(tmp_path / "bad", live_extra={})
    for iso, want_rc_zero in ((good, True), (bad, False)):
        out = iso.parent / "out"
        out.mkdir(exist_ok=True)
        (out / "limpet-test.iso").write_bytes(iso.read_bytes())
        r = subprocess.run(
            ["bash", str(BUILD), "stage11b_verify_installer"],
            env={"PATH": "/usr/bin:/bin:/usr/sbin:/sbin",
                 "LIMPET_ISO_OUT_DIR": str(out),
                 "LIMPET_ISO_NAME": "limpet-test.iso",
                 "LIMPET_ISO_WORKDIR": str(iso.parent / "work")},
            capture_output=True, text=True,
        )
        assert (r.returncode == 0) is want_rc_zero, r.stdout + r.stderr
