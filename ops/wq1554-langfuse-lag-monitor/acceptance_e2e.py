"""wq#3124 acceptance run: the REAL monitor module against the LIVE instance.

Positive path: pass 1 plants a canary (exit 0, pending saved), pass 2 after 20s
confirms it (exit 0, last_ok_at advanced, fresh pending planted). Negative
control (a probe that can fail, wq#2630): a host outside the monitor's real
target — an unreachable port on localhost — with pre-aged state must ALERT via
the stubbed ntfy path and must NOT advance last_ok_at. Prints pass/fail lines
only; exits nonzero on any failed expectation.
"""
import json
import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                os.pardir, os.pardir, "harness"))
import langfuse_lag_monitor as mod

failures = []


def check(name, cond):
    print(f"{'PASS' if cond else 'FAIL'}: {name}")
    if not cond:
        failures.append(name)


env_path = os.getenv("LANGFUSE_ENV", "/home/lumen/.secrets/langfuse-foreman.env")
state_path = os.path.join(tempfile.mkdtemp(prefix="wq3124-e2e-"), "state.json")

rc1 = mod.run(env_path, 2700, state_path=state_path)
s1 = mod.load_state(state_path)
check("pass1 exit 0 (canary planted)", rc1 == 0)
check("pass1 pending_id saved", bool(s1.get("pending_id")))

time.sleep(20)
rc2 = mod.run(env_path, 2700, state_path=state_path)
s2 = mod.load_state(state_path)
check("pass2 exit 0", rc2 == 0)
check("pass2 confirmed canary (last_ok_at set)", bool(s2.get("last_ok_at")))
check("pass2 planted fresh canary", s2.get("pending_id") not in (None, s1.get("pending_id")))

# Negative control: unreachable host + stale state => alert fires, no green.
alerts = []
mod.alert_real = mod.alert
mod.alert = lambda lag, thresh: alerts.append((lag, thresh))
neg_state = os.path.join(os.path.dirname(state_path), "neg.json")
old = time.time() - 4000
with open(neg_state, "w") as f:
    json.dump({"pending_id": "never-existed", "pending_ingested_at": old,
               "last_ok_at": old}, f)
orig_load_creds = mod.load_creds
mod.load_creds = lambda p: ("http://127.0.0.1:9", "pk", "sk")
rc3 = mod.run("unused", 2700, state_path=neg_state)
mod.load_creds = orig_load_creds
mod.alert = mod.alert_real
s3 = mod.load_state(neg_state)
check("negative: alert fired on stale pipeline", len(alerts) == 1 and alerts[0][0] > 2700)
check("negative: skip exit (nothing reachable)", rc3 == 1)
check("negative: last_ok_at NOT advanced", s3.get("last_ok_at") == old)

sys.exit(1 if failures else 0)
