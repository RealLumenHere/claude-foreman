"""wq#3407: `ops/wq3383-emba/install.sh --verify --deps` reports a missing
embeddedanalyzer/emba image as MISS (exit 1), never ERR (exit 2).

wq#3404 shipped the flag setting _V_ERR=1, so a conclusive "the image is not
pulled" exited 2. ops/_verify.sh:8-9 and :85-87 reserve exit 2 for "I could not
look", and liveness.ops_verified() maps rc=1 to STILL_BLOCKED but rc=2 to
UNKNOWN — so the blocked dependency read as an indeterminate probe failure.

`docker` is shimmed on PATH so both arms are deterministic on any host,
including one that has a real container engine with the image already pulled.
"""
import os
import subprocess

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
INSTALL_SH = os.path.join(REPO, "ops", "wq3383-emba", "install.sh")


def _shim(tmp_path, name, rc):
    """A `docker` that exits RC for every call, ahead of any real one on PATH."""
    d = tmp_path / name
    d.mkdir()
    p = d / "docker"
    p.write_text("#!/bin/sh\nexit %d\n" % rc)
    p.chmod(0o755)
    return str(d)


def _verify(*args, path_prefix=None):
    env = dict(os.environ)
    if path_prefix:
        env["PATH"] = path_prefix + os.pathsep + env["PATH"]
    p = subprocess.run(["bash", INSTALL_SH, "--verify", *args], env=env,
                       capture_output=True, text=True, stdin=subprocess.DEVNULL)
    return p.returncode, p.stdout + p.stderr


@pytest.fixture
def base():
    """The bundle's own exit code without --deps: 0 deployed, 1 not.

    A 2 means the base checks themselves could not look (nothing to do with
    --deps), which makes the comparisons below meaningless.
    """
    rc, out = _verify()
    if rc == 2:
        pytest.skip("base --verify cannot look on this host: %s" % out[-200:])
    return rc


def test_absent_image_is_miss_not_err(base, tmp_path):
    rc, out = _verify("--deps", path_prefix=_shim(tmp_path, "absent", 1))
    assert "MISS DEPS MISSING" in out, out
    assert rc == 1, "a detected-absent image must exit 1 (MISS), got %d: %s" % (rc, out)


def test_present_image_does_not_change_the_verdict(base, tmp_path):
    rc, out = _verify("--deps", path_prefix=_shim(tmp_path, "present", 0))
    assert "DEPS OK" in out, out
    assert rc == base, "a present image must not alter the base verdict %d, got %d" % (base, rc)


def test_deps_flag_is_opt_in(base):
    """Without --deps the dependency is not consulted at all: the sandbox clone
    (no root, no container engine) must keep passing the base contract."""
    rc, out = _verify()
    assert "DEPS" not in out, out
    assert rc == base
