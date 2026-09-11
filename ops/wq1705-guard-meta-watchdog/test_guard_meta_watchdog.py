#!/usr/bin/env python3
"""Tests for guard_meta_watchdog (wq#1705). Run: python3 test_guard_meta_watchdog.py

Every test drives the sweep through a fake Systemd, so nothing here restarts,
resets or even reads a real unit.
"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "staged"))
import guard_meta_watchdog as gmw  # noqa: E402

FINDING_LIST = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "staged", "guard-finding-exit.list")


class FakeSystemd:
    """Scriptable stand-in. `units` maps unit -> dict(result, exit, description,
    cat, heals_after). `heals_after` is the attempt number on which a restart
    finally sticks; None means it never recovers."""

    def __init__(self, units):
        self.units = units
        self.restarts = []
        self.resets = []

    def failed_units(self):
        return sorted(u for u, m in self.units.items() if m.get("failed", True))

    def show(self, unit, props):
        m = self.units.get(unit, {})
        return {"Result": m.get("result", "exit-code"),
                "ExecMainStatus": m.get("exit", "1"),
                "Description": m.get("description", ""),
                "ActiveState": "failed"}

    def cat(self, unit):
        return self.units.get(unit, {}).get("cat", "")

    def reset_failed(self, unit):
        self.resets.append(unit)
        return True

    def restart(self, unit):
        self.restarts.append(unit)
        m = self.units[unit]
        n = m.get("heals_after")
        if n is not None and self.restarts.count(unit) >= n:
            m["failed"] = False
            return True
        return False

    def is_failed(self, unit):
        return self.units.get(unit, {}).get("failed", True)


class RecordingNotifier:
    def __init__(self):
        self.sent = []

    def __call__(self, priority, key, title, message):
        self.sent.append({"priority": priority, "key": key, "title": title,
                          "message": message})
        return True

    def titles(self):
        return [s["title"] for s in self.sent]


def fresh_state():
    return {"units": {}}


class Classification(unittest.TestCase):
    def test_finding_exit_guard_is_not_a_crash(self):
        """wq#1657 Mode B: a listed guard exiting 1 found something."""
        sd = FakeSystemd({"lumen-pat-scan.service": {
            "result": "exit-code", "exit": "1", "description": "pat-scan"}})
        kind, _ = gmw.classify("lumen-pat-scan.service", sd,
                               gmw._finding_list())
        self.assertEqual(kind, "finding")

    def test_finding_exit_guard_exiting_2_is_a_crash(self):
        """Only exit 1 is a finding; anything else nonzero is a real crash."""
        sd = FakeSystemd({"lumen-pat-scan.service": {
            "result": "exit-code", "exit": "2", "description": "pat-scan"}})
        kind, _ = gmw.classify("lumen-pat-scan.service", sd, gmw._finding_list())
        self.assertEqual(kind, "crash")

    def test_signal_death_of_listed_guard_is_a_crash(self):
        sd = FakeSystemd({"lumen-budget-alert.service": {
            "result": "signal", "exit": "0", "description": "budget alert"}})
        kind, _ = gmw.classify("lumen-budget-alert.service", sd,
                               gmw._finding_list())
        self.assertEqual(kind, "crash")

    def test_inline_tag_counts_without_the_bridge_list(self):
        sd = FakeSystemd({"nightly-thing.service": {
            "result": "exit-code", "exit": "1", "description": "drift sentinel",
            "cat": "[Service]\nX-Lumen-Guard=finding-exit\n"}})
        kind, _ = gmw.classify("nightly-thing.service", sd, set())
        self.assertEqual(kind, "finding")

    def test_non_guard_is_left_alone(self):
        sd = FakeSystemd({"postgresql.service": {
            "result": "exit-code", "exit": "1", "description": "database"}})
        kind, _ = gmw.classify("postgresql.service", sd, set())
        self.assertEqual(kind, "not-a-guard")

    def test_guard_detected_from_description_not_just_name(self):
        self.assertTrue(gmw.is_guard("nightly-thing.service",
                                     "drift sentinel for the repo"))
        self.assertFalse(gmw.is_guard("postgresql.service", "database server"))


class SelfHeal(unittest.TestCase):
    def test_crashed_guard_is_reset_then_restarted_and_reported_healed(self):
        sd = FakeSystemd({"lumen-embed-guard.service": {
            "result": "exit-code", "exit": "2", "description": "embed guard",
            "heals_after": 1}})
        n = RecordingNotifier()
        st = fresh_state()
        r = gmw.sweep(systemd=sd, notify=n, state=st, now=1000, persist=False)
        self.assertEqual([u["unit"] for u in r["healed"]],
                         ["lumen-embed-guard.service"])
        # reset-failed must precede restart or a start-limit-parked unit refuses.
        self.assertEqual(sd.resets, ["lumen-embed-guard.service"])
        self.assertEqual(sd.restarts, ["lumen-embed-guard.service"])
        self.assertEqual(st["units"]["lumen-embed-guard.service"]["attempts"], 0)

    def test_finding_guard_is_never_restarted(self):
        sd = FakeSystemd({"lumen-pat-scan.service": {
            "result": "exit-code", "exit": "1", "description": "pat-scan"}})
        n = RecordingNotifier()
        r = gmw.sweep(systemd=sd, notify=n, state=fresh_state(), now=1000,
                      persist=False)
        self.assertEqual(sd.restarts, [])
        self.assertEqual([f["unit"] for f in r["findings"]],
                         ["lumen-pat-scan.service"])

    def test_non_guard_is_never_restarted(self):
        sd = FakeSystemd({"postgresql.service": {
            "result": "exit-code", "exit": "1", "description": "database"}})
        r = gmw.sweep(systemd=sd, notify=RecordingNotifier(),
                      state=fresh_state(), now=1000, persist=False)
        self.assertEqual(sd.restarts, [])
        self.assertEqual(r["skipped"], [{"unit": "postgresql.service",
                                         "why": "not-a-guard"}])

    def test_protected_guard_is_never_restarted(self):
        sd = FakeSystemd({"lumen-agent.service": {
            "result": "exit-code", "exit": "2", "description": "agent watchdog"}})
        r = gmw.sweep(systemd=sd, notify=RecordingNotifier(),
                      state=fresh_state(), now=1000, persist=False)
        self.assertEqual(sd.restarts, [])
        self.assertEqual([s["why"] for s in r["skipped"]], ["protected"])


class BackoffAndEscalation(unittest.TestCase):
    def _stuck(self):
        return FakeSystemd({"lumen-drift-guard.service": {
            "result": "exit-code", "exit": "2", "description": "drift guard",
            "heals_after": None}})

    def test_second_sweep_inside_backoff_does_not_retry(self):
        sd, n, st = self._stuck(), RecordingNotifier(), fresh_state()
        gmw.sweep(systemd=sd, notify=n, state=st, now=1000, persist=False)
        r = gmw.sweep(systemd=sd, notify=n, state=st, now=1001, persist=False)
        self.assertEqual(len(sd.restarts), 1)
        self.assertEqual([b["unit"] for b in r["backing_off"]],
                         ["lumen-drift-guard.service"])

    def test_backoff_doubles_and_caps(self):
        self.assertEqual(gmw.backoff_for(0), 300)
        self.assertEqual(gmw.backoff_for(1), 600)
        self.assertEqual(gmw.backoff_for(2), 1200)
        self.assertEqual(gmw.backoff_for(99), gmw.BACKOFF_CAP_S)

    def test_escalates_after_max_attempts_and_pages_exactly_once(self):
        sd, n, st = self._stuck(), RecordingNotifier(), fresh_state()
        now = 1000
        for _ in range(gmw.MAX_ATTEMPTS):
            gmw.sweep(systemd=sd, notify=n, state=st, now=now, persist=False)
            now += gmw.BACKOFF_CAP_S  # always past next_at
        self.assertEqual(len(sd.restarts), gmw.MAX_ATTEMPTS)

        r = gmw.sweep(systemd=sd, notify=n, state=st, now=now, persist=False)
        self.assertEqual([e["why"] for e in r["escalated"]], ["max-attempts"])
        pages = [s for s in n.sent if s["priority"] == "high"]
        self.assertEqual(len(pages), 1)
        self.assertIn("lumen-drift-guard.service", pages[0]["title"])
        # aq dedups on (source, key); the key must be the unit.
        self.assertEqual(pages[0]["key"], "lumen-drift-guard.service")

        now += gmw.BACKOFF_CAP_S
        gmw.sweep(systemd=sd, notify=n, state=st, now=now, persist=False)
        self.assertEqual(len([s for s in n.sent if s["priority"] == "high"]), 1,
                         "a stuck guard must not re-page every 10 minutes")
        self.assertEqual(len(sd.restarts), gmw.MAX_ATTEMPTS,
                         "no further restarts once escalated")

    def test_flapping_guard_escalates_instead_of_being_healed_forever(self):
        sd = FakeSystemd({"lumen-ecc-watch.service": {
            "result": "exit-code", "exit": "2", "description": "ecc watch",
            "heals_after": 1}})
        n, st = RecordingNotifier(), fresh_state()
        now = 1000
        for _ in range(gmw.FLAP_LIMIT):
            sd.units["lumen-ecc-watch.service"]["failed"] = True
            gmw.sweep(systemd=sd, notify=n, state=st, now=now, persist=False)
            now += 600
        self.assertEqual(len(st["units"]["lumen-ecc-watch.service"]["heals"]),
                         gmw.FLAP_LIMIT)

        sd.units["lumen-ecc-watch.service"]["failed"] = True
        restarts_before = len(sd.restarts)
        r = gmw.sweep(systemd=sd, notify=n, state=st, now=now, persist=False)
        self.assertEqual([e["why"] for e in r["escalated"]], ["flapping"])
        self.assertEqual(len(sd.restarts), restarts_before,
                         "a flapping guard must not be restarted again")
        self.assertIn("Guard flapping: lumen-ecc-watch.service", n.titles())

    def test_heals_outside_the_flap_window_do_not_count(self):
        sd = FakeSystemd({"lumen-ecc-watch.service": {
            "result": "exit-code", "exit": "2", "description": "ecc watch",
            "heals_after": 1}})
        n, st = RecordingNotifier(), fresh_state()
        now = 1000
        for _ in range(gmw.FLAP_LIMIT):
            sd.units["lumen-ecc-watch.service"]["failed"] = True
            gmw.sweep(systemd=sd, notify=n, state=st, now=now, persist=False)
            now += gmw.FLAP_WINDOW_S + 1  # each heal ages out before the next
        sd.units["lumen-ecc-watch.service"]["failed"] = True
        r = gmw.sweep(systemd=sd, notify=n, state=st, now=now, persist=False)
        self.assertEqual(r["escalated"], [])
        self.assertTrue(r["healed"])


class Recovery(unittest.TestCase):
    def test_recovery_clears_state_and_notifies(self):
        sd = FakeSystemd({"lumen-drift-guard.service": {
            "result": "exit-code", "exit": "2", "description": "drift guard",
            "heals_after": None}})
        n, st = RecordingNotifier(), fresh_state()
        gmw.sweep(systemd=sd, notify=n, state=st, now=1000, persist=False)
        self.assertIn("lumen-drift-guard.service", st["units"])

        sd.units["lumen-drift-guard.service"]["failed"] = False
        r = gmw.sweep(systemd=sd, notify=n, state=st, now=9999, persist=False)
        self.assertEqual(r["recovered"], ["lumen-drift-guard.service"])
        self.assertEqual(st["units"], {})
        self.assertIn("Recovered: lumen-drift-guard.service", n.titles())

    def test_recovery_of_an_untouched_unit_does_not_page(self):
        sd = FakeSystemd({})
        n = RecordingNotifier()
        st = {"units": {"quiet.service": {"attempts": 0, "heals": [], "next_at": 0}}}
        r = gmw.sweep(systemd=sd, notify=n, state=st, now=1000, persist=False)
        self.assertEqual(r["recovered"], ["quiet.service"])
        self.assertEqual(n.sent, [])


class StateIO(unittest.TestCase):
    def test_state_round_trips_and_survives_corruption(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "sub", "state.json")
            self.assertTrue(gmw.save_state({"units": {"a.service": {"attempts": 2}}}, p))
            self.assertEqual(gmw.load_state(p)["units"]["a.service"]["attempts"], 2)
            with open(p, "w") as f:
                f.write("{not json")
            self.assertEqual(gmw.load_state(p), {"units": {}})

    def test_load_state_of_a_missing_file_is_empty(self):
        self.assertEqual(gmw.load_state("/nonexistent/dir/state.json"),
                         {"units": {}})


class FailOpen(unittest.TestCase):
    def test_main_exits_0_when_the_sweep_explodes(self):
        original = gmw.sweep
        gmw.sweep = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom"))
        try:
            self.assertEqual(gmw.main([]), 0)
        finally:
            gmw.sweep = original


class ShippedListIsUsable(unittest.TestCase):
    def test_fallback_list_parses_and_names_real_guards(self):
        names = gmw._finding_list()
        self.assertIn("lumen-pat-scan.service", names)
        self.assertTrue(all(n.endswith(".service") for n in names), names)
        self.assertTrue(all(not n.startswith("#") for n in names))


if __name__ == "__main__":
    unittest.main(verbosity=2)
