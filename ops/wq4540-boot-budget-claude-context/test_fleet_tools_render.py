"""fleet-tools-render --boot (wq#4540). Subprocess-against-fixture, same pattern as
bin/tests/test_instincts_render.py. RENDER_BIN overridable so a pre-landing gate can
test this staged copy before it replaces /home/lumen/bin/fleet-tools-render."""
import os
import subprocess
import sys

RENDER_BIN = os.environ.get(
    "FLEET_TOOLS_RENDER_BIN",
    os.path.join(os.path.dirname(__file__), "fleet-tools-render"),
)
FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures", "fleet_tools.yaml")


def run(tmp_path, *args):
    out = tmp_path / "out.md"
    r = subprocess.run(
        [sys.executable, RENDER_BIN, "--src", FIXTURE, "--out", str(out)] + list(args),
        capture_output=True, text=True,
    )
    return r, (out.read_text() if out.exists() else "")


def test_full_render_has_note_and_group_desc_and_two_lines_per_tool(tmp_path):
    r, text = run(tmp_path)
    assert r.returncode == 2  # fixture has a deliberate drift tool
    assert "Query state, never trust dated prose." in text
    assert "_Fleet facts + episodic memory._" in text
    assert "- **lumen_state.py** — Query live roster facts." in text
    assert "  - `python3 lumen_state.py current`" in text


def test_boot_render_drops_note_and_group_desc_one_line_per_tool(tmp_path):
    r, text = run(tmp_path, "--boot")
    assert r.returncode == 2
    assert "Query state, never trust dated prose." not in text
    assert "Fleet facts + episodic memory." not in text
    assert "- **lumen_state.py** — Query live roster facts. — `python3 lumen_state.py current`" in text


def test_boot_render_flags_drift_tool(tmp_path):
    r, text = run(tmp_path, "--boot")
    assert "missing-tool" in text
    assert "DRIFT" in text


def test_boot_render_is_smaller_than_full_render(tmp_path):
    _, full_text = run(tmp_path)
    _, boot_text = run(tmp_path, "--boot")
    assert len(boot_text) < len(full_text)
