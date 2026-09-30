"""model-watchdog roster gate (wq#2903): a rostered name the gateway doesn't serve is
CONFIG DRIFT (no probe, one aq row), never a DOWN verdict."""
import builtins, importlib.util, io, json, os

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(os.path.dirname(HERE), "lumen_model_watchdog.py")
_real_open = builtins.open


def _fake_open(path, *a, **kw):
    if str(path).startswith("/home/lumen/.secrets/"):   # import-time gatus token read
        return io.StringIO("GATUS_EXTERNAL_TOKEN=" + "x" * 4 + "\n")
    return _real_open(path, *a, **kw)


builtins.open = _fake_open
try:
    spec = importlib.util.spec_from_file_location("wd", SRC)
    wd = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(wd)
finally:
    builtins.open = _real_open


def test_mistral_small_off_roster():
    assert "mistral-small" not in wd.CLOUD + wd.LOCAL


def test_split_drift():
    assert wd.split_drift(["a", "b", "c"], {"a", "c", "z"}) == (["a", "c"], ["b"])
    assert wd.split_drift(["a", "b"], None) == (["a", "b"], [])   # list unreadable


class Harness:
    def __init__(self, monkeypatch, tmp_path, live, roster, prev=None, groups=None):
        self.probed, self.events, self.cleared, self.drifts = [], [], [], []
        wd.STATE = tmp_path / "state.json"
        if prev is not None:
            wd.STATE.write_text(json.dumps(prev))
        # wq#3212: pin the config-derived host groups (never the live config)
        monkeypatch.setattr(wd, "config_host_groups", lambda: groups)
        monkeypatch.setattr(wd, "CLOUD", roster)
        monkeypatch.setattr(wd, "LOCAL", [])
        monkeypatch.setattr(wd, "SLOW", {})
        monkeypatch.setattr(wd, "_key", lambda: "k")
        monkeypatch.setattr(wd, "live_models", lambda key: live)
        monkeypatch.setattr(wd, "probe", lambda m, k: (self.probed.append(m) or (True, "")))
        monkeypatch.setattr(wd, "push_gatus", lambda *a, **kw: None)
        monkeypatch.setattr(wd, "aq_event", lambda m, *a: self.events.append(m))
        monkeypatch.setattr(wd, "aq_clear", lambda m, why="recovered", key=None:
                            self.cleared.append(key or f"model-{m}"))
        monkeypatch.setattr(wd, "aq_drift", lambda d: self.drifts.append(list(d)))

    def state(self):
        return json.loads(wd.STATE.read_text())


def test_absent_name_is_drift_not_down(monkeypatch, tmp_path, capsys):
    h = Harness(monkeypatch, tmp_path, live={"cerebras-llama"},
                roster=["cerebras-llama", "gone-rung"])
    wd.run()
    assert h.probed == ["cerebras-llama"]
    assert h.events == []                          # no DOWN row
    assert h.drifts == [["gone-rung"]]
    st = h.state()
    assert "gone-rung" not in st["up"] and st["drift"] == ["gone-rung"]
    out = capsys.readouterr().out
    assert "down=none" in out and "drift=['gone-rung']" in out


def test_drift_row_raised_once_then_cleared(monkeypatch, tmp_path):
    h = Harness(monkeypatch, tmp_path, live={"a"}, roster=["a", "gone"])
    wd.run(); wd.run()
    assert h.drifts == [["gone"]]                  # unchanged set: no repeat
    monkeypatch.setattr(wd, "live_models", lambda key: {"a", "gone"})
    wd.run()
    assert wd.DRIFT_KEY in h.cleared and h.state()["drift"] == []


def test_deployed_state_resolves_stale_mistral_row(monkeypatch, tmp_path):
    prev = {"up": {"cerebras-llama": True, "mistral-small": False}, "last_probe": {}}
    h = Harness(monkeypatch, tmp_path, live={"cerebras-llama"},
                roster=["cerebras-llama"], prev=prev)
    wd.run()
    assert "model-mistral-small" in h.cleared
    assert "mistral-small" not in h.state()["up"]


def test_unreadable_model_list_probes_full_roster(monkeypatch, tmp_path):
    h = Harness(monkeypatch, tmp_path, live=None, roster=["a", "b"])
    wd.run()
    assert h.probed == ["a", "b"] and h.drifts == []


def test_uncovered_config_host_joins_roster(monkeypatch, tmp_path):
    """wq#3212: a config host with no curated model on it must be probed (first
    eligible model as representative) — this is the exact hole that let
    `probed=6 down=none` coexist with 3 dead config rungs. A host already
    covered by a curated name adds nothing (no double probe)."""
    groups = {"cerebras.example": ["cerebras-llama", "cerebras-oss-120b"],
              "host.docker.internal:8091": ["claude-max-sonnet", "claude-max-opus"]}
    h = Harness(monkeypatch, tmp_path,
                live={"cerebras-llama", "claude-max-sonnet"},
                roster=["cerebras-llama"], groups=groups)
    wd.run()
    assert h.probed == ["cerebras-llama", "claude-max-sonnet"]


def test_config_derived_extra_down_pages_via_aq(monkeypatch, tmp_path):
    groups = {"host.docker.internal:8000": ["kimik3-0.4b-local"]}
    h = Harness(monkeypatch, tmp_path, live={"a", "kimik3-0.4b-local"},
                roster=["a"], groups=groups)
    monkeypatch.setattr(wd, "probe",
                        lambda m, k: (h.probed.append(m) or (m == "a", "dead")))
    monkeypatch.setattr(wd.time, "sleep", lambda s: None)  # skip the retry backoff
    wd.run()
    assert "kimik3-0.4b-local" in h.events
