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
        c = container("oh-agent-server-abc", "exited", finished_at=NOW - timedelta(seconds=600))
        self.assertEqual(reaper.reap_candidates([c], now=NOW), ["oh-agent-server-abc"])

    def test_exited_sandbox_within_grace_is_kept(self):
        c = container("oh-agent-server-abc", "exited", finished_at=NOW - timedelta(seconds=10))
        self.assertEqual(reaper.reap_candidates([c], now=NOW), [])

    def test_long_running_sandbox_past_idle_threshold_is_reaped(self):
        c = container("oh-agent-server-abc", "running", started_at=NOW - timedelta(hours=2))
        self.assertEqual(reaper.reap_candidates([c], now=NOW), ["oh-agent-server-abc"])

    def test_recently_started_running_sandbox_is_kept(self):
        c = container("oh-agent-server-abc", "running", started_at=NOW - timedelta(minutes=5))
        self.assertEqual(reaper.reap_candidates([c], now=NOW), [])

    def test_app_container_is_never_matched_even_if_exited(self):
        c = container("openhands", "exited", finished_at=NOW - timedelta(days=1))
        self.assertEqual(reaper.reap_candidates([c], now=NOW), [])

    def test_unrelated_container_is_ignored(self):
        c = container("litellm", "running", started_at=NOW - timedelta(hours=5))
        self.assertEqual(reaper.reap_candidates([c], now=NOW), [])

    def test_dead_status_is_treated_like_exited(self):
        c = container("oh-agent-server-xyz", "dead", finished_at=NOW - timedelta(seconds=301))
        self.assertEqual(reaper.reap_candidates([c], now=NOW), ["oh-agent-server-xyz"])

    def test_missing_timestamps_never_crash_and_are_kept(self):
        c = {"Name": "/oh-agent-server-no-ts", "State": {"Status": "exited"}}
        self.assertEqual(reaper.reap_candidates([c], now=NOW), [])

    def test_real_sandbox_name_from_docker_inspect_is_reaped(self):
        # Name shape confirmed live 2026-10-18 against the installed
        # agent-server 1.26 image (wq#4199 ticket detail): oh-agent-server-<id>.
        c = container("oh-agent-server-f3a9c21e7b4d", "exited",
                       finished_at=NOW - timedelta(seconds=600))
        self.assertEqual(reaper.reap_candidates([c], now=NOW),
                         ["oh-agent-server-f3a9c21e7b4d"])

    def test_old_unconfirmed_prefix_no_longer_matches(self):
        # wq#4149 shipped with "openhands-runtime-" (unconfirmed guess); it
        # matched nothing on the real host. Pin that it stays unmatched now
        # that the default prefix is fixed to the confirmed one.
        c = container("openhands-runtime-abc", "exited", finished_at=NOW - timedelta(seconds=600))
        self.assertEqual(reaper.reap_candidates([c], now=NOW), [])


class SandboxIdFromNameTest(unittest.TestCase):
    def test_strips_prefix(self):
        self.assertEqual(
            reaper.sandbox_id_from_name("oh-agent-server-f3a9c21e7b4d"),
            "f3a9c21e7b4d",
        )

    def test_leading_slash_is_stripped_like_docker_inspect_name(self):
        self.assertEqual(
            reaper.sandbox_id_from_name("/oh-agent-server-f3a9c21e7b4d"),
            "f3a9c21e7b4d",
        )

    def test_non_matching_name_returns_none(self):
        self.assertIsNone(reaper.sandbox_id_from_name("litellm"))


class ReapRequestTest(unittest.TestCase):
    def test_builds_delete_with_sandbox_id_query_param(self):
        # Query param form confirmed live wq#4199: 200, removed in <4s. The
        # bare path form (/api/v1/sandboxes/<id>) returns 422.
        self.assertEqual(
            reaper.reap_request("oh-agent-server-f3a9c21e7b4d"),
            ("DELETE", "http://127.0.0.1:3000/api/v1/sandboxes?sandbox_id=f3a9c21e7b4d"),
        )

    def test_non_matching_name_returns_none(self):
        self.assertIsNone(reaper.reap_request("litellm"))


if __name__ == "__main__":
    unittest.main()
