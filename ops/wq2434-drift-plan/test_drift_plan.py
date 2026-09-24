"""Tests for the wq#2434 drift-plan generator and the dashboard route patcher."""
import datetime, importlib.util, json, os, subprocess, sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))


def _load(name):
    spec = importlib.util.spec_from_file_location(name, os.path.join(HERE, f"{name}.py"))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


dp = _load("drift_plan")
pd = _load("patch_dashboard")

CONFIG_YAML = """\
model_list:
- model_name: cerebras-oss-120b
  litellm_params: {model: cerebras/gpt-oss-120b}
  model_info: {mode: chat}
- model_name: lumen-embed
  litellm_params: {model: ollama/nomic-embed-text}
  model_info:
    mode: embedding
- model_name: deepseek-v4-pro
  litellm_params: {model: deepseek/deepseek-chat}
- model_name: cerebras-oss-120b
  litellm_params: {model: duplicate/ignored}
"""


@pytest.fixture
def config(tmp_path):
    p = tmp_path / "litellm_config.yaml"
    p.write_text(CONFIG_YAML)
    return str(p)


@pytest.fixture
def out_dir(tmp_path):
    d = tmp_path / "drift_plans"
    d.mkdir()
    return str(d)


def gen(config, out_dir, overrides=None, now=None, tmp_path=None):
    path = ""
    if overrides is not None:
        path = str(tmp_path / "overrides.json")
        (tmp_path / "overrides.json").write_text(json.dumps(overrides))
    return dp.generate(config, out_dir, path, now=now)


# ---- rung enumeration -------------------------------------------------------

def test_rungs_are_read_in_order_and_deduped(config):
    assert dp.load_rungs(config) == [
        ("cerebras-oss-120b", "chat"),
        ("lumen-embed", "rag"),
        ("deepseek-v4-pro", "chat"),
    ]


def test_embedding_mode_classifies_as_rag_and_missing_model_info_as_chat(config):
    types = dict(dp.load_rungs(config))
    assert types["lumen-embed"] == "rag"
    assert types["deepseek-v4-pro"] == "chat"


def test_missing_model_list_is_an_error(tmp_path):
    p = tmp_path / "bad.yaml"
    p.write_text("not_a_model_list: 1\n")
    with pytest.raises(dp.PlanError):
        dp.load_rungs(str(p))


# ---- draft shape ------------------------------------------------------------

def test_plans_are_born_draft_with_every_tunable_null(config, out_dir, tmp_path):
    plans, _, _ = gen(config, out_dir, tmp_path=tmp_path)
    for plan in plans:
        assert plan["status"] == dp.DRAFT
        assert plan["schema_version"] == dp.SCHEMA_VERSION
        for field in list(dp.CALIBRATION_FIELDS) + list(dp.SIGNOFF_FIELDS):
            assert plan[field] is None, field
        for metric in plan["metrics"].values():
            assert metric["threshold"] is None


def test_metric_block_matches_system_type(config, out_dir, tmp_path):
    plans, _, _ = gen(config, out_dir, tmp_path=tmp_path)
    by_rung = {p["rung"]: p for p in plans}
    chat = by_rung["cerebras-oss-120b"]["metrics"]
    rag = by_rung["lumen-embed"]["metrics"]
    assert "retrieval_recall" not in chat
    assert {"retrieval_recall", "grounding_rate", "corpus_update_lag_h"} <= set(rag)
    assert "latency_p95_s" in chat and "latency_p95_s" in rag
    assert "unauthorized_tool_effects" not in chat


def test_every_metric_declares_a_real_source_or_is_flagged(config, out_dir, tmp_path):
    plans, _, _ = gen(config, out_dir,
                      {"deepseek-v4-pro": {"system_type": "tool-agent"}},
                      tmp_path=tmp_path)
    agent = next(p for p in plans if p["rung"] == "deepseek-v4-pro")
    unauth = agent["metrics"]["unauthorized_tool_effects"]
    assert unauth["threshold"] is None
    assert unauth["no_telemetry_source"] is True
    assert unauth["source"] is None
    for name, metric in agent["metrics"].items():
        if name != "unauthorized_tool_effects":
            assert metric["source"] and "no_telemetry_source" not in metric


def test_metrics_carry_declared_bounds(config, out_dir, tmp_path):
    plans, _, _ = gen(config, out_dir, tmp_path=tmp_path)
    latency = plans[0]["metrics"]["latency_p95_s"]
    assert (latency["min"], latency["max"]) == dp.METRICS["latency_p95_s"]["bounds"]


# ---- overrides --------------------------------------------------------------

FULL = {
    "daily_volume": 1200,
    "eval_sampling_pct": 5.0,
    "baseline_days": 14,
    "observation_window": 30,
    "min_sample_target": 384,
    "reviewer": "frank",
    "rollback_target": "deepseek-v4-pro",
    "metrics": {"latency_p95_s": 12.5, "task_success_rate": 0.75},
}


def test_full_calibration_promotes_to_calibrated(config, out_dir, tmp_path):
    plans, _, index = gen(config, out_dir, {"cerebras-oss-120b": FULL}, tmp_path=tmp_path)
    plan = next(p for p in plans if p["rung"] == "cerebras-oss-120b")
    assert plan["status"] == dp.CALIBRATED
    assert plan["reviewer"] == "frank"
    assert plan["metrics"]["latency_p95_s"]["threshold"] == 12.5
    assert index["counts"]["calibrated"] == 1
    assert index["counts"]["draft"] == 2


def test_partial_calibration_stays_draft(config, out_dir, tmp_path):
    partial = {k: v for k, v in FULL.items() if k != "reviewer"}
    plans, _, _ = gen(config, out_dir, {"cerebras-oss-120b": partial}, tmp_path=tmp_path)
    assert next(p for p in plans if p["rung"] == "cerebras-oss-120b")["status"] == dp.DRAFT


@pytest.mark.parametrize("bad, needle", [
    ({"metrics": {"task_success_rate": 1.5}}, "outside declared bounds"),
    ({"metrics": {"task_success_rate": -0.1}}, "outside declared bounds"),
    ({"metrics": {"no_such_metric": 1}}, "unknown metric"),
    ({"metrics": {"retrieval_recall": 0.9}}, "does not apply"),
    ({"eval_sampling_pct": 101}, "outside declared bounds"),
    ({"baseline_days": 0}, "outside declared bounds"),
    ({"observation_window": 2.5}, "whole number"),
    ({"daily_volume": "lots"}, "must be a number"),
    ({"reviewer": ""}, "non-empty string"),
    ({"system_type": "oracle"}, "system_type"),
    ({"who_knows": 1}, "unknown field"),
    ({"rollback_target": "not-a-rung"}, "not a known rung"),
])
def test_bad_override_is_refused(config, out_dir, tmp_path, bad, needle):
    with pytest.raises(dp.PlanError) as e:
        gen(config, out_dir, {"cerebras-oss-120b": bad}, tmp_path=tmp_path)
    assert needle in str(e.value)


def test_threshold_on_metric_without_telemetry_is_refused(config, out_dir, tmp_path):
    with pytest.raises(dp.PlanError) as e:
        gen(config, out_dir,
            {"deepseek-v4-pro": {"system_type": "tool-agent",
                                 "metrics": {"unauthorized_tool_effects": 3}}},
            tmp_path=tmp_path)
    assert "no telemetry source" in str(e.value)


def test_override_for_unknown_rung_is_refused(config, out_dir, tmp_path):
    with pytest.raises(dp.PlanError) as e:
        gen(config, out_dir, {"ghost-rung": {"reviewer": "frank"}}, tmp_path=tmp_path)
    assert "no such rung" in str(e.value)


def test_underscore_keys_are_comments_not_rungs(config, out_dir, tmp_path):
    plans, _, _ = gen(config, out_dir,
                      {"_comment": "notes", "cerebras-oss-120b": {"reviewer": "frank"}},
                      tmp_path=tmp_path)
    assert next(p for p in plans if p["rung"] == "cerebras-oss-120b")["reviewer"] == "frank"


def test_shipped_example_overrides_file_validates(config, out_dir):
    example = os.path.join(HERE, "drift_plan_overrides.example.json")
    plans, _, _ = dp.generate(config, out_dir, example)
    calibrated = next(p for p in plans if p["rung"] == "cerebras-oss-120b")
    assert calibrated["status"] == dp.CALIBRATED


def test_unreadable_overrides_file_is_refused(config, out_dir, tmp_path):
    bad = tmp_path / "overrides.json"
    bad.write_text("{not json")
    with pytest.raises(dp.PlanError):
        dp.generate(config, out_dir, str(bad))


def test_missing_overrides_file_is_fine(config, out_dir, tmp_path):
    plans, _, _ = dp.generate(config, out_dir, str(tmp_path / "absent.json"))
    assert len(plans) == 3


# ---- regeneration merge -----------------------------------------------------

def test_regeneration_preserves_human_calibration(config, out_dir, tmp_path):
    assert dp.main(["--config", config, "--out-dir", out_dir,
                    "--overrides", str(tmp_path / "none.json")]) == 0
    path = os.path.join(out_dir, "cerebras-oss-120b.json")
    plan = json.loads(open(path).read())
    plan.update({"reviewer": "frank", "rollback_target": "deepseek-v4-pro",
                 "baseline_days": 7})
    plan["metrics"]["latency_p95_s"]["threshold"] = 9.0
    open(path, "w").write(json.dumps(plan))

    assert dp.main(["--config", config, "--out-dir", out_dir,
                    "--overrides", str(tmp_path / "none.json")]) == 0
    after = json.loads(open(path).read())
    assert after["reviewer"] == "frank"
    assert after["rollback_target"] == "deepseek-v4-pro"
    assert after["baseline_days"] == 7
    assert after["metrics"]["latency_p95_s"]["threshold"] == 9.0


def test_first_generated_at_is_the_observation_clock(config, out_dir, tmp_path):
    t0 = datetime.datetime(2026, 1, 1, tzinfo=datetime.timezone.utc)
    plans, _, _ = gen(config, out_dir, now=t0, tmp_path=tmp_path)
    dp.write_json(os.path.join(out_dir, "cerebras-oss-120b.json"), plans[0])
    later = t0 + datetime.timedelta(days=99)
    plans2, _, _ = gen(config, out_dir, now=later, tmp_path=tmp_path)
    again = next(p for p in plans2 if p["rung"] == "cerebras-oss-120b")
    assert again["first_generated_at"] == dp._iso(t0)
    assert again["generated_at"] == dp._iso(later)


def test_overrides_beat_the_previous_file(config, out_dir, tmp_path):
    path = os.path.join(out_dir, "cerebras-oss-120b.json")
    plans, _, _ = gen(config, out_dir, tmp_path=tmp_path)
    stale = next(p for p in plans if p["rung"] == "cerebras-oss-120b")
    stale["reviewer"] = "stale-owner"
    dp.write_json(path, stale)
    plans2, _, _ = gen(config, out_dir, {"cerebras-oss-120b": {"reviewer": "frank"}},
                       tmp_path=tmp_path)
    assert next(p for p in plans2 if p["rung"] == "cerebras-oss-120b")["reviewer"] == "frank"


def test_generator_owned_fields_are_not_inherited_from_a_stale_file(config, out_dir,
                                                                    tmp_path):
    path = os.path.join(out_dir, "cerebras-oss-120b.json")
    plans, _, _ = gen(config, out_dir, tmp_path=tmp_path)
    stale = next(p for p in plans if p["rung"] == "cerebras-oss-120b")
    stale["status"] = dp.CALIBRATED
    stale["schema_version"] = 99
    dp.write_json(path, stale)
    plans2, _, _ = gen(config, out_dir, tmp_path=tmp_path)
    fresh = next(p for p in plans2 if p["rung"] == "cerebras-oss-120b")
    assert fresh["status"] == dp.DRAFT
    assert fresh["schema_version"] == dp.SCHEMA_VERSION


def test_corrupt_existing_plan_is_ignored_not_fatal(config, out_dir, tmp_path):
    open(os.path.join(out_dir, "cerebras-oss-120b.json"), "w").write("{oops")
    plans, _, _ = gen(config, out_dir, tmp_path=tmp_path)
    assert len(plans) == 3


def test_tool_agent_override_survives_regeneration(config, out_dir, tmp_path):
    plans, _, _ = gen(config, out_dir, {"deepseek-v4-pro": {"system_type": "tool-agent"}},
                      tmp_path=tmp_path)
    agent = next(p for p in plans if p["rung"] == "deepseek-v4-pro")
    dp.write_json(os.path.join(out_dir, "deepseek-v4-pro.json"), agent)
    plans2, _, _ = gen(config, out_dir, tmp_path=tmp_path)
    again = next(p for p in plans2 if p["rung"] == "deepseek-v4-pro")
    assert again["system_type"] == "tool-agent"
    assert "unauthorized_tool_effects" in again["metrics"]


# ---- unsafe names -----------------------------------------------------------

@pytest.mark.parametrize("name", ["../escape", "a/b", ".hidden", "with space", "-lead"])
def test_unsafe_rung_names_are_skipped_never_written(tmp_path, out_dir, name):
    cfg = tmp_path / "c.yaml"
    cfg.write_text(json.dumps({"model_list": [{"model_name": name},
                                              {"model_name": "ok-rung"}]}))
    plans, skipped, index = dp.generate(str(cfg), out_dir, "")
    assert [p["rung"] for p in plans] == ["ok-rung"]
    assert skipped == [{"rung": name, "reason": "unsafe_rung_name"}]
    assert index["counts"]["skipped"] == 1


def test_unsafe_name_does_not_fail_the_run(tmp_path, out_dir, capsys):
    cfg = tmp_path / "c.yaml"
    cfg.write_text(json.dumps({"model_list": [{"model_name": "../evil"}]}))
    # exit 0: a skipped rung is a finding, not a broken timer (FLEET_SERVICE_ATLAS
    # finding 3 — guards that exit 1 to signal a finding pollute `systemctl --failed`).
    assert dp.main(["--config", str(cfg), "--out-dir", out_dir, "--overrides", ""]) == 0
    assert "skipped rung" in capsys.readouterr().err
    assert os.listdir(out_dir) == ["index.json"]


# ---- index ------------------------------------------------------------------

def test_index_lists_every_plan_with_its_file(config, out_dir, tmp_path):
    plans, _, index = gen(config, out_dir, tmp_path=tmp_path)
    assert index["counts"]["total"] == len(plans) == 3
    assert {e["file"] for e in index["plans"]} == {f"{p['rung']}.json" for p in plans}
    assert index["plans"][0]["signoff_overdue"] is False


def test_signoff_overdue_once_the_window_elapses(config, out_dir, tmp_path):
    t0 = datetime.datetime(2026, 1, 1, tzinfo=datetime.timezone.utc)
    plans, _, _ = gen(config, out_dir, {"cerebras-oss-120b": {"observation_window": 30}},
                      now=t0, tmp_path=tmp_path)
    plan = next(p for p in plans if p["rung"] == "cerebras-oss-120b")
    assert dp.signoff_overdue(plan, t0 + datetime.timedelta(days=29)) is False
    assert dp.signoff_overdue(plan, t0 + datetime.timedelta(days=31)) is True


def test_calibrated_plan_is_never_overdue(config, out_dir, tmp_path):
    t0 = datetime.datetime(2026, 1, 1, tzinfo=datetime.timezone.utc)
    plans, _, _ = gen(config, out_dir, {"cerebras-oss-120b": FULL}, now=t0,
                      tmp_path=tmp_path)
    plan = next(p for p in plans if p["rung"] == "cerebras-oss-120b")
    assert dp.signoff_overdue(plan, t0 + datetime.timedelta(days=999)) is False


def test_plan_without_a_window_is_never_overdue(config, out_dir, tmp_path):
    plans, _, _ = gen(config, out_dir, tmp_path=tmp_path)
    assert dp.signoff_overdue(plans[0], dp._now() + datetime.timedelta(days=9999)) is False


def test_index_reports_metrics_without_source(config, out_dir, tmp_path):
    _, _, index = gen(config, out_dir, {"deepseek-v4-pro": {"system_type": "tool-agent"}},
                      tmp_path=tmp_path)
    agent = next(e for e in index["plans"] if e["rung"] == "deepseek-v4-pro")
    assert agent["metrics_without_source"] == ["unauthorized_tool_effects"]


# ---- writing ----------------------------------------------------------------

def test_main_writes_one_file_per_rung_plus_index(config, out_dir, tmp_path):
    assert dp.main(["--config", config, "--out-dir", out_dir, "--overrides", ""]) == 0
    assert sorted(os.listdir(out_dir)) == sorted(
        ["cerebras-oss-120b.json", "deepseek-v4-pro.json", "index.json",
         "lumen-embed.json"])
    index = json.loads(open(os.path.join(out_dir, "index.json")).read())
    assert index["counts"]["total"] == 3


def test_dry_run_writes_nothing(config, out_dir, capsys):
    assert dp.main(["--config", config, "--out-dir", out_dir, "--overrides", "",
                    "--dry-run"]) == 0
    assert os.listdir(out_dir) == []
    assert "would write 3 plans" in capsys.readouterr().out


def test_main_creates_the_output_directory(config, tmp_path):
    fresh = str(tmp_path / "nested" / "drift_plans")
    assert dp.main(["--config", config, "--out-dir", fresh, "--overrides", ""]) == 0
    assert os.path.exists(os.path.join(fresh, "index.json"))


def test_bad_override_exits_2_and_writes_nothing(config, out_dir, tmp_path, capsys):
    bad = tmp_path / "overrides.json"
    bad.write_text(json.dumps({"cerebras-oss-120b": {"baseline_days": 999}}))
    assert dp.main(["--config", config, "--out-dir", out_dir,
                    "--overrides", str(bad)]) == 2
    assert os.listdir(out_dir) == []
    assert "outside declared bounds" in capsys.readouterr().err


def test_missing_config_exits_2(out_dir, tmp_path):
    with pytest.raises(FileNotFoundError):
        dp.generate(str(tmp_path / "absent.yaml"), out_dir, "")


def test_write_json_is_atomic_and_leaves_no_temp_files(out_dir):
    path = os.path.join(out_dir, "x.json")
    dp.write_json(path, {"a": 1})
    assert json.loads(open(path).read()) == {"a": 1}
    assert os.listdir(out_dir) == ["x.json"]


def test_json_flag_prints_the_index(config, out_dir, capsys):
    assert dp.main(["--config", config, "--out-dir", out_dir, "--overrides", "",
                    "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["counts"]["total"] == 3


def test_list_metrics_names_every_catalog_entry(capsys):
    assert dp.main(["--list-metrics"]) == 0
    out = capsys.readouterr().out
    for name in dp.METRICS:
        assert name in out
    assert "NO TELEMETRY SOURCE" in out


# ---- ops bundle contract ----------------------------------------------------

@pytest.mark.parametrize("script", ["install.sh", "rollback.sh"])
def test_shell_scripts_parse(script):
    assert subprocess.run(["bash", "-n", os.path.join(HERE, script)]).returncode == 0


def test_install_carries_the_verify_marker():
    # ops/_verify.sh contract: ops-deploy-drift only runs --verify on bundles
    # where it finds this literal marker; a bundle without it would INSTALL.
    src = open(os.path.join(HERE, "install.sh")).read()
    assert '[ "${1:-}" = --verify ]' in src
    assert src.index("--verify") < src.index("install -m")


def test_units_point_at_the_installed_generator():
    unit = open(os.path.join(HERE, "lumen-drift-plan.service")).read()
    assert "ExecStart=/usr/bin/python3 /home/lumen/minerva/drift_plan.py" in unit
    timer = open(os.path.join(HERE, "lumen-drift-plan.timer")).read()
    assert "Persistent=true" in timer and "WantedBy=timers.target" in timer


# ---- dashboard patcher ------------------------------------------------------

FAKE_DASHBOARD = '''\
import json
from pathlib import Path
from fastapi import FastAPI
from fastapi.responses import JSONResponse

app = FastAPI()


@app.get("/")
def root():
    return "hi"


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8123)
'''


@pytest.fixture
def dash(tmp_path):
    p = tmp_path / "dashboard_api.py"
    p.write_text(FAKE_DASHBOARD)
    return str(p)


def test_patch_inserts_above_the_main_guard(dash):
    assert pd.main(["--target", dash]) == 0
    text = open(dash).read()
    assert text.index(pd.BEGIN) < text.index(pd.ANCHOR)
    assert "/api/v1/drift-plans" in text
    compile(text, dash, "exec")


def test_patched_route_is_registered_and_degrades_to_empty(dash, tmp_path, monkeypatch):
    pytest.importorskip("fastapi")
    assert pd.main(["--target", dash]) == 0
    mod = _load_from(dash, "patched_dashboard")
    routes = {r.path for r in mod.app.routes}
    assert "/api/v1/drift-plans" in routes
    monkeypatch.setattr(mod, "DRIFT_PLANS_DIR", tmp_path / "absent")
    body = json.loads(mod.drift_plans().body)
    assert body == {"counts": {"total": 0}, "plans": [], "skipped_rungs": [],
                    "available": False}


def test_patched_route_serves_a_real_index(dash, config, out_dir, tmp_path):
    pytest.importorskip("fastapi")
    assert dp.main(["--config", config, "--out-dir", out_dir, "--overrides", ""]) == 0
    assert pd.main(["--target", dash]) == 0
    mod = _load_from(dash, "patched_dashboard2")
    mod.DRIFT_PLANS_DIR = __import__("pathlib").Path(out_dir)
    body = json.loads(mod.drift_plans().body)
    assert body["counts"]["total"] == 3 and body["available"] is True


def _load_from(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_patch_is_idempotent(dash):
    assert pd.main(["--target", dash]) == 0
    once = open(dash).read()
    assert pd.main(["--target", dash]) == 0
    assert open(dash).read() == once


def test_check_reports_presence(dash, capsys):
    assert pd.main(["--target", dash, "--check"]) == 1
    assert "absent" in capsys.readouterr().out
    pd.main(["--target", dash])
    assert pd.main(["--target", dash, "--check"]) == 0


def test_revert_restores_the_original_bytes(dash):
    before = open(dash).read()
    pd.main(["--target", dash])
    assert pd.main(["--target", dash, "--revert"]) == 0
    assert open(dash).read() == before


def test_revert_when_absent_is_a_no_op(dash):
    before = open(dash).read()
    assert pd.main(["--target", dash, "--revert"]) == 0
    assert open(dash).read() == before


def test_patch_leaves_a_backup(dash, tmp_path):
    pd.main(["--target", dash])
    backups = [f for f in os.listdir(tmp_path) if "wq2434" in f]
    assert len(backups) == 1
    assert open(str(tmp_path / backups[0])).read() == FAKE_DASHBOARD


def test_missing_anchor_is_refused(tmp_path, capsys):
    p = tmp_path / "dashboard_api.py"
    p.write_text("app = None\n")
    assert pd.main(["--target", str(p)]) == 2
    assert "anchor" in capsys.readouterr().err
    assert p.read_text() == "app = None\n"


def test_missing_target_exits_2(tmp_path, capsys):
    assert pd.main(["--target", str(tmp_path / "nope.py")]) == 2
    assert "patch_dashboard" in capsys.readouterr().err


def test_uncompilable_patch_never_lands(dash, monkeypatch, capsys):
    before = open(dash).read()
    monkeypatch.setattr(pd, "BLOCK", f"{pd.BEGIN}\ndef (\n{pd.END}\n\n")
    assert pd.main(["--target", dash]) == 2
    assert "does not compile" in capsys.readouterr().err
    assert open(dash).read() == before
    assert not [f for f in os.listdir(os.path.dirname(dash)) if "wq2434" in f]


@pytest.mark.parametrize("flag", [[], ["--check"], ["--revert"]])
def test_half_marker_is_refused_on_every_verb(dash, capsys, flag):
    before = open(dash).read() + f"\n{pd.BEGIN}\n"
    open(dash, "w").write(before)
    assert pd.main(["--target", dash] + flag) == 2
    assert "refusing" in capsys.readouterr().err
    assert open(dash).read() == before
