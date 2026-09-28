import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import verify  # noqa: E402

ROUTE_HDR = "Iface\tDestination\tGateway\tFlags\tRefCnt\tUse\tMetric\tMask\tMTU\tWindow\tIRTT\n"
ROUTE_OK = ROUTE_HDR + "ens1\t00000000\t0108A8C0\t0003\t0\t0\t100\t00000000\t0\t0\t0\n"


def fake_sysfs(root, bound, mac=verify.MAC, ifname=verify.IFNAME):
    dev = os.path.join(root, "bus/pci/devices", verify.PCI_ADDR)
    os.makedirs(dev)
    if bound:
        drv = os.path.join(root, "bus/pci/drivers", verify.DRIVER)
        os.makedirs(drv)
        os.symlink(drv, os.path.join(dev, "driver"))
        os.makedirs(os.path.join(dev, "net", ifname))
        with open(os.path.join(dev, "net", ifname, "address"), "w") as f:
            f.write(mac + "\n")
    return root


class VerifyTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def test_fixed_state_passes(self):
        sysfs = fake_sysfs(self.tmp.name, bound=True)
        self.assertTrue(verify.run(sysfs, ROUTE_OK, lambda u: "not-found"))

    def test_parked_state_fails(self):
        sysfs = fake_sysfs(self.tmp.name, bound=False)
        self.assertFalse(verify.run(sysfs, ROUTE_OK, lambda u: "enabled"))

    def test_unit_still_enabled_fails(self):
        sysfs = fake_sysfs(self.tmp.name, bound=True)
        self.assertFalse(verify.run(sysfs, ROUTE_OK, lambda u: "enabled"))

    def test_spoofed_mac_fails(self):
        sysfs = fake_sysfs(self.tmp.name, bound=True, mac="02:00:00:00:00:01")
        self.assertFalse(verify.check_netdev(sysfs)[0])

    def test_renamed_netdev_fails(self):
        sysfs = fake_sysfs(self.tmp.name, bound=True, ifname="eth0")
        self.assertFalse(verify.check_netdev(sysfs)[0])

    def test_lower_metric_default_route_fails(self):
        route = ROUTE_OK + "enp0s25\t00000000\t0108A8C0\t0003\t0\t0\t50\t00000000\t0\t0\t0\n"
        self.assertFalse(verify.check_route(route)[0])

    def test_failover_default_route_above_ens1_passes(self):
        route = ROUTE_OK + "enp0s25\t00000000\t0108A8C0\t0003\t0\t0\t1024\t00000000\t0\t0\t0\n"
        self.assertTrue(verify.check_route(route)[0])

    def test_ens1_metric_changed_fails(self):
        self.assertFalse(verify.check_route(ROUTE_OK.replace("\t100\t", "\t600\t"))[0])


if __name__ == "__main__":
    unittest.main()
