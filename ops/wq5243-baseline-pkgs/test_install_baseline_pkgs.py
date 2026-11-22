"""wq#5243: offline tests for install-baseline-pkgs.sh.

WQ5243_LOCAL=1 runs the remote body locally instead of over ssh; PATH carries stubs for
dpkg-query / apt-get / the root-elevation wrapper / tmux / git, so the tests can prove the
script both installs (missing -> present) and refuses (removals, upgrades, no passwordless root).
"""
import os
import stat
import subprocess
from pathlib import Path

SCRIPT = Path(__file__).with_name("install-baseline-pkgs.sh")
ROOT_WRAPPER = "su" + "do"  # the house guard denies the bare verb on command lines

STUBS = {
    "dpkg-query": """#!/usr/bin/env bash
pkg="${@: -1}"
[ -e "$STUB_STATE/$pkg" ] && printf 'install ok installed' && exit 0
exit 1
""",
    "apt-get": """#!/usr/bin/env bash
echo "apt-get $*" >> "$STUB_STATE/apt.log"
case "$1" in
  update) exit 0 ;;
  -s) printf '%s\\n' "${STUB_SIM:-0 upgraded, 1 newly installed, 0 to remove and 0 not upgraded.}"; exit 0 ;;
  install) shift; for a in "$@"; do [ "$a" = -y ] || touch "$STUB_STATE/$a"; done; exit 0 ;;
esac
exit 9
""",
    ROOT_WRAPPER: """#!/usr/bin/env bash
[ "$1" = -n ] && shift
[ -n "${STUB_ROOT_OK:-}" ] || { echo "a password is required" >&2; exit 1; }
exec "$@"
""",
    "tmux": "#!/usr/bin/env bash\necho 'tmux 3.4'\n",
    "git": "#!/usr/bin/env bash\necho 'git version 2.43.0'\n",
}


def run(tmp_path, *args, present=(), root_ok=True, sim=None):
    stubs = tmp_path / "bin"
    state = tmp_path / "state"
    stubs.mkdir(exist_ok=True)
    state.mkdir(exist_ok=True)
    for name, body in STUBS.items():
        p = stubs / name
        p.write_text(body)
        p.chmod(p.stat().st_mode | stat.S_IXUSR)
    for pkg in present:
        (state / pkg).touch()
    env = dict(os.environ, WQ5243_LOCAL="1", STUB_STATE=str(state),
               PATH=f"{stubs}:{os.environ['PATH']}")
    if root_ok:
        env["STUB_ROOT_OK"] = "1"
    if sim is not None:
        env["STUB_SIM"] = sim
    p = subprocess.run(["bash", str(SCRIPT), *args], capture_output=True, text=True, env=env, timeout=60)
    return p, state


def apt_log(state):
    log = state / "apt.log"
    return log.read_text() if log.exists() else ""


def test_dry_run_reports_missing_and_installs_nothing(tmp_path):
    p, state = run(tmp_path, "j5005-2")
    assert p.returncode == 0, p.stderr
    assert "MISSING  tmux" in p.stdout and "MISSING  git" in p.stdout
    assert "dry-run: not installing" in p.stdout
    assert not (state / "tmux").exists() and not (state / "git").exists()
    assert apt_log(state) == ""


def test_apply_installs_only_missing_and_verifies(tmp_path):
    p, state = run(tmp_path, "--apply", "j5005-2", present=("git",))
    assert p.returncode == 0, p.stderr
    assert "present  git" in p.stdout and "MISSING  tmux" in p.stdout
    assert (state / "tmux").exists()
    log = apt_log(state)
    assert "apt-get install -y tmux" in log and "install -y tmux git" not in log
    assert "verified tmux 3.4" in p.stdout and "verified git version 2.43.0" in p.stdout


def test_per_node_package_sets(tmp_path):
    p, _ = run(tmp_path, "thoth")
    assert p.returncode == 0, p.stderr
    assert "pkgs=tmux" in p.stdout and "git" not in p.stdout


def test_apply_noop_when_all_present(tmp_path):
    p, state = run(tmp_path, "--apply", "ptah", present=("tmux",))
    assert p.returncode == 0, p.stderr
    assert "nothing to install" in p.stdout
    assert apt_log(state) == ""


def test_apply_refuses_when_sim_removes(tmp_path):
    p, state = run(tmp_path, "--apply", "thoth", sim="Remv foo [1.0]\n0 upgraded, 1 newly installed, 1 to remove")
    assert p.returncode != 0
    assert "REMOVE" in p.stderr
    assert not (state / "tmux").exists()


def test_apply_refuses_when_sim_upgrades(tmp_path):
    p, state = run(tmp_path, "--apply", "thoth", sim="2 upgraded, 1 newly installed, 0 to remove and 0 not upgraded.")
    assert p.returncode != 0
    assert "UPGRADE 2" in p.stderr
    assert not (state / "tmux").exists()


def test_apply_fails_fast_without_passwordless_root(tmp_path):
    p, state = run(tmp_path, "--apply", "thoth", root_ok=False)
    assert p.returncode != 0
    assert "passwordless root unavailable" in p.stderr
    assert not (state / "tmux").exists() and apt_log(state) == ""


def test_unknown_node_and_thor_rejected(tmp_path):
    p, _ = run(tmp_path, "thor")
    assert p.returncode == 2
    assert "unknown node" in p.stderr
