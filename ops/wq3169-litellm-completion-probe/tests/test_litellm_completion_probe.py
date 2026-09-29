import importlib.util
import json
import pathlib
import sys
from importlib.machinery import SourceFileLoader

HERE = pathlib.Path(__file__).resolve().parent.parent
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


def test_apply_results_is_edge_triggered(tmp_path, monkeypatch):
    monkeypatch.setattr(mod, "STATE_DIR", tmp_path)
    notified = []
    aq_added = []
    monkeypatch.setattr(mod, "_notify", lambda p, t, m: notified.append((p, t)))
    monkeypatch.setattr(mod, "_aq_add", lambda k, t, m: aq_added.append(k))

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
