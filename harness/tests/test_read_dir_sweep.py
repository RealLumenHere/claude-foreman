"""read_dir_sweep (wq#1441) — the masking is the point: a printed credential is a
burned credential, and the first throwaway sweep this ticket used truncated the
line BEFORE masking, so a token cut at the 100-char edge escaped the replace and
was printed whole. That exact shape is pinned here."""
import os
import sys
import time

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
    # sweep an own subdir: conftest autouse fixtures drop stub files in tmp_path
    # itself (wq#2901's lumen_episode_stub.py), which the file count would pick up
    root = tmp_path / "tree"
    _write(root, "playbooks/a.md", "api_key: os.environ/OPENROUTER_KEY\napi_key: sk-none\n")
    _write(root, "b.yaml", "token: ${GATUS_TOKEN}\npassword: <redacted>\n")
    hits, n = rds.sweep_root(str(root))
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
    monkeypatch.setattr(rds, "worker_add_dirs", lambda *a, **k: [])
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


def test_reviewed_hit_fingerprint_suppresses_only_that_exact_match(tmp_path, monkeypatch):
    """wq#2120: this sweep had no ack mechanism for a hit a human has actually reviewed
    and confirmed is not a credential, so a `dir()` grant under a root with one residual
    kv_secret hit stayed refused forever (capability_lint.py fails closed on ANY sweep
    hit under the cited root). REVIEWED_HIT_FINGERPRINTS keys the ack to the exact
    matched span, not the path or line, so a genuinely different secret on the very same
    line still fires."""
    line = "Signing Secret: " + "0123456789abcdef0123456789ab"
    _write(tmp_path, "f.txt", line + "\n")
    hits, _ = rds.sweep_root(str(tmp_path))
    assert [h[2] for h in hits] == ["kv_secret"]

    for kind, rx in rds.PATTERNS:
        m = rx.search(line)
        if m:
            break
    fp = rds.reviewed_hit_fingerprint(m.group(0))
    monkeypatch.setattr(rds, "REVIEWED_HIT_FINGERPRINTS",
                         rds.REVIEWED_HIT_FINGERPRINTS | {fp})
    hits, _ = rds.sweep_root(str(tmp_path))
    assert hits == []

    other_line = "Signing Secret: " + "ffeeddccbbaa99887766554433221100"
    _write(tmp_path, "g.txt", other_line + "\n")
    hits, _ = rds.sweep_root(str(tmp_path))
    assert [h[2] for h in hits] == ["kv_secret"]


# wq#2993: the fleet's own mask literal is not a credential body.
@pytest.mark.parametrize("line", [
    "url = " + _url("lumen", "pw_" + "MASKED"),                # mask_at_rest: <kept prefix>MASKED
    "url = " + _url("lumen", "MASKED"),
    "api_key: " + "sk-" + "prod-abcdefghijk" + "MASKED",
    'token = "' + "ghp_" + 'MASKED"',                          # what the mask writes for a PAT
])
def test_mask_literal_is_not_a_credential_body(tmp_path, line):
    _write(tmp_path, "mask.log", line + "\n")
    hits, _ = rds.sweep_root(str(tmp_path))
    assert hits == []


# ---- wq#2993: the fleet's own mask output is not a credential ----------------------------
# Probe inputs are pinned in fixtures/wq2993_report_lines.py (committed, seams split),
# not drawn from a live mask_at_rest log — an unpinned probe is not re-runnable evidence.
import importlib.util as _ilu  # noqa: E402

_spec = _ilu.spec_from_file_location(
    "wq2993_report_lines",
    os.path.join(os.path.dirname(__file__), "fixtures", "wq2993_report_lines.py"))
w2993 = _ilu.module_from_spec(_spec)
_spec.loader.exec_module(w2993)


def test_mask_literal_url_value_is_not_a_hit(tmp_path):
    """`http://lumen:ghp_MASKED@host` is mask output read back, not a basic-auth pw."""
    _write(tmp_path, "notes.md", w2993.MASKED_URL + "\n")
    hits, _ = rds.sweep_root(str(tmp_path))
    assert hits == []


@pytest.mark.parametrize("line", [
    "url = " + _url("lumen", "pw_" + "MASKEDx9Zq2Lm"),          # MASKED mid-value: still a hit
    "url = " + _url("lumen", "Kf3" + "x9Zq2Lm"),
    "api_key: " + "sk-" + "prod-abcdefghijk" + "MASKEDx9Zq2Lm",
    "api_key: " + "sk-" + "prod-abcdefghijk" + "Wv7Zq2Lm8Kf3",
])
def test_mask_literal_exemption_does_not_hide_real_credentials(tmp_path, line):
    _write(tmp_path, "real.cfg", line + "\n")
    hits, _ = rds.sweep_root(str(tmp_path))
    assert hits, line[:12]


def test_mask_literal_kv_value_is_not_a_hit(tmp_path):
    _write(tmp_path, "notes.md", w2993.MASKED_KV + "\n")
    hits, _ = rds.sweep_root(str(tmp_path))
    assert hits == []


def test_mask_literal_behind_a_token_prefix_is_not_a_hit(tmp_path):
    """`ghp_<quoted chars>MASKED` — a context window quoting masked output around a kept
    prefix. The prefixed patterns refuse the tail too, not only url/kv."""
    _write(tmp_path, "notes.md", w2993.MASKED_GHPAT + "\n")
    hits, _ = rds.sweep_root(str(tmp_path))
    assert hits == []


def test_mask_literal_refusal_is_case_sensitive_and_tail_only(tmp_path):
    """A body that merely CONTAINS the literal mid-run is not mask output; neither is a
    lower-case one. Both must still page — this is what keeps the refusal falsifiable."""
    _write(tmp_path, "a.md", _url("lumen", "ghp_" + "MASK" + "EDx9Zq2Lm") + "\n")
    _write(tmp_path, "b.md", "signing secret = " + "0123456789abcdef" + "mask" + "ed\n")
    hits, _ = rds.sweep_root(str(tmp_path))
    assert sorted(h[2] for h in hits) == ["kv_secret", "url_basic_auth"]


def test_mask_report_artifacts_are_skipped(tmp_path):
    """A mask finding row is a report ABOUT a credential. Both report shapes —
    _runs/mask_at_rest/*.jsonl and _runs/<ticket>/mask-hits-*.json — are exempt from
    content patterns, even when the context window quotes a real-shaped body."""
    _write(tmp_path, "_runs/mask_at_rest/20260101T000000Z.jsonl",
           w2993.REPORT_ROW_REAL + "\n" + w2993.REPORT_ROW_MASKED + "\n")
    _write(tmp_path, "_runs/wq0000/mask-hits-1.json", w2993.REPORT_ROW_REAL + "\n")
    hits, _ = rds.sweep_root(str(tmp_path))
    assert hits == []


def test_report_shaped_row_outside_report_paths_still_pages(tmp_path):
    """Falsifiability probe from OUTSIDE the mask-log population (wq#2630): the same
    `label: value` JSONL row with a real-shaped body, sitting anywhere that is not a
    report path, must still page. If this goes quiet the exemption grew too wide."""
    _write(tmp_path, "docs/triage.jsonl", w2993.REPORT_ROW_REAL + "\n")
    _write(tmp_path, "_runs/mask_at_rest/deeper/x.jsonl", w2993.REPORT_ROW_REAL + "\n")
    _write(tmp_path, "_runs/wq0000/notes.json", w2993.REPORT_ROW_REAL + "\n")
    hits, _ = rds.sweep_root(str(tmp_path))
    assert len(hits) == 3 and all(h[2] == "url_basic_auth" for h in hits)
    assert all(w2993.REAL_PW[:3] not in h[3] for h in hits)   # still masked when printed


def test_credential_basename_inside_report_dir_still_pages(tmp_path):
    """The exemption covers content patterns only — a credential FILE dropped into a
    report dir keeps its basename hit."""
    _write(tmp_path, "_runs/mask_at_rest/leaked.env", "X=1\n")
    hits, _ = rds.sweep_root(str(tmp_path))
    assert [h[2] for h in hits] == ["basename"]


# ---- wq#3167: a huge/pathological root must not blow through the whole run --------
# 09-29 05:16Z: the service's 600s TimeoutStartSec killed the sweep after the first
# night wq#2119's `defaults.add_dirs` fold-in put /home/lumen/foreman (its _runs/
# snapshot archive alone: 40797 files, 77% of the tree) into the swept roots. The
# process never finished, so runtime/read_dir_sweep.json never got a fresh write, and
# capability_lint.sweep_stale() fails EVERY dir() grant closed off that one top-level
# `ts` — a global outage from one slow root, not just missing coverage for that root.


def test_bounded_sweep_root_is_partial_when_deadline_already_passed(tmp_path):
    _write(tmp_path, "a.txt", "nothing\n")
    _write(tmp_path, "b.txt", "nothing\n")
    hits, nfiles, partial, _ = rds._sweep_root_bounded(str(tmp_path), deadline=time.monotonic() - 1)
    assert partial is True
    assert nfiles == 0 and hits == []


def test_bounded_sweep_root_with_no_deadline_matches_public_wrapper(tmp_path):
    _write(tmp_path, "prod.env", "nothing\n")
    hits, nfiles, partial, not_reached = rds._sweep_root_bounded(str(tmp_path), deadline=None)
    assert partial is False
    assert not_reached == []
    assert (hits, nfiles) == rds.sweep_root(str(tmp_path))


def test_sweep_report_marks_a_root_partial_when_its_own_timeout_elapses(tmp_path, monkeypatch):
    root = tmp_path / "root"
    _write(root, "a.txt", "nothing\n")
    monkeypatch.setattr(rds, "ROOT_TIMEOUT_S", -1)  # already-expired budget
    report = rds._sweep_report([str(root)])
    entry = report[str(root)]
    assert entry["partial"] is True
    assert entry["files"] == 0
    assert "elapsed_s" in entry


def test_sweep_report_records_elapsed_s_for_a_normal_root(tmp_path):
    root = tmp_path / "root"
    _write(root, "a.txt", "nothing\n")
    report = rds._sweep_report([str(root)])
    entry = report[str(root)]
    assert "partial" not in entry
    assert entry["files"] == 1
    assert entry["elapsed_s"] >= 0


def test_sweep_report_skips_a_root_entirely_once_the_global_deadline_passes(tmp_path):
    slow = tmp_path / "slow"
    fast = tmp_path / "fast"
    _write(slow, "a.txt", "nothing\n")
    _write(fast, "b.txt", "nothing\n")
    report = rds._sweep_report([str(slow), str(fast)], deadline_s=-1)
    assert "sweep deadline reached" in report[str(slow)]["error"]
    assert report[str(slow)]["hits"] == []
    assert "sweep deadline reached" in report[str(fast)]["error"]


def test_sweep_report_earlier_roots_are_unaffected_by_a_later_deadline_skip(tmp_path, monkeypatch):
    """The order write_runtime() builds `roots` in (policy_read_dirs() then
    worker_add_dirs()) puts the capability_lint-gating roots first — a deadline that
    trims the run must only ever cost the LAST root(s), never an earlier one. The first
    root's own sweep is real and fast (well inside the 100ms budget); a spy sleeps
    AFTER it returns to push elapsed time past the 100ms global deadline before the
    second root's pre-start check, without touching the deadline math under test."""
    first = tmp_path / "first"
    second = tmp_path / "second"
    _write(first, "a.txt", "nothing\n")
    _write(second, "b.txt", "nothing\n")
    real_bounded = rds._sweep_root_bounded

    def slow_after_first(root, deadline=None, bulk_deadline=None):
        result = real_bounded(root, deadline=deadline, bulk_deadline=bulk_deadline)
        if root == str(first):
            time.sleep(0.2)
        return result

    monkeypatch.setattr(rds, "_sweep_root_bounded", slow_after_first)
    report = rds._sweep_report([str(first), str(second)], deadline_s=0.1)
    assert "error" not in report[str(first)]
    assert report[str(first)]["files"] == 1
    assert "sweep deadline reached" in report[str(second)]["error"]


def test_write_runtime_finishes_and_writes_fresh_ts_when_a_root_is_incomplete(tmp_path):
    """The actual wq#3167 fix: a root that can't finish in time must truncate itself,
    never the whole write_runtime() call — capability_lint.sweep_stale() gates on this
    file's top-level `ts` existing and being recent, regardless of any one root's
    per-root completeness (harness/capability_lint.py:673-676)."""
    huge = tmp_path / "huge"
    _write(huge, "a.txt", "nothing\n")
    _write(huge, "b.txt", "nothing\n")
    _write(huge, "c.txt", "nothing\n")
    runtime = tmp_path / "runtime" / "read_dir_sweep.json"
    report, _ = rds.write_runtime(roots=[str(huge)], runtime_path=str(runtime),
                                  deadline_s=-1)
    assert runtime.exists()
    import json
    written = json.loads(runtime.read_text())
    assert written["ts"]
    assert "sweep deadline reached" in report[str(huge)]["error"]


# ---- wq#3203: BULK_DIRS deferral — sorted() put `_runs` (0x5F) ahead of every ----------
# lowercase sibling, so foreman's 42k-file snapshot archive ate the whole 90s budget and
# ~9.9k source files (events, ops, reports, harness, …) were silently never swept.


class _FakeClock:
    """One second per monotonic() call — every deadline check is a tick, so a time
    budget becomes a deterministic FILE budget and the starvation shape needs no
    real sleeping. Patched as rds.time so nothing outside the module sees it."""
    def __init__(self):
        self.t = 0.0

    def monotonic(self):
        self.t += 1.0
        return self.t


def test_pytest_cache_is_skipped(tmp_path):
    """wq#3203: 215 of foreman's 241 live hits were pytest node ids in
    .pytest_cache/v/cache/nodeids whose `[sk...` parametrize suffix matches sk_key."""
    root = tmp_path / "tree"
    _write(root, "harness/a.txt", "nothing\n")
    _write(root, ".pytest_cache/v/cache/nodeids",
           '["tests/test_x.py::test_y[sk-' + "a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6]\"]\n")
    hits, n = rds.sweep_root(str(root))
    assert hits == [] and n == 1


def test_bulk_dir_cannot_starve_source_siblings(tmp_path, monkeypatch):
    """The ticket's shape: a foreman-like tree, big `_runs` plus small siblings, and a
    budget far smaller than `_runs` alone. Every SOURCE dir must be fully reached —
    including `tools`, which sorts after `_runs` and was exactly what the live 09-30
    run never got to — with only `_runs` truncated. Fails on the pre-wq#3203 walk."""
    root = tmp_path / "foreman"
    _write(root, "README.md", "nothing\n")
    for d in ("dispatcher", "events", "harness", "ops"):
        _write(root, d + "/a.txt", "nothing\n")
        _write(root, d + "/b.txt", "nothing\n")
    _write(root, "tools/leak.txt", "GITHUB_TOKEN=" + FAKE_PAT + "\n")
    for i in range(60):
        _write(root, "_runs/wq%03d/snap.txt" % i, "nothing\n")
    _write(root, "_runs/zz_last/leak.txt", "GITHUB_TOKEN=" + FAKE_PAT + "\n")
    monkeypatch.setattr(rds, "time", _FakeClock())
    # 40 ticks: source needs ~16 (10 files + 6 dir prechecks), `_runs` alone needs 62+
    hits, nfiles, partial, not_reached = rds._sweep_root_bounded(str(root), deadline=40)
    paths = [h[0] for h in hits]
    assert str(root / "tools" / "leak.txt") in paths      # last-sorted source dir reached
    assert str(root / "_runs" / "zz_last" / "leak.txt") not in paths
    assert partial is True
    assert not_reached == []                              # `_runs` started, then truncated
    assert 10 <= nfiles < 71                              # all 10 source files, not all bulk


def test_bulk_dir_has_its_own_budget_separate_from_source(tmp_path):
    root = tmp_path / "root"
    _write(root, "harness/a.txt", "nothing\n")
    _write(root, "_runs/r/leak.env", "nothing\n")
    hits, nfiles, partial, not_reached = rds._sweep_root_bounded(
        str(root), deadline=time.monotonic() + 60, bulk_deadline=time.monotonic() - 1)
    assert hits == [] and nfiles == 1                     # source swept, bulk never read
    assert partial is True
    assert not_reached == ["_runs"]


def test_starved_source_phase_does_not_forfeit_the_bulk_budget(tmp_path):
    root = tmp_path / "root"
    _write(root, "harness/a.txt", "nothing\n")
    _write(root, "_runs/r/leak.env", "nothing\n")
    hits, nfiles, partial, not_reached = rds._sweep_root_bounded(
        str(root), deadline=time.monotonic() - 1, bulk_deadline=time.monotonic() + 60)
    assert partial is True
    assert not_reached == ["harness"]
    assert any(k == "basename" and p.endswith("leak.env") for p, _, k, _ in hits)


def test_sweep_report_records_not_reached_for_a_partial_root(tmp_path, monkeypatch):
    """wq#3203(c): `partial=true` said a root was cut short but not WHAT was cut —
    prior-day hits under the unreached dirs just vanished from the report."""
    root = tmp_path / "root"
    _write(root, "harness/a.txt", "nothing\n")
    _write(root, "ops/b.txt", "nothing\n")
    monkeypatch.setattr(rds, "ROOT_TIMEOUT_S", -1)
    monkeypatch.setattr(rds, "BULK_TIMEOUT_S", 0)
    report = rds._sweep_report([str(root)])
    entry = report[str(root)]
    assert entry["partial"] is True
    assert entry["not_reached"] == ["harness", "ops"]


def test_write_runtime_mode_prints_not_reached_in_unit_log(tmp_path, monkeypatch, capsys):
    root = tmp_path / "root"
    _write(root, "harness/a.txt", "nothing\n")
    monkeypatch.setattr(rds, "RUNTIME_PATH", str(tmp_path / "runtime" / "sweep.json"))
    monkeypatch.setattr(rds, "AQ_BIN", str(tmp_path / "no-aq"))
    monkeypatch.setattr(rds, "policy_read_dirs", lambda *a, **k: [str(root)])
    monkeypatch.setattr(rds, "worker_add_dirs", lambda *a, **k: [])
    monkeypatch.setattr(rds, "ROOT_TIMEOUT_S", -1)
    monkeypatch.setattr(rds, "BULK_TIMEOUT_S", 0)
    assert rds._main(["--write-runtime"]) == 0
    out = capsys.readouterr().out
    assert "not reached: harness" in out
    import json
    written = json.loads((tmp_path / "runtime" / "sweep.json").read_text())
    assert written["roots"][str(root)]["not_reached"] == ["harness"]
