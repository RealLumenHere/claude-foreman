"""read_dir_sweep (wq#1441) — the masking is the point: a printed credential is a
burned credential, and the first throwaway sweep this ticket used truncated the
line BEFORE masking, so a token cut at the 100-char edge escaped the replace and
was printed whole. That exact shape is pinned here."""
import os
import sys

import pytest

HARNESS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HARNESS)
import read_dir_sweep as rds  # noqa: E402

FAKE_PAT = "github_pat_11ABCDEFGH0nTgGh17bRlBn_C2ieRMdH3r81BdXM9Gb0T8MwcmtnQ4fNOy9tMQWKEslKOR3FVFBxw"


def _write(root, rel, text):
    p = os.path.join(root, rel)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w") as f:
        f.write(text)
    return p


def test_clean_tree_has_no_hits(tmp_path):
    _write(tmp_path, "playbooks/a.md", "api_key: os.environ/OPENROUTER_KEY\napi_key: sk-none\n")
    _write(tmp_path, "b.yaml", "token: ${GATUS_TOKEN}\npassword: <redacted>\n")
    hits, n = rds.sweep_root(str(tmp_path))
    assert hits == [] and n == 2


def test_token_at_truncation_edge_is_still_masked(tmp_path):
    line = "x" * 95 + " GITHUB_TOKEN=" + FAKE_PAT + "\n"
    _write(tmp_path, "env.bak.1", line)
    hits, _ = rds.sweep_root(str(tmp_path))
    kinds = [h[2] for h in hits]
    assert "gh_pat" in kinds
    for _, _, _, excerpt in hits:
        assert FAKE_PAT not in excerpt
        assert FAKE_PAT[:12] not in excerpt  # more than MASK_KEEP chars never leaks


@pytest.mark.parametrize("name", [".env", "prod.env", "x.token", "id_ed25519", "k.pem", ".env.bak.20260903"])
def test_credential_basenames_are_hits(tmp_path, name):
    _write(tmp_path, name, "nothing\n")
    hits, _ = rds.sweep_root(str(tmp_path))
    assert [h[2] for h in hits] == ["basename"]


@pytest.mark.parametrize("line,kind", [
    ('TOKEN = "sntl-p9f8e7d6c5b4a3"', "sntl"),
    ("DEEPSEEK_API_KEY=sk-4a3b2c1d0e9f8a7b6c5d4e3f2a1b0c9d", "sk_key"),
    ("-----BEGIN PRIVATE KEY-----", "privkey"),
    ('"Token: xapp-1-A0B0BRS436F-1234567890123-abcdefghij"', "slack"),
    ("password: Correct-Horse-Battery-Staple-2026", "kv_secret"),
])
def test_content_shapes(tmp_path, line, kind):
    _write(tmp_path, "f.txt", line + "\n")
    hits, _ = rds.sweep_root(str(tmp_path))
    assert [h[2] for h in hits] == [kind]


def test_binary_and_git_skipped(tmp_path):
    _write(tmp_path, ".git/config", "url = https://ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ012345@x\n")
    with open(os.path.join(tmp_path, "blob.bin"), "wb") as f:
        f.write(b"\0" + FAKE_PAT.encode())
    hits, _ = rds.sweep_root(str(tmp_path))
    assert hits == []


def test_mask():
    assert rds.mask(FAKE_PAT) == "gith…" and rds.mask("ab") == "…"


# --- write_runtime / aq_alert (wq#1482) --------------------------------------------

def test_write_runtime_writes_ts_and_masked_hits(tmp_path):
    root = tmp_path / "root"
    _write(root, "prod.env", "nothing\n")
    runtime = tmp_path / "runtime" / "read_dir_sweep.json"
    report, new_hits = rds.write_runtime(roots=[str(root)], runtime_path=str(runtime))
    assert runtime.exists()
    import json
    written = json.loads(runtime.read_text())
    assert written["ts"] and written["roots"][str(root)]["hits"][0]["kind"] == "basename"
    assert len(new_hits) == 1 and new_hits[0][0] == str(root)


def test_write_runtime_second_run_same_hit_is_not_new(tmp_path):
    root = tmp_path / "root"
    _write(root, "prod.env", "nothing\n")
    runtime = tmp_path / "runtime" / "read_dir_sweep.json"
    rds.write_runtime(roots=[str(root)], runtime_path=str(runtime))
    _, new_hits = rds.write_runtime(roots=[str(root)], runtime_path=str(runtime))
    assert new_hits == []


def test_write_runtime_flags_only_the_newly_added_hit(tmp_path):
    root = tmp_path / "root"
    _write(root, "prod.env", "nothing\n")
    runtime = tmp_path / "runtime" / "read_dir_sweep.json"
    rds.write_runtime(roots=[str(root)], runtime_path=str(runtime))
    _write(root, "id_ed25519", "nothing\n")
    _, new_hits = rds.write_runtime(roots=[str(root)], runtime_path=str(runtime))
    assert len(new_hits) == 1
    assert new_hits[0][1]["path"].endswith("id_ed25519")


def test_aq_alert_calls_aq_add_per_new_hit(tmp_path, monkeypatch):
    calls = []
    fake_aq = tmp_path / "aq"
    fake_aq.write_text("#!/bin/sh\nexit 0\n")
    fake_aq.chmod(0o755)
    monkeypatch.setattr(rds.subprocess, "run", lambda argv, **kw: calls.append(argv))
    new_hits = [("/root", {"path": "/root/x.env", "line": 0, "kind": "basename",
                           "excerpt": "x.env"})]
    rds.aq_alert(new_hits, aq_bin=str(fake_aq))
    assert len(calls) == 1
    assert calls[0][:3] == [str(fake_aq), "add", rds.AQ_SOURCE]


def test_aq_alert_noop_without_new_hits_or_binary(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(rds.subprocess, "run", lambda argv, **kw: calls.append(argv))
    rds.aq_alert([], aq_bin=str(tmp_path / "aq"))
    rds.aq_alert([("/root", {"path": "/root/x", "line": 1, "kind": "basename",
                             "excerpt": "x"})], aq_bin=str(tmp_path / "does-not-exist"))
    assert calls == []
