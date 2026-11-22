#!/usr/bin/env python3
"""wq#5317 adversarial probe of wq#5275 (PR 2743, 148bf837): wq394-netdata-canary --verify.

Exits 0 only if every check below holds:
  1. live: liveness.ops_verified('wq394-netdata-canary') == SATISFIED (harness's own probe),
     AND the live units really are disabled+inactive, so the pass came from the OFF branch
     the PR added and matches the intended-off subtraction state (not a deleted check).
  2. drift detection kept: a scratch copy of the bundle with a mutated
     wq394-netdata-sampler.{service,timer} turns --verify into STILL_BLOCKED naming the
     live /etc/systemd/system path (vunit_file is still unconditional).
  3. the gate is real: forcing the timer gate / netdata gate open in a scratch copy makes
     --verify fail on the live box (payload drift + not enabled/active for the timer,
     not active for netdata). A gate that hid nothing could not fail here.
  4. the install arm refuses (rc=2, before any step) without WQ394_REENABLE=1, and with it
     proceeds past the refusal. curl/cp/sed/mkdir/systemctl are shimmed so nothing runs.
  5. the intended-off claim predates this PR: the pre-PR install.sh (148bf837^) already
     recorded the timer as intended-off by subtraction 1G (wq#4910, a different author).
"""
import os
import shutil
import subprocess
import sys

sys.path.insert(0, "/home/lumen/foreman/harness")
import liveness  # noqa: E402

BUNDLE = "wq394-netdata-canary"
SRC = "/home/lumen/foreman/ops"
SCRATCH = "/tmp/fworker-wq5317/ops"
TIMER = "wq394-netdata-sampler.timer"
TIMER_GATE = "if systemctl is-enabled --quiet wq394-netdata-sampler.timer 2>/dev/null; then"
ND_GATE = "if systemctl is-enabled --quiet netdata.service 2>/dev/null; then"
fails = []


def check(name, ok, detail):
    print(("PASS " if ok else "FAIL ") + name + ": " + str(detail))
    if not ok:
        fails.append(name)


def sysctl(verb, unit):
    p = subprocess.run(["systemctl", verb, unit], capture_output=True, text=True)
    return p.stdout.strip()


# 1. live
liveness.OPS_ROOT = SRC
st, msg = liveness.ops_verified(BUNDLE)
check("live --verify", st == liveness.SATISFIED and "VERIFY OK" in msg, (st, msg))
states = {u: (sysctl("is-enabled", u), sysctl("is-active", u))
          for u in (TIMER, "netdata.service")}
check("live units intended-off", all(e in ("disabled", "masked") and a == "inactive"
                                     for e, a in states.values()), states)


def fresh_copy():
    if os.path.isdir(SCRATCH):
        shutil.rmtree(SCRATCH)
    os.makedirs(SCRATCH)
    shutil.copy2(os.path.join(SRC, "_verify.sh"), SCRATCH)
    shutil.copytree(os.path.join(SRC, BUNDLE), os.path.join(SCRATCH, BUNDLE),
                    ignore=shutil.ignore_patterns("__pycache__"))
    liveness.OPS_ROOT = SCRATCH
    return os.path.join(SCRATCH, BUNDLE, "install.sh")


fresh_copy()
st, msg = liveness.ops_verified(BUNDLE)
check("scratch baseline", st == liveness.SATISFIED, (st, msg))

# 2. unit-file drift still caught while the units are off
for s in ("service", "timer"):
    fresh_copy()
    with open(os.path.join(SCRATCH, BUNDLE, "wq394-netdata-sampler." + s), "a") as f:
        f.write("\n# wq5317 drift probe\n")
    st, msg = liveness.ops_verified(BUNDLE)
    # ops_verified keeps only the last 200 chars, which can cut the /etc path; the
    # vfile MISS line ends "(from <bundle file>)", which survives the cut.
    check("drift unit ." + s, st == liveness.STILL_BLOCKED
          and "(from wq394-netdata-sampler." + s + ")" in msg, (st, msg))


def force_open(gate):
    inst = fresh_copy()
    body = open(inst).read()
    assert body.count(gate) == 1, "gate line not found exactly once: " + gate
    open(inst, "w").write(body.replace(gate, "if true; then"))


# 3. the gates hide real live state (they can fail)
force_open(TIMER_GATE)
st, msg = liveness.ops_verified(BUNDLE)
check("timer gate forced open fails", st == liveness.STILL_BLOCKED and TIMER in msg, (st, msg))
force_open(ND_GATE)
st, msg = liveness.ops_verified(BUNDLE)
check("netdata gate forced open fails", st == liveness.STILL_BLOCKED
      and "netdata.service" in msg, (st, msg))

# 4. install arm refuses without opt-in; shims make any regressed step inert
inst = fresh_copy()
shims = os.path.join(os.path.dirname(SCRATCH), "shims")
os.makedirs(shims, exist_ok=True)
for tool in ("curl", "cp", "sed", "mkdir", "chown", "chmod", "systemctl", "date"):
    p = os.path.join(shims, tool)
    with open(p, "w") as fh:
        fh.write("#!/bin/sh\necho \"shim: $0 refused\" >&2\nexit 97\n")
    os.chmod(p, 0o755)
env = {k: v for k, v in os.environ.items() if k != "WQ394_REENABLE"}
env["PATH"] = shims + os.pathsep + env.get("PATH", "")
r = subprocess.run(["bash", inst, "100.64.0.1"], capture_output=True, text=True, env=env,
                   timeout=60)
check("install refuses w/o opt-in", r.returncode == 2 and "WQ394_REENABLE=1" in r.stderr
      and "shim:" not in r.stderr, (r.returncode, r.stderr[-200:]))
env["WQ394_REENABLE"] = "1"
r = subprocess.run(["bash", inst, "100.64.0.1"], capture_output=True, text=True, env=env,
                   timeout=60)
check("install opt-in passes refusal", r.returncode == 97 and "intended-off" not in r.stderr,
      (r.returncode, r.stderr[-200:]))
shutil.rmtree(os.path.dirname(SCRATCH))
liveness.OPS_ROOT = SRC

# 5. intended-off predates the PR (wq#4910 comment in the parent revision)
old = subprocess.run(["git", "-C", "/home/lumen/foreman", "show",
                      "148bf837^:ops/wq394-netdata-canary/install.sh"],
                     capture_output=True, text=True).stdout
check("intended-off predates PR", "intended-off, disabled by the" in old
      and "subtraction (scratch/subtraction/REPORT.md 1G)" in old, "parent install.sh wq#4910")

print("RESULT:", "CONFIRMED" if not fails else "CONTRADICTED " + ",".join(fails))
sys.exit(1 if fails else 0)
