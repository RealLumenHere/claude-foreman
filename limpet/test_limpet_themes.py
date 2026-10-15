"""Acceptance probe for the wq#3948 theme assets (docs/branding-spec.md §4).

Contract: every hex color painted in the GRUB/Plymouth/LightDM/GTK theme files,
the wallpaper, and the app icons is a token hex from design/limpet/brand/
tokens.json (either mode) -- no theme may invent a color, the same rule
test_limpet_brand.py enforces on the logo. The probe can fail: drop a non-token
hex (e.g. the ticket's superseded installer #1a1a2e/#00d4aa/#00ff41) into any
asset and this suite rejects it.

Falsifiability (wq#2630): the inputs are the committed theme fixtures pinned by
this branch. The boundary probe (test_installer_hexes_are_absent) checks the
OPPOSITE population -- the three raw installer hexes the ticket named but the
formalized palette rejected -- so it can fail if anyone pastes them back in.
"""
import json
import re
from pathlib import Path

BRAND = Path(__file__).resolve().parents[1] / "design" / "limpet" / "brand"

THEME_FILES = [
    BRAND / "themes" / "grub" / "theme.txt",
    BRAND / "themes" / "plymouth" / "limpet.script",
    BRAND / "themes" / "lightdm" / "limpet-greeter.css",
    BRAND / "themes" / "gtk" / "gtk.css",
    BRAND / "wallpaper" / "limpet-wallpaper.svg",
    BRAND / "icons" / "limpet-agent.svg",
    BRAND / "icons" / "limpet-workspace.svg",
    BRAND / "icons" / "limpet-settings.svg",
]

# The ticket text named these raw installer hexes; the formalized, AA-tested
# palette (docs/branding-spec.md §2) superseded them. They must not reappear.
SUPERSEDED_INSTALLER_HEXES = {"#1A1A2E", "#00D4AA", "#00FF41"}


def _tokens():
    return json.loads((BRAND / "tokens.json").read_text())


def _allowed_hexes():
    return {v.upper() for palette in _tokens()["modes"].values() for v in palette.values()}


def _painted_hexes(path: Path):
    text = path.read_text()
    # strip SVG/CSS/GRUB comments so hexes cited in prose don't count as paint
    text = re.sub(r"<!--.*?-->", "", text, flags=re.S)
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    text = re.sub(r"(?m)^\s*#.*$", "", text)  # GRUB/plymouth '#' comment lines
    painted = {h.upper() for h in re.findall(r"#[0-9A-Fa-f]{6}", text)}
    # Plymouth paints via 0..1 float RGB triples, not hex: convert each to hex.
    for r, g, b in re.findall(r"(0?\.\d+)\s*,\s*(0?\.\d+)\s*,\s*(0?\.\d+)", text):
        painted.add("#%02X%02X%02X" % tuple(round(float(c) * 255) for c in (r, g, b)))
    return painted


def test_theme_files_exist():
    missing = [str(p) for p in THEME_FILES if not p.exists()]
    assert not missing, missing


def test_themes_use_only_token_colors():
    allowed = _allowed_hexes()
    for path in THEME_FILES:
        painted = _painted_hexes(path)
        assert painted, f"{path.name}: no colors found (fixture changed?)"
        assert painted <= allowed, (path.name, painted - allowed)


def test_installer_hexes_are_absent():
    for path in THEME_FILES:
        assert not (_painted_hexes(path) & SUPERSEDED_INSTALLER_HEXES), path.name
