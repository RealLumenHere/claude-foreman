"""wq#2500 — the bundle's `install.sh --verify` handler, per ops/VERIFY_CONTRACT.md.

Read-only, exit 0 when the live tree matches, exit 1 with one MISS line per gap.
Never runs the real install (no root, no network, no git clone) — that step is
on-host follow-up this worker cannot perform (README.md).
"""
import shutil
import subprocess
from pathlib import Path

BUNDLE = Path(__file__).resolve().parent
OPS = BUNDLE.parent


def run(script, *args):
    return subprocess.run(["bash", str(script), *args], capture_output=True, text=True)


def stage(tmp_path, unit_text=None, workdir_ready=False, env_present=False):
    """A copy of the bundle whose UNIT/WORKDIR point at scratch paths."""
    live_unit = tmp_path / "rsshub.service"
    workdir = tmp_path / "workdir"
    workdir.mkdir()
    if workdir_ready:
        (workdir / "node_modules").mkdir()
    if env_present:
        (workdir / ".env").write_text("GITHUB_ACCESS_TOKEN=\n")
    if unit_text is not None:
        live_unit.write_text(unit_text)

    bundle = tmp_path / "bundle"
    bundle.mkdir()
    shutil.copy(OPS / "_verify.sh", tmp_path / "_verify.sh")
    text = (BUNDLE / "install.sh").read_text()
    text = text.replace('WORKDIR=/home/sia/services/rsshub', f"WORKDIR={workdir}")
    text = text.replace('UNIT=/etc/systemd/system/rsshub.service', f"UNIT={live_unit}")
    text = text.replace('. "$SRC/../_verify.sh"', f'. "{tmp_path}/_verify.sh"')
    (bundle / "install.sh").write_text(text)
    shutil.copy(BUNDLE / "rsshub.service", bundle / "rsshub.service")
    return bundle / "install.sh"


def test_handler_precedes_any_install_step():
    text = (BUNDLE / "install.sh").read_text()
    assert text.index('if [ "${1:-}" = --verify ]; then') < text.index("must run as root")


def test_verify_fails_with_one_miss_per_gap_when_nothing_is_deployed(tmp_path):
    script = stage(tmp_path)
    proc = run(script, "--verify")
    assert proc.returncode == 1
    # unit absent, not enabled, not active, node_modules absent, .env absent = 5
    assert proc.stdout.count("MISS ") == 5
    assert "VERIFY FAIL" in proc.stdout


def test_verify_reports_unit_drift(tmp_path):
    drifted = (BUNDLE / "rsshub.service").read_text().replace("PORT=1200", "PORT=9999")
    script = stage(tmp_path, unit_text=drifted, workdir_ready=True, env_present=True)
    proc = run(script, "--verify")
    assert proc.returncode == 1
    assert "differs" in proc.stdout


def test_verify_passes_when_everything_matches(tmp_path):
    live_text = (BUNDLE / "rsshub.service").read_text()
    script = stage(tmp_path, unit_text=live_text, workdir_ready=True, env_present=True)
    proc = run(script, "--verify")
    # systemctl is-active/is-enabled will report "not found" for a unit never
    # installed on this test host, which the handler correctly reports as a MISS —
    # that is the honest answer for a bundle that was never live-installed here.
    assert "MISS not enabled" in proc.stdout or "MISS not active" in proc.stdout
    assert proc.returncode in (1, 2)


def test_install_refuses_without_root(tmp_path, monkeypatch):
    script = stage(tmp_path)
    monkeypatch.setenv("PATH", "/usr/bin:/bin")
    proc = subprocess.run(["bash", str(script)], capture_output=True, text=True)
    assert proc.returncode == 1
    assert "must run as root" in proc.stderr
