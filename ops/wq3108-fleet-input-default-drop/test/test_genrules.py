"""wq#3108 genrules tests. Fixtures are committed (immutable probe inputs, wq#2630):
a container-host node (DOCKER-* chains, no ts-input) and a tailscaled-managed node
(ts-input jump). The container-host fixture is deliberately OUTSIDE the population
the standard was designed around — its FORWARD table and custom chains must survive
a full restore untouched, and its wildcard :8080 listener must be reported CUT."""
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
GEN = HERE.parent / "genrules.py"
VARS = HERE / "fixtures" / "test.vars"
CONTAINER_NODE = HERE / "fixtures" / "docker-node"
TS_NODE = HERE / "fixtures" / "ts-node"


def run(recon, vars_file, out):
    return subprocess.run(
        [sys.executable, str(GEN), "--recon", str(recon),
         "--vars", str(vars_file), "--out", str(out)],
        capture_output=True, text=True)


def test_refuses_without_recon_bundle(tmp_path):
    r = run(tmp_path / "nonexistent", VARS, tmp_path / "out")
    assert r.returncode == 2
    assert "recon" in r.stderr


def test_refuses_tbd_vars(tmp_path):
    tbd = tmp_path / "tbd.vars"
    tbd.write_text("LAN_IFACE=TBD-from-recon\nLAN_SUBNET=192.168.8.0/24\n")
    r = run(CONTAINER_NODE, tbd, tmp_path / "out")
    assert r.returncode == 2
    assert "LAN_IFACE" in r.stderr


def test_container_node_staged_v4(tmp_path):
    out = tmp_path / "out"
    assert run(CONTAINER_NODE, VARS, out).returncode == 0
    staged = (out / "staged.v4").read_text()
    live = (CONTAINER_NODE / "live.v4").read_text()

    assert ":INPUT DROP [1000:80000]" in staged
    # nat table INPUT untouched
    assert ":INPUT ACCEPT [50:3000]" in staged
    # every live custom-chain rule survives verbatim (full-restore safety)
    for line in live.splitlines():
        if line.startswith("-A "):
            assert line in staged, f"live rule lost: {line}"
    assert ":FORWARD DROP [0:0]" in staged
    # the standard, in order, ahead of policy enforcement
    assert "-A INPUT -i lo -j ACCEPT" in staged
    assert "-A INPUT -m conntrack --ctstate RELATED,ESTABLISHED -j ACCEPT" in staged
    assert "-A INPUT -i tailscale0 -j ACCEPT" in staged
    assert ("-A INPUT -i enp1s0 -s 192.168.8.0/24 -p tcp --dport 22 -j ACCEPT"
            in staged)
    assert "-A INPUT -p icmp -j ACCEPT" in staged
    assert ("-A INPUT -i enp1s0 -s 192.168.8.0/24 -p tcp --dport 445 -j ACCEPT"
            in staged)


def test_container_node_staged_v6(tmp_path):
    out = tmp_path / "out"
    assert run(CONTAINER_NODE, VARS, out).returncode == 0
    staged = (out / "staged.v6").read_text()
    assert ":INPUT DROP [500:40000]" in staged
    assert "-A INPUT -p ipv6-icmp -j ACCEPT" in staged
    assert "--dport 546" not in staged  # DHCPV6=no


def test_ts_node_keeps_jump_and_skips_duplicate_accept(tmp_path):
    out = tmp_path / "out"
    assert run(TS_NODE, VARS, out).returncode == 0
    staged = (out / "staged.v4").read_text()
    lines = staged.splitlines()
    assert "-A INPUT -j ts-input" in lines
    # tailscaled owns tailnet admission on this node; no duplicate INPUT accept
    assert "-A INPUT -i tailscale0 -j ACCEPT" not in lines
    assert "-A ts-input -i tailscale0 -j ACCEPT" in lines  # its own chain intact
    # jump stays ahead of our inserted standard block, matching live order
    assert (lines.index("-A INPUT -j ts-input")
            < lines.index("-A INPUT -i lo -j ACCEPT"))


def test_coverage_report(tmp_path):
    out = tmp_path / "out"
    assert run(CONTAINER_NODE, VARS, out).returncode == 0
    cov = (out / "coverage.txt").read_text()
    row = {l.split()[1]: l for l in cov.splitlines()}
    assert "LAN ssh (rule 4)" in row["0.0.0.0:22"]
    assert "EXTRA accept" in row["0.0.0.0:445"]
    assert "lo only" in row["127.0.0.1:5432"]
    # the falsifying case: an unreviewed wildcard listener MUST surface as CUT
    assert "CUT" in row["*:8080"]
    assert "CUT" in row["0.0.0.0:5353"]  # avahi udp
