"""wq#2778: the wq2764-rotation-2760 bundle's `install.sh --verify` handler, per
ops/VERIFY_CONTRACT.md.

Read-only, exit 0 when named_values.py still hashes clean and mask_replay.py 2760 still
settles at 0 unaccounted; exit 1 with one MISS line per gap.
"""
import shutil
import subprocess
from pathlib import Path

BUNDLE = Path(__file__).resolve().parent
OPS = BUNDLE.parent
LIVE_LINE = 'LIVE="$SRC/named_values.py"'
REPLAY_LINE = "REPLAY=/home/lumen/foreman/harness/mask_replay.py"

GOOD_NAMED = "#!/usr/bin/env python3\nprint('17f27c50b603 named')\nprint('self-mask clean')\n"
GOOD_REPLAY = ("#!/usr/bin/env python3\n"
               "print('wq#2760: 8 credential-shaped values, 0 unaccounted')\n")


def run(script, *args):
    return subprocess.run(["bash", str(script), *args], capture_output=True, text=True)


def stage(tmp_path, named_text=GOOD_NAMED, replay_text=GOOD_REPLAY,
          drop_named=False, drop_replay=False):
    """A copy of the bundle whose LIVE and REPLAY point at scratch stand-ins."""
    bundle = tmp_path / "ops" / "wq2764-rotation-2760"
    bundle.mkdir(parents=True)
    shutil.copy(OPS / "_verify.sh", tmp_path / "ops" / "_verify.sh")
    live = bundle / "scratch_named_values.py"
    replay = tmp_path / "scratch_mask_replay.py"
    if not drop_named:
        live.write_text(named_text)
    if not drop_replay:
        replay.write_text(replay_text)
    text = (BUNDLE / "install.sh").read_text()
    text = text.replace(LIVE_LINE, f'LIVE="{live}"')
    text = text.replace(REPLAY_LINE, f"REPLAY={replay}")
    assert str(live) in text and str(replay) in text
    (bundle / "install.sh").write_text(text)
    return bundle / "install.sh"


def test_handler_precedes_any_install_step():
    text = (BUNDLE / "install.sh").read_text()
    assert text.index('if [ "${1:-}" = --verify ]; then') < text.index("nothing to install")


def test_verify_passes_on_the_live_tree():
    proc = run(BUNDLE / "install.sh", "--verify")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "VERIFY OK" in proc.stdout


def test_verify_fails_with_one_miss_when_named_values_is_absent(tmp_path):
    proc = run(stage(tmp_path, drop_named=True), "--verify")
    assert proc.returncode == 1
    assert proc.stdout.count("MISS ") == 1
    assert "MISS absent" in proc.stdout
    assert "VERIFY FAIL" in proc.stdout


def test_verify_fails_when_a_named_hash_no_longer_matches(tmp_path):
    proc = run(stage(tmp_path, named_text=GOOD_NAMED.replace("named", "MISMATCH")),
               "--verify")
    assert proc.returncode == 1
    assert "hash mismatch" in proc.stdout


def test_verify_fails_when_the_fixture_no_longer_masks_clean(tmp_path):
    named_text = "#!/usr/bin/env python3\nprint('17f27c50b603 named')\nprint('FIRES url-auth')\n"
    proc = run(stage(tmp_path, named_text=named_text), "--verify")
    assert proc.returncode == 1
    assert "no longer masks clean" in proc.stdout


def test_verify_fails_when_the_replay_no_longer_settles(tmp_path):
    replay_text = ("#!/usr/bin/env python3\n"
                    "print('wq#2760: 8 credential-shaped values, 4 unaccounted')\n")
    proc = run(stage(tmp_path, replay_text=replay_text), "--verify")
    assert proc.returncode == 1
    assert "no longer settles at 0 unaccounted" in proc.stdout


def test_verify_fails_with_one_miss_when_replay_is_absent(tmp_path):
    proc = run(stage(tmp_path, drop_replay=True), "--verify")
    assert proc.returncode == 1
    assert "MISS absent" in proc.stdout


def test_verify_runs_python_without_writing_bytecode():
    verify_arm = (BUNDLE / "install.sh").read_text().split("nothing to install")[0]
    assert "PYTHONDONTWRITEBYTECODE=1 python3" in verify_arm
