"""Acceptance probe for the wq#3907 brand tokens (docs/branding-spec.md §2.3).

Contract: every non-container token in design/limpet/brand/tokens.json is a
text color and must hold WCAG AA (>= 4.5:1) against BOTH `background` and
`surface` of its own mode, in both modes; and the logo SVG may only paint with
token hexes. The probe can fail: re-run it after nudging any token toward its
background (e.g. dark `muted` #7A8C8A sits at 4.64:1 on `surface` -- two or
three shades darker and this suite rejects it). Falsifiability (wq#2630): the
inputs are the committed tokens.json/SVG fixtures, pinned by this branch, not
a live mutable file; the light mode exercised here is OUTSIDE the dark-first
default every operator surface ships with.
"""
import json
import re
from pathlib import Path

BRAND = Path(__file__).resolve().parents[1] / "design" / "limpet" / "brand"

# `panel` is a container color (spec section 2.3): text on it is foreground/muted only.
CONTAINER_TOKENS = {"background", "surface", "panel"}
REQUIRED_TOKENS = CONTAINER_TOKENS | {
    "foreground", "muted", "primary", "secondary",
    "accent", "success", "warning", "error",
}
AA_NORMAL_TEXT = 4.5


def _linear(channel: float) -> float:
    return channel / 12.92 if channel <= 0.03928 else ((channel + 0.055) / 1.055) ** 2.4


def _luminance(hexcolor: str) -> float:
    h = hexcolor.lstrip("#")
    r, g, b = (_linear(int(h[i:i + 2], 16) / 255) for i in (0, 2, 4))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(a: str, b: str) -> float:
    hi, lo = sorted((_luminance(a), _luminance(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


def _tokens():
    return json.loads((BRAND / "tokens.json").read_text())


def test_both_modes_present_with_all_tokens():
    modes = _tokens()["modes"]
    assert set(modes) == {"dark", "light"}
    for mode, palette in modes.items():
        assert set(palette) == REQUIRED_TOKENS, (mode, set(palette) ^ REQUIRED_TOKENS)
        for name, value in palette.items():
            assert re.fullmatch(r"#[0-9A-F]{6}", value), (mode, name, value)


def test_text_tokens_meet_aa_on_background_and_surface():
    for mode, palette in _tokens()["modes"].items():
        for name, value in palette.items():
            if name in CONTAINER_TOKENS:
                continue
            for ground in ("background", "surface"):
                ratio = contrast(value, palette[ground])
                assert ratio >= AA_NORMAL_TEXT, (
                    f"{mode}.{name} {value} on {ground} {palette[ground]}: "
                    f"{ratio:.2f} < {AA_NORMAL_TEXT}"
                )


def test_logo_uses_only_token_colors():
    svg = (BRAND / "limpet-logo.svg").read_text()
    # hexes in comments are prose; strip comments before extracting paint values
    painted = set(re.findall(r"#[0-9A-Fa-f]{6}", re.sub(r"<!--.*?-->", "", svg, flags=re.S)))
    allowed = {v for palette in _tokens()["modes"].values() for v in palette.values()}
    assert painted and painted <= allowed, painted - allowed


def test_ansi16_fallback_covers_every_text_token():
    data = _tokens()
    text_tokens = REQUIRED_TOKENS - CONTAINER_TOKENS
    assert set(data["ansi16_fallback"]) == text_tokens
