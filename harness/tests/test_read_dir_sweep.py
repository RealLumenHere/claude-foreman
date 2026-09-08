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
