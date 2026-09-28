#!/usr/bin/env python3
"""wq#2510 — the MacPro6,1 fan-floor alarm catches the real isis event and never acts on Thor.

Runs standalone: python3 ops/tests/test_wq2510_fan_floor_alarm.py
No node is contacted. The replay reads fixtures/wq2510_isis_0918_0919.csv, isis's retained
netdata Te0T + fan1 at 60 s for 2026-09-17T23:00Z -> 09-20T00:00Z, saved by
`fan_floor_alarm.py replay isis 1789689600 1789862400 --save ...` on 2026-09-28.

The event being pinned is ISIS_POWEROFF_RCA.md:84-105: fan at the 790 rpm SMC floor with
Te0T 81-84 C for two days, found only by a human hand-querying netdata. Boot -4 ran
09-18 11:44:27 -> 09-19 10:47:46 and ended in an abrupt power loss (RCA :44).
"""
import copy
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import fan_floor_alarm as ffa  # noqa: E402

FIXTURE = os.path.join(HERE, "fixtures", "wq2510_isis_0918_0919.csv")
BOOT4_START, BOOT4_CUT = 1789731867, 1789814866  # 09-18T11:44:27Z, 09-19T10:47:46Z


def flat(te0t, fan, minutes, t0=1789000000):
    return [(t0 + i * ffa.STEP_S, te0t, fan) for i in range(minutes)]


class Replay(unittest.TestCase):
    def setUp(self):
        self.rule = ffa.load_config()["rule"]
        self.samples = ffa.read_csv(FIXTURE)

    def test_fixture_is_the_rca_window(self):
        # RCA hourly buckets top out at 796.6 rpm (:91); per minute the SMC reaches 850.6
        self.assertEqual(len(self.samples), 49 * 60)
        self.assertEqual(max(f for _, _, f in self.samples if f is not None), 850.6)

    def test_fires_on_the_boot_minus_4_event_and_holds_through_the_cut(self):
        tr = ffa.evaluate(self.samples, self.rule)
        raises = [t for t in tr if t["state"] == "raise" and BOOT4_START < t["ts"] < BOOT4_CUT]
        self.assertEqual(len(raises), 1, tr)
        up = raises[0]["ts"]
        self.assertEqual(ffa.stamp(up), "2026-09-18T19:56Z")
        # no clear between the raise and the power loss: one alarm, not a flapping one
        self.assertFalse([t for t in tr if t["state"] == "clear" and up < t["ts"] <= BOOT4_CUT])

    def test_whole_window_transitions(self):
        got = [(ffa.stamp(t["ts"]), t["state"]) for t in ffa.evaluate(self.samples, self.rule)]
        self.assertEqual(got, [
            ("2026-09-18T00:00Z", "raise"),  # boot -5 tail, already hot at window start
            ("2026-09-18T02:30Z", "clear"),
            ("2026-09-18T04:50Z", "raise"),
            ("2026-09-18T12:45Z", "clear"),  # first full window after the 05:31 poweroff
            ("2026-09-18T19:56Z", "raise"),  # the event: held through the 09-19 10:47 cut
            ("2026-09-19T16:14Z", "clear"),  # first full window after the 15:13 reboot
            ("2026-09-19T18:00Z", "raise"),  # boot -3, idle at the floor (RCA :92)
        ])

    def test_clearing_at_the_raise_line_would_flap(self):
        # why te0t_clear_below_c exists: at the floor Te0T idles on 80 C
        rule = dict(self.rule, te0t_clear_below_c=self.rule["te0t_above_c"])
        self.assertGreater(len(ffa.evaluate(self.samples, rule)), 7)


class Rule(unittest.TestCase):
    def setUp(self):
        self.rule = ffa.load_config()["rule"]

    def test_mbpfan_holding_the_fan_never_fires(self):
        # post-wq#2508 isis: 1099 rpm; khepri's soak peaked Te0T 87.5 C at the floor
        self.assertEqual(ffa.evaluate(flat(87.5, 1100.0, 180), self.rule), [])

    def test_a_short_spike_does_not_fire(self):
        # 15 min at 90 C inside an hour at 72 C averages 76.5 C
        s = flat(72.0, 790.0, 60) + flat(90.0, 790.0, 15, t0=1789000000 + 3600) \
            + flat(72.0, 790.0, 60, t0=1789000000 + 75 * 60)
        self.assertEqual(ffa.evaluate(s, self.rule), [])

    def test_sustained_floor_fires_once(self):
        tr = ffa.evaluate(flat(82.0, 790.0, 300), self.rule)
        self.assertEqual([t["state"] for t in tr], ["raise"])

    def test_fan_leaving_the_floor_clears(self):
        s = flat(82.0, 790.0, 60) + flat(82.0, 1100.0, 5, t0=1789000000 + 3600)
        self.assertEqual([t["state"] for t in ffa.evaluate(s, self.rule)], ["raise", "clear"])

    def test_gap_carries_state_instead_of_clearing(self):
        s = flat(82.0, 790.0, 60) + flat(None, None, 30, t0=1789000000 + 3600)
        self.assertEqual([t["state"] for t in ffa.evaluate(s, self.rule)], ["raise"])
        self.assertIsNone(ffa.window_stats(s[-60:], self.rule))


class ThorCarveOut(unittest.TestCase):
    def setUp(self):
        self.cfg = ffa.load_config()

    def test_shipped_config_notifies_only_on_thor(self):
        thor = self.cfg["nodes"]["thor"]
        self.assertEqual(thor["action"], "notify")
        self.assertIsNone(thor.get("hold_subsystem"))
        others = {k: v["action"] for k, v in self.cfg["nodes"].items() if k != "thor"}
        self.assertEqual(set(others.values()), {"hold"})

    def refuses(self, mutate):
        cfg = copy.deepcopy(self.cfg)
        mutate(cfg["nodes"])
        with self.assertRaises(ffa.ConfigError):
            ffa.validate(cfg)

    def test_thor_hold_is_refused(self):
        self.refuses(lambda n: n["thor"].update(action="hold", hold_subsystem="thor"))

    def test_thor_hold_subsystem_alone_is_refused(self):
        self.refuses(lambda n: n["thor"].update(hold_subsystem="thor"))

    def test_dropping_thor_is_refused(self):
        self.refuses(lambda n: n.pop("thor"))

    def test_thor_by_address_under_another_name_is_refused(self):
        self.refuses(lambda n: n.update(
            workstation={"netdata": ffa.THOR_IP + ":19999", "action": "hold", "hold_subsystem": "ws"}))

    def test_check_never_hands_thor_a_hold_subsystem(self):
        cfg = dict(self.cfg, nodes={"thor": self.cfg["nodes"]["thor"]})  # thor has no source: offline
        [thor] = ffa.check(cfg)
        self.assertEqual((thor["action"], thor["hold_subsystem"], thor["raise"]), ("notify", None, None))


if __name__ == "__main__":
    unittest.main(verbosity=1)
