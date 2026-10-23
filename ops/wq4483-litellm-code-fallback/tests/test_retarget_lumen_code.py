import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
FIXTURE = HERE / "tests" / "fixtures" / "litellm_config.yaml"
SCRIPT = HERE / "retarget_lumen_code.py"


def run(*args, cwd=None):
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        capture_output=True, text=True, cwd=cwd,
    )


def test_check_fails_on_broken_fixture():
    r = run("check", str(FIXTURE))
    assert r.returncode == 1
    assert "lumen/code" in r.stdout


def test_apply_write_fixes_check(tmp_path):
    cfg = tmp_path / "litellm_config.yaml"
    shutil.copy2(FIXTURE, cfg)
    r = run("apply", str(cfg), "--write")
    assert r.returncode == 0, r.stderr
    r2 = run("check", str(cfg))
    assert r2.returncode == 0, r2.stdout


def test_apply_retargets_only_lumen_code(tmp_path):
    cfg = tmp_path / "litellm_config.yaml"
    shutil.copy2(FIXTURE, cfg)
    run("apply", str(cfg), "--write")
    text = cfg.read_text()
    assert "http://100.100.129.24:11434" not in text.split("- model_name: isis-coder-14b")[0].split(
        "- model_name: lumen/code"
    )[1]
    # isis-coder-14b deployment and its stale description must survive untouched
    assert "qwen2.5-coder:14b Q4_K_M on isis, 16384 ctx -- stale description" in text
    assert "ollama/qwen3.6:35b-a3b" in text  # still present under isis-coder-14b


def test_apply_adds_fallback_entry(tmp_path):
    cfg = tmp_path / "litellm_config.yaml"
    shutil.copy2(FIXTURE, cfg)
    run("apply", str(cfg), "--write")
    text = cfg.read_text()
    assert "- lumen/code:" in text
    assert "- deepseek-v4-flash" in text
    # pre-existing fallback must survive
    assert "- lumen-lfm2-1b:" in text
    assert "- openrouter-free" in text


def test_apply_is_idempotent(tmp_path):
    cfg = tmp_path / "litellm_config.yaml"
    shutil.copy2(FIXTURE, cfg)
    run("apply", str(cfg), "--write")
    once = cfg.read_text()
    run("apply", str(cfg), "--write")
    twice = cfg.read_text()
    assert once.count("- lumen/code:") == twice.count("- lumen/code:") == 1


def test_apply_writes_byte_identical_backup(tmp_path):
    cfg = tmp_path / "litellm_config.yaml"
    shutil.copy2(FIXTURE, cfg)
    original = cfg.read_bytes()
    run("apply", str(cfg), "--write")
    backups = list(tmp_path.glob("litellm_config.yaml.bak-wq4483-*"))
    assert len(backups) == 1
    assert backups[0].read_bytes() == original


def test_apply_without_write_leaves_file_untouched(tmp_path):
    cfg = tmp_path / "litellm_config.yaml"
    shutil.copy2(FIXTURE, cfg)
    original = cfg.read_bytes()
    r = run("apply", str(cfg))
    assert r.returncode == 0
    assert cfg.read_bytes() == original
    assert "- lumen/code:" in r.stdout


def test_apply_aborts_on_unknown_target(tmp_path):
    cfg = tmp_path / "litellm_config.yaml"
    shutil.copy2(FIXTURE, cfg)
    r = run("apply", str(cfg), "--target", "nonexistent-model", "--write")
    assert r.returncode == 3
    assert "ABORT" in r.stderr
