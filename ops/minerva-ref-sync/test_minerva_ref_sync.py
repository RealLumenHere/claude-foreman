"""Sweep-gate acceptance tests for minerva_ref_sync (ops/minerva-ref-sync).

The load-bearing property: a mirror that contains a secret is never published, even when
the secret lives in a file the exclusion list did not anticipate. Runs against temp trees,
uses the real harness/read_dir_sweep.py.
"""
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SYNC = os.path.join(HERE, "minerva_ref_sync.py")
SWEEP = os.path.join(HERE, "..", "..", "harness", "read_dir_sweep.py")
FAKE_KEY = "s" + "k-ant-api03-" + "A" * 48   # matches the sweep's sk_key regex; never a real key


def run(src, dst, extra=()):
    r = subprocess.run([sys.executable, SYNC, "--src", src, "--dst", dst, "--sweep", SWEEP,
                        "--aq", "/nonexistent/aq", "--json", *extra],
                       capture_output=True, text=True, timeout=300)
    last = [l for l in r.stdout.splitlines() if l.startswith("{")][-1]
    return r.returncode, json.loads(last), r.stdout


def make_src(tmp_path):
    src = tmp_path / "minerva"
    (src / "context" / "sections").mkdir(parents=True)
    (src / "prompts").mkdir()
    (src / "eval").mkdir()
    (src / "lumen_recall2.py").write_text("def search_v3(q):\n    return q\n")
    (src / "eval" / "golden.jsonl").write_text('{"qid": 1, "query": "where is the token stored"}\n')
    (src / "context" / "sections" / "027_credentials.md").write_text(f"key: {FAKE_KEY}\n")
    (src / "minerva.db").write_bytes(b"SQLite format 3\x00")
    (src / "notes.bak.20260101").write_text(f"old {FAKE_KEY}\n")
    return src


def test_clean_tree_publishes(tmp_path):
    src = make_src(tmp_path)
    dst = tmp_path / "minerva-ref"
    rc, res, _ = run(str(src), str(dst))
    assert rc == 0 and res["status"] == "published"
    assert (dst / "lumen_recall2.py").exists()
    assert (dst / "eval" / "golden.jsonl").exists()
    assert not (dst / "context").exists()
    assert not (dst / "minerva.db").exists()
    assert not (dst / "notes.bak.20260101").exists()
    manifest = json.load(open(dst / ".minerva-ref-sync.json"))
    assert manifest["hits"] == 0 and manifest["status"] == "published"
    assert not any(p.name.startswith(".minerva-ref.staging") for p in tmp_path.iterdir())


def test_secret_in_unanticipated_file_refuses(tmp_path):
    src = make_src(tmp_path)
    dst = tmp_path / "minerva-ref"
    (src / "prompts" / "new_note.txt").write_text(f"pasted: {FAKE_KEY}\n")  # not excluded
    rc, res, out = run(str(src), str(dst))
    assert rc == 2 and res["status"] == "refused"
    assert res["hit_files"] == ["prompts/new_note.txt"]
    assert not dst.exists(), "refused sync must not publish"
    assert "REFUSED" in out
    assert not any(p.name.startswith(".minerva-ref.staging") for p in tmp_path.iterdir())


def test_refusal_keeps_previous_good_mirror(tmp_path):
    src = make_src(tmp_path)
    dst = tmp_path / "minerva-ref"
    assert run(str(src), str(dst))[0] == 0
    first = json.load(open(dst / ".minerva-ref-sync.json"))["ts"]
    (src / "prompts" / "later.md").write_text(f"token={FAKE_KEY}\n")
    rc, res, _ = run(str(src), str(dst))
    assert rc == 2
    assert json.load(open(dst / ".minerva-ref-sync.json"))["ts"] == first
    assert not (dst / "prompts" / "later.md").exists()
    assert not (tmp_path / "minerva-ref.prev").exists()


def test_sweep_tool_failure_fails_closed(tmp_path):
    src = make_src(tmp_path)
    dst = tmp_path / "minerva-ref"
    r = subprocess.run([sys.executable, SYNC, "--src", str(src), "--dst", str(dst),
                        "--sweep", "/nonexistent/read_dir_sweep.py", "--aq", "/nonexistent/aq",
                        "--json"], capture_output=True, text=True, timeout=120)
    assert r.returncode == 3
    assert not dst.exists()


def test_dry_run_never_publishes(tmp_path):
    src = make_src(tmp_path)
    dst = tmp_path / "minerva-ref"
    rc, res, _ = run(str(src), str(dst), ("--dry-run",))
    assert rc == 0 and res["status"] == "dry_run_clean"
    assert not dst.exists()
