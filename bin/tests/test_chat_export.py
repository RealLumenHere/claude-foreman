"""chat-export: block parsing, page_token tracking, archive merge/dedup, credential masking.
Synthetic fixtures only."""
import json
import os
import subprocess
import sys

BIN = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "chat-export")
U1 = "11111111-2222-3333-4444-555555555555"
U2 = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
FAKE_CF = "cf" + "at_" + "A1b2C3d4" * 5          # built at runtime: never a literal token in the repo
FAKE_PAT = "github_pat_" + "Q9" * 22

PAGE1 = (
    '<chat url="https://claude.ai/chat/%s" updated_at="2026-09-28T09:34:38+00:00" kind="summary" '
    'page_token="tok-1">Title: First chat\nbody one, key=%s</chat>'
    '<chat url="https://claude.ai/chat/%s" updated_at="2026-09-29T20:00:00+00:00" kind="conversation" '
    'page_token="tok-1">Title: Second chat\nbody two, %s</chat>'
) % (U1, FAKE_CF, U2, FAKE_PAT)

# Page 2: U1 reappears with a newer updated_at (should replace), U2 reappears unchanged.
PAGE2 = (
    '<chat url="https://claude.ai/chat/%s" updated_at="2026-09-30T00:00:00+00:00" kind="summary" '
    'page_token="tok-2">Title: First chat (edited)\nbody one revised</chat>'
    '<chat url="https://claude.ai/chat/%s" updated_at="2026-09-29T20:00:00+00:00" kind="conversation" '
    'page_token="tok-2">Title: Second chat\nbody two, %s</chat>'
) % (U1, U2, FAKE_PAT)


def run(text, archive, *args):
    return subprocess.run([sys.executable, BIN, "--archive", archive, *args],
                           input=text, capture_output=True, text=True, timeout=30)


def test_first_page_all_added_and_masked(tmp_path):
    archive = str(tmp_path / "archive.json")
    r = run(PAGE1, archive)
    assert r.returncode == 0, r.stderr
    report = json.loads(r.stdout)
    assert report == {"added": 2, "updated": 0, "unchanged": 0, "skipped": 0,
                       "last_page_token": "tok-1", "total": 2, "archive": archive, "dry_run": False}
    assert FAKE_CF not in r.stdout and FAKE_PAT not in r.stdout
    data = json.load(open(archive))
    assert FAKE_CF not in json.dumps(data) and "<redacted>" in json.dumps(data)


def test_second_page_updates_newer_and_skips_unchanged(tmp_path):
    archive = str(tmp_path / "archive.json")
    run(PAGE1, archive)
    r = run(PAGE2, archive)
    assert r.returncode == 0, r.stderr
    report = json.loads(r.stdout)
    assert report["added"] == 0
    assert report["updated"] == 1          # U1: 09-30 newer than 09-28
    assert report["unchanged"] == 1        # U2: same updated_at, left alone
    assert report["last_page_token"] == "tok-2"
    assert report["total"] == 2
    data = json.load(open(archive))
    by_id = {c["id"]: c for c in data["chats"]}
    assert by_id[U1]["title"] == "First chat (edited)"
    assert by_id[U1]["updated_at"] == "2026-09-30T00:00:00+00:00"


def test_dry_run_does_not_write(tmp_path):
    archive = str(tmp_path / "archive.json")
    r = run(PAGE1, archive, "--dry-run")
    assert r.returncode == 0, r.stderr
    assert not os.path.exists(archive)


def test_print_dumps_merged_archive(tmp_path):
    archive = str(tmp_path / "archive.json")
    run(PAGE1, archive)
    r = subprocess.run([sys.executable, BIN, "--archive", archive, "--print"],
                        capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, r.stderr
    chats = json.loads(r.stdout)["chats"]
    assert {c["id"] for c in chats} == {U1, U2}


def test_bad_file_path_fails(tmp_path):
    archive = str(tmp_path / "archive.json")
    r = subprocess.run([sys.executable, BIN, "--archive", archive, "--file", "/nonexistent-chat-export-input"],
                        capture_output=True, text=True, timeout=30)
    assert r.returncode != 0


def test_empty_input_exit_2(tmp_path):
    archive = str(tmp_path / "archive.json")
    r = run("no chat blocks here", archive)
    assert r.returncode == 2 and "no <chat> blocks" in r.stderr
