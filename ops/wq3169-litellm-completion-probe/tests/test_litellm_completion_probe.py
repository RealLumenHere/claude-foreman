import importlib.util
import json
import pathlib
import sys
import time
from importlib.machinery import SourceFileLoader

HERE = pathlib.Path(__file__).resolve().parent.parent
# wq#3212: the probe imports the shared litellm_rungs module (deployed next to
# it in /home/lumen/bin); in-repo it lives in ops/wq3212-litellm-dead-rungs/.
sys.path.insert(0, str(HERE.parent / "wq3212-litellm-dead-rungs"))
loader = SourceFileLoader("litellm_completion_probe", str(HERE / "litellm-completion-probe"))
spec = importlib.util.spec_from_loader(loader.name, loader)
mod = importlib.util.module_from_spec(spec)
sys.modules["litellm_completion_probe"] = mod
loader.exec_module(mod)


def test_one_rung_per_host_dedupes_and_skips_non_chat(tmp_path):
    cfg = tmp_path / "litellm_config.yaml"
    cfg.write_text(
        """
model_list:
  - model_name: cerebras-llama
    litellm_params: {model: cerebras/llama, api_base: "https://cerebras.example"}
  - model_name: cerebras-oss
    litellm_params: {model: cerebras/oss, api_base: "https://cerebras.example"}
  - model_name: local-embed
    litellm_params: {model: ollama/embed-model, api_base: "http://host.docker.internal:11434"}
  - model_name: local-chat
    litellm_params: {model: ollama/chat-model, api_base: "http://host.docker.internal:11434"}
"""
    )
    rungs = mod.one_rung_per_host(str(cfg))
    hosts = [r.host for r in rungs]
    assert hosts == ["cerebras.example", "host.docker.internal:11434"]
    # first model per host wins; the embed deployment on that host is skipped
    # entirely, so the chat deployment on the same host is what's probed
    local = [r for r in rungs if r.host == "host.docker.internal:11434"][0]
    assert local.model_name == "local-chat"


def test_read_master_key_extracts_without_logging(tmp_path, capsys):
    env = tmp_path / ".env"
    env.write_text("OTHER=1\nLITELLM_MASTER_KEY=sk-abc123XYZ\nMORE=2\n")
    key = mod.read_master_key(str(env))
    assert key == "sk-abc123XYZ"
    assert "sk-abc123XYZ" not in capsys.readouterr().out


def test_never_probe_models_are_excluded(tmp_path):
    """wq#3212: a host whose only rungs are NEVER_PROBE (LiteLLM bug #22249,
    messenger passthrough) must not appear at all — before the shared module,
    removing claude-opus-5 would have elected messenger-chat as the anthropic
    host's representative and probed it."""
    cfg = tmp_path / "litellm_config.yaml"
    cfg.write_text(
        """
model_list:
  - model_name: messenger-chat
    litellm_params: {model: anthropic/claude-sonnet}
  - model_name: cerebras-llama
    litellm_params: {model: cerebras/llama, api_base: "https://cerebras.example"}
"""
    )
    assert [r.host for r in mod.one_rung_per_host(str(cfg))] == ["cerebras.example"]


def test_apply_results_is_edge_triggered(tmp_path, monkeypatch):
    monkeypatch.setattr(mod, "STATE_DIR", tmp_path)
    notified = []
    aq_added = []
    monkeypatch.setattr(mod, "_notify", lambda p, t, m: notified.append((p, t)))
    monkeypatch.setattr(mod, "_aq_add", lambda k, t, m: aq_added.append(k))
    monkeypatch.setattr(mod, "_aq_resolve", lambda k, w: None)

    breach = [{"host": "h1", "model": "m1", "ok": False, "elapsed_s": 60.0, "reason": "timeout"}]
    state1 = mod.apply_results(breach)
    assert state1["h1"]["failed"] is True
    assert notified == [("urgent", "LiteLLM completion probe: h1 unreachable")]
    assert aq_added == ["h1"]

    (tmp_path / "state.json").write_text(json.dumps(state1))
    notified.clear()
    aq_added.clear()
    state2 = mod.apply_results(breach)
    assert notified == []  # still breached: aq bump, no re-ntfy
    assert aq_added == ["h1"]

    (tmp_path / "state.json").write_text(json.dumps(state2))
    notified.clear()
    aq_added.clear()
    recovered = [{"host": "h1", "model": "m1", "ok": True, "elapsed_s": 0.5, "reason": ""}]
    state3 = mod.apply_results(recovered)
    assert notified == [("low", "LiteLLM completion probe: h1 recovered")]
    assert aq_added == []
    assert state3["h1"]["failed"] is False


def test_read_langfuse_creds_extracts_both_keys_without_logging(tmp_path, capsys):
    env = tmp_path / ".env"
    env.write_text("LANGFUSE_HOST=http://x\nLANGFUSE_PUBLIC_KEY=pk-lf-abc\nLANGFUSE_SECRET_KEY=sk-lf-def\n")
    pk, sk = mod.read_langfuse_creds(str(env))
    assert (pk, sk) == ("pk-lf-abc", "sk-lf-def")
    out = capsys.readouterr().out
    assert "pk-lf-abc" not in out and "sk-lf-def" not in out


def test_read_langfuse_creds_missing_file_returns_none_pair(tmp_path):
    assert mod.read_langfuse_creds(str(tmp_path / "nope.env")) == (None, None)


def test_check_langfuse_trace_skips_ok_when_creds_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(mod, "LANGFUSE_ENV_PATH", str(tmp_path / "nope.env"))
    r = mod.check_langfuse_trace(time.time())
    assert r == {"host": mod.LANGFUSE_TRACE_HOST_KEY, "model": mod.LANGFUSE_TRACE_NAME, "ok": True,
                "elapsed_s": r["elapsed_s"], "reason": "skipped: no langfuse creds"}


def test_check_langfuse_trace_ok_when_trace_found(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text("LANGFUSE_PUBLIC_KEY=pk-lf-a\nLANGFUSE_SECRET_KEY=sk-lf-b\n")
    monkeypatch.setattr(mod, "LANGFUSE_ENV_PATH", str(env))
    monkeypatch.setattr(mod, "LANGFUSE_POLL_TIMEOUT_S", 5)
    monkeypatch.setattr(mod, "LANGFUSE_POLL_INTERVAL_S", 1)

    class R:
        def read(self):
            return b'{"meta": {"totalItems": 1}}'

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(mod.urllib.request, "urlopen", lambda req, timeout=10: R())
    r = mod.check_langfuse_trace(time.time())
    assert r["ok"] is True and r["reason"] == ""


def test_check_langfuse_trace_fails_when_no_trace_within_timeout(tmp_path, monkeypatch):
    """TEST TO FAILURE (wq#3221): a canary with zero matching traces (e.g. the
    LANGFUSE_PUBLIC_KEY/SECRET_KEY self-referential config trap silently poisoning
    the callback's creds) must FAIL this assertion, not pass it."""
    env = tmp_path / ".env"
    env.write_text("LANGFUSE_PUBLIC_KEY=pk-lf-a\nLANGFUSE_SECRET_KEY=sk-lf-b\n")
    monkeypatch.setattr(mod, "LANGFUSE_ENV_PATH", str(env))
    monkeypatch.setattr(mod, "LANGFUSE_POLL_TIMEOUT_S", 2)
    monkeypatch.setattr(mod, "LANGFUSE_POLL_INTERVAL_S", 1)

    class R:
        def read(self):
            return b'{"meta": {"totalItems": 0}}'

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(mod.urllib.request, "urlopen", lambda req, timeout=10: R())
    r = mod.check_langfuse_trace(time.time())
    assert r["ok"] is False
    assert "no 'litellm-acompletion' trace" in r["reason"]


def test_main_appends_langfuse_check_only_when_a_completion_succeeded(tmp_path, monkeypatch):
    """wq#3221: no point polling Langfuse if every completion probe already failed -
    the host-failure alert already covers that, and a Langfuse check would just add
    noise (nothing succeeded to have generated a trace)."""
    monkeypatch.setattr(mod, "STATE_DIR", tmp_path)
    monkeypatch.setattr(mod, "read_master_key", lambda p: "sk-test")
    monkeypatch.setattr(mod, "one_rung_per_host", lambda p: [mod.Rung(host="h1", model_name="m1")])
    calls = {"langfuse": 0}
    monkeypatch.setattr(mod, "_notify", lambda *a: None)
    monkeypatch.setattr(mod, "_aq_add", lambda *a: None)
    monkeypatch.setattr(mod, "_aq_resolve", lambda *a: None)

    def fake_check_langfuse_trace(since_ts):
        calls["langfuse"] += 1
        return {"host": mod.LANGFUSE_TRACE_HOST_KEY, "model": mod.LANGFUSE_TRACE_NAME, "ok": True,
                "elapsed_s": 0.1, "reason": ""}

    monkeypatch.setattr(mod, "check_langfuse_trace", fake_check_langfuse_trace)
    monkeypatch.setattr(mod, "run_probes",
                        lambda key, rungs: [{"host": "h1", "model": "m1", "ok": False,
                                             "elapsed_s": 1.0, "reason": "down"}])
    monkeypatch.setattr(sys, "argv", ["litellm-completion-probe"])
    mod.main()
    assert calls["langfuse"] == 0

    monkeypatch.setattr(mod, "run_probes",
                        lambda key, rungs: [{"host": "h1", "model": "m1", "ok": True,
                                             "elapsed_s": 1.0, "reason": ""}])
    mod.main()
    assert calls["langfuse"] == 1


def test_aq_row_resolved_on_recovery_and_on_leaving_probe_set(tmp_path, monkeypatch):
    """wq#3212: a failing host's aq row must close on recovery AND when the rung
    is removed from litellm_config.yaml (host vanishes from the probe set) —
    before this, aq 2579/2583/2585 could only ever be closed by hand."""
    monkeypatch.setattr(mod, "STATE_DIR", tmp_path)
    resolved = []
    monkeypatch.setattr(mod, "_notify", lambda p, t, m: None)
    monkeypatch.setattr(mod, "_aq_add", lambda k, t, m: None)
    monkeypatch.setattr(mod, "_aq_resolve", lambda k, w: resolved.append(k))

    state = mod.apply_results([
        {"host": "h1", "model": "m1", "ok": False, "elapsed_s": 60.0, "reason": "down"},
        {"host": "h2", "model": "m2", "ok": False, "elapsed_s": 60.0, "reason": "down"},
    ])
    (tmp_path / "state.json").write_text(json.dumps(state))
    assert resolved == []

    # h1 recovers; h2's rung was removed from the config so it is not probed at all
    state2 = mod.apply_results(
        [{"host": "h1", "model": "m1", "ok": True, "elapsed_s": 0.5, "reason": ""}])
    assert sorted(resolved) == ["h1", "h2"]
    assert "h2" not in state2
