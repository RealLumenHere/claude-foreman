"""wq#5334 adversarial probes against ops/wq5247-failed-units (PR #2786, f208b82df0).

Run: python3 -m pytest -q reports/5334_verify_wq5247_probe_test.py
Each probe drives the shipped scripts with the bundle's own stub pattern (no root, no
ssh). The bundle is pinned to this branch's checkout (ops/wq5247-failed-units, which is
byte-identical to merge f208b82df0), not a live node.

P1 asserts the CURRENT (defective) behaviour so it passes while the defect exists:
an unreadable-but-healthy grubenv is classed bad-header and recreated.
P2 asserts the design gap: a maksoor unit that is not failed right now is left without
the skip drop-in even though its backlight device is absent (re-fails next boot).
P3 asserts the bes fail-safe: a healthy on-disk grubenv (the LP#2156423 boot-race
signature) is not touched and is reported UNFIXED.
"""
import os
import stat
import subprocess
import textwrap

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
B = os.path.join(ROOT, "ops", "wq5247-failed-units")
LIB = os.path.join(B, "lib.sh")
GOOD_ENV = b"# GRUB Environment Block\n" + b"#" * (1024 - 25)


def write_exe(path, body):
    with open(path, "w") as f:
        f.write("#!/usr/bin/env bash\n" + textwrap.dedent(body))
    os.chmod(path, 0o755)


@pytest.fixture
def stubs(tmp_path):
    b = tmp_path / "bin"
    b.mkdir()
    state = tmp_path / "unit_state"
    state.write_text("failed")
    write_exe(b / "systemctl", f"""
        case "$1" in
          is-failed) cat "{state}" ;;
          restart) echo inactive > "{state}" ;;
          cat) cat "{tmp_path}/unit_text" 2>/dev/null ;;
          *) : ;;
        esac
    """)
    # sudo stub: restore read permission first, standing in for root's DAC override
    # (only for the commands that touch grubenv, so the unprivileged read stays unprivileged)
    write_exe(b / "sudo", '[ "$1" = -n ] && shift\n'
              'case "$1" in cp|grub-editenv) chmod u+rw "$WQ5247_GRUBENV" 2>/dev/null ;; esac\n'
              'exec "$@"\n')
    write_exe(b / "journalctl", "echo stub-journal\n")
    write_exe(b / "grub-editenv", """
        if [ "$2" = create ]; then printf '# GRUB Environment Block\\n' > "$1"; exit 0; fi
        [ -r "$1" ] || { echo "grub-editenv: error: cannot open \\`$1': Permission denied." >&2; exit 1; }
        head -c 24 "$1" | grep -q '^# GRUB Environment Block' && exit 0
        echo "grub-editenv: error: invalid environment block." >&2; exit 1
    """)
    env = dict(os.environ, PATH=f"{b}:{os.environ['PATH']}")
    return tmp_path, b, state, env


def remote(name, env, *args):
    body = open(LIB).read() + open(os.path.join(B, name)).read()
    return subprocess.run(["bash", "-s", "--", *args], input=body, capture_output=True,
                          text=True, env=env, timeout=60)


@pytest.mark.skipif(os.geteuid() == 0, reason="root reads a 0000 file; probe needs non-root")
def test_p1_unreadable_healthy_grubenv_is_recreated(stubs):
    tmp, b, state, env = stubs
    g = tmp / "grubenv"
    g.write_bytes(GOOD_ENV.replace(b"#" * 20, b"saved_entry=keepme\n#", 1)[:1024])
    original = g.read_bytes()
    os.chmod(g, 0)
    env = dict(env, WQ5247_GRUBENV=str(g))
    p = subprocess.run(["bash", "-c", f'. "{LIB}"; grubenv_state "{g}"'],
                       capture_output=True, text=True, env=env, timeout=30)
    assert p.stdout.strip() == "bad-header"  # read failure reported as corruption
    p = remote("remote-bes.sh", env, "0", "P1")
    assert "VERDICT bes: grubenv corrupt" in p.stdout, p.stdout + p.stderr
    os.chmod(g, 0o644)
    assert b"saved_entry=keepme" not in g.read_bytes()  # healthy env wiped
    assert (tmp / "grubenv.corrupt.bak.P1").read_bytes() == original  # but backed up


def test_p2_maksoor_absent_device_not_failed_gets_no_dropin(stubs):
    tmp, b, state, env = stubs
    state.write_text("inactive")
    (tmp / "unit_text").write_text(
        "ExecStart=/bin/sh -c 'echo 1 > /sys/class/backlight/wq5334_absent/brightness'\n")
    d = tmp / "dropin.d"
    p = remote("remote-maksoor.sh", dict(env, WQ5247_DROPIN_DIR=str(d)), "0")
    assert p.returncode == 0 and "absent: /sys/class/backlight/wq5334_absent" in p.stdout
    assert "nothing to do" in p.stdout and not d.exists()


def test_p3_bes_healthy_grubenv_failed_unit_is_unfixed_not_touched(stubs):
    tmp, b, state, env = stubs
    g = tmp / "grubenv"
    g.write_bytes(GOOD_ENV)
    p = remote("remote-bes.sh", dict(env, WQ5247_GRUBENV=str(g)), "0", "P3")
    assert p.returncode == 1 and "UNFIXED bes: grubenv is not corrupt" in p.stdout
    assert g.read_bytes() == GOOD_ENV and state.read_text().strip() == "failed"
