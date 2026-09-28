"""wq#2919: the bundle's `install.sh --verify` handler, per ops/VERIFY_CONTRACT.md.

Read-only, exit 0 when the live gateway config carries the merged
wq2821-judge-llama8b model_list entry, exit 1 with one MISS line when it
doesn't. /home/lumen/agent is a PRIMARY_TREES root no worker sandbox can
read (guard.sh wq#900/wq#1719) and litellm_config.yaml carries live API
keys for other model_list entries, so this stages a scratch copy rather
than pointing the handler at the real path -- never exercise a bundle's
--verify handler against a file that could echo a credential back.
"""
import shutil
import subprocess
from pathlib import Path

BUNDLE = Path(__file__).resolve().parent
OPS = BUNDLE.parent
LIVE_LINE = "LIVE_CONFIG=/home/lumen/agent/litellm_config.yaml"


def run(script, *args):
    return subprocess.run(["bash", str(script), *args], capture_output=True, text=True)


def stage(tmp_path, config_text):
    """A copy of the bundle whose LIVE_CONFIG points at a scratch file."""
    live_config = tmp_path / "litellm_config.yaml"
    if config_text is not None:
        live_config.write_text(config_text)
    bundle = tmp_path / "ops" / "wq2904-judge-model-group-fix"
    bundle.mkdir(parents=True)
    shutil.copy(OPS / "_verify.sh", tmp_path / "ops" / "_verify.sh")
    text = (BUNDLE / "install.sh").read_text().replace(LIVE_LINE, f"LIVE_CONFIG={live_config}")
    assert str(live_config) in text
    (bundle / "install.sh").write_text(text)
    return bundle / "install.sh"


def test_handler_precedes_any_install_step():
    text = (BUNDLE / "install.sh").read_text()
    assert text.index('if [ "${1:-}" = --verify ]; then') < text.index("no automated install step")


def test_verify_fails_when_the_alias_is_absent(tmp_path):
    proc = run(stage(tmp_path, "model_list:\n  - model_name: qwen36-live\n"), "--verify")
    assert proc.returncode == 1
    assert "MISS " in proc.stdout
    assert "VERIFY FAIL" in proc.stdout


def test_verify_fails_when_the_config_file_is_missing(tmp_path):
    proc = run(stage(tmp_path, None), "--verify")
    assert proc.returncode == 1
    assert "absent" in proc.stdout


def test_verify_passes_once_the_snippet_is_merged(tmp_path):
    snippet = (BUNDLE / "litellm_config_snippet.yaml").read_text()
    proc = run(stage(tmp_path, f"model_list:\n{snippet}\n  - model_name: qwen36-live\n"), "--verify")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "VERIFY OK" in proc.stdout
