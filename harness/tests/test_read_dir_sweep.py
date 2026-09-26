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

FAKE_PAT = "github_pat_" + "11ABCDEFGH0nTgGh17bRlBn_C2ieRMdH3r81BdXM9Gb0T8MwcmtnQ4fNOy9tMQWKEslKOR3FVFBxw"


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
    ('TOKEN = "' + 'sntl-p9f8e7d6c5b4a3"', "sntl"),
    ("DEEPSEEK_API_KEY=sk-" + "4a3b2c1d0e9f8a7b6c5d4e3f2a1b0c9d", "sk_key"),
    ("-----BEGIN PRIVATE KEY-----", "privkey"),
    ('"Token: ' + 'xapp-1-A0B0BRS436F-1234567890123-abcdefghij"', "slack"),
    ("password: " + "Correct-Horse-Battery-Staple-2026", "kv_secret"),
    # wq#2115: the label is whatever the source service's docs print — space-separated
    # compounds and synonyms the old fixed keyword list did not enumerate. All three of
    # these sat on the same messenger line (wq#1483) the old pattern only half-caught.
    ("Client Secret: " + "8f7e6d5c4b3a2918071625344352", "kv_secret"),
    ("Signing Secret: " + "0123456789abcdef0123456789ab", "kv_secret"),
    ("Verifaction Token: " + "abcdefghijklmnopqrstuvwx", "kv_secret"),
    ("DB Password = " + "hunter2-hunter2-hunter2-hunter2", "kv_secret"),
    ("refresh-token: " + "1zz0gLongLookingRefreshTokenValue", "kv_secret"),
    ("clientSecret: " + "8f7e6d5c4b3a2918071625344352", "kv_secret"),
    ("PrivateKey = " + "aGVsbG8gd29ybGRhYmNkZWZnaGlqaw", "kv_secret"),
    # wq#2122: the label used to have to touch its separator, so these all passed
    # unflagged. Standard JSON is the biggest miss -- the closing quote sits between
    # the label and the colon, and JSON is how most of the fleet stores credentials.
    ('"api_key": "' + '8f7e6d5c4b3a2918071625344352"', "kv_secret"),
    ('"signing_secret": "' + '0123456789abcdef0123456789ab"', "kv_secret"),
    ("*Client Secret*: 8f7e6d5c4b3a2918071625344352", "kv_secret"),   # Slack bold
    ("Client Secret: `8f7e6d5c4b3a2918071625344352`", "kv_secret"),   # backtick value
    ("Signing Secret is: 0123456789abcdef0123456789ab", "kv_secret"),
    ("Signing Secret - 0123456789abcdef0123456789ab", "kv_secret"),
    # the all-caps pointer guard must not swallow a credential that merely STARTS with
    # an all-caps run -- `-` is not a value terminator, so this is still a hit
    ("api_key: " + "AAAA_BBBB-ccc123ddd456eee789fff", "kv_secret"),
    ('"api_key": "' + 'AAAAB3NzaC1yc2EAAAADAQABAAABgQ"', "kv_secret"),
])
def test_content_shapes(tmp_path, line, kind):
    _write(tmp_path, "f.txt", line + "\n")
    hits, _ = rds.sweep_root(str(tmp_path))
    assert [h[2] for h in hits] == [kind]


@pytest.mark.parametrize("line", [
    "key: value-that-is-quite-long-here",       # bare `key` is ordinary YAML, not a label
    "max_tokens: 40960000000000000000000000",   # `token` only inside a longer word
    "The secret to good code: write tests for every branch of it",
    "monkey: abcdefghijklmnopqrstuvwxyz",       # no separator, no camel hump
    "PublicKey = aGVsbG8gd29ybGRhYmNkZWZnaGlqaw",
    "public_key: AAAAB3NzaC1yc2EAAAADAQABAAABgQ",
    "pub_key = serialization.load_pem_public_key",
    "Fleet master key: /home/lumen/.ssh/id_ed25519",   # a path to the key, not the key
    "const secret = env.MAIL_PROBE_SECRET_VALUE;",
    "secret = process.env.MAIL_PROBE_SECRET_VAL",
    "api_key = os.getenv('OPENROUTER_API_KEY_X')",
    "sed -i 's|<a key: bacTIkbOHNwlyQEsTVmT|x|'",     # one-letter qualifier is prose
    # wq#2122: the dash separator requires surrounding whitespace. Without that, a
    # hyphenated identifier reads as label + separator + value and every kebab-case
    # filename in the tree becomes a hit.
    "api-key-rotation-procedure-documentation",
    "client-secret-rotation-runbook-v2-draft",
    # the connector list is enumerated, not \w+, so a sentence cannot bridge a prose
    # `secret` to an unrelated long token further down the line
    "The secret here is that abcdefghijklmnopqrstuvwxyz is not a credential",
    # wq#2122: allowing a quoted label opened a new POINTER shape -- the value is a
    # constant, or the NAME of an env var, not its value. Both of these were real
    # false positives in the first --policy run of this change.
    'headers={"x-internal-key": ' + 'BILLING_INTERNAL_KEY}, timeout=3)',
    '"azure": {"name": "Azure OpenAI", "env_key": "' + 'AZURE_OPENAI_API_KEY"},',
    "api_key: " + "SOME_CONSTANT_NAME_HERE",
])
def test_broadened_label_does_not_fire_on_ordinary_lines(tmp_path, line):
    _write(tmp_path, "f.txt", line + "\n")
    hits, _ = rds.sweep_root(str(tmp_path))
    assert hits == []


@pytest.mark.parametrize("name", [
    ".git-credentials.bak.20260830_124941_pre_rotate_finish",
    "prod.env.bak.20260811_150332_pre_health",
    "id_ed25519.bak.20260101.orig",
    ".netrc~",
    "server.key.old",
    "creds.env.dpkg-old",
])
def test_backup_copies_of_credential_files_are_hits(tmp_path, name):
    """wq#2114: the globs matched the canonical name only, so every backup of a
    credential file was invisible. `.bak.<TS>_pre_<reason>` is the fleet's universal
    convention before a risky edit, so the convention that exists to make changes safe
    was quietly producing credential copies no basename rule could see."""
    _write(tmp_path, name, "nothing token-shaped in here\n")
    hits, _ = rds.sweep_root(str(tmp_path))
    assert [h[2] for h in hits] == ["basename"], f"{name} was not flagged"


@pytest.mark.parametrize("name", [
    "notes.txt.bak.20260101",
    "app.py.bak",
    "README.md",
    "dispatcher.log.2.gz",
])
def test_backups_of_ordinary_files_stay_clean(tmp_path, name):
    """Stripping the suffix must reveal a credential name, not flag every backup in the
    tree — the fleet is full of .bak copies of ordinary files."""
    _write(tmp_path, name, "ordinary content\n")
    hits, _ = rds.sweep_root(str(tmp_path))
    assert hits == [], f"{name} was flagged"


@pytest.mark.parametrize("prefix", ["ghp_", "gho_", "ghs_", "ghr_", "ghu_", "glpat-"])
def test_all_github_token_prefixes_are_caught(tmp_path, prefix):
    """wq#2114 audit: ghr_/ghu_ were absent here but present in the token-engine
    broker's copy, so the scanners disagreed on which prefixes exist."""
    _write(tmp_path, "f.txt", f"token = {prefix}{'A' * 32}\n")
    hits, _ = rds.sweep_root(str(tmp_path))
    assert "gh_pat" in [h[2] for h in hits], f"{prefix} not caught"


def test_binary_and_git_skipped(tmp_path):
    _write(tmp_path, ".git/config", "url = https://ghp_" + "ABCDEFGHIJKLMNOPQRSTUVWXYZ012345@x\n")
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


def test_write_runtime_mode_exits_zero_on_a_hit(tmp_path, monkeypatch):
    """wq#2115: a finding is not a failure — a hit-driven exit 1 would mark
    lumen-read-dir-sweep.service failed and skip its cron stamp every day."""
    root = tmp_path / "root"
    _write(root, "prod.env", "nothing\n")
    monkeypatch.setattr(rds, "RUNTIME_PATH", str(tmp_path / "runtime" / "sweep.json"))
    monkeypatch.setattr(rds, "AQ_BIN", str(tmp_path / "no-aq"))
    monkeypatch.setattr(rds, "policy_read_dirs", lambda *a, **k: [str(root)])
    assert rds._main(["--write-runtime"]) == 0
    assert rds._main([str(root)]) == 1  # interactive mode still exits 1 on hits


def test_aq_alert_noop_without_new_hits_or_binary(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(rds.subprocess, "run", lambda argv, **kw: calls.append(argv))
    rds.aq_alert([], aq_bin=str(tmp_path / "aq"))
    rds.aq_alert([("/root", {"path": "/root/x", "line": 1, "kind": "basename",
                             "excerpt": "x"})], aq_bin=str(tmp_path / "does-not-exist"))
    assert calls == []


# ---- wq#2479: credential embedded in a URL (scheme://user:secret@host) -------------------
# Fixtures are assembled at runtime so no single source literal is itself credential-shaped
# (the tripwire reads this file too, see kb/ISSUE-LOG rows 577/578).
def _url(user, pw, scheme="https", host="100.64.0.7/git/repo.git"):
    return scheme + "://" + user + ":" + pw + "@" + host


@pytest.mark.parametrize("url", [
    _url("lumen", "Kf3" + "x9Zq2Lm"),
    _url("admin", "hunter" + "2hunter2", scheme="http"),
    _url("app", "p" + "4ssw0rd-long-1", scheme="postgres", host="db.internal:5432/x"),
    _url("x-access-token", "tok" + "en1234567890", scheme="HTTPS"),
])
def test_url_basic_auth_is_a_hit_and_masked(tmp_path, url):
    _write(tmp_path, "remote.cfg", "url = " + url + "\n")
    hits, _ = rds.sweep_root(str(tmp_path))
    assert [h[2] for h in hits] == ["url_basic_auth"]
    excerpt = hits[0][3]
    userinfo = url.split("://", 1)[1].split("@", 1)[0]
    assert userinfo not in excerpt
    assert userinfo.split(":", 1)[0] not in excerpt      # not even the user name
    assert userinfo.split(":", 1)[1][:3] not in excerpt   # no password character survives


@pytest.mark.parametrize("url", [
    _url("user", ""),                                    # empty password
    _url("user", "${GIT_" + "TOKEN}"),                   # env-ref
    _url("user", "$TOKEN" + "VALUE"),
    _url("{user}", "{pw" + "}"),                         # format template
    _url("user", "%s" * 5),
    _url("user", "<pass" + "word>"),                     # placeholder
    _url("user", "pass" + "word"),                       # doc placeholder word
    _url("user", "CHANGE" + "ME_TOKEN"),                 # ALL_CAPS pointer
    _url("user", "os.environ" + "['X']"),
    _url("user", "short"),                               # under the length floor
    "https://host:8080/path@thing",
    "ssh://git@github.com:22/org/repo.git",              # user only, no password
    "https://example.com/a?b=c:d@efghijkl",              # colon and @ in the path, not userinfo
    "mailto:someone@example.com",
])
def test_url_basic_auth_non_credentials_are_not_hits(tmp_path, url):
    _write(tmp_path, "notes.md", "see " + url + "\n")
    hits, _ = rds.sweep_root(str(tmp_path))
    assert hits == []


def test_url_basic_auth_masks_before_truncating(tmp_path):
    pw = "Zq" + "8Lm2Kf3x9Wv7"
    _write(tmp_path, "x.cfg", "y" * 100 + " " + _url("lumen", pw) + "\n")
    hits, _ = rds.sweep_root(str(tmp_path))
    assert hits and all(pw not in h[3] and pw[:6] not in h[3] for h in hits)


def test_url_basic_auth_does_not_shadow_a_prefixed_token(tmp_path):
    """gh_pat is earlier in PATTERNS, so a PAT in the password slot keeps its own kind."""
    pat = "ghp_" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4"
    _write(tmp_path, "r.cfg", _url("x-access-token", pat) + "\n")
    hits, _ = rds.sweep_root(str(tmp_path))
    assert [h[2] for h in hits] == ["gh_pat"]
