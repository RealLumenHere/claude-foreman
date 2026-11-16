#!/usr/bin/env python3
"""Tests for auto-dispatch -- stubs every external binary (wq, systemctl, dispatch-batch,
ntfy-send, router, park), touches no live state.

Run:  python3 /home/lumen/foreman/ops/wq4956-auto-dispatch/test_auto_dispatch.py -v
Tests the bundle copy next to this file; env AUTO_DISPATCH_BIN overrides the script under
test (e.g. AUTO_DISPATCH_BIN=/home/lumen/bin/auto-dispatch for the deployed one).
"""
import datetime as dt
import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest

import yaml

SCRIPT = os.environ.get("AUTO_DISPATCH_BIN",
                        os.path.join(os.path.dirname(os.path.abspath(__file__)), "auto-dispatch"))

WQ_STUB = r'''#!/usr/bin/env python3
import json, os, sys
T = json.load(open(os.environ["AD_TICKETS"]))
a = sys.argv[1:]
if a[0] == "list":
    lane = a[-1]
    for i, t in sorted(T.items(), key=lambda kv: (kv[1].get("pri", 2), int(kv[0]))):
        if t["status"] == "open" and t["lane"] == lane:
            print("[%4s] %s p%s open           %s" % (i, t["lane"], t.get("pri", 2), t["title"]))
elif a[0] == "show":
    t = T.get(a[1])
    if not t:
        sys.exit(1)
    keys = ["id","lane","pri","title","detail","deps","status","claimed_by","model","effort","gate","gate_type",
            "frank_response","hold_by","hold_expires","created_by","created_at","released_at"]
    d = dict(t, id=a[1])
    for k in keys:
        v = d.get(k, "")
        print("%-12s : %s" % (k, v if v != "" else "None"))
elif a[0] == "orch":
    print(os.environ.get("AD_ORCH", "wq orch: no orchestrator holds a lane-set lease."))
'''
SYSTEMCTL_STUB = r'''#!/bin/bash
if [ "$1" = "list-units" ]; then
  n=${AD_RUNNING:-0}; for i in $(seq 1 $n); do echo "lumen-job-fworker-wq$i-1.service loaded active running x"; done
elif [ "$1" = "is-active" ]; then echo "${AD_BROKER:-inactive}"; fi
exit 0
'''
DB_STUB = '#!/bin/bash\necho "$@" >> "$AD_CALLS"\necho "UNIT=lumen-job-fworker-wq$1-1"\nexit ${AD_DB_RC:-0}\n'
NTFY_STUB = '#!/bin/bash\necho "$@" >> "$AD_NTFY"\nexit 0\n'
ROUTER_STUB = r'''import json, os, sys
if os.environ.get("AD_ROUTER_RC", "0") != "0":
    sys.exit(int(os.environ["AD_ROUTER_RC"]))
T = json.load(open(os.environ["AD_TICKETS"]))
T[sys.argv[2]]["model"] = os.environ.get("AD_ROUTER_MODEL", "claude-sonnet-5-5")
json.dump(T, open(os.environ["AD_TICKETS"], "w"))
'''

# wq#5009 (v3): the park-and-resume sweep is ALWAYS stubbed here. Without this the pass
# would run the real foreman/harness/park.py against the live _runs tree and could
# `wq review accept` a real parked ticket from inside a unit test.
PARK_STUB = r'''import json, os, sys
with open(os.environ["AD_PARK_CALLS"], "a") as f:
    f.write(" ".join(sys.argv[1:]) + "\n")
rc = int(os.environ.get("AD_PARK_RC", "0"))
if rc:
    sys.stderr.write("park stub failing on request\n")
    sys.exit(rc)
print(os.environ.get("AD_PARK", json.dumps({"actions": {}, "ready": []})))
'''

NOON_PT = "2026-11-12T12:00:00-08:00"    # inside default waking window
NIGHT_PT = "2026-11-12T03:00:00-08:00"   # outside it


def tk(lane="build", pri=2, title="work item", **kw):
    d = dict(lane=lane, pri=pri, title=title, status="open", detail="", deps="", model="claude-sonnet-5-5")
    d.update(kw)
    return d


class Base(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp(prefix="ad-test-")
        p = lambda n: os.path.join(self.d, n)
        for name, body in (("wq", WQ_STUB), ("systemctl", SYSTEMCTL_STUB), ("dispatch-batch", DB_STUB),
                           ("ntfy-send", NTFY_STUB)):
            with open(p(name), "w") as f:
                f.write(body)
            os.chmod(p(name), os.stat(p(name)).st_mode | stat.S_IEXEC)
        with open(p("router.py"), "w") as f:
            f.write(ROUTER_STUB)
        with open(p("park.py"), "w") as f:
            f.write(PARK_STUB)
        self.paths = dict(tickets=p("tickets.json"), calls=p("calls"), ntfy=p("ntfy"), log=p("ad.log"),
                          state=p("state.json"), budget=p("budget.json"), pause=p("PAUSE"), disarm=p("DISARM"),
                          stop=p("STOP"), lock=p("lock"), cfg=p("cfg.yaml"), park_calls=p("park_calls"))
        self.tickets = {}
        self.env = dict(os.environ, AD_TICKETS=self.paths["tickets"], AD_CALLS=self.paths["calls"],
                        AD_NTFY=self.paths["ntfy"], AD_RUNNING="0", AD_BROKER="inactive",
                        AD_PARK_CALLS=self.paths["park_calls"])
        self.limits = {"session": {"percent": 20}, "weekly_all": {"percent": 30},
                       "fable_weekly_scoped": {"percent": 10}}
        self.overrides = {}

    def write(self, budget_stale=False, cache_age=5.0):
        json.dump(self.tickets, open(self.paths["tickets"], "w"))
        json.dump({"updated_at": dt.datetime.now(dt.timezone.utc).isoformat(), "cache_age_s": cache_age,
                   "stale": budget_stale, "limits": self.limits}, open(self.paths["budget"], "w"))
        pt = self.paths
        cfg = {
            "lanes": ["build", "ops", "research"],
            "halt_files": [pt["pause"], pt["disarm"], pt["stop"]],
            "foreman_broker": {"timer": "x.timer", "disarm_file": pt["disarm"], "stop_file": pt["stop"]},
            "budget": {"state_file": pt["budget"]},
            "log_file": pt["log"], "state_file": pt["state"], "lock_file": pt["lock"],
            "bins": {"wq": os.path.join(self.d, "wq"), "dispatch_batch": os.path.join(self.d, "dispatch-batch"),
                     "ntfy_send": os.path.join(self.d, "ntfy-send"), "router": os.path.join(self.d, "router.py"),
                     "systemctl": os.path.join(self.d, "systemctl"),
                     "park": os.path.join(self.d, "park.py")},
        }
        for k, v in self.overrides.items():
            cfg[k] = {**cfg.get(k, {}), **v} if isinstance(v, dict) and isinstance(cfg.get(k), dict) else v
        yaml.safe_dump(cfg, open(pt["cfg"], "w"))

    def run_ad(self, *args, now=NIGHT_PT, **env):
        self.write()
        e = dict(self.env, **{k: str(v) for k, v in env.items()})
        r = subprocess.run([sys.executable, SCRIPT, "--config", self.paths["cfg"], "--now", now, *args],
                           capture_output=True, text=True, env=e, timeout=60)
        return r

    def events(self):
        if not os.path.exists(self.paths["log"]):
            return []
        return [json.loads(l) for l in open(self.paths["log"]) if l.strip()]

    def calls(self):
        f = self.paths["calls"]
        return open(f).read().split() if os.path.exists(f) else []

    def park_calls(self):
        f = self.paths["park_calls"]
        return open(f).read().splitlines() if os.path.exists(f) else []

    def ntfys(self):
        f = self.paths["ntfy"]
        return open(f).read().splitlines() if os.path.exists(f) else []

    def halts(self):
        return [e for e in self.events() if e["event"] == "halt"]

    def skips(self):
        return {e["ticket"]: e["reason"] for e in self.events() if e["event"] == "skip"}


class TestHaltGates(Base):
    def test_pause_file_halts(self):
        self.tickets = {"1": tk()}
        open(self.paths["pause"], "w").close()
        r = self.run_ad()
        self.assertEqual(r.returncode, 0)
        self.assertEqual(self.calls(), [])
        self.assertIn("PAUSE", self.halts()[0]["reason"])

    def test_disarm_file_halts(self):
        self.tickets = {"1": tk()}
        open(self.paths["disarm"], "w").close()
        self.run_ad()
        self.assertEqual(self.calls(), [])
        self.assertIn("DISARM", self.halts()[0]["reason"])

    def test_stop_file_halts(self):
        self.tickets = {"1": tk()}
        open(self.paths["stop"], "w").close()
        self.run_ad()
        self.assertEqual(self.calls(), [])

    def test_armed_foreman_broker_refused(self):
        self.tickets = {"1": tk()}
        self.run_ad(AD_BROKER="active")
        self.assertEqual(self.calls(), [])
        self.assertEqual(self.halts()[0]["kind"], "broker_conflict")

    def test_latched_broker_is_not_a_conflict(self):
        self.tickets = {"1": tk()}
        self.overrides = {"halt_files": [self.paths["pause"]]}  # Frank removed DISARM from halt_files
        open(self.paths["disarm"], "w").close()                  # ...broker stays latched
        self.run_ad(AD_BROKER="active")
        self.assertEqual(self.calls(), ["1"])

    def test_stale_budget_fails_closed(self):
        self.tickets = {"1": tk()}
        self.write(budget_stale=True, cache_age=2197312)
        r = subprocess.run([sys.executable, SCRIPT, "--config", self.paths["cfg"], "--now", NIGHT_PT],
                           capture_output=True, text=True, env=self.env)
        self.assertEqual(r.returncode, 0)
        self.assertEqual(self.calls(), [])
        self.assertEqual(self.halts()[0]["kind"], "budget_stale")
        self.assertIn("25.4 days", self.halts()[0]["reason"])

    def test_stale_budget_advisory_mode_dispatches(self):
        self.tickets = {"1": tk()}
        self.overrides = {"budget": {"on_stale": "advisory"}}
        self.write(budget_stale=True, cache_age=2197312)
        subprocess.run([sys.executable, SCRIPT, "--config", self.paths["cfg"], "--now", NIGHT_PT],
                       capture_output=True, text=True, env=self.env)
        self.assertEqual(self.calls(), ["1"])


class TestWakingHoursAndConcurrency(Base):
    def test_waking_hours_small_queue_defers_to_frank(self):
        self.tickets = {str(i): tk() for i in range(1, 6)}
        self.run_ad(now=NOON_PT)
        self.assertEqual(self.calls(), [])
        self.assertEqual(self.halts()[0]["kind"], "waking_hours")

    def test_waking_hours_big_queue_dispatches(self):
        self.tickets = {str(i): tk() for i in range(1, 13)}  # 12 > 10
        self.run_ad(now=NOON_PT)
        self.assertEqual(len(self.calls()), 3)

    def test_threshold_is_strictly_greater_than(self):
        self.tickets = {str(i): tk() for i in range(1, 11)}  # exactly 10
        self.run_ad(now=NOON_PT)
        self.assertEqual(self.calls(), [])

    def test_outside_waking_hours_small_queue_dispatches(self):
        self.tickets = {"1": tk(), "2": tk()}
        self.run_ad(now=NIGHT_PT)
        self.assertEqual(sorted(self.calls()), ["1", "2"])

    def test_ceiling_reached_halts(self):
        self.tickets = {"1": tk()}
        self.run_ad(AD_RUNNING=3)
        self.assertEqual(self.calls(), [])
        self.assertEqual(self.halts()[0]["kind"], "concurrency")

    def test_only_free_slots_are_used(self):
        self.tickets = {str(i): tk() for i in range(1, 8)}
        self.run_ad(AD_RUNNING=2)
        self.assertEqual(len(self.calls()), 1)

    def test_never_more_than_ceiling_from_idle(self):
        self.tickets = {str(i): tk() for i in range(1, 8)}
        self.run_ad(AD_RUNNING=0)
        self.assertEqual(len(self.calls()), 3)

    def test_priority_order(self):
        self.tickets = {"10": tk(pri=3), "11": tk(pri=0), "12": tk(pri=1), "13": tk(pri=2)}
        self.run_ad()
        self.assertEqual(self.calls(), ["11", "12", "13"])


class TestTicketGates(Base):
    def test_skip_reasons(self):
        self.tickets = {
            "1": tk(title="[no-auto] rotate keys"),
            "2": tk(detail="Run manually --no-auto"),
            "3": tk(deps="900,901"),
            "4": tk(title="[needs-window] kernel work"),
            "5": tk(gate_type="frank_decision"),
            "6": tk(lane="fleet"),                       # lane not in config
            "7": tk(),                                   # the only eligible one
            "900": tk(status="done", lane="x"),
            "901": tk(status="open", lane="x"),
        }
        self.run_ad()
        self.assertEqual(self.calls(), ["7"])
        s = self.skips()
        self.assertIn("no-auto", s[1])
        self.assertIn("no-auto", s[2])
        self.assertIn("unmet dep #901", s[3])
        self.assertIn("needs-window", s[4])
        self.assertIn("Frank-gated", s[5])
        self.assertNotIn(6, s)

    def test_frank_gated_status_never_listed(self):
        self.tickets = {"1": tk(status="frank_gated"), "2": tk(status="claimed", claimed_by="fworker-wq2")}
        self.run_ad()
        self.assertEqual(self.calls(), [])

    def test_claim_race_after_list_is_caught(self):
        t = tk()
        self.tickets = {"1": t}
        # listed as open but show says claimed: simulate by claiming between list and show
        t["claimed_by"] = "fworker-wq1"
        self.run_ad()
        self.assertEqual(self.calls(), [])

    def test_lane_lease_held_by_other_orchestrator(self):
        self.tickets = {"1": tk(lane="build"), "2": tk(lane="ops")}
        self.run_ad(AD_ORCH="orch-x holds lanes: build since 2026-11-12 21:00")
        self.assertEqual(self.calls(), ["2"])
        self.assertIn("lease", self.skips()[1])

    def test_unmet_dep_satisfied_when_done(self):
        self.tickets = {"1": tk(deps="2"), "2": tk(status="done", lane="x")}
        self.run_ad()
        self.assertEqual(self.calls(), ["1"])

    def test_cooldown_blocks_redispatch_and_cap_applies(self):
        self.tickets = {"1": tk()}
        self.run_ad()
        self.assertEqual(self.calls(), ["1"])
        self.run_ad()                                        # ledger: still open, dispatched seconds ago
        self.assertEqual(self.calls(), ["1"])
        self.assertIn("cooldown", self.skips()[1])
        st = json.load(open(self.paths["state"]))
        st["dispatched"]["1"] = [__import__("time").time() - 4000, __import__("time").time() - 3000]
        json.dump(st, open(self.paths["state"], "w"))        # two attempts in 24h, cooldown elapsed
        self.run_ad()
        self.assertEqual(self.calls(), ["1"])
        self.assertTrue(any("attempt cap" in e.get("reason", "") for e in self.events() if e["event"] == "skip"))

    def test_unstamped_ticket_is_router_assigned_then_dispatched(self):
        self.tickets = {"1": tk(model="")}
        self.run_ad(AD_ROUTER_MODEL="claude-opus-5-5")
        self.assertEqual(self.calls(), ["1"])
        d = [e for e in self.events() if e["event"] == "dispatch"][0]
        self.assertEqual((d["model"], d["model_source"]), ("claude-opus-5-5", "router-assigned"))

    def test_router_failure_skips_and_alerts_not_dispatches(self):
        self.tickets = {"1": tk(model="")}
        r = self.run_ad(AD_ROUTER_RC=3)
        self.assertEqual(self.calls(), [])
        self.assertEqual(r.returncode, 1)
        self.assertTrue(any("FAILURE" in n for n in self.ntfys()))


class TestBrokerMirroredGates(Base):
    """Opt-outs mirrored from dispatcher/gates.py: the fleet's own definition of 'not auto-dispatchable'."""

    def one(self, **kw):
        for k in ("calls", "state", "log", "ntfy"):          # each probe starts from a clean slate
            if os.path.exists(self.paths[k]):
                os.remove(self.paths[k])
        self.tickets = {"1": tk(**kw)}
        self.run_ad()
        return self.calls() == ["1"], self.skips().get(1, "")

    def test_nodispatch_markers(self):
        for title in ("[EPIC] rollup", "[do not claim] x", "[DO-NOT-CLAIM] y"):
            ok, why = self.one(title=title)
            self.assertFalse(ok, title)
            self.assertIn("nodispatch", why)

    def test_frank_title_markers(self):
        for title in ("[FRANK GATE] pick", "[manual] step", "task owner = frank", "[rotation] cloudflare token",
                      "[rotation] CF_API key"):
            ok, why = self.one(title=title)
            self.assertFalse(ok, title)
            self.assertIn("Frank-gated", why)

    def test_non_cf_rotation_is_not_held(self):
        ok, _ = self.one(title="[rotation] gemini key")
        self.assertTrue(ok)

    def test_decision_gate_pending_vs_answered(self):
        ok, why = self.one(gate_type="frank_decision")
        self.assertFalse(ok)
        self.assertIn("decision gate", why)
        ok, _ = self.one(gate_type="frank_decision", frank_response="approved")
        self.assertTrue(ok)

    def test_wq_gate_path_precondition(self):
        ok, why = self.one(gate="/nonexistent/precondition")
        self.assertFalse(ok)
        self.assertIn("gate unmet", why)
        ok, _ = self.one(gate="/tmp")
        self.assertTrue(ok)
        ok, _ = self.one(gate="/tmp:missing")
        self.assertFalse(ok)

    def test_live_hold_vs_expired(self):
        ok, why = self.one(hold_by="orch-a", hold_expires="2026-11-12 12:00:00")   # NIGHT_PT == 11:00Z
        self.assertFalse(ok)
        self.assertIn("hold", why)
        ok, _ = self.one(hold_by="orch-a", hold_expires="2026-11-12 10:00:00")
        self.assertTrue(ok)

    def test_orch_fresh_grace(self):
        ok, why = self.one(created_by="orch-x", created_at="2026-11-12 10:55:00")  # 5 min old at 11:00Z
        self.assertFalse(ok)
        self.assertIn("orch-fresh", why)
        ok, _ = self.one(created_by="orch-x", created_at="2026-11-12 10:30:00")
        self.assertTrue(ok)
        ok, _ = self.one(created_by="orch-x", created_at="2026-11-12 10:55:00", released_at="2026-11-12 10:56:00")
        self.assertTrue(ok)
        ok, _ = self.one(created_by="fworker-9", created_at="2026-11-12 10:55:00")
        self.assertTrue(ok)

    def test_human_lane_refused_at_config_load(self):
        self.tickets = {"1": tk()}
        self.overrides = {"lanes": ["build", "user"]}
        r = self.run_ad()
        self.assertEqual(r.returncode, 2)
        self.assertIn("human-owned", r.stderr)

    def test_padded_ids_parse(self):
        self.tickets = {"7": tk(), "42": tk()}
        self.run_ad()
        self.assertEqual(sorted(self.calls()), ["42", "7"])


class TestFailureBreaker(Base):
    def stuck(self, ids, age_s=3000):
        import time
        json.dump({"dispatched": {str(i): [time.time() - age_s] for i in ids}}, open(self.paths["state"], "w"))

    def test_trips_when_dispatched_tickets_are_still_open(self):
        self.tickets = {str(i): tk() for i in range(1, 6)}
        self.stuck([1, 2, 3])
        self.run_ad()
        self.assertEqual(self.calls(), [])
        self.assertEqual(self.halts()[0]["kind"], "failure_breaker")
        self.assertTrue(any("BREAKER" in n and "lumen-alerts" in n for n in self.ntfys()))

    def test_below_threshold_keeps_dispatching(self):
        self.tickets = {str(i): tk() for i in range(1, 6)}
        self.stuck([1, 2])
        self.run_ad()
        self.assertTrue(self.calls())

    def test_claimed_tickets_do_not_count(self):
        self.tickets = {"1": tk(status="claimed", claimed_by="fworker-wq1"), "2": tk(), "3": tk(), "4": tk()}
        self.stuck([1, 2, 3])                      # 1 stuck-but-claimed (worker is alive) -> only 2 count
        self.run_ad()
        self.assertTrue(self.calls())

    def test_inside_cooldown_is_not_stuck_yet(self):
        self.tickets = {str(i): tk() for i in range(1, 6)}
        self.stuck([1, 2, 3], age_s=60)            # just dispatched, claim may not be visible yet
        self.run_ad()
        self.assertNotEqual(self.halts() and self.halts()[0].get("kind"), "failure_breaker")

    def test_old_attempts_age_out(self):
        self.tickets = {str(i): tk() for i in range(1, 6)}
        self.stuck([1, 2, 3], age_s=9000)          # > 2h window
        self.run_ad()
        self.assertTrue(self.calls())


class TestBrokerParity(unittest.TestCase):
    """Drift guard: mirrored rules vs the live broker functions. Skips where the broker is absent."""

    @classmethod
    def setUpClass(cls):
        import importlib.machinery, importlib.util
        try:
            sys.path.insert(0, "/home/lumen/foreman/dispatcher")
            import foreman_dispatcher as fd
            cls.fd = fd
        except Exception as e:  # noqa: BLE001
            raise unittest.SkipTest("broker not importable here: %r" % (e,))
        loader = importlib.machinery.SourceFileLoader("ad_under_test", SCRIPT)
        spec = importlib.util.spec_from_loader("ad_under_test", loader)
        cls.ad = importlib.util.module_from_spec(spec)
        loader.exec_module(cls.ad)

    def test_title_rules_agree(self):
        titles = ["plain", "[FRANK GATE] x", "[manual] y", "owner = frank", "owner=FRANK", "[rotation] cloudflare",
                  "[rotation] CF_TOKEN", "[rotation] gemini", "[EPIC] a", "[do not claim] b", "[DO-NOT-CLAIM] c",
                  "[donotclaim] d", "ROTATION cloudflare no bracket"]
        for t in titles:
            tk_ = {"title": t}
            self.assertEqual(self.fd.frank_gate_reason(tk_) is None, self.ad.frank_gate_reason(tk_) is None, t)
            self.assertEqual(self.fd.nodispatch_reason(tk_) is None, self.ad.nodispatch_reason(tk_) is None, t)

    def test_gate_fields_agree(self):
        cases = [dict(gate_type="frank_decision"), dict(gate_type="frank_decision", frank_response="ok"),
                 dict(gate_type="None", frank_response="None"), dict(gate="/nonexistent/x"), dict(gate="/tmp"),
                 dict(gate="/tmp:missing"), dict(gate="None"), dict(gate="")]
        for c in cases:
            t = dict(title="plain", **c)
            self.assertEqual(self.fd.frank_gate_reason(t) is None, self.ad.frank_gate_reason(t) is None, c)

    def test_hold_and_orch_fresh_agree(self):
        now = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)
        f = lambda d: d.strftime("%Y-%m-%d %H:%M:%S")
        for t in (dict(hold_by="a", hold_expires=f(now + dt.timedelta(hours=1))),
                  dict(hold_by="a", hold_expires=f(now - dt.timedelta(hours=1))),
                  dict(hold_by="None", hold_expires="None"), dict()):
            self.assertEqual(self.fd.hold_reason(t) is None, self.ad.hold_reason(t, now) is None, t)
        for t in (dict(created_by="orch-x", created_at=f(now - dt.timedelta(minutes=2))),
                  dict(created_by="orch-x", created_at=f(now - dt.timedelta(hours=3))),
                  dict(created_by="orch-x", created_at=f(now - dt.timedelta(minutes=2)), released_at=f(now)),
                  dict(created_by="fworker-1", created_at=f(now - dt.timedelta(minutes=2)))):
            self.assertEqual(self.fd.orch_fresh_until(t) is None, self.ad.orch_fresh_until(t, now) is None, t)

    def test_human_lanes_agree(self):
        self.assertEqual(set(self.fd.HUMAN_LANES), self.ad.HUMAN_LANES)


class TestBudgetFloors(Base):
    def test_session_floor(self):
        self.limits["session"]["percent"] = 75              # 25% left < 30% reserve
        self.tickets = {"1": tk()}
        self.run_ad()
        self.assertEqual(self.calls(), [])
        self.assertIn("session", self.skips()[1])

    def test_weekly_floor(self):
        self.limits["weekly_all"]["percent"] = 90
        self.tickets = {"1": tk()}
        self.run_ad()
        self.assertEqual(self.calls(), [])

    def test_fable_pool_checked_only_for_fable(self):
        self.limits["fable_weekly_scoped"]["percent"] = 95
        self.tickets = {"1": tk(model="claude-fable-5-2"), "2": tk(model="claude-sonnet-5-5")}
        self.run_ad()
        self.assertEqual(self.calls(), ["2"])
        self.assertIn("fable_weekly_scoped", self.skips()[1])

    def test_non_anthropic_model_ignores_max_pools(self):
        self.limits["session"]["percent"] = 99
        self.tickets = {"1": tk(model="deepseek-v4-flash")}
        self.run_ad()
        self.assertEqual(self.calls(), ["1"])


class TestFailuresLoggingDryRun(Base):
    def test_dispatch_batch_failure_alerts_and_exits_1(self):
        self.tickets = {"1": tk()}
        r = self.run_ad(AD_DB_RC=7)
        self.assertEqual(r.returncode, 1)
        self.assertTrue(any("FAILURE" in n and "lumen-alerts" in n for n in self.ntfys()))

    def test_success_notifies_fleet_topic(self):
        self.tickets = {"1": tk(title="ship the thing")}
        self.run_ad()
        n = self.ntfys()
        self.assertTrue(any("lumen-fleet" in x and "1 dispatched" in x for x in n))

    def test_noop_pass_sends_no_ntfy(self):
        self.tickets = {}
        self.run_ad()
        self.assertEqual(self.ntfys(), [])

    def test_failure_ntfy_is_deduped(self):
        self.tickets = {"1": tk()}
        self.run_ad(AD_DB_RC=7)
        st = json.load(open(self.paths["state"]))
        st["dispatched"] = {}                                 # clear cooldown, keep ntfy dedup
        json.dump(st, open(self.paths["state"], "w"))
        self.run_ad(AD_DB_RC=7)
        self.assertEqual(len([n for n in self.ntfys() if "FAILURE" in n]), 1)

    def test_every_pass_logs_a_summary_with_pt_timestamp(self):
        self.tickets = {"1": tk()}
        self.run_ad()
        ev = self.events()
        self.assertEqual(ev[-1]["event"], "pass_summary")
        self.assertTrue(ev[-1]["ts_pt"].endswith("-08:00"))
        self.assertTrue(all(e["pass"] == ev[0]["pass"] for e in ev))

    def test_dry_run_touches_nothing(self):
        self.tickets = {"1": tk(), "2": tk(model="")}
        r = self.run_ad("--dry-run")
        self.assertEqual(r.returncode, 0)
        self.assertEqual(self.calls(), [])
        self.assertEqual(self.ntfys(), [])
        self.assertFalse(os.path.exists(self.paths["log"]))
        self.assertFalse(os.path.exists(self.paths["state"]))
        self.assertEqual(json.load(open(self.paths["tickets"]))["2"]["model"], "")   # no router --live
        self.assertIn('"event": "dispatch"', r.stdout)
        self.assertIn("NOT called", r.stdout)

    def test_skip_logging_only_on_change(self):
        self.tickets = {"1": tk(title="[no-auto] x")}
        self.run_ad()
        self.run_ad()
        self.assertEqual(len([e for e in self.events() if e["event"] == "skip"]), 1)
        self.run_ad("--verbose")
        self.assertEqual(len([e for e in self.events() if e["event"] == "skip"]), 2)

    def test_wq_down_is_loud(self):
        self.tickets = {"1": tk()}
        self.overrides = {"bins": {"wq": "/nonexistent/wq"}}
        r = self.run_ad()
        self.assertEqual(r.returncode, 1)
        self.assertTrue(any("wq_list" in n for n in self.ntfys()))

    def test_bad_config_exits_2_loudly(self):
        self.write()
        open(self.paths["cfg"], "w").write("lanes: []\n")
        r = subprocess.run([sys.executable, SCRIPT, "--config", self.paths["cfg"]], capture_output=True, text=True)
        self.assertEqual(r.returncode, 2)
        self.assertIn("bad config", r.stderr)

    def test_lock_held_skips_pass(self):
        import fcntl
        self.tickets = {"1": tk()}
        self.write()
        f = open(self.paths["lock"], "w")
        fcntl.flock(f, fcntl.LOCK_EX)
        r = subprocess.run([sys.executable, SCRIPT, "--config", self.paths["cfg"], "--now", NIGHT_PT],
                           capture_output=True, text=True, env=self.env)
        f.close()
        self.assertEqual(r.returncode, 0)
        self.assertEqual(self.calls(), [])
        self.assertEqual(self.halts()[0]["kind"], "lock")


class TestParkResume(Base):
    """wq#5009 (v3): a ticket whose parked round Frank answered is a RESUME of an existing
    session, not a fresh dispatch."""

    def ready(self, *ids, actions=None):
        return json.dumps({"actions": actions or {str(i): "answered" for i in ids},
                           "ready": list(ids)})

    def answered(self, **kw):
        return tk(gate_type="frank_decision", frank_response="option A", **kw)

    def test_answered_round_goes_first_regardless_of_priority(self):
        self.tickets = {"5": tk(pri=1), "6": tk(pri=1), "9": self.answered(pri=3)}
        self.run_ad(AD_PARK=self.ready(9))
        self.assertEqual(self.calls(), ["9", "5", "6"])
        d = [e for e in self.events() if e["event"] == "dispatch"]
        self.assertEqual([e["park_resume"] for e in d], [True, False, False])
        self.assertEqual([e for e in self.events() if e["event"] == "park_sweep"][0]["ready"], [9])

    def test_resume_is_exempt_from_cooldown_and_daily_cap(self):
        import time
        self.tickets = {"9": self.answered(), "5": tk()}
        json.dump({"dispatched": {"9": [time.time() - 86000, time.time() - 30],
                                  "5": [time.time() - 30]}}, open(self.paths["state"], "w"))
        self.run_ad(AD_PARK=self.ready(9))
        self.assertEqual(self.calls(), ["9"])                    # 5 is still cooling down
        self.assertIn("cooldown", self.skips()[5])

    def test_without_an_answer_the_same_ticket_still_cools_down(self):
        import time
        self.tickets = {"9": self.answered()}
        json.dump({"dispatched": {"9": [time.time() - 30]}}, open(self.paths["state"], "w"))
        self.run_ad()
        self.assertEqual(self.calls(), [])

    def test_waking_hours_small_queue_resumes_only_the_answered_round(self):
        self.tickets = {"5": tk(pri=1), "9": self.answered(pri=3)}
        self.run_ad(now=NOON_PT, AD_PARK=self.ready(9))
        self.assertEqual(self.calls(), ["9"])
        self.assertEqual(self.halts(), [])

    def test_waking_hours_small_queue_without_a_resume_still_defers_to_frank(self):
        self.tickets = {"5": tk(), "9": self.answered()}
        self.run_ad(now=NOON_PT)
        self.assertEqual(self.calls(), [])
        self.assertEqual(self.halts()[0]["kind"], "waking_hours")

    def test_failure_breaker_does_not_count_a_reopened_round(self):
        import time
        self.tickets = {str(i): tk() for i in (1, 2, 4)}
        self.tickets["3"] = self.answered()
        json.dump({"dispatched": {str(i): [time.time() - 3000] for i in (1, 2, 3)}},
                  open(self.paths["state"], "w"))
        self.run_ad(AD_PARK=self.ready(3))
        self.assertEqual(self.halts(), [])                       # 2 stuck < threshold 3
        self.assertEqual(self.calls()[0], "3")

    def test_failure_breaker_still_trips_on_real_stuck_tickets(self):
        import time
        self.tickets = {str(i): tk() for i in (1, 2, 4, 5)}
        self.tickets["3"] = self.answered()
        json.dump({"dispatched": {str(i): [time.time() - 3000] for i in (1, 2, 3, 4)}},
                  open(self.paths["state"], "w"))
        self.run_ad(AD_PARK=self.ready(3))
        self.assertEqual(self.calls(), [])
        self.assertEqual(self.halts()[0]["kind"], "failure_breaker")

    def test_sweep_runs_before_the_halts_but_a_halt_still_dispatches_nothing(self):
        self.tickets = {"9": self.answered()}
        open(self.paths["pause"], "w").close()
        self.run_ad(AD_PARK=self.ready(9))
        self.assertEqual(self.park_calls(), ["sweep"])
        self.assertEqual(self.calls(), [])
        self.assertIn("PAUSE", self.halts()[0]["reason"])

    def test_resume_still_obeys_the_concurrency_ceiling_and_ticket_gates(self):
        self.tickets = {"9": self.answered()}
        self.run_ad(AD_PARK=self.ready(9), AD_RUNNING=3)
        self.assertEqual(self.calls(), [])
        self.assertEqual(self.halts()[0]["kind"], "concurrency")
        self.tickets = {"9": self.answered(title="[no-auto] hands off")}
        self.run_ad(AD_PARK=self.ready(9))
        self.assertEqual(self.calls(), [])

    def test_ready_id_that_is_not_an_open_ticket_is_ignored(self):
        self.tickets = {"5": tk(), "9": self.answered(status="pending_review")}
        self.run_ad(AD_PARK=self.ready(9, 77))
        self.assertEqual(self.calls(), ["5"])

    def test_sweep_failure_is_logged_and_the_pass_carries_on(self):
        self.tickets = {"5": tk()}
        r = self.run_ad(AD_PARK_RC=3)
        self.assertEqual(r.returncode, 0)
        self.assertEqual(self.calls(), ["5"])
        self.assertTrue([e for e in self.events() if e["event"] == "park_sweep_error"])
        self.tickets = {"6": tk()}
        self.run_ad(AD_PARK="not json at all")
        self.assertEqual(self.calls(), ["5", "6"])

    def test_dry_run_sweeps_dry(self):
        self.tickets = {"9": self.answered()}
        r = self.run_ad("--dry-run", AD_PARK=self.ready(9))
        self.assertEqual(self.park_calls(), ["sweep --dry-run"])
        self.assertEqual(self.calls(), [])
        self.assertIn('"park_resume": true', r.stdout)

    def test_hook_can_be_switched_off(self):
        self.tickets = {"9": self.answered(), "5": tk(pri=1)}
        self.overrides = {"park_resume": {"enabled": False}}
        self.run_ad(AD_PARK=self.ready(9))
        self.assertEqual(self.park_calls(), [])
        self.assertEqual(self.calls(), ["5", "9"])               # plain priority order

    def test_quiet_sweep_writes_no_log_line(self):
        self.tickets = {"5": tk()}
        self.run_ad(AD_PARK=json.dumps({"actions": {"7": "waiting"}, "ready": []}))
        self.assertFalse([e for e in self.events() if e["event"].startswith("park_sweep")])


if __name__ == "__main__":
    unittest.main()
