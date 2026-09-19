import pytest


@pytest.fixture(autouse=True)
def _hallucination_guard_off(monkeypatch):
    """The guard defaults to advisory, which calls live isis ollama from every verify()
    (wq#2045: test_success_settles_spawn_reservation 24s vs 4.4s). The guard's own tests
    inject `check=` or set the mode themselves."""
    monkeypatch.setenv("FOREMAN_HALLUCINATION_GUARD", "off")


@pytest.fixture(autouse=True)
def _no_refill_kick(monkeypatch):
    """wq#2069: every fworker exit calls trigger_refill(), which sudo-starts the LIVE
    lumen-foreman-dispatch.service. Subprocess fworkers inherit os.environ, so this
    keeps the suite from kicking production passes. test_wq992_refill.py delenv's it
    for the tests that exercise the kick path (they fake subprocess.run)."""
    monkeypatch.setenv("FOREMAN_NO_REFILL", "1")
