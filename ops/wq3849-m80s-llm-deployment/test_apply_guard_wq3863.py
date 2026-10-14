"""wq#3863: apply.sh's mode selector lives at $2 (API_BASE is $1), so a 1-arg
invocation like `apply.sh verify` leaves $2 unset, falls through to the apply
path, and treats the literal string "verify" as API_BASE -- silently writing
it into the live config. Guards against the regression: API_BASE must look
like a URL or apply.sh refuses before touching CFG.
"""
import subprocess
from pathlib import Path

BUNDLE = Path(__file__).resolve().parent
BASH = subprocess.run(["which", "bash"], capture_output=True, text=True).stdout.strip()

FIXTURE_CFG = """model_list:
  - model_name: existing-alias
    litellm_params:
      model: ollama/existing
      api_base: http://10.0.0.1:11434
"""


def run(*args, cfg_path):
    env = {"PATH": "/usr/bin:/bin", "WQ3849_CFG": str(cfg_path)}
    return subprocess.run(
        [BASH, str(BUNDLE / "apply.sh"), *args],
        capture_output=True, text=True, env=env,
    )


def test_one_arg_invocation_rejects_before_touching_config(tmp_path):
    cfg = tmp_path / "litellm_config.yaml"
    cfg.write_text(FIXTURE_CFG)

    proc = run("verify", cfg_path=cfg)  # the exact 1-arg shape wq1986 trains callers to use

    assert proc.returncode != 0, proc.stdout + proc.stderr
    assert "usage:" in proc.stderr
    assert cfg.read_text() == FIXTURE_CFG
    assert not cfg.with_name(cfg.name + ".bak.wq3849").exists()


def test_bogus_api_base_rejected_even_with_mode_arg_present(tmp_path):
    cfg = tmp_path / "litellm_config.yaml"
    cfg.write_text(FIXTURE_CFG)

    proc = run("verify", "apply", cfg_path=cfg)

    assert proc.returncode != 0, proc.stdout + proc.stderr
    assert cfg.read_text() == FIXTURE_CFG


def test_valid_api_base_passes_the_guard(tmp_path):
    cfg = tmp_path / "litellm_config.yaml"
    cfg.write_text("model_list:\n  - model_name: agent-fast\n")  # already applied -> idempotent exit

    proc = run("http://100.x.x.x:11434", "apply", cfg_path=cfg)

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "APPLY_OK -- agent-fast already present" in proc.stdout
