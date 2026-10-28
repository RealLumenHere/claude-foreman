import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ops_apply import identity

WORKER_CGROUP = (
    "0::/lumen-workers.slice/lumen-job-fworker-wq4764-1771999999.service\n"
)
NON_WORKER_CGROUP = "0::/user.slice/user-1000.slice/session-1.scope\n"
OTHER_SLICE_LOOKALIKE = (
    "0::/some-other.slice/lumen-job-fworker-wq4764-1771999999.service\n"
)


class WorkerTicketFromCgroupTests(unittest.TestCase):
    def test_worker_cgroup_extracts_ticket(self):
        self.assertEqual(identity.worker_ticket_from_cgroup(WORKER_CGROUP), 4764)

    def test_non_worker_cgroup_is_none(self):
        self.assertIsNone(identity.worker_ticket_from_cgroup(NON_WORKER_CGROUP))

    def test_requires_the_worker_slice_marker(self):
        # a unit name that merely looks right, outside lumen-workers.slice,
        # must not be treated as a worker identity.
        self.assertIsNone(identity.worker_ticket_from_cgroup(OTHER_SLICE_LOOKALIKE))

    def test_worker_unit_from_cgroup_full_name(self):
        self.assertEqual(
            identity.worker_unit_from_cgroup(WORKER_CGROUP),
            "lumen-job-fworker-wq4764-1771999999.service")

    def test_worker_unit_from_cgroup_none_for_non_worker(self):
        self.assertIsNone(identity.worker_unit_from_cgroup(NON_WORKER_CGROUP))


if __name__ == "__main__":
    unittest.main()
