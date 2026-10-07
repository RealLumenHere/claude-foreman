"""wq#3575 stage 2: install.sh patches only the cloudflared row, is idempotent,
and --verify can fail. Runs against a committed fixture copy, never the live table."""
import os
import shutil
import subprocess

import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
BUNDLE = os.path.dirname(HERE)
INSTALL = os.path.join(BUNDLE, "install.sh")


def _run(tmp_path, *args):
    env = dict(os.environ,
               HEALER_WATCH_TABLE=str(tmp_path / "table.yaml"),
               CFD_HEAL_BIN=str(tmp_path / "cfd-heal-request"))
    return subprocess.run(["bash", INSTALL, *args], env=env,
                          capture_output=True, text=True)


def _units(tmp_path):
    return yaml.safe_load((tmp_path / "table.yaml").read_text())["units"]


def _setup(tmp_path):
    shutil.copy(os.path.join(HERE, "fixture_watch_table.yaml"), tmp_path / "table.yaml")


def test_verify_fails_before_install(tmp_path):
    _setup(tmp_path)
    r = _run(tmp_path, "--verify")
    assert r.returncode == 1, r.stdout
    assert "absent:" in r.stdout and "lacks restart_cmd" in r.stdout


def test_install_routes_only_cloudflared(tmp_path):
    _setup(tmp_path)
    before = _units(tmp_path)
    r = _run(tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr
    after = _units(tmp_path)
    want = str(tmp_path / "cfd-heal-request")
    assert after["cloudflared"] == dict(before["cloudflared"], restart_cmd=want)
    assert {k: v for k, v in after.items() if k != "cloudflared"} == \
           {k: v for k, v in before.items() if k != "cloudflared"}
    assert (tmp_path / "table.yaml.pre-wq3575").exists()
    assert os.access(want, os.X_OK)
    assert _run(tmp_path, "--verify").returncode == 0


def test_install_is_idempotent(tmp_path):
    _setup(tmp_path)
    _run(tmp_path)
    once = (tmp_path / "table.yaml").read_text()
    r = _run(tmp_path)
    assert r.returncode == 0 and "unchanged" in r.stdout
    assert (tmp_path / "table.yaml").read_text() == once


def test_wrapper_files_a_restart_request_with_no_args():
    """The healer calls restart_cmd as subprocess.run([cmd]), so the wrapper must
    carry every argument itself."""
    body = open(os.path.join(BUNDLE, "cfd-heal-request")).read()
    assert "exec /home/lumen/bin/cfd-actuator request --from self-healer --kind restart" in body
    assert "$@" not in body
