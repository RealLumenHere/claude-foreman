"""wq#1931: the three unit-hardening defects found at despag's first live install.

1. app.py _load_or_create_token chmod-ed the token file on EVERY start; under the unit's
   ProtectHome=read-only that is EROFS and the service never came up.
2. IPAddressAllow=localhost + IPAddressDeny=any filter EGRESS for the whole cgroup, so every
   spawned `claude` child lost the API and the turn hung.
3. burn_rate.frank_active() failing the WRONG direction lives in
   dispatcher/tests/test_burn_rate.py (it is the dispatcher's function).
"""
from __future__ import annotations

import importlib
import logging
import os
import re
from pathlib import Path

import pytest

UNIT = Path(__file__).resolve().parents[2] / "harness" / "systemd" / "lumen-despag.service"

# Hardening directives the unit carried before wq#1931. The ONLY ones allowed to disappear
# are the two IP filters; everything else must survive (the security score may not regress
# except for those two).
KEPT_HARDENING = {
    "NoNewPrivileges": "true",
    "PrivateTmp": "true",
    "ProtectSystem": "strict",
    "ProtectHome": "read-only",
}
REMOVED = {"IPAddressAllow", "IPAddressDeny"}
# The operator drop-in's exact ReadWritePaths (the CLI's session/config/cache dirs + the token).
DROPIN_RW = ["/home/lumen/.claude", "/home/lumen/.cache", "/home/lumen/.config",
             "/home/lumen/.secrets/despag-api.token"]


def _directives():
    out = {}
    for line in UNIT.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or line.startswith("["):
            continue
        k, _, v = line.partition("=")
        out.setdefault(k, []).append(v)
    return out


def test_unit_drops_the_egress_filters():
    d = _directives()
    assert not (REMOVED & set(d)), f"{REMOVED & set(d)} filter egress for the spawned claude children"


def test_unit_keeps_every_other_hardening_directive():
    d = _directives()
    for k, v in KEPT_HARDENING.items():
        assert d.get(k) == [v], f"{k}={v} regressed"


def test_unit_path_reaches_the_npm_global_cli_first():
    paths = [e for e in _directives()["Environment"] if e.startswith("PATH=")]
    assert len(paths) == 1 and paths[0].startswith("PATH=/home/lumen/.npm-global/bin:")


def test_unit_readwritepaths_cover_the_operator_dropin_and_the_original_roots():
    rw = " ".join(_directives()["ReadWritePaths"]).split()
    for p in DROPIN_RW + ["/home/lumen/worktrees", "-/home/lumen/despag-data"]:
        assert p in rw, f"ReadWritePaths lost {p}"


# ---- defect 1: token chmod only on create ------------------------------------------------

@pytest.fixture()
def app_module(tmp_path, monkeypatch):
    monkeypatch.setenv("DESPAG_API_TOKEN_FILE", str(tmp_path / "token"))
    monkeypatch.setenv("DESPAG_DATA_DIR", str(tmp_path / "data"))
    import despag.app as m
    return importlib.reload(m)


def test_a_new_token_is_created_0600(app_module, tmp_path):
    p = tmp_path / "fresh-token"
    os.environ["DESPAG_API_TOKEN_FILE"] = str(p)
    tok = app_module._load_or_create_token()
    assert tok and (p.stat().st_mode & 0o777) == 0o600 and p.read_text().strip() == tok


def test_an_existing_token_is_never_chmoded(app_module, tmp_path, monkeypatch):
    """The read-only-fs shape: chmod would raise EROFS. The function must not even try."""
    p = tmp_path / "existing"
    p.write_text("abc")
    p.chmod(0o600)

    def no_chmod(*a, **k):
        raise OSError(30, "Read-only file system")
    monkeypatch.setattr(Path, "chmod", no_chmod)
    monkeypatch.setenv("DESPAG_API_TOKEN_FILE", str(p))
    assert app_module._load_or_create_token() == "abc"


def test_a_loose_existing_token_warns_and_is_left_alone(app_module, tmp_path, monkeypatch, caplog):
    p = tmp_path / "loose"
    p.write_text("abc")
    p.chmod(0o644)
    monkeypatch.setenv("DESPAG_API_TOKEN_FILE", str(p))
    with caplog.at_level(logging.WARNING, logger="despag"):
        assert app_module._load_or_create_token() == "abc"
    assert (p.stat().st_mode & 0o777) == 0o644
    assert re.search(r"mode 644", caplog.text)
