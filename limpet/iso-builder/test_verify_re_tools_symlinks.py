"""A pipx console script is installed as an ABSOLUTE symlink (/usr/local/bin/unblob ->
/usr/local/share/pipx/venvs/unblob/bin/unblob). verify_re_tools tested `[ -x "$root$bin" ]`,
and the kernel resolves the link's absolute target against the HOST root, not $root, so a
correctly baked tool read as missing (real run 2026-10-27: stage4 FAIL "missing RE tool
binary: /usr/local/bin/unblob" while the target existed and was executable inside the
chroot). The same check gates stage11 on the packed upper/, so it would also have refused
a good layer. The converse must also hold: a link whose target exists only on the host
(not in the chroot) must still be reported missing.
"""
import json
import subprocess
from pathlib import Path

BUILD = Path(__file__).resolve().parent / "build.sh"


def _verify(tmp_path, root):
    work = tmp_path / "work"
    work.mkdir(exist_ok=True)
    (work / "tool-packages.json").write_text(json.dumps({
        "apt_packages": [], "cargo_crates": [], "pip_git_packages": [], "binary_artifacts": [],
        "pip_packages": [{"bin": "foo"}],
    }))
    r = subprocess.run(
        ["bash", "-c", f'source "{BUILD}"; verify_re_tools "{root}"'],
        env={"PATH": "/usr/bin:/bin", "LIMPET_ISO_WORKDIR": str(work)},
        capture_output=True, text=True)
    return r.returncode, r.stderr


def _root(tmp_path, name):
    root = tmp_path / name
    (root / "usr/local/bin").mkdir(parents=True)
    return root


def test_absolute_symlink_resolved_inside_root(tmp_path):
    root = _root(tmp_path, "good")
    tgt = root / "usr/local/share/pipx/venvs/foo/bin/foo"
    tgt.parent.mkdir(parents=True)
    tgt.write_text("#!/bin/sh\n")
    tgt.chmod(0o755)
    (root / "usr/local/bin/foo").symlink_to("/usr/local/share/pipx/venvs/foo/bin/foo")
    rc, err = _verify(tmp_path, root)
    assert rc == 0, err


def test_link_target_only_on_host_is_still_missing(tmp_path):
    root = _root(tmp_path, "hostonly")
    (root / "usr/local/bin/foo").symlink_to("/usr/bin/env")  # exists on the host, not in root
    rc, err = _verify(tmp_path, root)
    assert rc == 1 and "missing RE tool binary: /usr/local/bin/foo" in err


def test_dangling_symlink_is_missing(tmp_path):
    root = _root(tmp_path, "dangling")
    (root / "usr/local/bin/foo").symlink_to("/opt/nowhere/foo")
    rc, err = _verify(tmp_path, root)
    assert rc == 1 and "missing RE tool binary" in err
