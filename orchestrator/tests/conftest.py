import pytest


@pytest.fixture(autouse=True)
def _scratch_cli_canary(monkeypatch, tmp_path):
    """wq#2527: router.select reads the canary cache; the live one must not hold a rung inside
    a routing test that isn't about it."""
    monkeypatch.setenv("FOREMAN_CLI_CANARY", str(tmp_path / "cli_canary.json"))
