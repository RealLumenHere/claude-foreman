"""obsidian_bridge.py unit tests (wq#2442) — note rendering, write/read
round-trip, log append, search. Zero quota, no fleet paths, no network."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import obsidian_bridge  # noqa: E402

SAMPLE_DIGEST = {
    "summary": "wq#42 attempt 1 SUCCESS: fix the thing",
    "changed": ["https://example/pulls/7"],
    "failed": ["attempt 0: TIMEOUT (killed)"],
    "why": "root cause was a stale cache",
    "kind": "code-ship",
    "tags": ["fleet", "fworker", "wq42"],
    "ref": "/home/lumen/foreman/_runs/wq42/episode.jsonl",
}


def test_note_path_and_log_path_share_vault_root(tmp_path):
    assert obsidian_bridge.note_path(42, str(tmp_path)) == str(
        tmp_path / "Fleet Memory" / "wq42.md")
    assert obsidian_bridge.log_path(str(tmp_path)) == str(
        tmp_path / "Fleet Memory" / "_Log.md")


def test_write_note_creates_file_with_frontmatter_and_body(tmp_path):
    path = obsidian_bridge.write_note(42, SAMPLE_DIGEST, vault_root=str(tmp_path))
    assert os.path.isfile(path)
    text = open(path).read()
    assert text.startswith("---\n")
    assert "ticket: 42" in text
    assert 'tags: ["fleet", "fworker", "wq42"]' in text
    assert "# wq#42" in text
    assert "## Changed" in text and "https://example/pulls/7" in text
    assert "## Failed attempts" in text and "TIMEOUT" in text
    assert "## Why" in text and "stale cache" in text


def test_write_note_appends_one_log_line_per_call(tmp_path):
    obsidian_bridge.write_note(42, SAMPLE_DIGEST, vault_root=str(tmp_path))
    obsidian_bridge.write_note(42, SAMPLE_DIGEST, vault_root=str(tmp_path))
    lines = open(obsidian_bridge.log_path(str(tmp_path))).read().splitlines()
    assert len(lines) == 2
    assert "[[wq42]]" in lines[0]


def test_write_note_overwrites_the_ticket_note_not_the_log(tmp_path):
    obsidian_bridge.write_note(42, SAMPLE_DIGEST, vault_root=str(tmp_path))
    second = dict(SAMPLE_DIGEST, summary="wq#42 attempt 2 SUCCESS: follow-up")
    obsidian_bridge.write_note(42, second, vault_root=str(tmp_path))
    text = open(obsidian_bridge.note_path(42, str(tmp_path))).read()
    assert "attempt 1" not in text
    assert "attempt 2" in text
    assert len(open(obsidian_bridge.log_path(str(tmp_path))).read().splitlines()) == 2


def test_read_note_missing_returns_none(tmp_path):
    assert obsidian_bridge.read_note(999, vault_root=str(tmp_path)) is None


def test_read_note_round_trips_write_note(tmp_path):
    obsidian_bridge.write_note(42, SAMPLE_DIGEST, vault_root=str(tmp_path))
    assert "stale cache" in obsidian_bridge.read_note(42, vault_root=str(tmp_path))


def test_search_notes_finds_substring_case_insensitively(tmp_path):
    obsidian_bridge.write_note(42, SAMPLE_DIGEST, vault_root=str(tmp_path))
    hits = obsidian_bridge.search_notes("STALE CACHE", vault_root=str(tmp_path))
    assert len(hits) == 1
    path, lineno, line = hits[0]
    assert path == obsidian_bridge.note_path(42, str(tmp_path))
    assert "stale cache" in line


def test_search_notes_no_match_is_empty(tmp_path):
    obsidian_bridge.write_note(42, SAMPLE_DIGEST, vault_root=str(tmp_path))
    assert obsidian_bridge.search_notes("nothing matches this", vault_root=str(tmp_path)) == []


def test_search_notes_respects_limit(tmp_path):
    many = dict(SAMPLE_DIGEST, why=" ".join("needle" for _ in range(30)))
    obsidian_bridge.write_note(42, many, vault_root=str(tmp_path))
    hits = obsidian_bridge.search_notes("needle", vault_root=str(tmp_path), limit=1)
    assert len(hits) == 1


def test_yaml_str_strips_quotes_and_newlines():
    rendered = obsidian_bridge._yaml_str('say "hi"\nnext')
    assert '"' not in rendered[1:-1]
    assert "\n" not in rendered
    assert rendered.startswith('"') and rendered.endswith('"')
