import os
import shutil
import subprocess
import tempfile
import unittest
from unittest import mock

import migrate

real_sh = migrate.sh


@unittest.skipUnless(shutil.which("rsync"), "rsync not installed")
class MoveTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.src = os.path.join(self.tmp, "home", "bulk")
        os.makedirs(os.path.join(self.src, "sub"))
        with open(os.path.join(self.src, "sub", "a.txt"), "w") as f:
            f.write("payload")
        os.symlink("sub/a.txt", os.path.join(self.src, "link"))
        self.fstab = os.path.join(self.tmp, "fstab")
        with open(self.fstab, "w") as f:
            f.write("UUID=x / ext4 defaults 0 1\n")
        self.units = os.path.join(self.tmp, "units")
        os.mkdir(self.units)
        self.mounted, self.calls, self.mount_fails = set(), [], False

        def fake_sh(cmd, check=True, capture=True):
            self.calls.append(cmd)
            out = subprocess.CompletedProcess(cmd, 0, "", "")
            if cmd[0] == "mount":
                if self.mount_fails:
                    raise migrate.Abort("mount boom")
                self.mounted.add(cmd[3])
            elif cmd[0] == "umount":
                self.mounted.discard(cmd[1])
            elif cmd[0] == "systemctl":
                out.stdout = "active\n" if cmd[1] == "is-active" else ""
            elif cmd[0] == "findmnt":
                out.stdout = "ext4\n"
            else:
                return real_sh(cmd, check, capture)
            return out

        patches = [
            mock.patch.object(migrate, "sh", fake_sh),
            mock.patch.object(migrate, "DEST_ROOT", os.path.join(self.tmp, "scratch", "mig")),
            mock.patch.object(migrate, "FSTAB", self.fstab),
            mock.patch.object(migrate, "UNIT_DIR", self.units),
            mock.patch.object(migrate, "MOVES", {"bulk": (self.src, ["svc.service"])}),
            mock.patch.object(migrate.os, "geteuid", lambda: 0),
            mock.patch.object(migrate.os.path, "ismount", lambda p: p == "/scratch" or p in self.mounted),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        self.dest = migrate.dest_of("bulk")

    def args(self, cmd):
        return migrate.main([cmd, "bulk", "--execute"])

    def test_move_then_finalize(self):
        self.assertEqual(self.args("move"), 0)
        old = self.src + migrate.SUFFIX
        for root in (old, self.dest):
            with open(os.path.join(root, "sub", "a.txt")) as f:
                self.assertEqual(f.read(), "payload")
            self.assertEqual(os.readlink(os.path.join(root, "link")), "sub/a.txt")
        self.assertFalse(os.path.exists(migrate.marker_of("bulk")))
        self.assertIn(self.src, self.mounted)
        with open(self.fstab) as f:
            self.assertIn(migrate.fstab_line("bulk", self.src), f.read())
        self.assertTrue(os.path.exists(self.fstab + migrate.SUFFIX))
        self.assertTrue(os.path.exists(os.path.join(self.units, "svc.service.d", migrate.DROPIN)))
        stops = [c for c in self.calls if c[:2] == ["systemctl", "stop"]]
        starts = [c for c in self.calls if c[:2] == ["systemctl", "start"]]
        self.assertEqual((len(stops), len(starts)), (1, 1))
        self.assertEqual(self.args("finalize"), 0)
        self.assertFalse(os.path.exists(old))

    def test_dry_run_touches_nothing(self):
        self.assertEqual(migrate.main(["move", "bulk"]), 0)
        self.assertFalse(os.path.exists(self.dest))
        self.assertEqual(self.mounted, set())

    def test_mount_failure_unwinds(self):
        self.mount_fails = True
        self.assertEqual(self.args("move"), 1)
        with open(os.path.join(self.src, "sub", "a.txt")) as f:
            self.assertEqual(f.read(), "payload")
        self.assertFalse(os.path.exists(self.src + migrate.SUFFIX))
        with open(self.fstab) as f:
            self.assertNotIn("bind", f.read())
        self.assertFalse(os.path.exists(os.path.join(self.units, "svc.service.d", migrate.DROPIN)))
        self.assertTrue([c for c in self.calls if c[:2] == ["systemctl", "start"]])

    def test_rollback_restores_and_keeps_new_writes(self):
        self.args("move")
        with open(os.path.join(self.dest, "new.txt"), "w") as f:
            f.write("written after move")
        self.assertEqual(self.args("rollback"), 0)
        self.assertNotIn(self.src, self.mounted)
        self.assertTrue(os.path.exists(os.path.join(self.src, "new.txt")))
        self.assertFalse(os.path.exists(self.src + migrate.SUFFIX))
        with open(self.fstab) as f:
            self.assertNotIn("bind", f.read())

    def test_finalize_refuses_when_not_mounted(self):
        self.assertEqual(self.args("finalize"), 1)

    def test_preflight_refuses_busy_cwd(self):
        migrate.MOVES["bulk"] = (self.src, [])
        cwd = os.getcwd()
        os.chdir(self.src)
        self.addCleanup(os.chdir, cwd)
        self.assertEqual(self.args("move"), 1)
        self.assertFalse(os.path.exists(self.src + migrate.SUFFIX))

    def test_fstab_edit_idempotent(self):
        line = migrate.fstab_line("bulk", self.src)
        migrate.edit_fstab(line, add=True)
        migrate.edit_fstab(line, add=True)
        with open(self.fstab) as f:
            self.assertEqual(f.read().count(line), 1)
        migrate.edit_fstab(line, add=False)
        with open(self.fstab) as f:
            self.assertNotIn(line, f.read())


if __name__ == "__main__":
    unittest.main()
