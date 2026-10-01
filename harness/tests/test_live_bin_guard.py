"""wq#3222: the suite-wide guard that keeps tests off the live aq and ntfy-send."""
import importlib
import os
import shutil
import subprocess
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
HARNESS = os.path.dirname(HERE)
sys.path.insert(0, HARNESS)

import live_bin_guard as g  # noqa: E402

AQ, NTFY = g.LIVE_BINS


@pytest.mark.parametrize("args,shell,want", [
    ([AQ, "add", "src", "k", "t", "b"], False, AQ),
    (["timeout", "5", NTFY, "-t", "lumen-fleet"], False, NTFY),
    (f"echo hi; {AQ} add x y z w", True, AQ),
    ([AQ + "-shim"], False, None),
    (["echo", "x", "y", AQ], False, None),   # a path as an argument is not a spawn of it
])
def test_live_target_matches_the_resolved_live_path(args, shell, want):
    assert g.live_target(args, shell=shell) == want


def test_a_tests_own_fake_named_aq_is_not_live(tmp_path):
    """overnight-1's recorder matched any argv ending in "/aq", so test_scratch_grant's
    _fake_aq (tmp_path/aq) looked like 3 real aq calls — RESULT 2 of wq#3222. The
    guard compares resolved paths."""
    fake = tmp_path / "aq"
    fake.write_text("#!/bin/sh\n")
    assert g.live_target([str(fake), "add"]) is None


def test_bare_name_resolves_through_the_callers_path(tmp_path):
    if not os.path.exists(AQ):
        pytest.skip("no live aq on this host to resolve to")
    assert g.live_target(["aq", "add"], env={"PATH": os.path.dirname(AQ)}) == AQ
    assert g.live_target(["aq", "add"], env={"PATH": str(tmp_path)}) is None


def test_env_points_every_alert_binary_somewhere_that_does_not_exist():
    for var, path in g.ENV.items():
        assert os.environ[var] == path
        assert not os.path.exists(path)


def test_no_worker_session_env_reaches_a_test():
    assert not [k for k in g.WORKER_SESSION_ENV if k in os.environ]


def test_reloaded_and_reimported_modules_still_read_the_fake():
    """The order-dependence wq#3222 was filed on: a per-test setattr on a module global
    is escaped by any module object created after it — reload() or a sys.modules pop
    (test_liveness_clear.clear_mod). The env var is read by every fresh import."""
    import capability_proposal
    import mask_at_rest
    assert importlib.reload(capability_proposal).AQ_BIN == g.NO_AQ
    assert importlib.reload(mask_at_rest).AQ_BIN == g.NO_AQ
    assert mask_at_rest.NTFY_BIN == g.NO_NTFY
    saved = sys.modules.pop("read_dir_sweep", None)
    try:
        import read_dir_sweep
        assert read_dir_sweep.AQ_BIN == g.NO_AQ
    finally:
        if saved is not None:
            sys.modules["read_dir_sweep"] = saved


def _stand_in_live_bin(tmp_path, monkeypatch, live=NTFY):
    """A script the guard treats as the live `live`; it leaves a marker if it ever
    actually runs. Nothing here can reach a real binary even if the guard is broken."""
    marker = tmp_path / "ran"
    bin_ = tmp_path / "pretend-live-bin"
    bin_.write_text(f"#!/bin/sh\ntouch {marker}\n")
    bin_.chmod(0o755)
    monkeypatch.setattr(g, "_live_real", {**g._live_real, os.path.realpath(bin_): live})
    return str(bin_), marker


def test_live_aq_on_a_scratch_queue_is_allowed(tmp_path, monkeypatch):
    """dispatcher/tests/test_dispatch_health_idle.py drives the real aq CLI on a
    tmp_path AQ_DB and asserts the row lands there; that is not the live queue."""
    bin_, marker = _stand_in_live_bin(tmp_path, monkeypatch, live=AQ)
    seen = len(g.hits)
    subprocess.run([bin_], check=True, env=dict(os.environ, AQ_DB=str(tmp_path / "a.db")))
    assert marker.exists() and len(g.hits) == seen
    for db in (None, g.LIVE_AQ_DB):
        env = {k: v for k, v in os.environ.items() if k != "AQ_DB"}
        if db:
            env["AQ_DB"] = db
        with pytest.raises(FileNotFoundError):
            subprocess.run([bin_], env=env)
    del g.hits[seen:]


def test_a_live_spawn_is_refused_recorded_and_never_run(tmp_path, monkeypatch):
    bin_, marker = _stand_in_live_bin(tmp_path, monkeypatch)
    seen = len(g.hits)
    with pytest.raises(FileNotFoundError, match="live_bin_guard"):
        subprocess.run([bin_, "add"], capture_output=True)
    with pytest.raises(FileNotFoundError):
        subprocess.Popen(f"true && {bin_} add", shell=True)
    assert not marker.exists()
    new = g.hits[seen:]
    del g.hits[seen:]  # consumed here, or conftest's _no_live_aq fails this test
    assert [argv for _, argv in new] == [[bin_, "add"], [f"true && {bin_} add"]]
    assert all("test_a_live_spawn_is_refused" in t for t, _ in new)
    subprocess.run(["true"], check=True)  # ordinary spawns are untouched


def test_a_live_spawn_fails_the_test_that_made_it(tmp_path):
    """End to end through a child pytest on a copy of this conftest: the offending test
    fails by name and the run exits non-zero even though the code under test swallowed
    the OSError (the fail-open shape every alert call site has)."""
    for f in ("conftest.py", "live_bin_guard.py"):
        shutil.copy(os.path.join(HERE, f), tmp_path / f)
    (tmp_path / "test_leak.py").write_text(
        "import os, subprocess\n"
        "import live_bin_guard as g\n\n"
        "def test_swallows_it(tmp_path):\n"
        "    b = tmp_path / 'pretend-live-bin'\n"
        "    b.write_text('#!/bin/sh\\n')\n"
        "    b.chmod(0o755)\n"
        "    g._live_real[os.path.realpath(b)] = g.LIVE_BINS[1]\n"
        "    try:\n"
        "        subprocess.run([str(b)])\n"
        "    except OSError:\n"
        "        pass\n\n"
        "def test_clean():\n"
        "    pass\n")
    env = dict(os.environ, PYTHONPATH=os.pathsep.join([HARNESS, str(tmp_path)]))
    p = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:randomly",
                        "-p", "no:cacheprovider", f"--basetemp={tmp_path / 'bt'}",
                        str(tmp_path / "test_leak.py")],
                       cwd=tmp_path, env=env, capture_output=True, text=True, timeout=300)
    out = p.stdout + p.stderr
    assert p.returncode == 1, out
    assert "2 passed, 1 error" in out, out
    assert "ERROR test_leak.py::test_swallows_it - Failed: reached a live alert binary" in out, out
