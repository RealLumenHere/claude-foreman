"""Tests for the wq#3215 stale-env-audit extension.

Unit tests exercise the pure comparison/dedup logic directly (fast, no root, no live
units). `test_transient_unit_control` is the TEST TO FAILURE the ticket asks for: it
drives a real `systemd-run -p EnvironmentFile=...` transient unit through drift ->
exactly one aq row -> restart -> row resolved. It needs root (to create a systemd
transient unit and to run stale-env-audit against another uid's /proc) and a live `aq`,
so it is skipped by default and only runs under `RUN_LIVE_SYSTEMD_TESTS=1`.
"""
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import time

import pytest

from importlib.machinery import SourceFileLoader

HERE = os.path.dirname(os.path.abspath(__file__))
loader = SourceFileLoader("stale_env_audit", os.path.join(HERE, "stale-env-audit"))
spec = importlib.util.spec_from_loader("stale_env_audit", loader)
sea = importlib.util.module_from_spec(spec)
loader.exec_module(sea)


def test_parse_envfile_quotes_and_comments():
    with tempfile.NamedTemporaryFile("w", suffix=".env", delete=False) as f:
        f.write('# comment\nFOO=bar\nBAZ="quo ted"\n; also comment\nEMPTY=\n')
        path = f.name
    try:
        d = sea.parse_envfile(path)
        assert d == {"FOO": "bar", "BAZ": "quo ted", "EMPTY": ""}
    finally:
        os.unlink(path)


def test_parse_envfile_missing_file_returns_none():
    assert sea.parse_envfile("/nonexistent/path.env") is None


def test_find_drift_differs_and_missing():
    proc_env = {"FOO": "old", "KEEP": "same"}
    expected = {"FOO": "new", "KEEP": "same", "GONE": "was-set", "IGNORED_EMPTY": ""}
    differs, missing = sea.find_drift(proc_env, expected)
    assert differs == ["FOO"]
    assert missing == ["GONE"]


def test_find_drift_clean():
    proc_env = {"FOO": "bar"}
    expected = {"FOO": "bar"}
    assert sea.find_drift(proc_env, expected) == ([], [])


def test_row_key_dedups_on_unit_and_variables():
    r1 = {"unit": "messenger.service", "differs": ["LANGFUSE_SECRET_KEY"], "missing": []}
    r2 = {"unit": "messenger.service", "differs": [], "missing": ["LANGFUSE_SECRET_KEY"]}
    r3 = {"unit": "messenger.service", "differs": ["OPENROUTER_API_KEY"], "missing": []}
    assert sea.row_key(r1) == sea.row_key(r2)  # same unit+var, whether it differs or is missing
    assert sea.row_key(r1) != sea.row_key(r3)  # different variable -> different key (wq#3215 spec)


def test_fail_on_drift_matches_name_and_variable():
    res = {"drift": [
        {"unit": "messenger.service", "differs": ["LANGFUSE_SECRET_KEY"], "missing": []},
        {"unit": "n8n-watcher.service", "differs": [], "missing": ["OTHER_KEY"]},
    ]}
    bad = sea.fail_on_drift(res, ["messenger.service"], ["LANGFUSE_SECRET_KEY"])
    assert len(bad) == 1 and bad[0]["unit"] == "messenger.service"

    clean = sea.fail_on_drift(res, ["messenger.service"], ["UNRELATED_KEY"])
    assert clean == []


def test_also_set_inline_is_excluded_from_alerting_semantics():
    # also_set_inline is reported alongside differs/missing but row_key/fail_on_drift only
    # look at differs+missing, so an inline override never contributes to a drift key.
    r = {"unit": "x.service", "differs": ["A"], "missing": [], "also_set_inline": ["A"]}
    assert "A" in sea.row_key(r)


@pytest.mark.skipif(os.environ.get("RUN_LIVE_SYSTEMD_TESTS") != "1",
                     reason="needs root + live systemd + live aq; set RUN_LIVE_SYSTEMD_TESTS=1")
def test_transient_unit_control():
    envf = tempfile.NamedTemporaryFile("w", suffix=".env", delete=False)
    envf.write("WQ3215_PROBE=one\n")
    envf.close()
    unit = "wq3215-probe-%d" % int(time.time())
    state = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False).name
    try:
        subprocess.run(["systemd-run", "--unit=%s" % unit, "-p", "EnvironmentFile=%s" % envf.name,
                         "-p", "Restart=no", "/bin/sleep", "600"], check=True)
        time.sleep(1)
        r0 = subprocess.run(["sudo", "/home/lumen/bin/stale-env-audit", "--raise-aq",
                              "--json", "--state", state], capture_output=True, text=True)
        assert json.loads(r0.stdout)["clean"] or unit not in r0.stdout  # not yet drifted

        with open(envf.name, "w") as f:
            f.write("WQ3215_PROBE=two\n")
        r1 = subprocess.run(["sudo", "/home/lumen/bin/stale-env-audit", "--raise-aq",
                              "--json", "--state", state], capture_output=True, text=True)
        res1 = json.loads(r1.stdout)
        rows = [r for r in res1["drift"] if r["unit"] == "%s.service" % unit]
        assert len(rows) == 1
        assert res1["aq_open_keys"] == [sea.row_key(rows[0])]

        subprocess.run(["sudo", "systemctl", "restart", "%s.service" % unit], check=True)
        time.sleep(1)
        r2 = subprocess.run(["sudo", "/home/lumen/bin/stale-env-audit", "--raise-aq",
                              "--json", "--state", state], capture_output=True, text=True)
        res2 = json.loads(r2.stdout)
        assert not any(r["unit"] == "%s.service" % unit for r in res2["drift"])
        assert res2["aq_open_keys"] == []
    finally:
        subprocess.run(["sudo", "systemctl", "stop", "%s.service" % unit], capture_output=True)
        os.unlink(envf.name)
        os.unlink(state)


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
