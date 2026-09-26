#!/usr/bin/env python3
"""wq#2166 — gpu-lowpower must never exit 0 while a card is not at dpm=low.

Runs standalone: python3 ops/tests/test_wq2166_gpu_lowpower.py
No node is contacted: the script cases run against a fake DRM tree (DRM_ROOT), the
install.sh --verify cases stub `ssh` on PATH.

The defect being pinned: hathor's unit wrote the knob inline with
`for f in /sys/class/drm/card*/...; do echo low > "$f" 2>/dev/null || true; done; exit 0`.
When the glob matched nothing — which is what happened on both 2026-09-19 boots, because
multi-user.target came at monotonic 17.8s and the cards at 18.2s/19.2s — bash passed the
literal pattern through, every redirection failed, and the unit still reported success.
`systemctl is-active` read `active` for a day while both D300s sat at auto/balanced.

So every case here asks one of two questions: does an unmatched glob stay a no-op, and
does a card that is not at low produce a non-zero exit.
"""
import hashlib
import os
import stat
import subprocess
import sys
import tempfile
import textwrap
import threading
import time
import unittest

REPO = os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir, os.pardir)
BUNDLE = os.path.join(REPO, "ops", "wq2166-gpu-lowpower-ordering")
SCRIPT = os.path.join(BUNDLE, "gpu-lowpower.sh")
INSTALL = os.path.join(BUNDLE, "install.sh")

LVL = "power_dpm_force_performance_level"
ST = "power_dpm_state"

DSTS = {
    "/usr/local/sbin/gpu-lowpower.sh": "gpu-lowpower.sh",
    "/etc/systemd/system/gpu-lowpower.service": "gpu-lowpower.service",
    "/etc/systemd/system/gpu-lowpower@.service": "gpu-lowpower@.service",
    "/etc/udev/rules.d/99-gpu-lowpower.rules": "99-gpu-lowpower.rules",
}


def sha(path):
    with open(path, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


def make_card(root, name, level="auto", state="balanced"):
    dev = os.path.join(root, name, "device")
    os.makedirs(dev, exist_ok=True)
    with open(os.path.join(dev, LVL), "w") as fh:
        fh.write(level + "\n")
    if state is not None:
        with open(os.path.join(dev, ST), "w") as fh:
            fh.write(state + "\n")
    return dev


def read_attr(root, name, attr):
    with open(os.path.join(root, name, "device", attr)) as fh:
        return fh.read().strip()


def run_script(root, *args, **env):
    e = dict(os.environ, DRM_ROOT=root)
    e.setdefault("WAIT_SECS", "1")
    e.setdefault("CARD_WAIT_SECS", "1")
    e.setdefault("EXPECTED_CARDS", "2")
    e.update({k: str(v) for k, v in env.items()})
    return subprocess.run(["bash", SCRIPT, *args], env=e, capture_output=True, text=True)


class Applier(unittest.TestCase):
    def test_two_cards_apply_and_verify(self):
        with tempfile.TemporaryDirectory() as root:
            make_card(root, "card1")
            make_card(root, "card2")
            r = run_script(root)
            self.assertEqual(r.returncode, 0, r.stderr)
            for c in ("card1", "card2"):
                self.assertEqual(read_attr(root, c, LVL), "low")
                self.assertEqual(read_attr(root, c, ST), "battery")

    def test_no_cards_fails_and_writes_no_literal_glob(self):
        """The wq#2166 bug itself: an unmatched glob must be a no-op, not a path."""
        with tempfile.TemporaryDirectory() as root:
            r = run_script(root)
            self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
            self.assertIn("0/2", r.stderr)
            self.assertEqual(os.listdir(root), [], "an unmatched glob was treated as a filename")

    def test_card_that_appears_late_is_still_applied(self):
        """The race hathor lost by 0.4s and 1.3s on 2026-09-19."""
        with tempfile.TemporaryDirectory() as root:
            make_card(root, "card1")
            threading.Timer(2.0, make_card, args=(root, "card2")).start()
            start = time.time()
            r = run_script(root, WAIT_SECS=20)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertGreaterEqual(time.time() - start, 2.0)
            self.assertEqual(read_attr(root, "card2", LVL), "low")

    def test_unwritable_knob_fails_loudly(self):
        with tempfile.TemporaryDirectory() as root:
            make_card(root, "card1")
            dev = make_card(root, "card2")
            os.chmod(os.path.join(dev, LVL), 0o444)
            r = run_script(root)
            self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
            self.assertIn("card2", r.stderr)

    def test_write_accepted_but_value_does_not_stick_fails(self):
        """sysfs can take a write and keep the old value. Only a read-back catches it."""
        with tempfile.TemporaryDirectory() as root:
            make_card(root, "card1")
            dev = make_card(root, "card2")
            os.remove(os.path.join(dev, LVL))
            os.symlink(os.devnull, os.path.join(dev, LVL))
            r = run_script(root)
            self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
            self.assertIn("card2", r.stderr)
            self.assertEqual(read_attr(root, "card1", LVL), "low")

    def test_missing_power_dpm_state_is_not_a_failure(self):
        with tempfile.TemporaryDirectory() as root:
            make_card(root, "card1", state=None)
            make_card(root, "card2", state=None)
            r = run_script(root)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_card_mode_applies_only_that_card(self):
        with tempfile.TemporaryDirectory() as root:
            make_card(root, "card1")
            make_card(root, "card2")
            r = run_script(root, "--card", "card1")
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertEqual(read_attr(root, "card1", LVL), "low")
            self.assertEqual(read_attr(root, "card2", LVL), "auto")

    def test_card_mode_missing_card_fails(self):
        with tempfile.TemporaryDirectory() as root:
            make_card(root, "card1")
            r = run_script(root, "--card", "card9")
            self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
            self.assertIn("card9", r.stderr)

    def test_check_mode_writes_nothing(self):
        with tempfile.TemporaryDirectory() as root:
            make_card(root, "card1")
            make_card(root, "card2")
            r = run_script(root, "--check")
            self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
            self.assertEqual(read_attr(root, "card1", LVL), "auto")
            self.assertEqual(read_attr(root, "card2", LVL), "auto")

    def test_check_mode_passes_when_already_low(self):
        with tempfile.TemporaryDirectory() as root:
            make_card(root, "card1", level="low", state="battery")
            make_card(root, "card2", level="low", state="battery")
            r = run_script(root, "--check")
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_unknown_argument_exits_2(self):
        with tempfile.TemporaryDirectory() as root:
            r = run_script(root, "--wat")
            self.assertEqual(r.returncode, 2, r.stdout + r.stderr)


def node_probe(level="low", state="battery", active="active", status="0",
               cards=("card1", "card2"), shas=None, drop=()):
    """Build what the --verify probe prints on a node."""
    lines = []
    for c in cards:
        lines.append("level=%s:%s" % (c, level))
        if state is not None:
            lines.append("state=%s:%s" % (c, state))
    lines.append("active=" + active)
    lines.append("status=" + status)
    for dst, src in DSTS.items():
        if dst in drop:
            lines.append("sha=%s:" % dst)
            continue
        if shas and dst in shas:
            lines.append("sha=%s:%s" % (dst, shas[dst]))
            continue
        lines.append("sha=%s:%s" % (dst, sha(os.path.join(BUNDLE, src))))
    return "\n".join(lines) + "\n"


def run_verify(node_out, node_rc=None):
    node_rc = node_rc or {}
    with tempfile.TemporaryDirectory() as tmp:
        for node, out in node_out.items():
            with open(os.path.join(tmp, node + ".out"), "w") as fh:
                fh.write(out)
        for node, rc in node_rc.items():
            with open(os.path.join(tmp, node + ".rc"), "w") as fh:
                fh.write(str(rc))
        stub = os.path.join(tmp, "ssh")
        with open(stub, "w") as fh:
            fh.write(textwrap.dedent("""\
                #!/usr/bin/env bash
                node="${@:(-2):1}"
                if [ -f "$STUB_DIR/$node.rc" ]; then exit "$(cat "$STUB_DIR/$node.rc")"; fi
                cat "$STUB_DIR/$node.out"
            """))
        os.chmod(stub, os.stat(stub).st_mode | stat.S_IEXEC)
        env = dict(os.environ, PATH=tmp + os.pathsep + os.environ["PATH"], STUB_DIR=tmp)
        return subprocess.run(["bash", INSTALL, "--verify"], env=env,
                              capture_output=True, text=True)


class VerifyContract(unittest.TestCase):
    def test_healthy_fleet_passes(self):
        r = run_verify({"hathor": node_probe(), "nephthys": node_probe()})
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("VERIFY OK", r.stdout)

    def test_hathor_as_found_on_2026_09_20_fails(self):
        """active, ExecMainStatus=0, both cards auto, none of the files installed.

        This is the exact reading wq#2157 took. A verify that passes it is worthless.
        """
        found = node_probe(level="auto", state="balanced", drop=tuple(DSTS))
        r = run_verify({"hathor": found, "nephthys": node_probe()})
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn("hathor: card1 dpm=auto", r.stdout)
        self.assertIn("hathor: card2 dpm=auto", r.stdout)
        self.assertIn("power_dpm_state=balanced", r.stdout)
        self.assertIn("absent: /usr/local/sbin/gpu-lowpower.sh", r.stdout)
        self.assertNotIn("nephthys", r.stdout)

    def test_one_card_missing_fails(self):
        r = run_verify({"hathor": node_probe(cards=("card1",)), "nephthys": node_probe()})
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn("1 cards expose the dpm knob", r.stdout)

    def test_nonzero_exec_status_fails(self):
        r = run_verify({"hathor": node_probe(status="1"), "nephthys": node_probe()})
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn("ExecMainStatus=1", r.stdout)

    def test_inactive_unit_fails(self):
        r = run_verify({"hathor": node_probe(active="failed"), "nephthys": node_probe()})
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn("is-active=failed", r.stdout)

    def test_drifted_file_fails(self):
        drifted = {"/usr/local/sbin/gpu-lowpower.sh": "0" * 64}
        r = run_verify({"hathor": node_probe(shas=drifted), "nephthys": node_probe()})
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn("differs: /usr/local/sbin/gpu-lowpower.sh", r.stdout)

    def test_unreachable_node_exits_2(self):
        r = run_verify({"nephthys": node_probe()}, node_rc={"hathor": 255})
        self.assertEqual(r.returncode, 2, r.stdout)
        self.assertIn("ERR cannot probe hathor", r.stdout)

    def test_verify_marker_is_present_for_the_drift_sweep(self):
        with open(INSTALL) as fh:
            body = fh.read()
        marker = body.index('if [ "${1:-}" = --verify ]; then')
        self.assertLess(marker, body.index("install -m0755"),
                        "--verify handler must precede every install step")


class UdevRuleContract(unittest.TestCase):
    """wq#2178: two rule defects found on hathor before the first apply.

    hathor exposes card1, card2 AND twelve connector nodes card1-DP-1 .. card2-DP-12
    (DEVTYPE=drm_connector) that also match KERNEL=="card[0-9]*" and DRIVERS=="amdgpu".
    And 99-systemd.rules only aliases sys-subsystem-{net,bluetooth}-devices-*, so the
    BindsTo= target in gpu-lowpower@.service must be created by this rule or it never exists.
    """

    def setUp(self):
        with open(os.path.join(BUNDLE, "99-gpu-lowpower.rules")) as fh:
            self.rules = [l for l in fh.read().splitlines()
                          if l.strip() and not l.lstrip().startswith("#")]
        with open(os.path.join(BUNDLE, "gpu-lowpower@.service")) as fh:
            self.tmpl = fh.read()

    def test_single_rule(self):
        self.assertEqual(len(self.rules), 1, self.rules)

    def test_rule_excludes_connectors(self):
        self.assertIn('ENV{DEVTYPE}=="drm_minor"', self.rules[0])

    def test_rule_creates_the_device_the_template_binds_to(self):
        self.assertIn("BindsTo=sys-subsystem-drm-devices-%i.device", self.tmpl)
        self.assertIn('ENV{SYSTEMD_ALIAS}+="/sys/subsystem/drm/devices/%k"', self.rules[0])


if __name__ == "__main__":
    unittest.main(verbosity=2, argv=[sys.argv[0], *sys.argv[1:]])
