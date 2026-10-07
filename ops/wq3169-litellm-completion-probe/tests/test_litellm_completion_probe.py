import importlib.util
import json
import pathlib
import sys
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
    # split at the `=` seam so reading this file arms no kv-secret tripwire (wq#3222)
    env.write_text("OTHER=1\nLITELLM_MASTER_KEY=" + "sk-abc123XYZ\nMORE=2\n")
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


def test_representative_is_fastest_configured_rung(tmp_path):
    """wq#3463: Lumen's host elected gemma4-e4b-local (timeout 600) because it
    came first; lumen-lfm2-1b (timeout 120) on the same host is the rung the
    operator marked fast. Untimed rungs rank last; ties keep config order."""
    cfg = tmp_path / "litellm_config.yaml"
    cfg.write_text(
        """
model_list:
  - model_name: big-local
    litellm_params: {model: ollama/big, api_base: "http://h1:11434", timeout: 600}
  - model_name: small-local
    litellm_params: {model: ollama/small, api_base: "http://h1:11434", timeout: 120}
  - model_name: untimed
    litellm_params: {model: openai/x, api_base: "http://h2:8095/v1"}
  - model_name: timed-a
    litellm_params: {model: openai/y, api_base: "http://h2:8095/v1", timeout: 960}
  - model_name: timed-b
    litellm_params: {model: openai/z, api_base: "http://h2:8095/v1", timeout: 960}
  - model_name: cloud-first
    litellm_params: {model: cloud/a}
  - model_name: cloud-second
    litellm_params: {model: cloud/b}
"""
    )
    rungs = mod.one_rung_per_host(str(cfg))
    assert [(r.host, r.model_name, r.timeout) for r in rungs] == [
        ("h1:11434", "small-local", 120.0),
        ("h2:8095", "timed-a", 960.0),
        ("cloud", "cloud-first", None)]


def test_probe_budget_follows_rung_timeout_within_floor_and_cap(monkeypatch):
    monkeypatch.setattr(mod, "TIMEOUT_S", 60)
    monkeypatch.setattr(mod, "TIMEOUT_CAP_S", 300)
    assert mod.probe_budget_s(mod.Rung("h", "m")) == 60          # untimed -> floor
    assert mod.probe_budget_s(mod.Rung("h", "m", 20.0)) == 60    # never below floor
    assert mod.probe_budget_s(mod.Rung("h", "m", 120.0)) == 120
    assert mod.probe_budget_s(mod.Rung("h", "m", 2460.0)) == 300  # capped


def test_passing_host_resolves_rows_without_a_failed_state_edge(tmp_path, monkeypatch):
    """wq#3463: rows close on any pass, not only on a failed->ok edge, so a
    lost state.json can't strand them."""
    monkeypatch.setattr(mod, "STATE_DIR", tmp_path)
    resolved = []
    monkeypatch.setattr(mod, "_notify", lambda p, t, m: None)
    monkeypatch.setattr(mod, "_aq_resolve", lambda k, w: resolved.append(k))
    mod.apply_results([{"host": "h1", "model": "m1", "ok": True, "elapsed_s": 0.5, "reason": ""}])
    assert resolved == ["h1"]


def test_aq_resolve_closes_open_and_acked_rows_only_for_its_key(tmp_path, monkeypatch):
    """wq#3463: aq 3140/1125736/1137450 were acked, still counted by
    sentinel-watch, and the next failure forked a fresh row beside them."""
    import sqlite3
    import types
    dbp = tmp_path / "alerts.db"
    c = sqlite3.connect(dbp)
    c.execute("CREATE TABLE alerts (id INTEGER PRIMARY KEY, source TEXT, key TEXT, "
              "status TEXT, note TEXT, resolved_ts TEXT)")
    c.executemany("INSERT INTO alerts (id, source, key, status) VALUES (?,?,?,?)", [
        (1, "litellm-completion", "h1", "open"),
        (2, "litellm-completion", "h1", "acked"),
        (3, "litellm-completion", "h1", "resolved"),
        (4, "litellm-completion", "h2", "open"),
        (5, "other-source", "h1", "acked"),
    ])
    c.commit()
    c.close()
    fake = types.SimpleNamespace(db=lambda: sqlite3.connect(dbp), now=lambda: "T")
    monkeypatch.setitem(sys.modules, "aq", fake)
    mod._aq_resolve("h1", "h1 answered")
    rows = dict(sqlite3.connect(dbp).execute("SELECT id, status FROM alerts"))
    assert rows == {1: "resolved", 2: "resolved", 3: "resolved", 4: "open", 5: "acked"}
