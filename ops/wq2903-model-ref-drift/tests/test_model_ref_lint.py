"""model-ref-lint against committed fixtures (wq#2903).

pre_fix.py holds the verbatim caller lines that produced 1643 ProxyModelNotFoundError
in 5d plus the wq#2904 judge caller; the lint must flag every one of them (a probe that
can fail). clean.py holds the look-alikes it must not flag.
"""
import importlib.util, json, os, shutil, subprocess, sys, tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
BUNDLE = os.path.dirname(HERE)
FIX = os.path.join(HERE, "fixtures")
LINT = os.path.join(BUNDLE, "model_ref_lint.py")
# the repo gitignores *.bak.*, so the backup is committed under another name and
# staged beside the config under its real naming here
_CFG_DIR = tempfile.mkdtemp(prefix="wq2903-cfg-")
CONFIG = os.path.join(_CFG_DIR, "litellm_config.yaml")
shutil.copyfile(os.path.join(FIX, "litellm_config.yaml"), CONFIG)
shutil.copyfile(os.path.join(FIX, "litellm_config.pre_mistral_drop.yaml"),
                CONFIG + ".bak.20260830_124308_pre_mistral_drop")

spec = importlib.util.spec_from_file_location("model_ref_lint", LINT)
mrl = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mrl)


def run(*roots):
    live, retired, findings = mrl.lint(list(roots), CONFIG, "/nonexistent/models.yaml", set())
    return live, retired, {(f["kind"], f["model"], os.path.basename(f["file"]), f["line"])
                           for f in findings}


def test_live_and_retired_sets_come_from_config_and_backups():
    live, retired, _ = run(os.path.join(FIX, "callers", "clean.py"))
    assert live == {"cerebras-llama", "deepseek-v4-flash", "deepseek-v4-pro", "hathor-llama8b"}
    assert retired == {"mistral-small", "local"}


def test_flags_every_pre_fix_caller():
    _, _, got = run(os.path.join(FIX, "callers", "pre_fix.py"))
    assert got == {
        ("RETIRED", "mistral-small", "pre_fix.py", 3),    # lumen_quota_manager PROVIDER_TO_MODEL
        ("RETIRED", "mistral-small", "pre_fix.py", 7),    # model_audit AUDIT_MODELS comma list
        ("RETIRED", "mistral-small", "pre_fix.py", 9),    # model-watchdog CLOUD roster
        ("UNKNOWN", "wq2821-judge-llama8b", "pre_fix.py", 10),  # wq#2904, never existed
    }


def test_clean_lookalikes_not_flagged():
    _, _, got = run(os.path.join(FIX, "callers", "clean.py"))
    assert got == set()


def test_allow_and_cli_roster_suppress(tmp_path):
    cli = tmp_path / "models.yaml"
    cli.write_text("models:\n  - id: wq2821-judge-llama8b\n")
    _, _, findings = mrl.lint([os.path.join(FIX, "callers", "pre_fix.py")], CONFIG, str(cli),
                              {"mistral-small"})
    assert findings == []


def test_walk_skips_backups_state_and_doc_dirs(tmp_path):
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "note.py").write_text('M = "mistral-small"\n')
    (tmp_path / "tool.py.bak.20260901").write_text('M = "mistral-small"\n')
    (tmp_path / "state.json").write_text('{"up": {"mistral-small": false}}\n')
    (tmp_path / "live_tool").write_text('#!/bin/bash\ncurl -d \'{"model": "mistral-small"}\'\n')
    (tmp_path / "blob").write_text('"mistral-small"\n')         # no shebang, no ext
    _, _, got = run(str(tmp_path))
    assert got == {("RETIRED", "mistral-small", "live_tool", 2)}


# ---- wq#3221: environment_variables: KEY: KEY self-reference config trap ----

def _write_config_with_env_block(tmp_path, env_lines):
    cfg = tmp_path / "litellm_config.yaml"
    cfg.write_text(
        "model_list:\n"
        "- model_name: cerebras-llama\n"
        "  litellm_params:\n"
        "    model: cerebras/llama3.1-8b\n"
        "environment_variables:\n"
        + "".join(f"  {ln}\n" for ln in env_lines)
    )
    return str(cfg)


def test_flags_self_referential_env_var_values(tmp_path):
    cfg = _write_config_with_env_block(tmp_path, [
        "LANGFUSE_PUBLIC_KEY: LANGFUSE_PUBLIC_KEY",
        "LANGFUSE_SECRET_KEY: LANGFUSE_SECRET_KEY",
    ])
    findings = mrl.find_env_self_refs(cfg)
    assert {(f["kind"], f["model"], f["line"]) for f in findings} == {
        ("SELF_REF", "LANGFUSE_PUBLIC_KEY", 6),
        ("SELF_REF", "LANGFUSE_SECRET_KEY", 7),
    }


def test_does_not_flag_a_correct_os_environ_reference(tmp_path):
    cfg = _write_config_with_env_block(tmp_path, [
        "LANGFUSE_PUBLIC_KEY: os.environ/LANGFUSE_PUBLIC_KEY",
        'CEREBRAS_API_KEY: "some-real-looking-value"',
    ])
    assert mrl.find_env_self_refs(cfg) == []


def test_env_self_ref_canary_fails_the_lint_end_to_end(tmp_path):
    """TEST TO FAILURE (wq#3221 part 4): a canary config with the two LANGFUSE
    self-referential lines restored must make `lint()`/the CLI FAIL, not silently
    pass - this is the regression this lint exists to catch on the next edit."""
    cfg = _write_config_with_env_block(tmp_path, [
        "LANGFUSE_PUBLIC_KEY: LANGFUSE_PUBLIC_KEY",
        "LANGFUSE_SECRET_KEY: LANGFUSE_SECRET_KEY",
    ])
    _, _, findings = mrl.lint([], cfg, "/nonexistent/models.yaml", set())
    assert any(f["kind"] == "SELF_REF" for f in findings)

    r = subprocess.run([sys.executable, LINT, "--config", cfg, "--models-yaml", "/nonexistent"],
                       capture_output=True, text=True)
    assert r.returncode == 1, r.stdout


def test_env_block_ends_at_next_top_level_key(tmp_path):
    cfg = tmp_path / "litellm_config.yaml"
    cfg.write_text(
        "model_list:\n"
        "- model_name: cerebras-llama\n"
        "  litellm_params:\n"
        "    model: cerebras/llama3.1-8b\n"
        "environment_variables:\n"
        "  GOOD: os.environ/GOOD\n"
        "router_settings:\n"
        "  num_retries: 3\n"
    )
    assert mrl.find_env_self_refs(str(cfg)) == []


def test_cli_exit_codes_and_json(tmp_path):
    base = [sys.executable, LINT, "--config", CONFIG, "--models-yaml", "/nonexistent"]
    bad = subprocess.run(base + ["--json", os.path.join(FIX, "callers", "pre_fix.py")],
                         capture_output=True, text=True)
    assert bad.returncode == 1
    assert len(json.loads(bad.stdout)["findings"]) == 4
    ok = subprocess.run(base + [os.path.join(FIX, "callers", "clean.py")],
                        capture_output=True, text=True)
    assert ok.returncode == 0, ok.stdout
    empty = tmp_path / "empty.yaml"
    empty.write_text("model_list: []\n")
    err = subprocess.run([sys.executable, LINT, "--config", str(empty), str(tmp_path)],
                         capture_output=True, text=True)
    assert err.returncode == 2
