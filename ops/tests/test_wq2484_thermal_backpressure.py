#!/usr/bin/env python3
"""wq#2484 — a hot node's subsystem is held, released on cooldown, one aq row per transition.

Runs standalone: python3 ops/tests/test_wq2484_thermal_backpressure.py
No node is contacted. Holds go through the real /home/lumen/bin/slock against a throwaway
queue.db (WQ_DB), and the dispatcher's own SubsystemHeld gate reads that db back, so a hold
here is exactly what the dispatcher skips. aq is a recorder.

Fixtures, both committed:
  wq2484_lumen_package_0925.csv  Lumen's CPU package, 60 s, 2026-09-25T00:00Z -> 20:00Z: the
      day holding the hottest 5-min mean in 37 d of netdata. Saved by `thermal_backpressure.py
      replay --node lumen --after 1790294400 --before 1790366400 --save ...` on 2026-09-28.
  wq2510_isis_0918_0919.csv      wq#2510's real isis fan-floor event (Te0T 81-84 C, fan at
      the SMC floor), fed through fan_floor_alarm: a trigger outside the package-temp rule.
"""
import copy
import os
import sys
import tempfile
import unittest
from types import SimpleNamespace

HERE = os.path.dirname(os.path.abspath(__file__))
OPS = os.path.dirname(HERE)
sys.path.insert(0, OPS)
sys.path.insert(0, os.path.join(os.path.dirname(OPS), "dispatcher"))
import fan_floor_alarm as ffa  # noqa: E402
import gates  # noqa: E402
import thermal_backpressure as tb  # noqa: E402

LUMEN_0925 = os.path.join(HERE, "fixtures", "wq2484_lumen_package_0925.csv")
ISIS_0918 = os.path.join(HERE, "fixtures", "wq2510_isis_0918_0919.csv")
SLOCK = "/home/lumen/bin/slock"


def flat(value, minutes, t0=1790000000):
    return [(t0 + i * tb.STEP_S, value) for i in range(minutes)]


class Config(unittest.TestCase):
    def test_shipped_config_loads_with_hysteresis_on_every_sensed_node(self):
        cfg = tb.load_config()
        for name, node in cfg["nodes"].items():
            if node.get("netdata"):
                self.assertLess(node["clear_c"], node["raise_c"], name)

    def test_thor_can_never_hold(self):
        cfg = tb.load_config()
        bad = copy.deepcopy(cfg)
        bad["nodes"]["thor"].update(action="hold")
        self.assertRaises(tb.ConfigError, tb.validate, bad)
        bad = copy.deepcopy(cfg)
        bad["nodes"]["thor"]["hold_subsystems"] = ["thor"]
        self.assertRaises(tb.ConfigError, tb.validate, bad)
        bad = copy.deepcopy(cfg)
        bad["nodes"]["alias"] = {"netdata": f"{tb.THOR_IP}:19999", "raise_c": 90,
                                 "clear_c": 80, "action": "hold"}
        self.assertRaises(tb.ConfigError, tb.validate, bad)

    def test_equal_raise_and_clear_is_refused(self):
        bad = copy.deepcopy(tb.load_config())
        bad["nodes"]["lumen"]["clear_c"] = bad["nodes"]["lumen"]["raise_c"]
        self.assertRaises(tb.ConfigError, tb.validate, bad)


class Rule(unittest.TestCase):
    def setUp(self):
        self.cfg = tb.load_config()
        self.lumen = self.cfg["nodes"]["lumen"]

    def test_hottest_real_day_does_not_hold_at_the_shipped_thresholds(self):
        samples = tb.read_csv(LUMEN_0925)
        self.assertEqual(len(samples), 1200)
        self.assertEqual(max(v for _, v in samples if v is not None), 77.3)
        self.assertEqual(tb.evaluate(samples, self.lumen, self.cfg["sustain_min"]), [])

    def test_same_day_holds_once_the_line_is_below_it(self):
        # the probe above can fail: pull raise under the day's peak and it fires
        low = dict(self.lumen, raise_c=74, clear_c=70)
        tr = tb.evaluate(tb.read_csv(LUMEN_0925), low, self.cfg["sustain_min"])
        self.assertTrue(tr and tr[0]["state"] == "raise", tr)

    def test_oscillation_across_the_raise_line_does_not_flap(self):
        # 5 min at 81, then 60 min swinging 78 <-> 80 across raise_c 79, never under clear_c 70
        swing = [(t, 78.0 if i % 2 else 80.0) for i, (t, _) in enumerate(flat(0, 60, 1790000300))]
        tr = tb.evaluate(flat(81.0, 5) + swing, self.lumen, self.cfg["sustain_min"])
        self.assertEqual([t["state"] for t in tr], ["raise"])

    def test_oscillation_across_the_clear_line_never_raises(self):
        swing = [(1790000000 + i * 60, 69.0 if i % 2 else 71.0) for i in range(120)]
        self.assertEqual(tb.evaluate(swing, self.lumen, self.cfg["sustain_min"]), [])

    def test_gap_is_no_verdict(self):
        n = int(self.cfg["sustain_min"])
        win = flat(90.0, n)
        win[2] = (win[2][0], None)
        self.assertIsNone(tb.window_mean(win, n))
        self.assertTrue(tb.next_temp(True, None, self.lumen))
        self.assertFalse(tb.next_temp(False, None, self.lumen))


class Recorder:
    def __init__(self, fail_first=0):
        self.calls, self.fail_first = [], fail_first

    def __call__(self, key, title, detail):
        self.calls.append((key, title, detail))
        if self.fail_first:
            self.fail_first -= 1
            return False, "aq down"
        return True, "ok"


class EndToEnd(unittest.TestCase):
    """poll() against the real slock and the dispatcher's own gate."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.wq_db = os.path.join(self.tmp.name, "queue.db")
        os.environ["WQ_DB"] = self.wq_db
        self.cfg = tb.load_config()
        self.con = tb.connect(os.path.join(self.tmp.name, "state.db"))
        self.aq = Recorder()
        self.act = tb.Actuator(self.cfg["holder"], self.cfg["hold_ttl_min"], slock=SLOCK)
        self.act.aq = self.aq

    def tearDown(self):
        self.con.close()
        self.tmp.cleanup()
        os.environ.pop("WQ_DB", None)

    def poll(self, isis=None, lumen=None, fans=None):
        temps = {n: (None, "not read") for n in self.cfg["nodes"]}
        temps["isis"] = (isis, None)
        temps["lumen"] = (lumen, None)
        return {d["node"]: d for d in tb.poll(self.con, self.cfg, self.act, temps, fans or {})}

    def holder(self, scope="isis"):
        writers, _ = gates._live_holds(self.wq_db)
        return writers.get(scope)

    def dispatcher_verdict(self, subsystems="isis"):
        g = SimpleNamespace(tid=99001, t={"subsystems": subsystems}, ctx=SimpleNamespace())
        return gates.SubsystemHeld().check(g)

    def test_hot_holds_once_cool_releases_and_nothing_persists(self):
        self.poll(isis=60.0)
        self.assertIsNone(self.holder())
        self.assertIs(self.dispatcher_verdict(), gates.PASS)

        self.poll(isis=82.0)
        self.assertEqual(self.holder(), "thermal-backpressure")
        res = self.dispatcher_verdict()
        self.assertEqual(res.reason, "subsystem_held")
        self.assertIn("isis held by thermal-backpressure", res.detail)
        self.assertEqual(len(self.aq.calls), 1)
        self.assertTrue(self.aq.calls[0][1].startswith("thermal hold: isis"))

        # ten more hot polls, then a reading swinging across raise_c 79: no second row
        for v in [82.0] * 10 + [78.0, 80.0, 74.0, 80.0, 73.5, 79.5]:
            self.poll(isis=v)
        self.assertEqual(self.holder(), "thermal-backpressure")
        self.assertEqual(len(self.aq.calls), 1)

        self.poll(isis=72.9)
        self.assertIsNone(self.holder())
        self.assertIs(self.dispatcher_verdict(), gates.PASS)
        self.assertEqual(len(self.aq.calls), 2)
        self.assertTrue(self.aq.calls[1][1].startswith("thermal release: isis"))

        for _ in range(5):
            self.poll(isis=60.0)
        self.assertIsNone(self.holder())
        self.assertEqual(len(self.aq.calls), 2)
        kinds = [r["kind"] for r in self.con.execute("SELECT kind FROM transitions ORDER BY id")]
        self.assertEqual(kinds, ["hold", "release"])

    def test_missing_reading_keeps_the_hold(self):
        self.poll(isis=82.0)
        self.poll(isis=None)
        self.assertEqual(self.holder(), "thermal-backpressure")
        self.assertEqual(len(self.aq.calls), 1)

    def test_aq_failure_retries_the_same_key_without_a_second_row(self):
        self.aq.fail_first = 1
        self.poll(isis=82.0)
        self.poll(isis=82.0)
        self.poll(isis=82.0)
        self.assertEqual([c[0] for c in self.aq.calls], ["isis:hold:1", "isis:hold:1"])

    def test_worker_lock_defers_the_hold_and_the_next_poll_takes_it(self):
        env = dict(os.environ)
        import subprocess
        subprocess.run([SLOCK, "acquire", "isis", "--holder", "fworker-wq1"], env=env,
                       capture_output=True, check=True)
        self.poll(isis=82.0)
        self.assertEqual(self.holder(), "fworker-wq1")
        pending = self.con.execute("SELECT applied_at, apply_note FROM transitions").fetchone()
        self.assertIsNone(pending["applied_at"])
        self.assertIn("DENIED", pending["apply_note"])
        self.assertEqual(len(self.aq.calls), 1)
        subprocess.run([SLOCK, "release", "isis", "--holder", "fworker-wq1"], env=env,
                       capture_output=True, check=True)
        self.poll(isis=82.0)
        self.assertEqual(self.holder(), "thermal-backpressure")
        self.assertIsNotNone(self.con.execute("SELECT applied_at FROM transitions").fetchone()[0])
        self.assertEqual(len(self.aq.calls), 1)

    def test_release_never_frees_someone_elses_lock(self):
        import subprocess
        self.poll(isis=82.0)
        subprocess.run([SLOCK, "release", "isis", "--holder", "thermal-backpressure"],
                       capture_output=True, check=True)
        subprocess.run([SLOCK, "acquire", "isis", "--holder", "fworker-wq2"],
                       capture_output=True, check=True)
        self.poll(isis=60.0)
        self.assertEqual(self.holder(), "fworker-wq2")

    def test_real_isis_fan_floor_event_holds_through_the_other_trigger(self):
        # package temp stays cool; only wq#2510's verdict, off its committed fixture, is hot
        rule = ffa.load_config()["rule"]
        samples = ffa.read_csv(ISIS_0918)
        n = int(rule["sustain_min"] * 60 // ffa.STEP_S)
        up = ffa.evaluate(samples, rule)[0]["ts"]
        i = next(k for k, s in enumerate(samples) if s[0] == up)
        stats = ffa.window_stats(samples[i - n + 1:i + 1], rule)
        verdict = {"raise": ffa.next_state(False, stats, rule),
                   "clear": not ffa.next_state(True, stats, rule),
                   "te0t_mean": round(stats[0], 1), "fan_max": stats[1]}
        self.assertTrue(verdict["raise"])
        self.poll(isis=55.0, fans={"isis": verdict})
        self.assertEqual(self.holder(), "thermal-backpressure")
        self.assertIn("fan-floor Te0T", self.aq.calls[0][2])
        # no verdict next poll (netdata gap): still held
        self.poll(isis=55.0, fans={"isis": {"raise": None, "clear": None}})
        self.assertEqual(self.holder(), "thermal-backpressure")
        self.poll(isis=55.0, fans={"isis": {"raise": False, "clear": True}})
        self.assertIsNone(self.holder())
        self.assertEqual(len(self.aq.calls), 2)

    def test_lumen_hold_also_bites_litellm_and_minerva_db_tickets(self):
        """wq#3045: no ticket declares subsystems="lumen" (wq#995), so the node-name-only
        hold used to skip nothing. lumen's hold_subsystems now also names litellm and
        minerva-db — the tokens tickets actually declare (queue.db, 2026-09-28) — so the
        same hold that locks `lumen` also locks those, and the dispatcher gate refuses
        both. A ticket on an unrelated subsystem still passes."""
        self.assertIs(self.dispatcher_verdict("litellm"), gates.PASS)
        self.assertIs(self.dispatcher_verdict("minerva-db"), gates.PASS)

        self.poll(lumen=82.0)
        self.assertEqual(self.holder("lumen"), "thermal-backpressure")
        self.assertEqual(self.holder("litellm"), "thermal-backpressure")
        self.assertEqual(self.holder("minerva-db"), "thermal-backpressure")

        for scope in ("lumen", "litellm", "minerva-db"):
            res = self.dispatcher_verdict(scope)
            self.assertEqual(res.reason, "subsystem_held")
            self.assertIn(f"{scope} held by thermal-backpressure", res.detail)

        self.assertIs(self.dispatcher_verdict("fleet-ops"), gates.PASS)

        self.poll(lumen=65.0)
        self.assertIsNone(self.holder("lumen"))
        self.assertIsNone(self.holder("litellm"))
        self.assertIsNone(self.holder("minerva-db"))
        self.assertIs(self.dispatcher_verdict("litellm"), gates.PASS)

    def test_thor_hot_is_one_aq_row_and_no_lock(self):
        hot = {"raise": True, "clear": False, "te0t_mean": 85.0, "fan_max": 790}
        for _ in range(3):
            self.poll(isis=60.0, fans={"thor": hot})
        self.assertEqual([c[0] for c in self.aq.calls], ["thor:hot:1"])
        writers, _ = gates._live_holds(self.wq_db)
        self.assertEqual(writers, {})


if __name__ == "__main__":
    unittest.main(verbosity=2)
