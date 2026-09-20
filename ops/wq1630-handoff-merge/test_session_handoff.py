"""wq#1630: session-handoff renders a generated region into HANDOFF.md and nowhere else."""
import importlib.machinery
import importlib.util
import os
import sqlite3
import stat
import subprocess
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(HERE, "session-handoff")

HAND_PROSE = "# LUMEN — SESSION HANDOFF\n\n## 4. VERBATIM CONSTRAINTS\n- Vault failback is DONE.\n"


def load():
    loader = importlib.machinery.SourceFileLoader("session_handoff", SCRIPT)
    spec = importlib.util.spec_from_loader("session_handoff", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


@pytest.fixture
def sh(tmp_path, monkeypatch):
    mod = load()
    db = tmp_path / "queue.db"
    c = sqlite3.connect(db)
    c.execute(
        "CREATE TABLE tasks (id INTEGER PRIMARY KEY, lane TEXT, title TEXT, status TEXT, "
        "claimed_by TEXT, claimed_at TEXT, done_at TEXT, notes TEXT)"
    )
    c.execute(
        "INSERT INTO tasks (id, lane, title, status, claimed_by, claimed_at) VALUES "
        "(1630, 'foreman', 'merge handoffs', 'claimed', 'build-w1', '2026-09-20 10:00:00')"
    )
    c.execute(
        "INSERT INTO tasks (id, lane, title, status, done_at, notes) VALUES "
        "(1234, 'foreman', 'older', 'done', '2026-09-19 10:00:00', 'PR https://git.example/o/r/pulls/77 landed')"
    )
    c.commit()
    c.close()
    monkeypatch.setattr(mod, "WQ_DB", str(db))
    monkeypatch.setattr(mod, "LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.setattr(mod, "NOTES_PATH", str(tmp_path / "notes.log"))
    mod.handoff = tmp_path / "HANDOFF.md"
    return mod


def test_render_prepends_region_and_keeps_hand_prose(sh):
    sh.handoff.write_text(HAND_PROSE)
    sh.render(str(sh.handoff))
    out = sh.handoff.read_text()
    assert out.startswith(sh.BEGIN_MARK)
    assert out.endswith(HAND_PROSE)
    assert "- wq#1630 [foreman] build-w1" in out
    assert "wq#1234" in out and "pulls/77" in out
    assert "- wq-health: last run never ran (no stamp)" in out


def test_render_is_idempotent_and_replaces_only_the_region(sh):
    sh.handoff.write_text(HAND_PROSE)
    sh.render(str(sh.handoff))
    first = sh.handoff.read_text()
    c = sqlite3.connect(sh.WQ_DB)
    c.execute("UPDATE tasks SET status='done', done_at='2026-09-20 11:00:00', notes='x' WHERE id=1630")
    c.commit()
    c.close()
    sh.render(str(sh.handoff))
    second = sh.handoff.read_text()
    assert second.count(sh.BEGIN_MARK) == 1 and second.count(sh.END_MARK) == 1
    assert "(none claimed)" in second and "(none claimed)" not in first
    assert second.endswith(HAND_PROSE)


def test_hand_edit_below_region_survives_rerender(sh):
    sh.handoff.write_text(HAND_PROSE)
    sh.render(str(sh.handoff))
    sh.handoff.write_text(sh.handoff.read_text() + "- new hand constraint\n")
    sh.render(str(sh.handoff))
    assert sh.handoff.read_text().endswith(HAND_PROSE + "- new hand constraint\n")


def test_missing_file_is_created(sh):
    sh.render(str(sh.handoff))
    out = sh.handoff.read_text()
    assert out.startswith(sh.BEGIN_MARK) and out.rstrip().endswith(sh.END_MARK)


def test_unpaired_markers_refuse_and_leave_file_untouched(sh):
    broken = sh.BEGIN_MARK + "\nhand prose that must not be eaten\n"
    sh.handoff.write_text(broken)
    with pytest.raises(sh.RegionError):
        sh.render(str(sh.handoff))
    assert sh.handoff.read_text() == broken
    only_end = "prose\n" + sh.END_MARK + "\n"
    sh.handoff.write_text(only_end)
    with pytest.raises(sh.RegionError):
        sh.render(str(sh.handoff))
    assert sh.handoff.read_text() == only_end


def test_file_mode_preserved_and_no_temp_files_left(sh):
    sh.handoff.write_text(HAND_PROSE)
    os.chmod(sh.handoff, 0o640)
    sh.render(str(sh.handoff))
    assert stat.S_IMODE(os.stat(sh.handoff).st_mode) == 0o640
    assert [p.name for p in sh.handoff.parent.iterdir() if p.name.startswith(".handoff.")] == []


def test_notes_are_shown_capped(sh):
    for i in range(8):
        sh.append_note(f"note-{i}")
    sh.handoff.write_text(HAND_PROSE)
    sh.render(str(sh.handoff))
    out = sh.handoff.read_text()
    assert "note-7" in out and "note-3" in out and "note-2" not in out


def test_no_kb_handoff_file_or_git_commit_in_script():
    src = open(SCRIPT).read()
    assert "SESSION-HANDOFF.md" not in src
    assert "git commit" not in src and "subprocess" not in src


def test_cli_render_failure_exits_nonzero_and_keeps_file(tmp_path):
    target = tmp_path / "HANDOFF.md"
    target.write_text(HAND_PROSE)
    env = dict(os.environ)
    r = subprocess.run(
        [sys.executable, "-c",
         "import importlib.machinery as m,sys;"
         f"mod=m.SourceFileLoader('s','{SCRIPT}').load_module();"
         f"mod.WQ_DB='{tmp_path}/nope.db';sys.argv=['s','--out','{target}'];mod.main()"],
        capture_output=True, text=True, env=env,
    )
    assert r.returncode == 1 and "render failed" in r.stderr
    assert target.read_text() == HAND_PROSE
