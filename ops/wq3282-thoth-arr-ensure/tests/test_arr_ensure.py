"""wq#3282: thoth-arr-ensure only starts things, only while wg0 is active, never touches wg0,
and honours disabled units and the pause file. A fake systemctl keeps state in a temp dir."""
import configparser
import os
import stat
import subprocess

HERE = os.path.dirname(os.path.abspath(__file__))
D = os.path.join(HERE, "..")
SCRIPT = os.path.join(D, "thoth-arr-ensure")

FAKE = """#!/usr/bin/env bash
D="$FAKE_DIR"; cmd="$1"; shift
case "$cmd" in
  is-active)  s=inactive; [ -f "$D/$1.active" ] && s=$(cat "$D/$1.active"); echo "$s"; [ "$s" = active ] ;;
  is-enabled) s=disabled; [ -f "$D/$1.enabled" ] && s=$(cat "$D/$1.enabled"); echo "$s"; [ "$s" = enabled ] ;;
  start) echo "start $1" >> "$D/log"; [ -f "$D/$1.startfail" ] && exit 1; echo active > "$D/$1.active" ;;
  reset-failed) echo "reset-failed $1" >> "$D/log" ;;
  *) echo "$cmd $*" >> "$D/log"; echo "UNEXPECTED $cmd" >> "$D/unexpected" ;;
esac
"""
UNITS = ["prowlarr", "sonarr", "radarr", "bazarr", "qbittorrent-nox"]


def run(tmp_path, state, pause=False, script=SCRIPT):
    fake = tmp_path / "systemctl"
    fake.write_text(FAKE)
    fake.chmod(fake.stat().st_mode | stat.S_IXUSR)
    for name, val in state.items():
        (tmp_path / name).write_text(val)
    env = dict(os.environ, FAKE_DIR=str(tmp_path), TA_SYSTEMCTL=str(fake),
               TA_PAUSE_FILE=str(tmp_path / "pause"))
    if pause:
        (tmp_path / "pause").write_text("")
    p = subprocess.run(["bash", script], env=env, capture_output=True, text=True)
    log = (tmp_path / "log").read_text().splitlines() if (tmp_path / "log").exists() else []
    assert not (tmp_path / "unexpected").exists(), "script called a systemctl verb other than is-*/start/reset-failed"
    return p, log


def up(*units, gate="active", extra=None):
    s = {"wg-quick@wg0.active": gate}
    for u in UNITS:
        s[f"{u}.enabled"] = "enabled"
        s[f"{u}.active"] = "active" if u in units else "inactive"
    s.update(extra or {})
    return s


def test_everything_up_is_a_noop(tmp_path):
    p, log = run(tmp_path, up(*UNITS))
    assert p.returncode == 0 and log == []


def test_one_stopped_unit_is_started_and_only_that_one(tmp_path):
    p, log = run(tmp_path, up(*[u for u in UNITS if u != "sonarr"]))
    assert p.returncode == 0 and log == ["start sonarr"]


def test_whole_stack_down_after_wg_restart_comes_back(tmp_path):
    p, log = run(tmp_path, up())
    assert p.returncode == 0 and log == [f"start {u}" for u in UNITS]


def test_wg0_down_means_hands_off(tmp_path):
    p, log = run(tmp_path, up(gate="inactive"))
    assert p.returncode == 0 and log == []


def test_wg0_activating_means_hands_off(tmp_path):
    p, log = run(tmp_path, up(gate="activating"))
    assert log == []


def test_disabled_unit_is_left_down(tmp_path):
    p, log = run(tmp_path, up(*[u for u in UNITS if u != "radarr"], extra={"radarr.enabled": "disabled"}))
    assert log == []


def test_failed_unit_is_reset_then_started(tmp_path):
    p, log = run(tmp_path, up(*[u for u in UNITS if u != "bazarr"], extra={"bazarr.active": "failed"}))
    assert log == ["reset-failed bazarr", "start bazarr"]


def test_pause_file_wins(tmp_path):
    p, log = run(tmp_path, up(), pause=True)
    assert p.returncode == 0 and log == []


def test_a_failed_start_exits_1_and_the_rest_still_get_tried(tmp_path):
    p, log = run(tmp_path, up(extra={"prowlarr.startfail": ""}))
    assert p.returncode == 1
    assert log == [f"start {u}" for u in UNITS]


def test_script_never_stops_or_restarts_anything():
    txt = open(SCRIPT).read()
    code = "\n".join(l for l in txt.splitlines() if not l.lstrip().startswith("#"))
    for verb in ("stop", "restart", "kill", "disable", "mask", "daemon-reload"):
        assert f'"$SYSTEMCTL" {verb}' not in code, verb


def _unit(name):
    p = configparser.ConfigParser(strict=False, interpolation=None)
    p.optionxform = str
    p.read(os.path.join(D, name))
    return p


def test_units_are_a_5min_oneshot_ordered_after_wg0():
    s = _unit("thoth-arr-ensure.service")
    assert s["Service"]["Type"] == "oneshot"
    assert s["Service"]["ExecStart"] == "/usr/local/sbin/thoth-arr-ensure"
    assert "wg-quick@wg0.service" in s["Unit"]["After"]
    assert "BindsTo" not in s["Unit"] and "Requires" not in s["Unit"] and "PartOf" not in s["Unit"]  # must not be pulled down with wg0
    t = _unit("thoth-arr-ensure.timer")
    assert t["Timer"]["OnUnitActiveSec"] == "5min"
    assert t["Install"]["WantedBy"] == "timers.target"


def test_install_has_verify_before_any_install_step_and_refuses_other_hosts():
    txt = open(os.path.join(D, "install.sh")).read()
    assert txt.index("--verify") < txt.index("install -m")
    assert '"$(hostname)" = thoth' in txt
