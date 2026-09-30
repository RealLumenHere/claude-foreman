"""wq#3215: the shipped units must run a real, executable tool with the flag that opens aq rows,
and treat 'drift found' (exit 1) as success while a total read failure (exit 2) is a failure."""
import configparser
import os

HERE = os.path.dirname(os.path.abspath(__file__))
D = os.path.join(HERE, "..")


def _unit(name):
    p = configparser.ConfigParser(strict=False, interpolation=None)
    p.optionxform = str
    p.read(os.path.join(D, name))
    return p


def test_service_runs_the_audit_with_aq_and_exit_1_is_success():
    s = _unit("lumen-stale-env-audit.service")["Service"]
    assert s["ExecStart"] == "/home/lumen/bin/stale-env-audit --aq"
    assert s["SuccessExitStatus"].split() == ["0", "1"]        # 2 (nothing readable) must stay a failure
    assert s["Type"] == "oneshot"
    assert "User" not in s                                       # root: needs other uids' /proc/environ


def test_timer_is_hourly_and_enabled_by_timers_target():
    t = _unit("lumen-stale-env-audit.timer")
    assert t["Timer"]["OnCalendar"] == "hourly"
    assert t["Install"]["WantedBy"] == "timers.target"


def test_install_script_has_the_verify_handler_before_any_install_step():
    txt = open(os.path.join(D, "install.sh")).read()
    assert txt.index("--verify") < txt.index("sudo install")
