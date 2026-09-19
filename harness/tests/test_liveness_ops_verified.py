"""wq#1696: ops bundles are verified on the box via their own `install.sh --verify`.

Sandbox OPS_ROOT. The safety case: a bundle whose install.sh has no --verify handler
must never be executed (it would ignore the flag and install).
"""
import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import liveness  # noqa: E402

VERIFY_SH = os.path.join(os.path.dirname(os.path.dirname(HERE)), "ops", "_verify.sh")


def _bundle(root, name, body):
    d = root / name
    d.mkdir()
    (d / "install.sh").write_text("#!/usr/bin/env bash\nset -euo pipefail\n"
                                  'SRC="$(cd "$(dirname "$0")" && pwd)"\n' + body)
    return d


@pytest.fixture
def ops(tmp_path, monkeypatch):
    root = tmp_path / "ops"
    root.mkdir()
    (root / "_verify.sh").write_text(open(VERIFY_SH).read())
    monkeypatch.setattr(liveness, "OPS_ROOT", str(root))
    return root


def test_live_target_satisfied(ops, tmp_path):
    live = tmp_path / "live.py"
    live.write_text("x = 1\n")
    d = _bundle(ops, "wq1-ok", f'if [ "${{1:-}}" = --verify ]; then . "$SRC/../_verify.sh"; '
                                f'vfile "$SRC/f.py" {live}; vdone; fi\necho INSTALLING\n')
    (d / "f.py").write_text("x = 1\n")
    v, detail = liveness.ops_verified("wq1-ok")
    assert v == liveness.SATISFIED and "VERIFY OK" in detail


def test_drifted_target_still_blocked(ops, tmp_path):
    live = tmp_path / "live.py"
    live.write_text("x = 2\n")
    d = _bundle(ops, "wq2-drift", f'if [ "${{1:-}}" = --verify ]; then . "$SRC/../_verify.sh"; '
                                  f'vfile "$SRC/f.py" {live}; vfile "$SRC/f.py" {tmp_path}/absent; vdone; fi\n')
    (d / "f.py").write_text("x = 1\n")
    v, detail = liveness.ops_verified("wq2-drift")
    assert v == liveness.STILL_BLOCKED
    assert "differs" in detail and "absent" in detail


def test_bundle_without_handler_is_never_executed(ops, tmp_path):
    marker = tmp_path / "INSTALLED"
    _bundle(ops, "wq3-legacy", f"touch {marker}\n")
    v, detail = liveness.ops_verified("wq3-legacy")
    assert v == liveness.UNKNOWN and "no --verify handler" in detail
    assert not marker.exists(), "a legacy install.sh must not run"


@pytest.mark.parametrize("name", ["../etc", "wq1-ok/../../x", "", "foo", "wq9-a b"])
def test_bad_bundle_names_refused(ops, name):
    v, _ = liveness.ops_verified(name)
    assert v == liveness.UNKNOWN


def test_registered_in_closed_vocabulary():
    assert "ops_verified" in liveness.PROBE_NAMES


def test_reference_bundles_carry_handler():
    ops_dir = os.path.dirname(VERIFY_SH)
    for b in ("wq1658-cron-stamp-dropin", "wq900-primary-tree-guard"):
        assert liveness._has_verify_handler(os.path.join(ops_dir, b, "install.sh")), b
