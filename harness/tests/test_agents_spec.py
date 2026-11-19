"""wq#1301 (foreman pattern D) — declarative agent archetype spec. Zero quota (no
`claude` child spawned)."""
import json
import os
import subprocess
import sys
import textwrap

import pytest

HARNESS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HARNESS_DIR)
import agents_spec  # noqa: E402

REAL_AGENTS_YAML = os.path.join(HARNESS_DIR, "agents.yaml")
REAL_MODELS_YAML = os.path.join(HARNESS_DIR, "..", "research", "models.yaml")
APPLY_CLI = os.path.join(HARNESS_DIR, "agents_apply.py")


def test_real_agents_yaml_loads_and_lints_clean():
    spec = agents_spec.load(REAL_AGENTS_YAML, force=True)
    assert "standard-engineering" in spec
    problems = agents_spec.lint(spec, models_yaml_path=REAL_MODELS_YAML)
    assert problems == []


def test_all_fworker_archetypes_present():
    # fworker.py's own archetype set (ARCHETYPE_MAX_TURNS keys pre-wq#1301) —
    # a regression here means fworker.py would silently fall back to
    # DEFAULT_MAX_TURNS for an archetype that used to have its own ceiling.
    spec = agents_spec.load(REAL_AGENTS_YAML, force=True)
    expected = {
        "mechanical-edit", "probe-build", "standard-engineering", "verify-pass",
        "research-synthesis", "novel-design", "rca-investigation",
        "long-horizon-campaign", "fire-response",
        "verify-probe",  # wq#4686 (design/orchestrator_verify_lane.md §5.2, archetype 10)
    }
    assert expected == set(spec.names())


def test_local_allowed_archetypes_match_pre_wq1301_constant():
    spec = agents_spec.load(REAL_AGENTS_YAML, force=True)
    local = {name for name in spec.names() if spec.get(name).local_allowed}
    assert local == {"mechanical-edit", "probe-build"}
    assert spec.get("mechanical-edit").local_max_turns == 10
    # 15 -> 40 on purpose (c9b7a75c, 2026-10-08 subtraction-phase2): 15 starved deepseek-v4-flash
    # at the lumen-pr step. The pre-wq#1301 pin that matters is the allowed SET above.
    assert spec.get("probe-build").local_max_turns == 40


def test_rca_investigation_keeps_doubled_wall_clock():
    spec = agents_spec.load(REAL_AGENTS_YAML, force=True)
    assert spec.get("rca-investigation").wall_clock_timeout_s == 7200
    assert spec.get("standard-engineering").wall_clock_timeout_s == 3600


def test_unknown_archetype_raises():
    spec = agents_spec.load(REAL_AGENTS_YAML, force=True)
    with pytest.raises(agents_spec.AgentsSpecError):
        spec.get("no-such-archetype")


def test_parse_primary_tuple():
    assert agents_spec.parse_primary_tuple("sonnet-5@high") == ("sonnet-5", "high")
    assert agents_spec.parse_primary_tuple("qwen36-local@local") == ("qwen36-local", None)
    assert agents_spec.parse_primary_tuple("DECOMPOSE") == ("DECOMPOSE", None)


def _write(path, text):
    with open(path, "w") as f:
        f.write(textwrap.dedent(text))


def test_lint_catches_missing_required_field(tmp_path):
    bad = tmp_path / "agents.yaml"
    _write(str(bad), """
        archetypes:
          standard-engineering:
            primary_tuple: sonnet-5@high
            max_turns: 600
            wall_clock_timeout_s: 3600
            add_dirs: []
        """)
    with pytest.raises(agents_spec.AgentsSpecError, match="prompt_template"):
        agents_spec.load(str(bad), force=True)


def test_lint_catches_nonpositive_max_turns(tmp_path):
    bad = tmp_path / "agents.yaml"
    _write(str(bad), """
        defaults:
          prompt_template: worker.md
          add_dirs: []
        archetypes:
          standard-engineering:
            primary_tuple: sonnet-5@high
            max_turns: 0
            wall_clock_timeout_s: 3600
        """)
    spec = agents_spec.load(str(bad), force=True)
    problems = agents_spec.lint(spec, models_yaml_path=REAL_MODELS_YAML)
    assert any("max_turns must be positive" in p for p in problems)


def test_lint_catches_missing_prompt_template(tmp_path):
    bad = tmp_path / "agents.yaml"
    _write(str(bad), """
        defaults:
          add_dirs: []
        archetypes:
          standard-engineering:
            primary_tuple: sonnet-5@high
            max_turns: 600
            wall_clock_timeout_s: 3600
            prompt_template: does-not-exist.md
        """)
    spec = agents_spec.load(str(bad), force=True)
    problems = agents_spec.lint(spec, models_yaml_path=REAL_MODELS_YAML)
    assert any("does-not-exist.md" in p for p in problems)


def test_lint_catches_primary_tuple_drift_against_models_yaml(tmp_path):
    bad = tmp_path / "agents.yaml"
    _write(str(bad), """
        defaults:
          prompt_template: worker.md
          add_dirs: []
        archetypes:
          standard-engineering:
            primary_tuple: opus-5@high
            max_turns: 600
            wall_clock_timeout_s: 3600
        """)
    spec = agents_spec.load(str(bad), force=True)
    problems = agents_spec.lint(spec, models_yaml_path=REAL_MODELS_YAML)
    assert any("drift" in p for p in problems)


def test_lint_catches_archetype_missing_from_agents_yaml(tmp_path):
    # standard-engineering has a models.yaml routing entry but this fixture never
    # declares it as an archetype.
    bad = tmp_path / "agents.yaml"
    _write(str(bad), """
        defaults:
          prompt_template: worker.md
          add_dirs: []
        archetypes:
          mechanical-edit:
            primary_tuple: qwen36-local@local
            max_turns: 600
            wall_clock_timeout_s: 3600
        """)
    spec = agents_spec.load(str(bad), force=True)
    problems = agents_spec.lint(spec, models_yaml_path=REAL_MODELS_YAML)
    assert any("standard-engineering" in p and "no agents.yaml archetype" in p for p in problems)


def test_apply_cli_lint_clean_on_real_file():
    result = subprocess.run([sys.executable, APPLY_CLI, "lint"], cwd=HARNESS_DIR,
                             capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert "clean" in result.stdout


def test_apply_cli_lint_fails_on_broken_file(tmp_path, monkeypatch):
    bad = tmp_path / "agents.yaml"
    _write(str(bad), """
        defaults:
          prompt_template: worker.md
          add_dirs: []
        archetypes:
          standard-engineering:
            primary_tuple: sonnet-5@high
            max_turns: -1
            wall_clock_timeout_s: 3600
        """)
    env = dict(os.environ, FOREMAN_AGENTS_YAML=str(bad))
    result = subprocess.run([sys.executable, APPLY_CLI, "lint"], cwd=HARNESS_DIR,
                             capture_output=True, text=True, env=env)
    assert result.returncode == 1
    assert "PROBLEM" in result.stderr


def test_apply_cli_writes_stable_content_addressed_lock(tmp_path):
    out = tmp_path / "agents.lock.json"
    result = subprocess.run([sys.executable, APPLY_CLI, "apply", "--out", str(out)],
                             cwd=HARNESS_DIR, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    lock = json.loads(out.read_text())
    assert set(lock["archetypes"]) == {
        "mechanical-edit", "probe-build", "standard-engineering", "verify-pass",
        "research-synthesis", "novel-design", "rca-investigation",
        "long-horizon-campaign", "fire-response", "verify-probe",
    }
    first_hash = lock["overall_hash"]

    # re-apply against the same source is a no-op (content-addressed, wq#954-style
    # discipline: no spurious diff on an unchanged spec)
    result2 = subprocess.run([sys.executable, APPLY_CLI, "apply", "--out", str(out)],
                              cwd=HARNESS_DIR, capture_output=True, text=True)
    assert result2.returncode == 0
    assert json.loads(out.read_text())["overall_hash"] == first_hash

    # --check passes against an up-to-date lock and fails once the spec changes
    check = subprocess.run([sys.executable, APPLY_CLI, "apply", "--out", str(out), "--check"],
                            cwd=HARNESS_DIR, capture_output=True, text=True)
    assert check.returncode == 0

    out.write_text(json.dumps({"stale": True}))
    check2 = subprocess.run([sys.executable, APPLY_CLI, "apply", "--out", str(out), "--check"],
                             cwd=HARNESS_DIR, capture_output=True, text=True)
    assert check2.returncode == 1
