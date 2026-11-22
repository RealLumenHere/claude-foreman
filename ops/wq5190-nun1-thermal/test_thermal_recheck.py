"""wq#5190: idle predicate of the nun1 thermal recheck runner."""
import importlib.util
import json
import os

_spec = importlib.util.spec_from_file_location(
    "thermal_recheck", os.path.join(os.path.dirname(__file__), "thermal_recheck.py"))
tr = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(tr)

S = tr.SEP


def out(busy="0\n", sensors="Tctl:         +61.2\u00b0C\nedge:         +55.0\u00b0C\n", models=()):
    ps = json.dumps({"models": [{"name": m} for m in models]})
    return f"{busy}{S}\n{sensors}{S}\n{ps}"


def test_parse_full_probe():
    p = tr.parse_probe(out(busy="2\n", models=["qwen3:14b"]))
    assert p == {"gpu_busy": 2, "tctl": 61.2, "models": ["qwen3:14b"]}


def test_resident_model_with_idle_gpu_is_idle():
    # The wq#5126 defect: a resident (keep_alive) model read as busy forever.
    assert tr.classify(False, tr.parse_probe(out(busy="1\n", models=["qwen3:14b"]))) == (True, "idle_gpu")


def test_gpu_load_is_busy():
    assert tr.classify(False, tr.parse_probe(out(busy="97\n"))) == (False, "gpu_busy")


def test_unreachable_is_not_idle():
    # The other wq#5126 defect: ssh failure -> empty output -> counted idle.
    assert tr.parse_probe("") is None
    assert tr.classify(False, None) == (False, "nun1_unreachable")


def test_local_bench_beats_everything():
    assert tr.classify(True, tr.parse_probe(out())) == (False, "local_bench_process")


def test_no_gpu_counter_falls_back_to_model_list():
    busy = tr.parse_probe(out(busy="", models=["qwen2.5-coder:7b"]))
    assert busy["gpu_busy"] is None
    assert tr.classify(False, busy) == (False, "models_resident_no_gpu_counter")
    assert tr.classify(False, tr.parse_probe(out(busy=""))) == (True, "idle_no_models")


def test_ollama_down_without_gpu_counter_is_not_idle():
    p = tr.parse_probe(f"{S}\n{S}\n")
    assert tr.classify(False, p) == (False, "ollama_unreadable")


def test_give_up_line_carries_reason_tally(tmp_path, monkeypatch):
    log = tmp_path / "v.log"
    monkeypatch.setattr(tr, "sample", lambda host: (False, None))
    rc = tr.main(["--log", str(log), "--deadline-hours", "0.0003", "--interval", "0"])
    text = log.read_text()
    assert rc == 3
    assert "GAVE UP" in text and "nun1_unreachable" in text
    assert "THERMAL RECHECK: nun1 idle" not in text
    assert (tmp_path / "v.log.trace").read_text().count("nun1_unreachable") >= 1


def test_idle_streak_reaches_validator(tmp_path, monkeypatch):
    log = tmp_path / "v.log"
    monkeypatch.setattr(tr, "sample", lambda host: (False, tr.parse_probe(out(models=["m"]))))
    called = {}
    monkeypatch.setattr(tr.os, "execvp", lambda f, args: called.setdefault("args", args))
    tr.main(["--log", str(log), "--idle-minutes", "3", "--interval", "0",
             "--validator", "/x/validate.py", "--lock", "/x/l"])
    assert "THERMAL RECHECK: nun1 idle 3 min, starting" in log.read_text()
    assert called["args"][-1] == "/x/validate.py"


def test_multi_card_takes_max():
    assert tr.parse_probe(out(busy="0\n44\n"))["gpu_busy"] == 44
