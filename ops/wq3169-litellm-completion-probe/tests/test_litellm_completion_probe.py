import importlib.util
import io
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


def test_read_service_key_strips_without_logging(tmp_path, capsys):
    keyfile = tmp_path / "model-watchdog.key"
    fake = "s" "k-" + "Q" * 16
    keyfile.write_text(fake + "\n")
    assert mod.read_service_key(str(keyfile)) == fake
    assert fake not in capsys.readouterr().out


# the live 2026-10-07 22:06 body from master_key_guard.py (aq 2671246 and siblings)
RETIRED_401 = ('{"error":{"message":"master key is retired for non-admin routes '
               '(docs/litellm/service_keys.md, wq#914)."}}')


def _fake_models(monkeypatch, outcome):
    import io
    import urllib.error

    def urlopen(req, timeout):
        assert req.full_url == mod.MODELS_URL
        if isinstance(outcome, int):
            raise urllib.error.HTTPError(req.full_url, outcome, "x", {}, io.BytesIO(RETIRED_401.encode()))
        if outcome is not None:
            raise outcome
        return io.BytesIO(b"{}")
    monkeypatch.setattr(mod.urllib.request, "urlopen", urlopen)


def test_check_probe_key_reports_rejection_once(tmp_path, monkeypatch):
    keyfile = tmp_path / "k"
    keyfile.write_text("Q" * 16)
    _fake_models(monkeypatch, 401)
    key, fail = mod.check_probe_key(str(keyfile))
    assert key is None and fail["host"] == mod.PROBE_KEY_HOST and fail["kind"] == "auth"
    assert "master key is retired" in fail["reason"] and "Q" * 16 not in fail["reason"]
    _fake_models(monkeypatch, None)
    assert mod.check_probe_key(str(keyfile)) == ("Q" * 16, None)
    # gateway down or 5xx is not a key problem: the host probes report it
    _fake_models(monkeypatch, 502)
    assert mod.check_probe_key(str(keyfile))[1] is None
    _fake_models(monkeypatch, ConnectionRefusedError())
    assert mod.check_probe_key(str(keyfile))[1] is None
    key, fail = mod.check_probe_key(str(tmp_path / "missing.key"))
    assert key is None and "unreadable" in fail["reason"]


def test_rejected_key_files_one_row_and_leaves_host_rows_alone(tmp_path, monkeypatch):
    """wq#3736: 12 per-host 'unreachable' rows were all the one retired-key 401."""
    keyfile = tmp_path / "k"
    keyfile.write_text("Q" * 16)
    prev = {"nephthys": {"failed": True, "checked_at": 5}, "cerebras": {"failed": False, "checked_at": 5}}
    (tmp_path / "state.json").write_text(json.dumps(prev))
    monkeypatch.setattr(mod, "KEY_PATH", str(keyfile))
    monkeypatch.setattr(mod, "one_rung_per_host", lambda p: [mod.Rung("nephthys", "q"), mod.Rung("cerebras", "c")])
    monkeypatch.setattr(mod, "run_probes", lambda k, r: (_ for _ in ()).throw(AssertionError("probed")))
    monkeypatch.setattr(sys, "argv", ["probe"])
    _fake_models(monkeypatch, 401)
    log = _capture(monkeypatch, tmp_path)
    mod.main()
    assert log["add"] == [("probe-key", "LiteLLM completion probe: gateway rejected the probe's own key")]
    assert log["resolve"] == [] and len(log["notify"]) == 1
    state = json.loads((tmp_path / "state.json").read_text())
    assert state["nephthys"] == prev["nephthys"] and state["cerebras"] == prev["cerebras"]

    # key accepted again: probe-key row closes, hosts are probed as usual
    _fake_models(monkeypatch, None)
    monkeypatch.setattr(mod, "run_probes", lambda k, rungs: [
        {"host": r.host, "model": r.model_name, "ok": True, "kind": "ok", "elapsed_s": 1.0, "reason": ""}
        for r in rungs])
    log = _capture(monkeypatch, tmp_path)
    mod.main()
    assert log["add"] == [] and sorted(log["resolve"]) == ["cerebras", "nephthys", "probe-key"]


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


def test_metered_rung_never_sends_a_completion(monkeypatch):
    """wq#4692: 1318 calls / $0.1216 in 7d to lumen/claude-sonnet alone came from
    probe_one POSTing a real chat completion to a metered rung every cycle.
    A metered Rung must hit GET /v1/models only — this test fails (asserts
    AssertionError, not a probe result) the moment probe_one regresses to
    POSTing GATEWAY_URL for a metered rung."""
    calls = []

    def urlopen(req, timeout):
        calls.append(req.full_url)
        assert req.full_url != mod.GATEWAY_URL, "metered rung must not be sent a real completion"
        assert req.full_url == mod.MODELS_URL
        return io.BytesIO(json.dumps({"data": [{"id": "lumen/claude-sonnet"}]}).encode())

    monkeypatch.setattr(mod.urllib.request, "urlopen", urlopen)
    rung = mod.Rung("anthropic", "lumen/claude-sonnet", 30.0, True)
    result = mod.probe_one("k", rung)
    assert calls == [mod.MODELS_URL]
    assert result == {"host": "anthropic", "model": "lumen/claude-sonnet", "ok": True,
                       "kind": "ok", "elapsed_s": result["elapsed_s"], "reason": ""}


def test_metered_rung_absent_from_models_is_a_fail(monkeypatch):
    def urlopen(req, timeout):
        return io.BytesIO(json.dumps({"data": [{"id": "some-other-model"}]}).encode())

    monkeypatch.setattr(mod.urllib.request, "urlopen", urlopen)
    rung = mod.Rung("anthropic", "lumen/claude-sonnet", None, True)
    result = mod.probe_one("k", rung)
    assert result["ok"] is False and result["kind"] == "unreachable"
    assert "not listed" in result["reason"]


def test_unmetered_rung_still_sends_a_completion(monkeypatch):
    """Control: a normal rung's probe_one must still POST the real completion —
    the metered branch must not swallow everything."""
    def urlopen(req, timeout):
        assert req.full_url == mod.GATEWAY_URL
        return io.BytesIO(json.dumps({"choices": [{"message": {"content": "pong"}}]}).encode())

    monkeypatch.setattr(mod.urllib.request, "urlopen", urlopen)
    rung = mod.Rung("cerebras.example", "cerebras-llama", 30.0, False)
    result = mod.probe_one("k", rung)
    assert result["ok"] is True


def test_passing_host_resolves_rows_without_a_failed_state_edge(tmp_path, monkeypatch):
    """wq#3463: rows close on any pass, not only on a failed->ok edge, so a
    lost state.json can't strand them."""
    monkeypatch.setattr(mod, "STATE_DIR", tmp_path)
    resolved = []
    monkeypatch.setattr(mod, "_notify", lambda p, t, m: None)
    monkeypatch.setattr(mod, "_aq_resolve", lambda k, w: resolved.append(k))
    mod.apply_results([{"host": "h1", "model": "m1", "ok": True, "elapsed_s": 0.5, "reason": ""}])
    assert resolved == ["h1"]


GEMINI_429 = ('{"error":{"code":429,"message":"You exceeded your current quota ... '
              'limit: 20, model: gemini-2.5-flash\\n Please retry in 10h20m19.507781116s."}}')
DEEPSEEK_402 = '{"error":{"message":"Insufficient Balance (request_id: x)"}}'


def test_classify_and_retry_hint():
    """wq#3587: the bodies are the live aq 3198 / 2652505 failure shapes."""
    assert mod.classify(402, DEEPSEEK_402) == "billing"
    assert mod.classify(400, DEEPSEEK_402) == "billing"   # litellm re-wraps as BadRequest
    assert mod.classify(429, GEMINI_429) == "rate_limited"
    assert mod.classify(None, "timed out") == "unreachable"
    assert mod.classify(500, "boom") == "unreachable"
    assert mod.retry_after_s(GEMINI_429) == 10 * 3600 + 20 * 60 + 19
    assert mod.retry_after_s("Please retry in 30s") == 30
    assert mod.retry_after_s("no hint") == mod.RATE_LIMIT_BACKOFF_S
    assert mod.retry_after_s("retry in 900h") == mod.RATE_LIMIT_BACKOFF_CAP_S


def _capture(monkeypatch, tmp_path, has_open=False):
    monkeypatch.setattr(mod, "STATE_DIR", tmp_path)
    log = {"notify": [], "add": [], "resolve": []}
    monkeypatch.setattr(mod, "_notify", lambda p, t, m: log["notify"].append((p, t)))
    monkeypatch.setattr(mod, "_aq_add", lambda k, t, m: log["add"].append((k, t)))
    monkeypatch.setattr(mod, "_aq_resolve", lambda k, w: log["resolve"].append(k))
    monkeypatch.setattr(mod, "_aq_has_open", lambda k: has_open)
    return log


def test_billing_failure_keeps_one_row_without_bumping(tmp_path, monkeypatch):
    """aq 3198 reached x135 because every 30-min run bumped a row only a
    top-up could clear; sentinel-watch's 100-repeat floor then filed wq#3587."""
    r = {"host": "deepseek", "model": "m", "ok": False, "kind": "billing", "elapsed_s": 0.8, "reason": "402"}
    log = _capture(monkeypatch, tmp_path, has_open=False)
    state = mod.apply_results([r])
    assert log["add"] == [("deepseek", "LiteLLM completion probe: deepseek out of credit")]
    assert log["notify"] == [("urgent", "LiteLLM completion probe: deepseek out of credit")]
    assert state["deepseek"]["failed"] is True

    (tmp_path / "state.json").write_text(json.dumps(state))
    log = _capture(monkeypatch, tmp_path, has_open=True)
    mod.apply_results([r])
    assert log == {"notify": [], "add": [], "resolve": []}

    # row resolved by hand while still out of credit: reopened once, no re-page
    log = _capture(monkeypatch, tmp_path, has_open=False)
    mod.apply_results([r])
    assert [k for k, _ in log["add"]] == ["deepseek"] and log["notify"] == []


def test_unreachable_still_bumps_with_an_open_row(tmp_path, monkeypatch):
    log = _capture(monkeypatch, tmp_path, has_open=True)
    mod.apply_results([{"host": "h1", "model": "m", "ok": False, "kind": "unreachable",
                        "elapsed_s": 60.0, "reason": "timed out"}])
    assert [k for k, _ in log["add"]] == ["h1"]


def test_rate_limited_host_counts_as_answered_and_backs_off(tmp_path, monkeypatch):
    (tmp_path / "state.json").write_text(json.dumps({"gemini": {"failed": True, "checked_at": 1}}))
    log = _capture(monkeypatch, tmp_path)
    state = mod.apply_results([{"host": "gemini", "model": "gemini-flash", "ok": False,
                                "kind": "rate_limited", "retry_s": 37219, "elapsed_s": 12.5, "reason": "429"}])
    assert log["add"] == [] and log["resolve"] == ["gemini"]
    assert log["notify"] == [("low", "LiteLLM completion probe: gemini recovered")]
    assert state["gemini"]["failed"] is False
    assert state["gemini"]["skip_until"] - state["gemini"]["checked_at"] == 37219


def test_due_rungs_honours_backoff_and_min_interval(monkeypatch):
    monkeypatch.setattr(mod, "HOST_MIN_INTERVAL_S", {"gemini": 21600})
    rungs = [mod.Rung("gemini", "gemini-flash"), mod.Rung("deepseek", "ds"), mod.Rung("cerebras", "c")]
    now = 1_000_000
    state = {"gemini": {"checked_at": now - 1800},                       # inside 6h interval
             "deepseek": {"checked_at": now - 1800, "skip_until": now + 60},  # 429 backoff
             "cerebras": {"checked_at": now - 1800}}
    due, skipped = mod.due_rungs(rungs, state, now)
    assert [r.host for r in due] == ["cerebras"] and skipped == {"gemini", "deepseek"}
    state["gemini"]["checked_at"] = now - 21600
    state["deepseek"]["skip_until"] = now
    due, skipped = mod.due_rungs(rungs, state, now)
    assert [r.host for r in due] == ["gemini", "deepseek", "cerebras"] and skipped == set()
    # a never-probed host is always due
    assert mod.due_rungs(rungs[:1], {}, now) == ([rungs[0]], set())


def test_skipped_failed_host_keeps_state_and_its_row(tmp_path, monkeypatch):
    """A failed host merely skipped this run must not be mistaken for a rung
    removed from the config (wq#3212 path) and auto-resolved."""
    prev = {"gemini": {"failed": True, "checked_at": 5}}
    (tmp_path / "state.json").write_text(json.dumps(prev))
    log = _capture(monkeypatch, tmp_path)
    state = mod.apply_results([], skipped={"gemini"})
    assert log["resolve"] == [] and state == prev


def test_default_host_intervals_parse():
    assert mod.HOST_MIN_INTERVAL_S == {"gemini": 21600}


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


def test_aq_has_open_sees_open_and_acked_rows_for_its_key(tmp_path, monkeypatch):
    import sqlite3
    import types
    dbp = tmp_path / "alerts.db"
    c = sqlite3.connect(dbp)
    c.execute("CREATE TABLE alerts (id INTEGER PRIMARY KEY, source TEXT, key TEXT, status TEXT)")
    c.executemany("INSERT INTO alerts (source, key, status) VALUES (?,?,?)", [
        ("litellm-completion", "a", "open"), ("litellm-completion", "b", "acked"),
        ("litellm-completion", "c", "resolved"), ("other-source", "d", "open")])
    c.commit()
    c.close()
    monkeypatch.setitem(sys.modules, "aq", types.SimpleNamespace(db=lambda: sqlite3.connect(dbp)))
    assert [mod._aq_has_open(k) for k in "abcd"] == [True, True, False, False]
    monkeypatch.setitem(sys.modules, "aq", types.SimpleNamespace(db=lambda: 1 / 0))
    assert mod._aq_has_open("a") is False  # unreadable db -> caller falls back to aq add
