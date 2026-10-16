"""wq#4073: every theme/font the shipped LightDM greeter conf names must be a
package the build actually installs, and bloat-removal.list must not purge an
icon theme.

wq#3946 shipped overlay/etc/lightdm/lightdm-gtk-greeter.conf with
`icon-theme-name = Papirus-Dark` while no stage installed papirus-icon-theme,
and listed xubuntu-icon-theme in bloat-removal.list's games block. These tests
are the guard; all three failed against ffd0282 and pass after the fix.

Falsifiability (wq#2630): the mapping is driven from the greeter conf itself,
not from a hardcoded list -- adding a fourth `*-theme-name` key to the conf
without a matching install makes test_greeter_assets_are_installed fail, so the
probe can fail on inputs the fix was not written against. The purge test probes
bloat-removal.list, a file this fix edits in the opposite direction (a removal),
so a revert of either edit is caught independently.
"""
import re
from pathlib import Path

HERE = Path(__file__).resolve().parent
GREETER = HERE / "overlay" / "etc" / "lightdm" / "lightdm-gtk-greeter.conf"
BUILD = HERE / "build.sh"
BLOAT = HERE / "bloat-removal.list"

# Theme/font value -> apt package. Sourced from docs/branding-spec.md:160-161
# (Greybird-dark = greybird-gtk-theme, Papirus-Dark = papirus-icon-theme) and
# :126 (Inter = fonts-inter). Not independently guessed.
ASSET_PACKAGES = {
    "Greybird-dark": "greybird-gtk-theme",
    "Papirus-Dark": "papirus-icon-theme",
    "Inter": "fonts-inter",
}


def _greeter_assets():
    """The theme/font values the greeter conf names, as written."""
    assets = {}
    for line in GREETER.read_text().splitlines():
        m = re.match(r"\s*((?:icon-)?theme-name|font-name)\s*=\s*(\S+)", line)
        if m:
            assets[m.group(1)] = m.group(2)
    return assets


def _uncommented(path):
    return [ln.strip() for ln in path.read_text().splitlines()
            if ln.strip() and not ln.lstrip().startswith("#")]


def test_greeter_assets_are_installed():
    build = BUILD.read_text()
    assets = _greeter_assets()
    assert assets, f"parsed no theme/font keys out of {GREETER}"
    for key, value in assets.items():
        pkg = ASSET_PACKAGES.get(value)
        assert pkg, f"{key} = {value} has no package mapping in ASSET_PACKAGES"
        assert pkg in build, (
            f"greeter conf sets {key} = {value} but build.sh never installs {pkg}")


def test_bloat_removal_purges_no_icon_theme():
    purged = _uncommented(BLOAT)
    offenders = [p for p in purged if "icon-theme" in p or "icon_theme" in p]
    assert not offenders, (
        f"bloat-removal.list purges icon theme package(s) {offenders}; the greeter "
        f"conf names an icon theme, so the build must not strip icon themes")


def test_production_lib_ships_no_pytest_files():
    build = BUILD.read_text()
    assert "! -name 'test_*.py'" in build, (
        "stage3 must exclude pytest modules when staging limpet/*.py into "
        "/opt/limpet/lib -- an unfiltered *.py glob ships the test suite")
