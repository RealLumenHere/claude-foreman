"""wq#3386: the bundle's `install.sh --verify` handler, per ops/VERIFY_CONTRACT.md.

Read-only, exit 0 when rizin and cutter are both installed and answer their
version command, exit 1 with one MISS line per gap otherwise.
"""
import os
import stat
import subprocess
from pathlib import Path

BUNDLE = Path(__file__).resolve().parent


BASH = subprocess.run(["which", "bash"], capture_output=True, text=True).stdout.strip()


def run(*args, path=None):
    env = dict(os.environ)
    if path is not None:
        env["PATH"] = path
    return subprocess.run(
        [BASH, str(BUNDLE / "install.sh"), *args],
        capture_output=True, text=True, env=env,
    )


def fake_bin(tmp_path, name, body):
    d = tmp_path / "fakebin"
    d.mkdir(exist_ok=True)
    f = d / name
    f.write_text(f"#!/bin/sh\n{body}\n")
    f.chmod(f.stat().st_mode | stat.S_IEXEC)
    return d


def test_handler_precedes_any_install_step():
    text = (BUNDLE / "install.sh").read_text()
    assert text.index('if [ "${1:-}" = --verify ]; then') < text.index("install (on Lumen, as root)")


def test_verify_reports_one_miss_per_absent_tool(tmp_path):
    """Neither package is installed on Lumen yet (ops/wq2095-re-bench-tools lists
    radare2/cutter on maksoor, not Lumen, and rizin nowhere) -- the real $PATH."""
    proc = run("--verify")
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert proc.stdout.count("MISS ") == 2
    assert "absent: rizin" in proc.stdout
    assert "absent: cutter" in proc.stdout
    assert "VERIFY FAIL" in proc.stdout


def test_verify_passes_when_both_tools_answer_their_version_flag(tmp_path):
    d = fake_bin(tmp_path, "rizin", 'echo "rizin 0.8.2"')
    (d / "cutter").write_text("#!/bin/sh\necho \"Cutter v2.3.4\"\n")
    (d / "cutter").chmod((d / "cutter").stat().st_mode | stat.S_IEXEC)
    proc = run("--verify", path=f"{d}:/usr/bin:/bin")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "VERIFY OK" in proc.stdout


def test_verify_reports_a_miss_when_one_tool_is_present_but_its_version_flag_fails(tmp_path):
    d = fake_bin(tmp_path, "rizin", 'echo "rizin 0.8.2"')
    (d / "cutter").write_text("#!/bin/sh\nexit 1\n")
    (d / "cutter").chmod((d / "cutter").stat().st_mode | stat.S_IEXEC)
    proc = run("--verify", path=f"{d}:/usr/bin:/bin")
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "cutter --version failed" in proc.stdout
