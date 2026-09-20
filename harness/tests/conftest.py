import pytest


@pytest.fixture(autouse=True)
def _hallucination_guard_off(monkeypatch):
    """The guard defaults to advisory, which calls live isis ollama from every verify()
    (wq#2045: test_success_settles_spawn_reservation 24s vs 4.4s). The guard's own tests
    inject `check=` or set the mode themselves."""
    monkeypatch.setenv("FOREMAN_HALLUCINATION_GUARD", "off")


@pytest.fixture(autouse=True)
def _no_live_aq(monkeypatch, tmp_path):
    """wq#1697: fworker's CAPABILITY_DENIED path files an aq row. No test may open one
    on the live alert queue; capability_proposal fails open on a missing binary."""
    import capability_proposal
    monkeypatch.setattr(capability_proposal, "AQ_BIN", str(tmp_path / "no-aq"))


@pytest.fixture(autouse=True)
def _scratch_root_in_tmp(monkeypatch, tmp_path):
    """wq#1697: build_env creates the wq scratch dir; keep it out of the real /tmp."""
    import scratch_grant
    monkeypatch.setattr(scratch_grant, "SCRATCH_ROOT", str(tmp_path / "scratch"))


@pytest.fixture(autouse=True)
def _no_live_token_engine(monkeypatch, tmp_path):
    """wq#2278: build_env on a local-pool model asks the LIVE token-engine broker
    for a per-ticket gateway key when its socket exists. Point every test at a
    nonexistent socket (mint falls back to the static key file with no I/O);
    test_gateway_key_per_ticket.py overrides with its own fake broker."""
    import fworker
    # env var too: tests that importlib.reload(fworker) re-read it from os.environ.
    monkeypatch.setenv("TOKEN_ENGINE_SOCK", str(tmp_path / "no-broker.sock"))
    monkeypatch.setattr(fworker, "TOKEN_ENGINE_SOCK", str(tmp_path / "no-broker.sock"))


@pytest.fixture(autouse=True)
def _no_refill_kick(monkeypatch):
    """wq#2069: every fworker exit calls trigger_refill(), which sudo-starts the LIVE
    lumen-foreman-dispatch.service. Subprocess fworkers inherit os.environ, so this
    keeps the suite from kicking production passes. test_wq992_refill.py delenv's it
    for the tests that exercise the kick path (they fake subprocess.run)."""
    monkeypatch.setenv("FOREMAN_NO_REFILL", "1")
