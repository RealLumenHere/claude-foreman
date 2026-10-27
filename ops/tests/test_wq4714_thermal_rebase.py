#!/usr/bin/env python3
"""wq#4714 — the rebased sensor layer: one dead node cannot hang a poll, and the sustain
window now lives in the guard's own db instead of in netdata's history.

Runs standalone: python3 ops/tests/test_wq4714_thermal_rebase.py
No live fleet node is contacted. The dead-node probe points at 192.0.2.1 — TEST-NET-1
(RFC 5737), reserved and guaranteed unroutable — deliberately a host from *outside* the
roster the fix was written around, pinned to a literal rather than to whichever fleet node
happens to be down today. A probe drawn only from nodes that answer could not fail; this
one fails the moment the fan-out stops being bounded, because the serial read it replaces
took 70 s and died at rc=124 (wq#3044).
"""
import copy
import os
import sys
import tempfile
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
OPS = os.path.dirname(HERE)
sys.path.insert(0, OPS)
import fan_floor_alarm as ffa  # noqa: E402
import thermal_backpressure as tb  # noqa: E402

UNROUTABLE = "192.0.2.1"  # RFC 5737 TEST-NET-1


def tiny_cfg(**sensor):
    """The shipped config's shape, reduced to one sensed node plus the mandatory thor entry."""
    cfg = {"sustain_min": 5, "hold_ttl_min": 20, "holder": "test-holder", "fan_floor": False,
           "sensor": {"hwmon_label": "Package id 0", "node_timeout_s": 8, "poll_budget_s": 30,
                      "chart_prefix": "sensors.temperature_coretemp",
                      "chart_suffix": "_Package_id_0_input"},
           "nodes": {"box": {"sensor": "local", "netdata": None, "raise_c": 79,
                             "clear_c": 70, "action": "hold"},
                     tb.THOR_NAME: {"sensor": None, "netdata": None, "action": "notify"}}}
    cfg["sensor"].update(sensor)
    return tb.validate(cfg)


class SensorSpec(unittest.TestCase):
    def test_the_three_accepted_shapes(self):
        self.assertEqual(tb.parse_sensor("local"), ("local", None))
        self.assertEqual(tb.parse_sensor("ssh://sobek@100.86.156.91"),
                         ("ssh", "sobek@100.86.156.91"))
        self.assertEqual(tb.parse_sensor(None), (None, None))

    def test_a_shape_it_does_not_understand_is_refused_not_guessed(self):
        for bad in ("100.86.156.91", "http://x:19999", "ssh://", "ssh://user@", "ssh://@host"):
            self.assertRaises(tb.ConfigError, tb.parse_sensor, bad)

    def test_sensor_host_reports_only_what_ssh_actually_reaches(self):
        self.assertEqual(tb.sensor_host("ssh://maam@100.105.219.34"), "100.105.219.34")
        self.assertEqual(tb.sensor_host("ssh://100.105.219.34"), "100.105.219.34")
        self.assertEqual(tb.sensor_host("local"), "")
        self.assertEqual(tb.sensor_host(None), "")


class Validate(unittest.TestCase):
    def test_a_budget_under_one_nodes_timeout_cannot_bound_the_fanout(self):
        tiny_cfg(node_timeout_s=8, poll_budget_s=8)  # equal is the floor, and is allowed
        with self.assertRaises(tb.ConfigError) as caught:
            tiny_cfg(node_timeout_s=8, poll_budget_s=7)
        self.assertIn("poll_budget_s", str(caught.exception))

    def test_both_timeouts_are_required_and_must_be_positive(self):
        for bad in ({"node_timeout_s": 0}, {"poll_budget_s": -1}, {"node_timeout_s": None},
                    {"poll_budget_s": "soon"}):
            self.assertRaises(tb.ConfigError, tiny_cfg, **bad)

    def test_hwmon_label_is_required(self):
        self.assertRaises(tb.ConfigError, tiny_cfg, hwmon_label=None)

    def test_thor_cannot_be_held_through_a_sensor_spec_either(self):
        # instinct 2 was enforced on the `netdata` address only. The live read moved to
        # `sensor`, so the same alias had to be closed there.
        cfg = tiny_cfg()
        bad = copy.deepcopy(cfg)
        bad["nodes"]["alias"] = {"sensor": f"ssh://someone@{tb.THOR_IP}", "netdata": None,
                                 "raise_c": 90, "clear_c": 80, "action": "hold"}
        with self.assertRaises(tb.ConfigError) as caught:
            tb.validate(bad)
        self.assertIn("Thor may only notify", str(caught.exception))

    def test_a_sensed_node_still_needs_hysteresis(self):
        bad = copy.deepcopy(tiny_cfg())
        bad["nodes"]["box"].pop("raise_c")
        self.assertRaises(tb.ConfigError, tb.validate, bad)


class DeadNodeCannotHangAPoll(unittest.TestCase):
    """The ticket's actual defect: four retired Macs x two 30 s curls, read serially."""

    def test_an_unroutable_node_finishes_inside_its_own_timeout(self):
        cfg = tiny_cfg(node_timeout_s=3, poll_budget_s=10)
        cfg["nodes"]["dead"] = {"sensor": f"ssh://nobody@{UNROUTABLE}", "netdata": None,
                                "raise_c": 79, "clear_c": 70, "action": "hold"}
        started = time.monotonic()
        temps = tb.sample_temps(tb.validate(cfg))
        elapsed = time.monotonic() - started
        self.assertLess(elapsed, cfg["sensor"]["poll_budget_s"], f"took {elapsed:.1f}s")
        mean, reason = temps["dead"]
        self.assertIsNone(mean)
        self.assertTrue(reason, "a node that could not be read must say why")

    def test_the_whole_roster_of_dead_nodes_costs_one_timeout_not_the_sum(self):
        # the shape wq#3044 would have installed: several dead hosts on one poll. Serial, at
        # one 3 s timeout each, six of these cost 18 s+; concurrent they cost one.
        cfg = tiny_cfg(node_timeout_s=3, poll_budget_s=20)
        for i in range(6):
            cfg["nodes"][f"dead{i}"] = {"sensor": f"ssh://nobody@{UNROUTABLE}",
                                        "netdata": None, "raise_c": 79, "clear_c": 70,
                                        "action": "hold"}
        started = time.monotonic()
        temps = tb.sample_temps(tb.validate(cfg))
        elapsed = time.monotonic() - started
        self.assertLess(elapsed, 12, f"took {elapsed:.1f}s — the fan-out is serial again")
        for i in range(6):
            self.assertIsNone(temps[f"dead{i}"][0])

    def test_a_node_with_no_sensor_is_not_an_error_it_is_a_gap(self):
        temps = tb.sample_temps(tiny_cfg())
        self.assertEqual(temps[tb.THOR_NAME], (None, "no sensor source"))


class StoredWindow(unittest.TestCase):
    """netdata supplied the sustain window; sysfs has only "now", so the guard keeps it."""

    def setUp(self):
        self.cfg = tiny_cfg()
        self.tmp = tempfile.TemporaryDirectory()
        self.con = tb.connect(os.path.join(self.tmp.name, "state.db"))
        self.t0 = 1790000000 - 1790000000 % tb.STEP_S

    def tearDown(self):
        self.con.close()
        self.tmp.cleanup()

    def record(self, temp, minute, node="box"):
        return tb.record_means(self.con, self.cfg, {node: (temp, None)},
                               self.t0 + minute * tb.STEP_S)[node]

    def test_a_partial_window_is_no_verdict_not_a_cool_reading(self):
        for minute in range(4):
            mean, reason = self.record(95.0, minute)
            self.assertIsNone(mean, f"minute {minute} should not have a verdict yet")
            self.assertEqual(reason, "window not fully covered")

    def test_five_polls_make_the_mean_the_rule_already_knew_how_to_read(self):
        for minute in range(4):
            self.record(80.0, minute)
        mean, reason = self.record(90.0, 4)
        self.assertIsNone(reason)
        self.assertAlmostEqual(mean, (80.0 * 4 + 90.0) / 5)
        self.assertTrue(tb.next_temp(False, mean, self.cfg["nodes"]["box"]))

    def test_a_missed_poll_reopens_the_window_so_the_state_carries(self):
        for minute in range(5):
            self.record(80.0, minute)
        self.assertIsNotNone(self.record(80.0, 5)[0])
        # minute 6 is missed entirely (node down): the window is no longer contiguous
        mean, reason = self.record(80.0, 7)
        self.assertIsNone(mean)
        self.assertEqual(reason, "window not fully covered")
        self.assertTrue(tb.next_temp(True, mean, self.cfg["nodes"]["box"]),
                        "a gap must carry a held node, never read as cool")

    def test_a_failed_read_stores_nothing_and_reports_its_own_reason(self):
        for minute in range(5):
            self.record(80.0, minute)
        out = tb.record_means(self.con, self.cfg, {"box": (None, "timeout after 8s")},
                              self.t0 + 5 * tb.STEP_S)
        self.assertEqual(out["box"], (None, "timeout after 8s"))
        rows = self.con.execute("SELECT COUNT(*) FROM samples WHERE node='box'").fetchone()
        self.assertEqual(rows[0], 5, "a failed read must not land in the window")

    def test_a_stale_full_window_is_not_mistaken_for_a_current_one(self):
        # found while writing this file: without an anchor on the newest sample, a node that
        # went dark kept answering off its last complete window for sustain_min more polls —
        # and a hot node whose last window was cool would have read as cool, which is the one
        # thing the guard must never do. record_means now requires the window to end at this
        # poll's slot.
        for minute in range(5):
            self.record(60.0, minute)
        self.assertIsNotNone(self.record(60.0, 5)[0])
        for minute in (6, 7, 8):
            mean, reason = tb.record_means(self.con, self.cfg, {"box": (None, "node down")},
                                           self.t0 + minute * tb.STEP_S)["box"]
            self.assertIsNone(mean, f"minute {minute} answered off a stale window")
            self.assertTrue(tb.next_temp(True, mean, self.cfg["nodes"]["box"]))

    def test_samples_older_than_the_retention_window_are_pruned(self):
        self.record(80.0, 0)
        late = self.t0 + tb.RETAIN_SAMPLES_S + 2 * tb.STEP_S
        tb.record_means(self.con, self.cfg, {"box": (80.0, None)}, late)
        kept = self.con.execute("SELECT MIN(ts) FROM samples WHERE node='box'").fetchone()[0]
        self.assertGreater(kept, self.t0)


class ShippedConfig(unittest.TestCase):
    def test_every_retired_mac_is_gone_from_the_roster(self):
        nodes = tb.load_config()["nodes"]
        for retired in ("isis", "hathor", "nephthys", "khepri"):
            self.assertNotIn(retired, nodes)

    def test_every_hold_node_has_a_live_sensor_and_thor_has_none(self):
        nodes = tb.load_config()["nodes"]
        for name, node in nodes.items():
            if node["action"] == "hold":
                self.assertTrue(node.get("sensor"), f"{name} holds but reads nothing")
        self.assertIsNone(nodes[tb.THOR_NAME].get("sensor"))
        self.assertEqual(nodes[tb.THOR_NAME]["action"], "notify")

    def test_poll_never_reads_netdata_any_more(self):
        # netdata is `disabled` on lumen and absent on maksoor, so the live read must not
        # depend on it. It stays only for observe/replay history.
        cfg = tb.load_config()
        self.assertFalse(cfg["fan_floor"], "fan-floor fetches four dead netdata hosts")
        for name in ("lumen", "maksoor"):
            self.assertIsNone(cfg["nodes"][name]["netdata"])
            self.assertTrue(cfg["nodes"][name]["sensor"])

    def test_observe_says_so_rather_than_crashing_where_netdata_is_gone(self):
        out = tb.observe(tb.load_config(), "lumen", 7)
        self.assertIn("no netdata history source", out["error"])


class RetiredFanFloor(unittest.TestCase):
    """wq#2510's alarm is kept, but its four MacPro6,1 nodes are gone. Reaching for them
    costs a 30 s curl each and can never produce a verdict."""

    def test_check_does_not_touch_a_retired_host(self):
        cfg = ffa.load_config()
        self.assertTrue(cfg["nodes"]["isis"]["retired"])
        boom = lambda *a, **k: self.fail("check() fetched from a retired node")  # noqa: E731
        saved, ffa.fetch_samples = ffa.fetch_samples, boom
        try:
            verdicts = {v["node"]: v for v in ffa.check(cfg)}
        finally:
            ffa.fetch_samples = saved
        for name in ("isis", "hathor", "nephthys", "khepri"):
            self.assertEqual(verdicts[name]["reason"], "node retired")
            self.assertIsNone(verdicts[name]["raise"], "no verdict, and never a clear")
            self.assertIsNone(verdicts[name]["clear"])

    def test_the_rule_and_its_thresholds_are_still_loadable(self):
        # kept on purpose: nothing on the live roster has a Te0T sensor to re-point this at,
        # so the numbers only survive here. Re-enabling is dropping `retired` on a node.
        rule = ffa.load_config()["rule"]
        self.assertEqual(rule["te0t_above_c"], 80)
        self.assertEqual(rule["fan_below_rpm"], 1000)
        self.assertFalse(tb.load_config()["fan_floor"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
