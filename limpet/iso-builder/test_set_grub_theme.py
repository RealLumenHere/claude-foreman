"""wq#3948: set_grub_theme.py rewrites GRUB_THEME/GRUB_GFXMODE idempotently.

Falsifiability (wq#2630): the replace path probes an input that ALREADY has the
keys (and a commented-out GRUB_GFXMODE, the Ubuntu default) and asserts no
duplication; the append path probes the OPPOSITE boundary -- a grub file with
neither key -- so a regression that only ever appends (or only ever replaces)
fails one of the two.
"""
import importlib.util
from pathlib import Path

_spec = importlib.util.spec_from_file_location(
    "set_grub_theme", Path(__file__).resolve().parent / "set_grub_theme.py")
mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mod)

THEME = "/boot/grub/themes/limpet/theme.txt"


def test_appends_when_absent(tmp_path):
    p = tmp_path / "grub"
    p.write_text("GRUB_DEFAULT=0\nGRUB_TIMEOUT=5\n")
    mod.main(["--default", str(p), "--theme", THEME])
    out = p.read_text()
    assert f'GRUB_THEME="{THEME}"' in out
    assert 'GRUB_GFXMODE="1024x768"' in out
    assert out.count("GRUB_THEME=") == 1


def test_replaces_existing_and_commented(tmp_path):
    p = tmp_path / "grub"
    p.write_text('GRUB_THEME="/old/theme.txt"\n#GRUB_GFXMODE=640x480\nGRUB_TIMEOUT=5\n')
    mod.main(["--default", str(p), "--theme", THEME])
    out = p.read_text()
    assert f'GRUB_THEME="{THEME}"' in out
    assert "/old/theme.txt" not in out
    assert out.count("GRUB_THEME=") == 1
    assert out.count("GRUB_GFXMODE=") == 1  # the commented one was rewritten, not duplicated
    assert 'GRUB_GFXMODE="1024x768"' in out


def test_idempotent(tmp_path):
    p = tmp_path / "grub"
    p.write_text("GRUB_TIMEOUT=5\n")
    mod.main(["--default", str(p), "--theme", THEME])
    first = p.read_text()
    mod.main(["--default", str(p), "--theme", THEME])
    assert p.read_text() == first
