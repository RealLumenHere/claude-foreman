"""wq#2410: the bundle's `install.sh --verify` handler, per ops/VERIFY_CONTRACT.md.

Read-only, exit 0 when the live tool matches, exit 1 with one MISS line per gap.
"""
import shutil
import subprocess
from pathlib import Path

BUNDLE = Path(__file__).resolve().parent
OPS = BUNDLE.parent
LIVE_LINE = "LIVE=/home/lumen/foreman/harness"


def run(script, *args):
    return subprocess.run(["bash", str(script), *args], capture_output=True, text=True)


def stage(tmp_path, live_files):
    """A copy of the bundle whose LIVE points at a scratch harness dir."""
    live = tmp_path / "harness"
    live.mkdir()
    for name, text in live_files.items():
        (live / name).write_text(text)
    bundle = tmp_path / "ops" / "wq2359-rotation-triage"
    bundle.mkdir(parents=True)
    shutil.copy(OPS / "_verify.sh", tmp_path / "ops" / "_verify.sh")
    text = (BUNDLE / "install.sh").read_text().replace(LIVE_LINE, f"LIVE={live}")
    assert str(live) in text
    (bundle / "install.sh").write_text(text)
    return bundle / "install.sh"


def test_handler_precedes_any_install_step():
    text = (BUNDLE / "install.sh").read_text()
    assert text.index('if [ "${1:-}" = --verify ]; then') < text.index("nothing to install")


def test_verify_passes_on_the_live_tree():
    proc = run(BUNDLE / "install.sh", "--verify")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "VERIFY OK" in proc.stdout


def test_verify_fails_with_one_miss_per_gap_when_the_tool_is_absent(tmp_path):
    script = stage(tmp_path, {})
    proc = run(script, "--verify")
    assert proc.returncode == 1
    assert proc.stdout.count("MISS ") == 5
    assert "VERIFY FAIL" in proc.stdout


def test_verify_fails_when_the_tool_lost_the_history_flag(tmp_path):
    live_src = Path("/home/lumen/foreman/harness")
    files = {"mask_replay.py": (live_src / "mask_replay.py").read_text(),
             "mask_triage.py": (live_src / "mask_triage.py").read_text().replace(
                 "--no-history", "--no-hist")}
    proc = run(stage(tmp_path, files), "--verify")
    assert proc.returncode == 1
    assert "--no-history" in proc.stdout


def test_verify_runs_python_without_writing_bytecode():
    verify_arm = (BUNDLE / "install.sh").read_text().split("nothing to install")[0]
    assert "PYTHONDONTWRITEBYTECODE=1 python3" in verify_arm
