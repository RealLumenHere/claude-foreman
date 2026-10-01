"""chat-export: block parsing, page_token tracking, archive merge/dedup, credential masking.
Synthetic fixtures only."""
import importlib.machinery
import importlib.util
import json
import os
import subprocess
import sys

BIN = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "chat-export")
U1 = "11111111-2222-3333-4444-555555555555"
U2 = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
FAKE_CF = "cf" + "at_" + "A1b2C3d4" * 5          # built at runtime: never a literal token in the repo
FAKE_PAT = "github_pat_" + "Q9" * 22
# 48 chars, letters+digits: matches chat-export's trailing long-token rule, so it is exactly
# what a blanket "mask every field" would corrupt. Must round-trip verbatim (OPAQUE_FIELDS).
PAGE_CURSOR = "Pg7x" * 12

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


def load_module():
    """Import chat-export (extensionless) for the unit-level masking checks."""
    spec = importlib.util.spec_from_loader("chat_export", importlib.machinery.SourceFileLoader("chat_export", BIN))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


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


# --- wq#3355: masking is per-field and on by default ------------------------------------
# wq#3343 found a credential in a chat's `Title:` line reaching archive.json verbatim while
# the same text in the body was redacted. These assert the fields, not just the body.

TITLED = ('<chat url="https://claude.ai/chat/%s?s=%s" updated_at="2026-09-28T09:34:38+00:00" '
          'kind="summary" page_token="%s">Title: leaked %s here\nbody is clean</chat>'
          ) % (U1, FAKE_CF, PAGE_CURSOR, FAKE_PAT)


def test_credential_in_title_is_redacted(tmp_path):
    archive = str(tmp_path / "archive.json")
    r = run(TITLED, archive)
    assert r.returncode == 0, r.stderr
    chat = json.load(open(archive))["chats"][0]
    assert FAKE_PAT not in chat["title"] and "<redacted>" in chat["title"]
    assert FAKE_PAT not in json.dumps(chat)


def test_credential_in_url_is_redacted_without_losing_the_merge_key(tmp_path):
    archive = str(tmp_path / "archive.json")
    r = run(TITLED, archive)
    assert r.returncode == 0, r.stderr
    chat = json.load(open(archive))["chats"][0]
    assert FAKE_CF not in chat["url"] and "<redacted>" in chat["url"]
    assert chat["id"] == U1          # id is read off the raw url, before masking


def test_page_token_round_trips_verbatim(tmp_path):
    """The exemption, probed from outside the set of fields the fix masks: a blanket mask
    would redact this cursor and stall the paginated recent_chats walk."""
    archive = str(tmp_path / "archive.json")
    r = run(TITLED, archive)
    assert r.returncode == 0, r.stderr
    assert json.loads(r.stdout)["last_page_token"] == PAGE_CURSOR
    assert json.load(open(archive))["chats"][0]["page_token"] == PAGE_CURSOR


def test_mask_block_redacts_a_field_nobody_opted_in(tmp_path):
    """The by-omission property: a field added later is masked without being listed."""
    mod = load_module()
    out = mod.mask_block({"body": "clean", "future_field": "see " + FAKE_PAT, "page_token": PAGE_CURSOR})
    assert FAKE_PAT not in out["future_field"] and "<redacted>" in out["future_field"]
    assert out["page_token"] == PAGE_CURSOR


def test_print_and_save_heal_an_archive_written_unmasked(tmp_path):
    """A legacy archive (title stored in plaintext by the pre-fix build) is masked on the
    next write and on --print, not left as-is on either box the pull script syncs."""
    archive = tmp_path / "archive.json"
    archive.write_text(json.dumps({"chats": [
        {"id": U1, "url": "https://claude.ai/chat/" + U1, "updated_at": "2026-09-01T00:00:00+00:00",
         "kind": "summary", "page_token": PAGE_CURSOR, "title": "leaked " + FAKE_PAT, "body": "clean"}]}))
    r = subprocess.run([sys.executable, BIN, "--archive", str(archive), "--print"],
                        capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, r.stderr
    assert FAKE_PAT not in r.stdout and "<redacted>" in r.stdout
    r = run(('<chat url="https://claude.ai/chat/%s" updated_at="2026-09-02T00:00:00+00:00" '
             'kind="conversation" page_token="">Title: other\nbody</chat>') % U2, str(archive))
    assert r.returncode == 0, r.stderr
    assert FAKE_PAT not in archive.read_text()
