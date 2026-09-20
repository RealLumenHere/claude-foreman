"""test_capability_profiles.py — wq#2252, the capability-refresh third of T9
(research/INFERENCE_NODE_REGISTRY.md §7.2, §8): 10 records at 20% auto-demote, 10 at 80% write
a proposal and leave `trusted` unchanged, 5 records move nothing."""
import json
import os
import shutil
import sys
import time

import pytest

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
DISPATCHER_DIR = os.path.dirname(TESTS_DIR)
REPO = os.path.dirname(DISPATCHER_DIR)
sys.path.insert(0, DISPATCHER_DIR)
sys.path.insert(0, os.path.join(REPO, "harness"))
import capability_profiles  # noqa: E402
import inference_registry  # noqa: E402
import self_heal  # noqa: E402

NOW = 1_790_000_000.0
TRUSTED = ("hathor-llama8b", "standard-engineering")      # seeded trusted: true
UNTRUSTED = ("qwen36-local", "standard-engineering")      # seeded trusted: false


@pytest.fixture
def env(tmp_path, monkeypatch):
    runs = tmp_path / "_runs"
    runs.mkdir()
    monkeypatch.setattr(self_heal, "RUNS_ROOT", str(runs))
    reg = tmp_path / "inference_nodes.yaml"
    shutil.copy(os.path.join(REPO, "research", "inference_nodes.yaml"), reg)
    return runs, str(reg)


def seed(runs, model, archetype, successes, total, age_days=1, first_id=9000):
    for i in range(total):
        d = runs / f"wq{first_id + i}"
        d.mkdir(exist_ok=True)
        (d / "attempt-1.json").write_text(json.dumps({
            "model": model, "archetype": archetype, "attempt": 1,
            "classification": "SUCCESS" if i < successes else "KILLED",
            "finished_at": NOW - age_days * 86400}))


def row(reg_path, family, archetype, node=None):
    reg = inference_registry.load(reg_path)
    for name, spec in reg["nodes"].items():
        for m in spec["models"]:
            if m["family"] == family and node in (None, name):
                return m["capabilities"][archetype]
    raise KeyError(family)


def test_seed_rows_are_what_the_tests_assume(env):
    _, reg = env
    assert row(reg, *TRUSTED)["trusted"] is True
    assert row(reg, *UNTRUSTED)["trusted"] is False


def test_ten_at_20_percent_demotes(env):
    runs, reg = env
    seed(runs, *TRUSTED, successes=2, total=10)
    res = capability_profiles.refresh(path=reg, now=NOW)
    r = row(reg, *TRUSTED)
    assert (r["trusted"], r["source"]) == (False, "measured")
    assert (r["n"], r["success"], r["window_days"], r["updated"]) == (10, 0.2, 30, "2026-09-21")
    assert r["ref"] == "harness/fworker.py:179-182"          # provenance survives the edit
    assert "2/10" in r["reason"]                             # lint: untrusted needs a reason
    assert [d["family"] for d in res["demoted"]] == ["hathor-llama8b"]
    assert res["proposals"] == []


def test_ten_at_80_percent_proposes_and_leaves_trusted(env, capsys):
    runs, reg = env
    seed(runs, *UNTRUSTED, successes=8, total=10)
    res = capability_profiles.refresh(path=reg, now=NOW)
    r = row(reg, *UNTRUSTED)
    assert r["trusted"] is False and r["source"] == "declared"
    assert (r["n"], r["success"]) == (10, 0.8)
    assert res["demoted"] == []
    saved = json.loads((runs / "capability_proposals.json").read_text())["proposals"]
    assert [(p["family"], p["archetype"], p["propose"]) for p in saved] == [
        ("qwen36-local", "standard-engineering", {"trusted": True})]
    assert saved[0]["n"] == 10 and saved[0]["success"] == 0.8


def test_five_records_move_nothing(env):
    runs, reg = env
    before = open(reg).read()
    seed(runs, *TRUSTED, successes=0, total=5)
    seed(runs, *UNTRUSTED, successes=5, total=5, first_id=9100)
    res = capability_profiles.refresh(path=reg, now=NOW)
    assert open(reg).read() == before
    assert res == {"demoted": [], "proposals": [], "measured": 0}
    assert json.loads((runs / "capability_proposals.json").read_text())["proposals"] == []


def test_middle_band_records_sample_without_flipping(env):
    runs, reg = env
    seed(runs, *TRUSTED, successes=4, total=10)
    capability_profiles.refresh(path=reg, now=NOW)
    r = row(reg, *TRUSTED)
    assert (r["trusted"], r["source"], r["n"], r["success"]) == (True, "declared", 10, 0.4)


def test_demotion_needs_ten_records_inside_the_window(env):
    runs, reg = env
    seed(runs, *TRUSTED, successes=0, total=6)
    seed(runs, *TRUSTED, successes=0, total=6, age_days=45, first_id=9100)
    capability_profiles.refresh(path=reg, now=NOW)
    assert row(reg, *TRUSTED)["trusted"] is True


def test_degraded_success_counts_and_stubs_do_not(env):
    runs, reg = env
    seed(runs, *TRUSTED, successes=0, total=10)
    for i in range(3):
        rec = runs / f"wq{9000 + i}" / "attempt-1.json"
        rec.write_text(json.dumps({**json.loads(rec.read_text()), "classification": "DEGRADED_SUCCESS"}))
    stub = runs / "wq9500"
    stub.mkdir()
    (stub / "attempt-1.json").write_text(json.dumps({"classification": "KILLED", "finished_at": NOW}))
    capability_profiles.refresh(path=reg, now=NOW)
    r = row(reg, *TRUSTED)
    assert (r["n"], r["success"], r["trusted"]) == (10, 0.3, True)     # 0.30 is not < 0.30


def test_edit_touches_only_the_measured_row_and_reloads_clean(env):
    runs, reg = env
    before = open(reg).read().split("\n")
    seed(runs, *TRUSTED, successes=1, total=10)
    capability_profiles.refresh(path=reg, now=NOW)
    after = open(reg).read().split("\n")
    changed = [i for i, (a, b) in enumerate(zip(before, after)) if a != b]
    assert len(before) == len(after) and len(changed) >= 1
    assert all("standard-engineering" in after[i] for i in changed)
    errors = [str(f) for f in inference_registry.lint(inference_registry.load(reg)) if f.level == "error"]
    assert not errors, errors


def test_dry_run_writes_nothing(env):
    runs, reg = env
    before = open(reg).read()
    seed(runs, *TRUSTED, successes=0, total=10)
    res = capability_profiles.refresh(path=reg, now=NOW, dry_run=True)
    assert len(res["demoted"]) == 1
    assert open(reg).read() == before
    assert not (runs / "capability_proposals.json").exists()


def test_cli_prints_the_proposal(env, capsys, monkeypatch):
    runs, reg = env
    seed(runs, *UNTRUSTED, successes=9, total=10)
    monkeypatch.setattr(time, "time", lambda: NOW)
    assert capability_profiles.main(["refresh", "--path", reg]) == 0
    out = capsys.readouterr().out
    assert "PROPOSAL" in out and "qwen36-local" in out and "1 promotion proposals" in out
