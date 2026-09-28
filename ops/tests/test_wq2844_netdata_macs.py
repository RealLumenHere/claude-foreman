#!/usr/bin/env python3
"""wq#2844 — netdata on the Macs must be bound to the tailnet IP only, and --verify must say so.

Standalone: python3 ops/tests/test_wq2844_netdata_macs.py
No node is contacted: `ssh` and `fleet-inv` are stubbed on PATH.
"""
import hashlib, os, re, stat, subprocess, tempfile, unittest

REPO = os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir, os.pardir)
BUNDLE = os.path.join(REPO, "ops", "wq2844-netdata-macs")
INSTALL = os.path.join(BUNDLE, "install.sh")


def _sha(p):
    with open(p, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


def _stub(d, name, body):
    p = os.path.join(d, name)
    with open(p, "w") as fh:
        fh.write("#!/bin/sh\n" + body)
    os.chmod(p, os.stat(p).st_mode | stat.S_IXUSR)


class Bundle(unittest.TestCase):
    def test_syntax(self):
        self.assertEqual(subprocess.run(["bash", "-n", INSTALL]).returncode, 0)

    def test_keyring_is_the_pinned_one(self):
        with open(INSTALL) as fh:
            pinned = re.search(r"^KEY_SHA256=(\w+)", fh.read(), re.M).group(1)
        self.assertEqual(_sha(os.path.join(BUNDLE, "netdata-archive-keyring.gpg")), pinned)

    def test_verify_marker_precedes_anything_that_installs(self):
        with open(INSTALL) as fh:
            s = fh.read()
        self.assertLess(s.index('= --verify ]'), s.index("apt-get"))

    def test_template_is_tailnet_only_and_agent_only(self):
        with open(os.path.join(BUNDLE, "netdata.conf.template")) as fh:
            t = fh.read()
        self.assertIn("bind to = TAILSCALE_IP:19999", t)
        self.assertNotIn("0.0.0.0", t)
        self.assertRegex(t, r"allow connections from = localhost 100\.\*")

    def test_refuses_non_tailnet_ip(self):
        if os.geteuid() == 0:
            self.skipTest("root: the non-root guard fires first elsewhere")
        r = subprocess.run(["bash", INSTALL, "192.168.8.1"], capture_output=True, text=True)
        self.assertEqual(r.returncode, 2)


class Verify(unittest.TestCase):
    def _run(self, listen):
        keysha = _sha(os.path.join(BUNDLE, "netdata-archive-keyring.gpg"))
        srcsha = _sha(os.path.join(BUNDLE, "netdata.sources"))
        with tempfile.TemporaryDirectory() as d:
            _stub(d, "fleet-inv", 'printf "hathor\\thathor@127.0.0.1\\n"\n')
            _stub(d, "ssh", f'''while [ "$1" = -o ]; do shift 2; done; shift
case "$*" in
  true) exit 0;;
  *is-enabled*) echo enabled;;
  *is-active*) echo active;;
  *keyring*) echo {keysha};;
  *netdata.sources*) echo {srcsha};;
  *"netdata -v"*) printf 'netdata v2.11.1\\n%s\\n' "{listen}";;
esac
''')
            env = dict(os.environ, PATH=d + ":" + os.environ["PATH"], NETDATA_MAC_NODES="hathor")
            return subprocess.run(["bash", INSTALL, "--verify"], capture_output=True, text=True, env=env)

    def test_wildcard_bind_is_a_miss(self):
        r = self._run("0.0.0.0:19999")
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn("listeners '0.0.0.0:19999'", r.stdout)

    def test_correct_bind_has_no_listener_miss(self):
        r = self._run("127.0.0.1:19999")
        self.assertNotIn("listeners", r.stdout)  # only the (unreachable) chart check may still miss


if __name__ == "__main__":
    unittest.main(verbosity=2)
