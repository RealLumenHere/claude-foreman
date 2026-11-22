"""wq#5247: decision logic of the failed-units bundle, driven against fixtures.

No root, no live audit subsystem, no ssh: system tools (systemctl, augenrules, auditctl,
grub-editenv, sudo, journalctl) are replaced by stubs on PATH, and every path the scripts
act on is a tmp fixture. Each test can fail: the fixtures put an absent path, a corrupt
grubenv or a missing backlight device in front of the real script logic.
"""
import os
import stat
import subprocess
import textwrap

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
LIB = os.path.join(HERE, "lib.sh")
SCRIPTS = ["install.sh", "lib.sh", "lumen-audit.sh", "remote-bes.sh", "remote-maksoor.sh"]
GOOD_ENV = b"# GRUB Environment Block\n" + b"#" * (1024 - 25)


def sh(script, *args, env=None, stdin=None):
    return subprocess.run(["bash", "-c", script, "bash", *args], capture_output=True,
                          text=True, env=env, input=stdin, timeout=60)


def lib(call, *args):
    p = sh(f'. "{LIB}"; {call}', *args)
    return p.returncode, p.stdout


def write_exe(path, body):
    with open(path, "w") as f:
        f.write("#!/usr/bin/env bash\n" + textwrap.dedent(body))
    os.chmod(path, os.stat(path).st_mode | stat.S_IXUSR)


@pytest.fixture
def stubs(tmp_path):
    """PATH dir with systemctl/sudo/journalctl/auditctl stubs; unit state in a file."""
    b = tmp_path / "bin"
    b.mkdir()
    state = tmp_path / "unit_state"
    state.write_text("failed")
    write_exe(b / "systemctl", f"""
        case "$1" in
          is-failed) cat "{state}" ;;
          restart) echo inactive > "{state}" ;;
          cat) if [ "$2" = grub2-common.service ]; then cat "{tmp_path}/g2c_text" 2>/dev/null
               else cat "{tmp_path}/unit_text" 2>/dev/null; fi ;;
          *) : ;;
        esac
    """)
    write_exe(b / "sudo", '[ "$1" = -n ] && shift; exec "$@"\n')
    write_exe(b / "journalctl", "echo stub-journal\n")
    write_exe(b / "auditctl", "echo enabled 1\n")
    env = dict(os.environ, PATH=f"{b}:{os.environ['PATH']}")
    return tmp_path, b, state, env


@pytest.mark.parametrize("name", SCRIPTS)
def test_scripts_parse(name):
    p = subprocess.run(["bash", "-n", os.path.join(HERE, name)], capture_output=True, text=True)
    assert p.returncode == 0, p.stderr


# --- lib.sh ---------------------------------------------------------------------------

def test_audit_error_lines():
    out = ("Error sending add rule data request (No such file or directory)\n"
           "There was an error in line 7 of /etc/audit/audit.rules\n"
           "There was an error in line 12 of /etc/audit/audit.rules\n")
    p = sh(f'. "{LIB}"; audit_error_lines', stdin=out)
    assert p.stdout.split() == ["7", "12"]


@pytest.mark.parametrize("rule,want", [
    ("-w /etc/shadow -p wa -k ident", "/etc/shadow"),
    ("-w/opt/x -p wa", "/opt/x"),
    ("-a always,exit -F dir=/srv/data -F perm=w -k d", "/srv/data"),
    ("-a always,exit -F path=/usr/bin/su -F perm=x", "/usr/bin/su"),
    ("-a always,exit -F arch=b64 -S execve -k exec", ""),
    ("-b 8192", ""),
])
def test_audit_watch_path(rule, want):
    assert lib('audit_watch_path "$1"', rule)[1].strip() == want


def test_audit_rule_sources_and_disable(tmp_path):
    d = tmp_path / "rules.d"
    d.mkdir()
    rule = "-w /nonexistent/wq5247 -p wa -k gone"
    (d / "10-base.rules").write_text("-D\n-b 8192\n")
    (d / "50-x.rules").write_text(f"# keep\n{rule}\r\n-w /etc/passwd -p wa -k id\n")
    rc, out = lib('audit_rule_sources "$1" "$2"', str(d), rule)
    assert out.split() == [str(d / "50-x.rules")]

    f = d / "50-x.rules"
    ino = f.stat().st_ino
    rc, _ = lib('audit_disable_rule "$1" "$2" "$3"', str(f), rule, "wq5247 test")
    assert rc == 0
    lines = f.read_text().splitlines()
    assert lines[0] == "# keep"
    assert lines[1].startswith("# wq5247 test: " + rule)
    assert lines[2] == "-w /etc/passwd -p wa -k id"
    assert f.stat().st_ino == ino  # edited in place, owner/mode kept

    before = f.read_text()
    rc, _ = lib('audit_disable_rule "$1" "$2" "$3"', str(f), "-w /not/there", "t")
    assert rc == 1 and f.read_text() == before


def test_grubenv_state(tmp_path):
    g = tmp_path / "grubenv"
    assert lib('grubenv_state "$1"', str(g))[1].strip() == "absent"
    g.write_bytes(GOOD_ENV)
    assert lib('grubenv_state "$1"', str(g))[1].strip() == "ok"
    g.write_bytes(b"\x00" * 1024)
    assert lib('grubenv_state "$1"', str(g))[1].strip() == "bad-header"
    g.write_bytes(GOOD_ENV[:600])
    assert lib('grubenv_state "$1"', str(g))[1].strip() == "bad-size:600"


def test_grubenv_state_unreadable_is_not_bad_header(tmp_path):
    # wq#5337 (verify 5334 probe P1): a read failure must not read as a bad signature.
    # A failing READER stands in for "no permission" so the probe holds even as root.
    g = tmp_path / "grubenv"
    g.write_bytes(GOOD_ENV)
    deny = tmp_path / "deny"
    write_exe(deny, 'case "$1" in head|stat) echo "$1: Permission denied" >&2; exit 1 ;; esac; exec "$@"\n')
    assert lib('grubenv_state "$1" "$2"', str(g), str(deny))[1].strip() == "unreadable"
    allow = tmp_path / "allow"
    write_exe(allow, 'exec "$@"\n')
    assert lib('grubenv_state "$1" "$2"', str(g), str(allow))[1].strip() == "ok"
    if os.geteuid() != 0:  # real mode-000 file, unprivileged reader
        g.chmod(0)
        try:
            assert lib('grubenv_state "$1"', str(g))[1].strip() == "unreadable"
        finally:
            g.chmod(0o644)


@pytest.mark.parametrize("text,want", [
    ("[Unit]\nAfter=local-fs.target\nAfter=grub2-common.service\n", "fixed"),
    ("[Unit]\nAfter=local-fs.target grub-common.service\n", "stale"),
    ("[Unit]\n# After=grub-common.service\nAfter=local-fs.target\n", "none"),
    ("[Unit]\nAfter=grub-common.service\n[Unit]\nAfter=grub2-common.service\n", "fixed"),
])
def test_initrd_fallback_order(text, want):
    assert sh(f'. "{LIB}"; initrd_fallback_order', stdin=text).stdout.strip() == want


def test_backlight_targets_and_dropin():
    unit = ("[Service]\n# /sys/class/backlight/commented/brightness\n"
            "ExecStart=/bin/sh -c 'echo 7142 > /sys/class/backlight/intel_backlight/brightness'\n")
    p = sh(f'. "{LIB}"; backlight_targets', stdin=unit)
    assert p.stdout.split() == ["/sys/class/backlight/intel_backlight"]
    _, out = lib('backlight_dropin "$@"', "/sys/class/backlight/intel_backlight")
    assert "[Unit]" in out and "ConditionPathExists=/sys/class/backlight/intel_backlight" in out
    _, out = lib("backlight_dropin")
    assert "ConditionPathExistsGlob=/sys/class/backlight/*" in out


# --- lumen-audit.sh -------------------------------------------------------------------

def _augenrules_stub(b, rules, rules_d, refuse_syscall=False):
    """Mimic augenrules --load: rebuild RULES from rules.d (comments dropped), then refuse
    each line whose watched path is absent (or any -S rule when refuse_syscall)."""
    write_exe(b / "augenrules", f"""
        [ "$1" = --check ] && {{ echo "augenrules: No change"; exit 0; }}
        cat "{rules_d}"/*.rules | sed 's/\\r$//' | grep -v '^[[:space:]]*#' | grep -v '^$' > "{rules}"
        rc=0; n=0
        while IFS= read -r l; do
          n=$((n+1)); set -- $l
          if [ "$1" = -w ] && [ ! -e "$2" ]; then echo "There was an error in line $n of {rules}"; rc=1; fi
          if [ "{int(refuse_syscall)}" = 1 ] && [[ "$l" == *" -S "* ]]; then
            echo "There was an error in line $n of {rules}"; rc=1; fi
        done < "{rules}"
        exit $rc
    """)


def _audit_env(stubs, refuse_syscall=False):
    tmp, b, state, env = stubs
    d = tmp / "rules.d"
    d.mkdir()
    (d / "10-base.rules").write_text("-D\n-b 8192\n-a always,exit -F arch=b64 -S execve -k e\n")
    (d / "60-agent.rules").write_text(
        "-w /etc/passwd -p wa -k id\n-w /nonexistent/wq5247/dir -p wa -k gone\n")
    rules = tmp / "audit.rules"
    _augenrules_stub(b, rules, d, refuse_syscall)
    env = dict(env, WQ5247_AUDIT_RULES=str(rules), WQ5247_RULES_D=str(d))
    return d, rules, state, env


def test_lumen_audit_disables_absent_watch(stubs):
    d, rules, state, env = _audit_env(stubs)
    p = subprocess.run(["bash", os.path.join(HERE, "lumen-audit.sh"), "0", "TESTTS"],
                       capture_output=True, text=True, env=env, timeout=60)
    assert p.returncode == 0, p.stdout + p.stderr
    assert "cause: watched path /nonexistent/wq5247/dir does not exist" in p.stdout
    assert "VERDICT lumen: loaded after disabling" in p.stdout
    txt = (d / "60-agent.rules").read_text()
    assert "# wq5247 disabled TESTTS" in txt and "-w /etc/passwd -p wa -k id\n" in txt
    assert (d / "60-agent.rules.bak.TESTTS_wq5247").read_text().count("/nonexistent/") == 1
    assert state.read_text().strip() == "inactive"


def test_lumen_audit_refuses_other_class(stubs):
    d, rules, state, env = _audit_env(stubs, refuse_syscall=True)
    (d / "60-agent.rules").write_text("-w /etc/passwd -p wa -k id\n")
    p = subprocess.run(["bash", os.path.join(HERE, "lumen-audit.sh"), "0", "T"],
                       capture_output=True, text=True, env=env, timeout=60)
    assert p.returncode == 1
    assert "UNFIXED lumen: line 3 is not the absent-path class" in p.stdout
    assert not list(d.glob("*.bak.*")) and state.read_text().strip() == "failed"


def test_lumen_audit_diagnose_is_static(stubs):
    d, rules, state, env = _audit_env(stubs)
    rules.write_text("-w /etc/passwd -p wa -k id\n-w /nonexistent/wq5247/dir -p wa -k gone\n")
    p = subprocess.run(["bash", os.path.join(HERE, "lumen-audit.sh"), "1"],
                       capture_output=True, text=True, env=env, timeout=60)
    assert p.returncode == 0
    assert "line 2 absent path /nonexistent/wq5247/dir" in p.stdout
    assert "load pass" not in p.stdout and not list(d.glob("*.bak.*"))


# --- remote-bes.sh / remote-maksoor.sh --------------------------------------------------

def _remote(name, env, *args):
    body = open(LIB).read() + open(os.path.join(HERE, name)).read()
    return subprocess.run(["bash", "-s", "--", *args], input=body, capture_output=True,
                          text=True, env=env, timeout=60)


def _grub_stub(b, g):
    write_exe(b / "grub-editenv", f"""
        if [ "$2" = create ]; then printf '# GRUB Environment Block\\n' > "$1"; exit 0; fi
        head -c 24 "$1" | grep -q '^# GRUB Environment Block' && exit 0
        echo "grub-editenv: error: invalid environment block." >&2; exit 1
    """)


def test_bes_corrupt_grubenv(stubs):
    tmp, b, state, env = stubs
    g = tmp / "grubenv"
    g.write_bytes(b"\x00" * 1024)
    _grub_stub(b, g)
    env = dict(env, WQ5247_GRUBENV=str(g))
    p = _remote("remote-bes.sh", env, "1", "TS1")
    assert p.returncode == 0, p.stdout + p.stderr
    assert "VERDICT bes: grubenv corrupt" in p.stdout and g.read_bytes() == b"\x00" * 1024
    p = _remote("remote-bes.sh", env, "0", "TS1")
    assert p.returncode == 0, p.stdout + p.stderr
    assert (tmp / "grubenv.corrupt.bak.TS1").read_bytes() == b"\x00" * 1024
    assert g.read_bytes().startswith(b"# GRUB Environment Block")
    assert state.read_text().strip() == "inactive"


def test_bes_healthy_grubenv_is_not_touched(stubs):
    # wq#5332: a valid grubenv means the boot-time failure is gone — re-run the unit,
    # never back up or recreate the file.
    tmp, b, state, env = stubs
    g = tmp / "grubenv"
    g.write_bytes(GOOD_ENV)
    _grub_stub(b, g)
    env = dict(env, WQ5247_GRUBENV=str(g))
    p = _remote("remote-bes.sh", env, "1", "TS2")
    assert p.returncode == 0 and "would: reset-failed + restart" in p.stdout, p.stdout
    assert state.read_text().strip() == "failed"
    p = _remote("remote-bes.sh", env, "0", "TS2")
    assert p.returncode == 0 and "boot-time failure only" in p.stdout, p.stdout + p.stderr
    assert state.read_text().strip() == "inactive"
    assert not (tmp / "grubenv.corrupt.bak.TS2").exists() and g.read_bytes() == GOOD_ENV


def test_bes_healthy_grubenv_restart_still_fails(stubs):
    tmp, b, state, env = stubs
    g = tmp / "grubenv"
    g.write_bytes(GOOD_ENV)
    _grub_stub(b, g)
    write_exe(b / "systemctl", f'[ "$1" = is-failed ] && cat "{state}"; exit 0\n')
    p = _remote("remote-bes.sh", dict(env, WQ5247_GRUBENV=str(g)), "0", "TS3")
    assert p.returncode == 1 and "UNFIXED bes: grub-initrd-fallback.service still fails" in p.stdout
    assert not (tmp / "grubenv.corrupt.bak.TS3").exists() and g.read_bytes() == GOOD_ENV


def test_bes_bad_size_grubenv_is_refused(stubs):
    tmp, b, state, env = stubs
    g = tmp / "grubenv"
    g.write_bytes(b"# GRUB Environment Block\n" + b"#" * 575)
    _grub_stub(b, g)
    p = _remote("remote-bes.sh", dict(env, WQ5247_GRUBENV=str(g)), "0", "TS4")
    assert p.returncode == 1 and "UNFIXED bes: grubenv is not corrupt" in p.stdout
    assert state.read_text().strip() == "failed"
    assert not (tmp / "grubenv.corrupt.bak.TS4").exists()


@pytest.mark.skipif(os.geteuid() == 0, reason="root reads a mode-000 file")
def test_bes_unreadable_grubenv_is_not_recreated(stubs):
    # wq#5337 / verify 5334 probe P1: healthy env the ssh user cannot read, sudo -n
    # refused. The first bundle called it bad-header -> "grubenv corrupt" -> recreate.
    tmp, b, state, env = stubs
    g = tmp / "grubenv"
    g.write_bytes(GOOD_ENV)
    _grub_stub(b, g)
    write_exe(b / "sudo", "echo 'sudo: a password is required' >&2; exit 1\n")
    g.chmod(0)
    try:
        for dry in ("1", "0"):
            p = _remote("remote-bes.sh", dict(env, WQ5247_GRUBENV=str(g)), dry, "TS5")
            assert p.returncode == 1, p.stdout + p.stderr
            assert "grubenv_state: unreadable" in p.stdout
            assert "UNFIXED bes: cannot read" in p.stdout
            assert "grubenv corrupt" not in p.stdout
    finally:
        g.chmod(0o644)
    assert g.read_bytes() == GOOD_ENV and not (tmp / "grubenv.corrupt.bak.TS5").exists()
    assert state.read_text().strip() == "failed"


STALE_UNIT = ("# /usr/lib/systemd/system/grub-initrd-fallback.service\n[Unit]\n"
              "After=local-fs.target\nAfter=grub-common.service\nAfter=sleep.target\n")


def _race_env(stubs, grub2_common=True):
    tmp, b, state, env = stubs
    g = tmp / "grubenv"
    g.write_bytes(GOOD_ENV)
    _grub_stub(b, g)
    (tmp / "unit_text").write_text(STALE_UNIT)
    if grub2_common:
        (tmp / "g2c_text").write_text("[Unit]\nDescription=Record successful boot for GRUB\n")
    d = tmp / "dropin"
    return g, d, state, dict(env, WQ5247_GRUBENV=str(g), WQ5247_DROPIN_DIR=str(d))


def test_bes_lp2156423_race_gets_ordering_dropin(stubs):
    g, d, state, env = _race_env(stubs)
    p = _remote("remote-bes.sh", env, "1", "TS6")
    assert p.returncode == 0, p.stdout + p.stderr
    assert "LP#2156423 race possible: 1" in p.stdout and "would: install drop-in" in p.stdout
    assert not d.exists() and state.read_text().strip() == "failed"
    p = _remote("remote-bes.sh", env, "0", "TS6")
    assert p.returncode == 0, p.stdout + p.stderr
    conf = (d / "wq5337-lp2156423.conf").read_text()
    assert "[Unit]\nAfter=grub2-common.service\n" in conf
    assert state.read_text().strip() == "inactive" and g.read_bytes() == GOOD_ENV


def test_bes_lp2156423_race_fixed_even_when_not_failed(stubs):
    g, d, state, env = _race_env(stubs)
    state.write_text("inactive")
    p = _remote("remote-bes.sh", env, "0", "TS7")
    assert p.returncode == 0 and "not failed now" in p.stdout, p.stdout + p.stderr
    assert (d / "wq5337-lp2156423.conf").exists() and g.read_bytes() == GOOD_ENV


def test_bes_old_name_without_grub2_common_is_no_race(stubs):
    # Pre-rename package: grub-common.service is the real unit, so the order is right.
    g, d, state, env = _race_env(stubs, grub2_common=False)
    p = _remote("remote-bes.sh", env, "0", "TS8")
    assert p.returncode == 0 and "LP#2156423 race possible: 0" in p.stdout, p.stdout
    assert not d.exists() and state.read_text().strip() == "inactive"


def test_maksoor_absent_backlight_gets_dropin(stubs):
    tmp, b, state, env = stubs
    (tmp / "unit_text").write_text(
        "[Service]\nType=oneshot\n"
        "ExecStart=/bin/sh -c 'echo 7142 > /sys/class/backlight/wq5247_absent_dev/brightness'\n")
    dropin = tmp / "dropin.d"
    env = dict(env, WQ5247_DROPIN_DIR=str(dropin))
    p = _remote("remote-maksoor.sh", env, "1")
    assert p.returncode == 0 and "would install" in p.stdout and not dropin.exists()
    p = _remote("remote-maksoor.sh", env, "0")
    assert p.returncode == 0, p.stdout + p.stderr
    conf = (dropin / "wq5247-require-backlight.conf").read_text()
    assert "ConditionPathExists=/sys/class/backlight/wq5247_absent_dev" in conf
    assert state.read_text().strip() == "inactive"


def test_maksoor_not_failed_is_left_alone(stubs):
    tmp, b, state, env = stubs
    state.write_text("inactive")
    (tmp / "unit_text").write_text(
        "ExecStart=/bin/sh -c 'echo 1 > /sys/class/backlight/wq5247_absent_dev/brightness'\n")
    dropin = tmp / "dropin.d"
    p = _remote("remote-maksoor.sh", dict(env, WQ5247_DROPIN_DIR=str(dropin)), "0")
    assert p.returncode == 0 and "not failed now" in p.stdout and not dropin.exists()
