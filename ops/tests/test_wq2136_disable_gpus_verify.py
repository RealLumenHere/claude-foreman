#!/usr/bin/env python3
"""wq#2157 — install.sh --verify must assert the RETIRED state of disable-gpus.service.

Runs standalone: python3 ops/tests/test_wq2136_disable_gpus_verify.py
Read-only against the fleet: every case stubs `ssh` on PATH, so no node is contacted.

Why this exists: the wq#2136 bundle's --verify used to assert that the unit file on
hathor/nephthys matched the copy in the bundle, i.e. that the unit was *installed*. The
wq#2140 RCA reversed that goal — the unit is retired, so the thing worth checking is the
opposite: not enabled, not active, not wanted by multi-user.target, and both D300s still
amdgpu-bound. A verify that cannot tell "retired" from "drifted back on" is worse than
none, because ops-deploy-drift runs it unattended and reports OK.
"""
import os
import stat
import subprocess
import sys
import tempfile
import textwrap
import unittest

REPO = os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir, os.pardir)
INSTALL = os.path.join(REPO, "ops", "wq2136-hathor-disable-gpus-unit", "install.sh")

RETIRED = (
    "enabled=disabled\n"
    "active=inactive\n"
    "wanted=no\n"
    "amdgpu=0000:02:00.0 0000:06:00.0 bind module unbind \n"
)


def _run(node_out, node_rc=None):
    """Run `install.sh --verify` with `ssh` stubbed to emit node_out per node."""
    node_rc = node_rc or {}
    with tempfile.TemporaryDirectory() as tmp:
        for node, out in node_out.items():
            with open(os.path.join(tmp, node + ".out"), "w") as fh:
                fh.write(out)
        for node, rc in node_rc.items():
            with open(os.path.join(tmp, node + ".rc"), "w") as fh:
                fh.write(str(rc))
        # The stub mimics `ssh [opts] <node> <command>`: the node is the second-to-last arg.
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
        return subprocess.run(
            ["bash", INSTALL, "--verify"],
            env=env, capture_output=True, text=True,
        )


class VerifyContract(unittest.TestCase):
    def test_retired_on_both_nodes_passes(self):
        r = _run({"hathor": RETIRED, "nephthys": RETIRED})
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("VERIFY OK", r.stdout)

    def test_deleted_unit_file_also_passes(self):
        """The stronger retirement: file gone, so systemd reports not-found/inactive."""
        gone = RETIRED.replace("enabled=disabled", "enabled=not-found")
        r = _run({"hathor": gone, "nephthys": gone})
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_unit_re_enabled_fails(self):
        r = _run({"hathor": RETIRED.replace("enabled=disabled", "enabled=enabled"),
                  "nephthys": RETIRED})
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("hathor", r.stdout)
        self.assertIn("is-enabled=enabled", r.stdout)

    def test_unit_active_fails(self):
        r = _run({"hathor": RETIRED,
                  "nephthys": RETIRED.replace("active=inactive", "active=active")})
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("nephthys", r.stdout)

    def test_still_wanted_by_multi_user_target_fails(self):
        r = _run({"hathor": RETIRED.replace("wanted=no", "wanted=yes"), "nephthys": RETIRED})
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("multi-user.target.wants", r.stdout)

    def test_gpu_removed_from_bus_fails(self):
        """The failure this bundle exists to catch: a card no longer amdgpu-bound."""
        stripped = RETIRED.replace("0000:02:00.0 0000:06:00.0", "0000:02:00.0")
        r = _run({"hathor": stripped, "nephthys": RETIRED})
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("0000:06:00.0 not bound to amdgpu", r.stdout)

    def test_both_gpus_gone_reports_both(self):
        stripped = RETIRED.replace("amdgpu=0000:02:00.0 0000:06:00.0 bind module unbind \n",
                                   "amdgpu=bind module unbind \n")
        r = _run({"hathor": stripped, "nephthys": stripped})
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertEqual(r.stdout.count("not bound to amdgpu"), 4)

    def test_unreachable_node_is_error_not_drift(self):
        """Contract: a node we cannot probe exits 2, never a false drift alert."""
        r = _run({"nephthys": RETIRED}, node_rc={"hathor": 255})
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertIn("VERIFY ERROR", r.stdout)

    def test_probe_string_composes_and_emits_four_keys(self):
        """The stubbed cases fake the probe's *output*; this runs the probe itself.

        Executed locally, not over ssh — the point is that the single-quoted one-liner
        survives shell composition and emits exactly the four keys the parser reads.
        disable-gpus.service does not exist on this host, so `absent` is the expected
        reading; the amdgpu line is whatever this host has, including empty.
        """
        with open(INSTALL) as fh:
            src = fh.read()
        start = src.index("_probe='") + len("_probe='")
        body = src[start:src.index("'\n", start)]
        r = subprocess.run(["bash", "-c", body], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        keys = [line.split("=", 1)[0] for line in r.stdout.splitlines()]
        self.assertEqual(keys, ["enabled", "active", "wanted", "amdgpu"])
        self.assertRegex(r.stdout, r"(?m)^enabled=(not-found|absent|disabled|enabled)$")
        self.assertRegex(r.stdout, r"(?m)^wanted=(yes|no)$")

    def test_no_install_path(self):
        """The bundle is retired: the bare invocation must install nothing and exit 3."""
        r = subprocess.run(["bash", INSTALL], capture_output=True, text=True)
        self.assertEqual(r.returncode, 3, r.stdout + r.stderr)
        self.assertIn("RETIRED", r.stderr)


if __name__ == "__main__":
    unittest.main(verbosity=2)
