"""wq#4071 regression guard: every packaging/<pkg>/debian/*.service must be one
that dh_installsystemd will actually pick up, and (when the toolchain is
present) the built .deb must really contain the unit.

Background: wq#3947 shipped debian/limpet-registry-daemon.service in package
`limpet-tools`. dh_installsystemd auto-discovers only debian/<package>.service
and debian/<package>.<name>.service, so the bare file was silently skipped and
the 0.1.0 .deb shipped the daemon binary with no unit.
"""
import re
import shutil
import subprocess
from pathlib import Path

import pytest

PKG_ROOT = Path(__file__).resolve().parent
REPO_ROOT = PKG_ROOT.parent


def _packages(pkgdir):
    ctl = (pkgdir / "debian" / "control").read_text()
    return re.findall(r"^Package:\s*(\S+)", ctl, flags=re.M)


def _expected_unit(pkgdir, unit):
    """Return (ok, installed_unit_name, why) for a debian/*.service source file."""
    pkgs = _packages(pkgdir)
    rules = (pkgdir / "debian" / "rules").read_text()
    names = [n for n in re.findall(r"--name[= ]([\w.@-]+)", rules)]
    stem = unit.name[: -len(".service")]
    for pkg in pkgs:
        if stem == pkg:
            return True, f"{pkg}.service", ""
        if stem.startswith(pkg + "."):
            name = stem[len(pkg) + 1:]
            if name in names:
                return True, f"{name}.service", ""
            return False, "", (f"{unit.name} needs --name={name} in debian/rules "
                               f"(found --name values: {names or 'none'})")
    return False, "", (f"{unit.name} matches no Package: {pkgs}; dh_installsystemd "
                       f"will silently skip it")


def _units():
    out = []
    for pkgdir in sorted(PKG_ROOT.iterdir()):
        if (pkgdir / "debian" / "control").is_file():
            for u in sorted((pkgdir / "debian").glob("*.service")):
                out.append((pkgdir, u))
    return out


def test_found_some_units():
    assert _units(), "no debian/*.service found under packaging/ - test is vacuous"


@pytest.mark.parametrize("pkgdir,unit", _units(), ids=lambda v: v.name)
def test_unit_name_is_discoverable(pkgdir, unit):
    ok, _, why = _expected_unit(pkgdir, unit)
    assert ok, why


def test_limpet_tools_installs_canonical_unit_name():
    pkgdir = PKG_ROOT / "limpet-tools"
    units = list((pkgdir / "debian").glob("*.service"))
    assert units, "limpet-tools has no unit source at all"
    names = {_expected_unit(pkgdir, u)[1] for u in units}
    assert "limpet-registry-daemon.service" in names  # docs/limpet-naming.md


@pytest.mark.skipif(not (shutil.which("dh_installsystemd") and shutil.which("dpkg-buildpackage")
                         and shutil.which("fakeroot")),
                    reason="debhelper/dpkg-dev/fakeroot not installed")
@pytest.mark.parametrize("pkgdir,unit", _units(), ids=lambda v: v.name)
def test_built_deb_contains_unit(pkgdir, unit, tmp_path):
    ok, installed, why = _expected_unit(pkgdir, unit)
    assert ok, why
    # rules reads ../../limpet relative to the package dir, so mirror that layout
    (tmp_path / "packaging").mkdir()
    shutil.copytree(pkgdir, tmp_path / "packaging" / pkgdir.name)
    for extra in ("limpet", "ops"):
        if (REPO_ROOT / extra).exists():
            shutil.copytree(REPO_ROOT / extra, tmp_path / extra, symlinks=True)
    build = tmp_path / "packaging" / pkgdir.name
    r = subprocess.run(["dpkg-buildpackage", "-us", "-uc", "-b"], cwd=build,
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stdout[-2000:] + r.stderr[-2000:]
    debs = list((tmp_path / "packaging").glob("*.deb"))
    assert debs, "build produced no .deb"
    listing = "\n".join(subprocess.run(["dpkg-deb", "-c", str(d)], capture_output=True,
                                       text=True).stdout for d in debs)
    assert re.search(rf"lib/systemd/system/{re.escape(installed)}\s*$", listing, re.M), (
        f"{installed} missing from built .deb:\n" + listing)
