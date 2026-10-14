"""wq#3814: the bundle's `install.sh --verify` handler, per ops/VERIFY_CONTRACT.md.

Read-only, exit 0 when the live hal.py still implements the documented CLI and
registry shape, exit 1 with one MISS line per gap.
"""
import shutil
import subprocess
from pathlib import Path

BUNDLE = Path(__file__).resolve().parent
OPS = BUNDLE.parent
LIVE_LINE = 'LIVE="/home/lumen/foreman/ops/wq3809-limpet-hal/hal.py"'


def run(script, *args):
    return subprocess.run(["bash", str(script), *args], capture_output=True, text=True)


def stage(tmp_path, hal_text):
    """A copy of the bundle whose LIVE points at a scratch hal.py."""
    live_dir = tmp_path / "live"
    live_dir.mkdir()
    live = live_dir / "hal.py"
    if hal_text is not None:
        live.write_text(hal_text)
    bundle = tmp_path / "ops" / "wq3809-limpet-hal"
    bundle.mkdir(parents=True)
    shutil.copy(OPS / "_verify.sh", tmp_path / "ops" / "_verify.sh")
    text = (BUNDLE / "install.sh").read_text().replace(LIVE_LINE, f'LIVE="{live}"')
    assert str(live) in text
    (bundle / "install.sh").write_text(text)
    return bundle / "install.sh"


def test_handler_precedes_any_install_step():
    text = (BUNDLE / "install.sh").read_text()
    assert text.index('if [ "${1:-}" = --verify ]; then') < text.index("nothing to install")


def test_verify_passes_on_the_live_tree():
    proc = run(BUNDLE / "install.sh", "--verify")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "VERIFY OK" in proc.stdout


def test_verify_fails_with_one_miss_per_gap_when_hal_is_absent(tmp_path):
    proc = run(stage(tmp_path, None), "--verify")
    assert proc.returncode == 1
    assert proc.stdout.count("MISS ") == 3
    assert "VERIFY FAIL" in proc.stdout


def test_verify_fails_when_a_subcommand_is_dropped(tmp_path):
    live_src = (OPS.parent / "ops" / "wq3809-limpet-hal" / "hal.py")
    text = live_src.read_text().replace(
        'sub.add_parser("list-plugins", help="JSON of every supported hardware class")',
        "",
    )
    proc = run(stage(tmp_path, text), "--verify")
    assert proc.returncode == 1
    assert "list-plugins" in proc.stdout


SDR_PLUGIN_BLOCK = """\
    HardwarePlugin(
        hw_type="sdr_receiver",
        name="SDR receiver (RTL-SDR)",
        capabilities=("rf_capture", "iq_sample", "rtl_sdr"),
        usb_ids=frozenset({("0bda", "2838"), ("0bda", "2832")}),
        desc_patterns=("realtek semiconductor",),
    ),
"""


def test_verify_fails_when_a_plugin_class_is_removed(tmp_path):
    live_src = (OPS.parent / "ops" / "wq3809-limpet-hal" / "hal.py")
    text = live_src.read_text()
    assert SDR_PLUGIN_BLOCK in text
    text = text.replace(SDR_PLUGIN_BLOCK, "")
    proc = run(stage(tmp_path, text), "--verify")
    assert proc.returncode == 1
    assert "expected 8" in proc.stdout
