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


def test_manifest_check_flags_the_wq4210_rename_and_nothing_else():
    """The drift fixture is wq#4210's own manifest shape: `deepseek-flash` is the one
    id the fixture gateway does not serve. The DARK row, the `down` rung and the
    session-pool row are all absent from the model_list too, on purpose — flagging any
    of them would make this check noise."""
    live = mrl.load_live(CONFIG)
    drift = mrl.manifest_drift(os.path.join(FIX, "models_wq4210_drift.yaml"), live)
    assert [(f["kind"], f["model"], f["line"]) for f in drift] == [("MANIFEST", "deepseek-flash", 12)]
    assert mrl.manifest_drift(os.path.join(FIX, "models_clean.yaml"), live) == []
    assert mrl.manifest_drift("/nonexistent/models.yaml", live) == []


def test_lint_reports_manifest_drift_alongside_caller_findings():
    _, _, findings = mrl.lint([os.path.join(FIX, "callers", "clean.py")], CONFIG,
                              os.path.join(FIX, "models_wq4210_drift.yaml"), set())
    assert [(f["kind"], f["model"]) for f in findings] == [("MANIFEST", "deepseek-flash")]


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
