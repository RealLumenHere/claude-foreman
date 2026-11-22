"""wq#5303: file_root_apply_followups must not file an [ops-root-apply] ticket for a
bundle whose own --verify already passes (wq#5262 -> wq#5303 loop), and must still
file whenever --verify fails or cannot run."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import fworker  # noqa: E402


class _P:
    def __init__(self, out):
        self.stdout, self.stderr, self.returncode = out, "", 0


def _script(tmp_path, name, rc):
    p = tmp_path / name
    p.write_text("#!/usr/bin/env bash\n"
                 "if [ \"${1:-}\" = --verify ]; then echo verify; exit %d; fi\n"
                 "echo INSTALLED > \"$(dirname \"$0\")/installed\"\n" % rc)
    return str(p)


def _run(monkeypatch, install_path):
    calls = []

    def fake_wq(*args, check=False):
        calls.append(args)
        return _P("added task 9001" if args[0] == "add" else "")

    monkeypatch.setattr(fworker, "wq", fake_wq)
    monkeypatch.setattr(fworker, "log", lambda m: None)
    filed = fworker.file_root_apply_followups(
        5262, "build", [{"bundle": "b", "install_path": install_path, "why": "x"}])
    return filed, [c for c in calls if c[0] == "add"]


def test_verify_passing_bundle_is_not_filed(tmp_path, monkeypatch):
    filed, adds = _run(monkeypatch, _script(tmp_path, "install.sh", 0))
    assert filed == [] and adds == []
    assert not (tmp_path / "installed").exists()  # only --verify ran, never the install


def test_verify_failing_bundle_is_still_filed(tmp_path, monkeypatch):
    filed, adds = _run(monkeypatch, _script(tmp_path, "install.sh", 1))
    assert filed == [9001] and len(adds) == 1


def test_verify_error_exit_2_is_still_filed(tmp_path, monkeypatch):
    filed, adds = _run(monkeypatch, _script(tmp_path, "install.sh", 2))
    assert filed == [9001] and len(adds) == 1


def test_missing_install_path_is_still_filed(tmp_path, monkeypatch):
    filed, adds = _run(monkeypatch, str(tmp_path / "gone" / "install.sh"))
    assert filed == [9001] and len(adds) == 1


def test_verify_timeout_is_false(tmp_path):
    p = tmp_path / "slow.sh"
    p.write_text("sleep 5\n")
    assert fworker._bundle_verify_passes(str(p), timeout=0.2) is False
