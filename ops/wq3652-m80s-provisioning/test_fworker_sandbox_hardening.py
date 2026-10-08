"""wq#3652 step 5: the fworker-sandbox@.service hardening directives are the one
part of this ticket a BUILD-worker sandbox CAN demonstrate without root, real M80
hardware, or any of the ssh/docker/useradd/mkfs primitives harness/worker-
settings.json and harness/fleet-exec-policy.yaml deny it. See README.md in this
directory for the steps that stay privileged/physical follow-ups.
"""
from __future__ import annotations

from pathlib import Path

UNIT = Path(__file__).resolve().parent / "fworker-sandbox@.service"

REQUIRED_HARDENING = {
    "NoNewPrivileges": "yes",
    "ProtectSystem": "strict",
    "ProtectHome": "yes",
    "PrivateTmp": "yes",
}


def _directives():
    out = {}
    for line in UNIT.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or line.startswith("["):
            continue
        k, _, v = line.partition("=")
        out.setdefault(k, []).append(v)
    return out


def test_unit_carries_the_ticket_literal_hardening_set():
    d = _directives()
    for k, v in REQUIRED_HARDENING.items():
        assert d.get(k) == [v], f"{k}={v} missing or changed"


def test_unit_scopes_readwrite_to_var_fworker_only():
    d = _directives()
    assert d["ReadWritePaths"] == ["/var/fworker"]


def test_unit_runs_as_the_fworker_system_account():
    d = _directives()
    assert d["User"] == ["fworker"]
    assert d["Group"] == ["fworker"]


def test_unit_denies_the_tailnet_cgnat_block_and_the_lan():
    d = _directives()
    denied = " ".join(d["IPAddressDeny"]).split()
    assert "100.64.0.0/10" in denied
    assert "192.168.8.0/24" in denied


def test_unit_allows_only_loopback_and_the_lumen_slash32():
    d = _directives()
    allowed = " ".join(d["IPAddressAllow"]).split()
    assert "100.117.66.102/32" in allowed
    # No bare LAN or tailnet-wide entry sneaks into the allow list — only /32s
    # and loopback, so the Allow side can never re-open what Deny just closed.
    for entry in allowed:
        if entry not in ("127.0.0.0/8", "::1"):
            assert entry.endswith("/32"), f"{entry} is not a loopback entry or a /32"
