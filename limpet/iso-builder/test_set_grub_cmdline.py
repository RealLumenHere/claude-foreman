"""wq#4526: set_grub_cmdline.py rewrites GRUB_CMDLINE_LINUX idempotently.

Falsifiability (wq#2630): the replace path probes an input that ALREADY has a
GRUB_CMDLINE_LINUX line (and a commented-out one, the Ubuntu default shape)
and asserts no duplication; the append path probes the OPPOSITE boundary -- a
grub file with neither -- so a regression that only ever appends (or only
ever replaces) fails one of the two.
"""
import importlib.util
from pathlib import Path

_spec = importlib.util.spec_from_file_location(
    "set_grub_cmdline", Path(__file__).resolve().parent / "set_grub_cmdline.py")
mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mod)

CONSOLE_ARG = "console=tty0 console=ttyS0,115200n8"


def test_appends_when_absent(tmp_path):
    p = tmp_path / "grub"
    p.write_text("GRUB_DEFAULT=0\nGRUB_TIMEOUT=5\n")
    mod.main(["--default", str(p)])
    out = p.read_text()
    assert f'GRUB_CMDLINE_LINUX="{CONSOLE_ARG}"' in out
    assert out.count("GRUB_CMDLINE_LINUX=") == 1


def test_replaces_existing(tmp_path):
    p = tmp_path / "grub"
    p.write_text('GRUB_CMDLINE_LINUX="quiet splash"\nGRUB_TIMEOUT=5\n')
    mod.main(["--default", str(p)])
    out = p.read_text()
    assert f'GRUB_CMDLINE_LINUX="{CONSOLE_ARG}"' in out
    assert "quiet splash" not in out
    assert out.count("GRUB_CMDLINE_LINUX=") == 1


def test_replaces_commented(tmp_path):
    p = tmp_path / "grub"
    p.write_text('#GRUB_CMDLINE_LINUX="old"\nGRUB_TIMEOUT=5\n')
    mod.main(["--default", str(p)])
    out = p.read_text()
    assert f'GRUB_CMDLINE_LINUX="{CONSOLE_ARG}"' in out
    assert "old" not in out
    assert out.count("GRUB_CMDLINE_LINUX=") == 1


def test_idempotent(tmp_path):
    p = tmp_path / "grub"
    p.write_text("GRUB_TIMEOUT=5\n")
    mod.main(["--default", str(p)])
    first = p.read_text()
    mod.main(["--default", str(p)])
    assert p.read_text() == first
