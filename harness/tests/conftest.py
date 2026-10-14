import pytest

import live_bin_guard
from id_mask import pytest_make_parametrize_id  # noqa: F401  (wq#3834 hook)

live_bin_guard.install()


@pytest.fixture(autouse=True)
def _hallucination_guard_off(monkeypatch):
    """The guard defaults to advisory, which calls live isis ollama from every verify()
    (wq#2045: test_success_settles_spawn_reservation 24s vs 4.4s). The guard's own tests
    inject `check=` or set the mode themselves."""
    monkeypatch.setenv("FOREMAN_HALLUCINATION_GUARD", "off")


@pytest.fixture(autouse=True)
def _no_live_aq():
    """wq#1697: fworker's CAPABILITY_DENIED path files an aq row, wq#3164: so does
    mask_at_rest's held-unmasked alert; no test may open one on the live alert queue.
    wq#3222: the per-test monkeypatch of two module globals this used to be is replaced
    by live_bin_guard (env set at conftest import + a refusing Popen wrapper); this
    fixture fails whichever test tried a live aq/ntfy-send spawn anyway."""
    seen = len(live_bin_guard.hits)
    yield
    new = live_bin_guard.hits[seen:]
    if new:
        pytest.fail(f"reached a live alert binary (refused, not run): {new}", pytrace=False)


def pytest_sessionfinish(session, exitstatus):
    live_bin_guard.session_finish(session)


@pytest.fixture(autouse=True)
def _scratch_root_in_tmp(monkeypatch, tmp_path):
    """wq#1697: build_env creates the wq scratch dir; keep it out of the real /tmp."""
    import scratch_grant
    monkeypatch.setattr(scratch_grant, "SCRATCH_ROOT", str(tmp_path / "scratch"))


@pytest.fixture(autouse=True)
def _fresh_mask_state():
    """wq#2404: the tripwire's dedup sets live for the process, because an fworker
    process lives for a whole ticket. A test process is many runs, and they share
    ticket ids (999290 across test_burn_filter.py), so each test starts fresh —
    otherwise test 2 reads test 1's already-alarmed label and stays silent."""
    import fworker
    fworker._MASK_STATE.clear()
    yield
    fworker._MASK_STATE.clear()


@pytest.fixture(autouse=True)
def _no_refill_kick(monkeypatch):
    """wq#2069: every fworker exit calls trigger_refill(), which sudo-starts the LIVE
    lumen-foreman-dispatch.service. Subprocess fworkers inherit os.environ, so this
    keeps the suite from kicking production passes. test_wq992_refill.py delenv's it
    for the tests that exercise the kick path (they fake subprocess.run)."""
    monkeypatch.setenv("FOREMAN_NO_REFILL", "1")


@pytest.fixture(autouse=True)
def _scratch_cli_canary(monkeypatch, tmp_path):
    """wq#2527: spawner -> router reads the canary cache; never the live one in a test."""
    monkeypatch.setenv("FOREMAN_CLI_CANARY", str(tmp_path / "cli_canary.json"))


@pytest.fixture(autouse=True)
def _no_live_episode_commit(monkeypatch, tmp_path):
    """wq#2901: the shim tests run fworker end to end, so commit_episode_to_archive
    fires for real against the LIVE minerva.db — 67 notes, 430 audit write rows and
    319 memoryd rejects a day under agent `fworker-wq1`, all from pytest (same class
    as wq#2784). Point the CLI at a no-op stub: the commit path still runs a real
    binary under subprocess.run(check=True), it just cannot reach the store. Tests
    that assert on the CLI pass episode_bin= themselves."""
    stub = tmp_path / "lumen_episode_stub.py"
    stub.write_text("import sys\nsys.exit(0)\n")
    monkeypatch.setenv("LUMEN_EPISODE_BIN", str(stub))


@pytest.fixture(autouse=True)
def _no_mask_at_rest_sweep(monkeypatch):
    """2026-09-26: mask_alarm() -> trigger_mask_at_rest() Popens a detached, REAL
    `mask_at_rest.py --store all` (whole store + offsite restic). test_masking.py calls
    mask_alarm directly, so every suite run of a masking ticket leaked a sweep into the
    worker's cgroup (wq#2720: 96 of them). Tests that exercise the trigger fake Popen."""
    monkeypatch.setenv("FOREMAN_NO_MASK_AT_REST", "1")


@pytest.fixture(autouse=True)
def _no_live_ntfy(monkeypatch):
    """mask_at_rest.page_alarm() runs the REAL /home/lumen/bin/ntfy-send -t lumen-fleet
    whenever a run finds an unaccounted value, and 8 tests in test_mask_at_rest.py plant
    exactly that, so every ordinary run of the file (workers, verifiers, hand runs) pushed
    8 real "credential found unmasked at rest" pages to lumen-fleet (measured 2026-09-30
    by intercepting subprocess). Tests that assert on paging patch page_alarm themselves,
    which wins over this. live_bin_guard would refuse the spawn anyway; this keeps the
    page out of the test so the guard only fires on a regression."""
    try:
        import mask_at_rest
    except ImportError:
        return
    monkeypatch.setattr(mask_at_rest, "page_alarm", lambda unaccounted, run_id: None)

