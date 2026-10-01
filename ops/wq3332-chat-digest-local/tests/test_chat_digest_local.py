"""chat-digest-local: archive loading, hostname->model default, prompt masking, dry-run path.
No network: --dry-run exercises everything except the actual Ollama call (that needs a live
node and is out of reach from a test suite — see README.md "Verifying the staged kit")."""
import json
import os
import subprocess
import sys

BIN = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "chat-digest-local")
FAKE_PAT = "github_pat_" + "Q9" * 22  # built at runtime: never a literal token in the repo

ARCHIVE = {
    "chats": [
        {"id": "11111111-2222-3333-4444-555555555555", "title": "First chat",
         "url": "https://claude.ai/chat/1111", "body": "decided to ship it, leaked %s" % FAKE_PAT},
        {"id": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee", "title": "", "body": "second chat body"},
    ]
}


def write_archive(tmp_path, data=ARCHIVE):
    p = tmp_path / "archive.json"
    p.write_text(json.dumps(data))
    return str(p)


def run(archive, *args):
    return subprocess.run([sys.executable, BIN, "--archive", archive, "--dry-run", *args],
                           capture_output=True, text=True, timeout=30)


def test_dry_run_builds_prompt_per_chat_and_masks_body(tmp_path):
    archive = write_archive(tmp_path)
    r = run(archive)
    assert r.returncode == 0, r.stderr
    out = json.loads(r.stdout)
    assert len(out["digests"]) == 2
    first = out["digests"][0]
    assert first["id"] == "11111111-2222-3333-4444-555555555555"
    assert "decided to ship it" in first["prompt"]
    assert FAKE_PAT not in first["prompt"] and "<redacted>" in first["prompt"]
    assert "error" not in first and "digest" not in first


def test_untitled_chat_falls_back_to_id(tmp_path):
    archive = write_archive(tmp_path)
    r = run(archive)
    second = json.loads(r.stdout)["digests"][1]
    assert second["title"] == "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"


def test_explicit_model_flag_overrides_hostname_default(tmp_path):
    archive = write_archive(tmp_path)
    r = run(archive, "--model", "llama3.1:8b")
    assert json.loads(r.stdout)["model"] == "llama3.1:8b"


def test_empty_archive_is_ok_zero_chats(tmp_path):
    archive = write_archive(tmp_path, {"chats": []})
    r = run(archive)
    assert r.returncode == 0, r.stderr
    assert json.loads(r.stdout)["digests"] == []


def test_missing_archive_exit_2(tmp_path):
    r = run(str(tmp_path / "nope.json"))
    assert r.returncode == 2 and "no such archive" in r.stderr


def test_bad_json_archive_exit_2(tmp_path):
    p = tmp_path / "bad.json"
    p.write_text("not json")
    r = run(str(p))
    assert r.returncode == 2 and "bad archive" in r.stderr
