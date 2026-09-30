"""langfuse_client.ingest() error-detail coverage (wq#3211).

Before this fix, an HTTPError from /api/public/ingestion propagated bare: both
call sites (fworker.emit_attempt_span, foreman_dispatcher.emit_langfuse) log only
repr(e), and HTTPError.__repr__ never includes the response body, so a 4002
"Invalid credentials" failure left no trace of *why* langfuse-web rejected the
request anywhere reachable. ingest() now reads the body and raises
LangfuseIngestError with it attached (masked), so repr(e) at either call site
carries the real diagnostic.
"""
import io
import os
import sys
import urllib.error

HARNESS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HARNESS_DIR)
import langfuse_client as mod  # noqa: E402


def _http_error(code, reason, body):
    return urllib.error.HTTPError(
        "http://x/api/public/ingestion", code, reason, {}, io.BytesIO(body.encode())
    )


def test_ingest_success_returns_batch_len(monkeypatch):
    class _Resp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(mod.urllib.request, "urlopen", lambda *a, **k: _Resp())
    batch = [{"id": "1"}, {"id": "2"}]
    assert mod.ingest("http://x", "pk", "sk", batch) == 2


def test_ingest_http_error_raises_with_body_detail(monkeypatch):
    err = _http_error(401, "Unauthorized",
                       '{"message":"Invalid credentials","error":"AuthenticationError"}')

    def _raise(*a, **k):
        raise err

    monkeypatch.setattr(mod.urllib.request, "urlopen", _raise)
    try:
        mod.ingest("http://x", "pk-lf-8a4fdeadbeef", "not-a-real-secret-value",
                   [{"id": "1"}])
        assert False, "expected LangfuseIngestError"
    except mod.LangfuseIngestError as e:
        msg = str(e)
        assert "401" in msg
        assert "Invalid credentials" in msg
        assert "pk-lf-8a4f" in msg  # public key prefix kept — it identifies the caller
        assert "not-a-real-secret-value" not in msg  # secret never appears in the message


def test_ingest_http_error_body_is_masked(monkeypatch):
    secret = "AKIA" + "Q" * 16
    err = _http_error(401, "Unauthorized", f"token leaked in body: {secret}")

    def _raise(*a, **k):
        raise err

    monkeypatch.setattr(mod.urllib.request, "urlopen", _raise)
    try:
        mod.ingest("http://x", "pk", "sk", [{"id": "1"}])
        assert False, "expected LangfuseIngestError"
    except mod.LangfuseIngestError as e:
        assert secret not in str(e)
