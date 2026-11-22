"""pytest wrapper for the offline shell test of fix-frank-gh-keys.sh (wq#5244)."""
import pathlib
import subprocess

HERE = pathlib.Path(__file__).resolve().parent


def test_fix_frank_gh_keys_offline():
    r = subprocess.run(
        ["bash", str(HERE / "test_fix_frank_gh_keys.sh")],
        capture_output=True, text=True, timeout=60,
    )
    print(r.stdout, r.stderr)
    assert r.returncode == 0, r.stdout + r.stderr
    assert r.stdout.count("PASS ") == 6, r.stdout
    assert "FAIL " not in r.stdout
