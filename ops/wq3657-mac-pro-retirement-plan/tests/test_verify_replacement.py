import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPT = HERE.parent / "verify_replacement.py"
FIX = HERE / "fixtures"


def run(candidate):
    return subprocess.run(
        [sys.executable, str(SCRIPT),
         "--baseline", str(FIX / "baseline-macpro.jsonl"),
         "--candidate", str(FIX / candidate),
         "--row", "qwen36-local"],
        capture_output=True, text=True,
    )


def test_pass_when_faster_and_lower_watts():
    r = run("candidate-pass.jsonl")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "VERDICT: PASS" in r.stdout


def test_fail_when_watts_not_improved():
    """Doctrine wq#2630: this probe must be able to fail. A candidate that is faster but
    still draws Mac-Pro-class power fails the watts check, because watts is the point
    of wq#3657's consolidation, not a secondary nicety."""
    r = run("candidate-fail-watts.jsonl")
    assert r.returncode == 1, r.stdout + r.stderr
    assert "VERDICT: FAIL" in r.stdout
    assert "watts:  FAIL" in r.stdout
    assert "decode: PASS" in r.stdout
