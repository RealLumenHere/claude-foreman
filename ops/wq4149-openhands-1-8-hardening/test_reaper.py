#!/usr/bin/env python3
"""wq#4149: pure-logic tests for reaper.reap_candidates, no docker needed."""
import unittest
from datetime import datetime, timedelta, timezone

import reaper

NOW = datetime(2026, 10, 17, 12, 0, 0, tzinfo=timezone.utc)


def container(name, status, finished_at=None, started_at=None):
    return {
        "Name": f"/{name}",
        "State": {
            "Status": status,
            "FinishedAt": finished_at.isoformat().replace("+00:00", "Z") if finished_at else "0001-01-01T00:00:00Z",
            "StartedAt": started_at.isoformat().replace("+00:00", "Z") if started_at else "0001-01-01T00:00:00Z",
        },
    }


class ReapCandidatesTest(unittest.TestCase):
    def test_exited_sandbox_past_grace_is_reaped(self):
        c = container("openhands-runtime-abc", "exited", finished_at=NOW - timedelta(seconds=600))
        self.assertEqual(reaper.reap_candidates([c], now=NOW), ["openhands-runtime-abc"])

    def test_exited_sandbox_within_grace_is_kept(self):
        c = container("openhands-runtime-abc", "exited", finished_at=NOW - timedelta(seconds=10))
        self.assertEqual(reaper.reap_candidates([c], now=NOW), [])

    def test_long_running_sandbox_past_idle_threshold_is_reaped(self):
        c = container("openhands-runtime-abc", "running", started_at=NOW - timedelta(hours=2))
        self.assertEqual(reaper.reap_candidates([c], now=NOW), ["openhands-runtime-abc"])

    def test_recently_started_running_sandbox_is_kept(self):
        c = container("openhands-runtime-abc", "running", started_at=NOW - timedelta(minutes=5))
        self.assertEqual(reaper.reap_candidates([c], now=NOW), [])

    def test_app_container_is_never_matched_even_if_exited(self):
        c = container("openhands", "exited", finished_at=NOW - timedelta(days=1))
        self.assertEqual(reaper.reap_candidates([c], now=NOW), [])

    def test_unrelated_container_is_ignored(self):
        c = container("litellm", "running", started_at=NOW - timedelta(hours=5))
        self.assertEqual(reaper.reap_candidates([c], now=NOW), [])

    def test_dead_status_is_treated_like_exited(self):
        c = container("openhands-runtime-xyz", "dead", finished_at=NOW - timedelta(seconds=301))
        self.assertEqual(reaper.reap_candidates([c], now=NOW), ["openhands-runtime-xyz"])

    def test_missing_timestamps_never_crash_and_are_kept(self):
        c = {"Name": "/openhands-runtime-no-ts", "State": {"Status": "exited"}}
        self.assertEqual(reaper.reap_candidates([c], now=NOW), [])


if __name__ == "__main__":
    unittest.main()
