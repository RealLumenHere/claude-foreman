import os

import handoff_s3_rewrite as hsr

HERE = os.path.dirname(os.path.abspath(__file__))
FIXTURE = os.path.join(HERE, "fixtures", "ORCHESTRATOR-HANDOFF-synthetic.md")


def load_fixture():
    with open(FIXTURE, encoding="utf-8") as f:
        return f.read()


def test_rewrite_replaces_s3_body_and_preserves_other_sections():
    text = load_fixture()
    new_text, archived = hsr.rewrite(text)
    assert "July-era and mostly done" not in new_text
    assert "## 3. Session notes" in new_text
    assert "## 2. Containers (3 total)" in new_text and "alpha, beta, gamma" in new_text
    assert "## 4. Rules" in new_text and "Do NOT add Anthropic memory slots" in new_text
    for line in hsr.REPLACEMENT_LINES:
        assert line in new_text


def test_archived_body_carries_the_deleted_session_prose():
    text = load_fixture()
    _, archived = hsr.rewrite(text)
    assert "July-era and mostly done" in archived
    assert "wq#383" in archived


def test_rewrite_is_idempotent_second_pass_raises_anchor_error():
    text = load_fixture()
    new_text, _ = hsr.rewrite(text)
    try:
        hsr.rewrite(new_text)
        assert False, "second rewrite should not find the marker again"
    except hsr.AnchorError:
        pass


def test_ambiguous_marker_raises_instead_of_guessing():
    text = load_fixture() + "\n## 5. Extra\n\nJuly-era and mostly done, again.\n"
    try:
        hsr.rewrite(text)
        assert False, "duplicate marker must raise, not pick one silently"
    except hsr.AnchorError:
        pass


def test_cli_check_mode_writes_nothing(tmp_path):
    import subprocess
    import sys

    p = tmp_path / "handoff.md"
    p.write_text(load_fixture())
    r = subprocess.run(
        [sys.executable, os.path.join(HERE, "handoff_s3_rewrite.py"),
         "--file", str(p), "--archive-dir", str(tmp_path / "sessions"), "--check"],
        capture_output=True, text=True,
    )
    assert r.returncode == 0
    assert p.read_text() == load_fixture()
    assert not (tmp_path / "sessions").exists()


def test_cli_apply_mode_archives_and_rewrites(tmp_path):
    import subprocess
    import sys

    p = tmp_path / "handoff.md"
    p.write_text(load_fixture())
    archive_dir = tmp_path / "sessions"
    r = subprocess.run(
        [sys.executable, os.path.join(HERE, "handoff_s3_rewrite.py"),
         "--file", str(p), "--archive-dir", str(archive_dir)],
        capture_output=True, text=True,
    )
    assert r.returncode == 0
    assert "July-era and mostly done" not in p.read_text()
    archived_files = list(archive_dir.glob("handoff_s3_archive_*.md"))
    assert len(archived_files) == 1
    assert "wq#383" in archived_files[0].read_text()
