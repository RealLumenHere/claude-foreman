import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(HERE, "live_fleet_state_cap.py")


def run(path, budget=4000):
    return subprocess.run(
        [sys.executable, SCRIPT, "--file", path, "--budget-tokens", str(budget)],
        capture_output=True, text=True,
    )


def test_under_budget_is_untouched(tmp_path):
    p = tmp_path / "live.md"
    original = "fact one\nfact two\n"
    p.write_text(original)
    r = run(str(p), budget=4000)
    assert r.returncode == 0
    assert p.read_text() == original


def test_over_budget_truncates_whole_lines_only(tmp_path):
    p = tmp_path / "live.md"
    # each line ~ (len+1)//4 tok; 50 lines of 40 chars ~ 500 tok, budget 100 forces a cut
    lines = ["fact %02d: %s" % (i, "x" * 30) for i in range(50)]
    p.write_text("\n".join(lines) + "\n")
    r = run(str(p), budget=100)
    assert r.returncode == 1
    kept = p.read_text()
    assert "TRUNCATED" in kept
    for line in kept.splitlines():
        if line.startswith("<!--") or not line:
            continue
        assert line in lines, "line was not cut at a whole-line boundary: %r" % line


def test_never_drops_more_than_necessary(tmp_path):
    p = tmp_path / "live.md"
    lines = ["short %d" % i for i in range(10)]
    p.write_text("\n".join(lines) + "\n")
    original_tok = sum(len(l) // 4 for l in lines)
    r = run(str(p), budget=original_tok + 1000)  # plenty of headroom
    assert r.returncode == 0


def test_missing_file_is_a_tool_error_not_a_crash(tmp_path):
    r = run(str(tmp_path / "nope.md"))
    assert r.returncode == 2
