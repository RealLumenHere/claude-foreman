"""Traversal-guard tests for kb_paths (wq#1151 P1)."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from kb_paths import (  # noqa: E402
    PathViolation,
    list_topic_dirs,
    safe_attachment,
    safe_note,
    safe_topic,
)


@pytest.fixture()
def vault(tmp_path):
    (tmp_path / "google-nest").mkdir()
    (tmp_path / "google-nest" / "setup.md").write_text("thermostat pairing\n")
    (tmp_path / ".git").mkdir()
    return tmp_path


def test_happy_paths(vault):
    assert safe_topic(vault, "google-nest") == vault / "google-nest"
    assert safe_note(vault, "google-nest", "setup.md").read_text().startswith("thermo")
    assert safe_attachment(vault, "google-nest", "wiring.jpg") \
        == vault / "google-nest" / "attachments" / "wiring.jpg"
    assert list_topic_dirs(vault) == ["google-nest"]  # .git excluded


@pytest.mark.parametrize("topic", [
    "../etc", "..", "a/../b", "/etc", ".git", ".hidden", "UPPER", "", "a b",
    "a" * 200,
])
def test_bad_topic_rejected(vault, topic):
    with pytest.raises(PathViolation):
        safe_topic(vault, topic)


@pytest.mark.parametrize("note", [
    "../../secret.md", "..md", ".gitignore.md~", "note.txt", "note", "",
    "sub/dir.md",
])
def test_bad_note_rejected(vault, note):
    with pytest.raises(PathViolation):
        safe_note(vault, "google-nest", note)


def test_symlink_escape_rejected(vault, tmp_path_factory):
    outside = tmp_path_factory.mktemp("outside")
    (outside / "loot.md").write_text("secret")
    (vault / "linked").symlink_to(outside)
    with pytest.raises(PathViolation):
        safe_note(vault, "linked", "loot.md")


def test_nonstring_rejected(vault):
    with pytest.raises(PathViolation):
        safe_topic(vault, None)  # type: ignore[arg-type]
