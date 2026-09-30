"""wq#3221 part 1 — the maint-ops/litellm-upgrade patch (patches/maint-ops-litellm-upgrade-
langfuse-verify.patch) actually applies to the live script and the python it adds to verify()
is syntactically valid and structurally does the right thing.

maint-ops/litellm-upgrade lives in the /home/lumen/bin repo, a different git repo than this
one, whose primary tree a BUILD worker cannot write to directly (kb/ISSUE-LOG.md row 1027 hit
this same class of refusal on wq#3097's litellm patch and landed the change as a unified diff
for the same reason). The fixture here is a pinned copy of that live file (immutable, committed
- ISSUE-LOG's acceptance-probe doctrine: never probe against a live mutable file in another
repo) so this test is re-runnable without touching /home/lumen/bin.
"""
import json
import os
import py_compile
import re
import subprocess
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
BUNDLE = os.path.dirname(HERE)
PATCH = os.path.join(BUNDLE, "patches", "maint-ops-litellm-upgrade-langfuse-verify.patch")
FIXTURE = os.path.join(BUNDLE, "patches", "fixtures", "maint-ops-litellm-upgrade.pre-wq3221")


def _apply_patch():
    with open(FIXTURE) as fh:
        pre = fh.read()
    with tempfile.TemporaryDirectory() as td:
        target = os.path.join(td, "maint-ops")
        os.makedirs(target)
        path = os.path.join(target, "litellm-upgrade")
        with open(path, "w") as fh:
            fh.write(pre)
        r = subprocess.run(["git", "apply", PATCH], cwd=td, capture_output=True, text=True)
        assert r.returncode == 0, f"patch failed to apply: {r.stderr}"
        with open(path) as fh:
            return fh.read()


def _extract_verify_heredoc(post_text):
    m = re.search(r"<<'PY'.*?\n(.*?)\nPY\n", post_text, re.S)
    assert m, "verify()'s python heredoc marker not found after patching"
    return m.group(1)


def _exec_langfuse_trace_seen(heredoc_src):
    """Isolate just the import line + langfuse_trace_seen() def, not the argv/keyfile
    parsing the rest of the heredoc does at module scope (that needs real CLI args and
    a real smoke-key file neither of which this unit test provides)."""
    imports = heredoc_src.splitlines()[0]
    m = re.search(r"(def langfuse_trace_seen.*?\n    return False\n)", heredoc_src, re.S)
    assert m, "langfuse_trace_seen() definition not found in patched heredoc"
    ns = {}
    exec(compile(imports + "\n" + m.group(1), "langfuse_trace_seen", "exec"), ns)  # noqa: S102 - fixed committed source, not untrusted input
    return ns["langfuse_trace_seen"]


def test_patch_applies_cleanly_to_the_pinned_pre_patch_fixture():
    post = _apply_patch()
    assert "LANGFUSE_ENV_FILE" in post
    assert "langfuse_trace_seen" in post


def test_patched_verify_heredoc_is_valid_python(tmp_path):
    post = _apply_patch()
    src = _extract_verify_heredoc(post)
    pyfile = tmp_path / "verify_heredoc.py"
    pyfile.write_text(src)
    py_compile.compile(str(pyfile), doraise=True)


def test_langfuse_trace_seen_true_when_a_trace_is_found(tmp_path, monkeypatch):
    post = _apply_patch()
    src = _extract_verify_heredoc(post)
    langfuse_trace_seen = _exec_langfuse_trace_seen(src)
    env = tmp_path / ".env"
    env.write_text("LANGFUSE_PUBLIC_KEY=pk-lf-a\nLANGFUSE_SECRET_KEY=sk-lf-b\n")

    class R:
        def read(self):
            return json.dumps({"meta": {"totalItems": 1}}).encode()

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    import urllib.request
    monkeypatch.setattr(urllib.request, "urlopen", lambda req, timeout=10: R())
    assert langfuse_trace_seen(0, str(env), "http://x", 5, poll_s=0) is True


def test_langfuse_trace_seen_false_when_no_trace_within_timeout(tmp_path, monkeypatch):
    """TEST TO FAILURE (wq#3221 part 4): a canary run against a config with the two
    LANGFUSE_* self-referential lines restored produces a callback authenticating with
    the wrong creds - no trace lands, and this assertion must return False, not True."""
    post = _apply_patch()
    src = _extract_verify_heredoc(post)
    langfuse_trace_seen = _exec_langfuse_trace_seen(src)
    env = tmp_path / ".env"
    env.write_text("LANGFUSE_PUBLIC_KEY=pk-lf-a\nLANGFUSE_SECRET_KEY=sk-lf-b\n")

    class R:
        def read(self):
            return json.dumps({"meta": {"totalItems": 0}}).encode()

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    import urllib.request
    monkeypatch.setattr(urllib.request, "urlopen", lambda req, timeout=10: R())
    assert langfuse_trace_seen(0, str(env), "http://x", 2, poll_s=0.1) is False


def test_langfuse_trace_seen_skips_none_when_creds_missing(tmp_path):
    post = _apply_patch()
    src = _extract_verify_heredoc(post)
    langfuse_trace_seen = _exec_langfuse_trace_seen(src)
    assert langfuse_trace_seen(0, str(tmp_path / "nope.env"), "http://x", 1) is None
