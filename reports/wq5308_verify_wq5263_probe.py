#!/usr/bin/env python3
"""wq#5308 adversarial probe of wq#5263 (PR 2734, c8c61de4): wq2434-drift-plan --verify.

Exits 0 only if every check below holds:
  1. live: liveness.ops_verified('wq2434-drift-plan') == SATISFIED (harness's own probe).
  2. drift detection kept: a scratch copy of the bundle with a mutated drift_plan.py and
     a mutated lumen-drift-plan.timer each turn --verify into STILL_BLOCKED.
  3. the gate is real, not a deleted check: the scratch copy with DRIFT_PLAN_ON=1 fails
     on the (live, disabled/inactive) timer -> STILL_BLOCKED naming lumen-drift-plan.timer.
  4. the install path no longer enables the timer unless DRIFT_PLAN_ON=1.
  5. the intended-off claim is grounded: the 2026-10-08 subtraction list names it.
"""
import os
import re
import shutil
import sys

sys.path.insert(0, "/home/lumen/foreman/harness")
import liveness  # noqa: E402

BUNDLE = "wq2434-drift-plan"
SRC = "/home/lumen/foreman/ops"
SCRATCH = "/tmp/fworker-wq5308/ops"
fails = []


def check(name, ok, detail):
    print(("PASS " if ok else "FAIL ") + name + ": " + str(detail))
    if not ok:
        fails.append(name)


# 1. live
liveness.OPS_ROOT = SRC
st, msg = liveness.ops_verified(BUNDLE)
check("live --verify", st == liveness.SATISFIED, (st, msg))


def fresh_copy():
    if os.path.isdir(SCRATCH):
        shutil.rmtree(SCRATCH)
    os.makedirs(SCRATCH)
    shutil.copy2(os.path.join(SRC, "_verify.sh"), SCRATCH)
    shutil.copytree(os.path.join(SRC, BUNDLE), os.path.join(SCRATCH, BUNDLE))
    liveness.OPS_ROOT = SCRATCH


def scratch_verify():
    return liveness.ops_verified(BUNDLE)


# baseline of the scratch copy must match live
fresh_copy()
st, msg = scratch_verify()
check("scratch baseline", st == liveness.SATISFIED, (st, msg))

# 2. drift detection kept
for fname, target in (("drift_plan.py", "/home/lumen/minerva/drift_plan.py"),
                      ("lumen-drift-plan.timer", "/etc/systemd/system/lumen-drift-plan.timer")):
    fresh_copy()
    with open(os.path.join(SCRATCH, BUNDLE, fname), "a") as f:
        f.write("\n# wq5308 drift probe\n")
    st, msg = scratch_verify()
    check("drift " + fname, st == liveness.STILL_BLOCKED and target in msg, (st, msg))

# 3. flip the intent switch: the timer checks must come back and fail on the live box
fresh_copy()
inst = os.path.join(SCRATCH, BUNDLE, "install.sh")
body = open(inst).read()
assert body.count("\nDRIFT_PLAN_ON=0\n") == 1, "switch line not found exactly once"
open(inst, "w").write(body.replace("\nDRIFT_PLAN_ON=0\n", "\nDRIFT_PLAN_ON=1\n"))
st, msg = scratch_verify()
check("DRIFT_PLAN_ON=1 re-asserts timer", st == liveness.STILL_BLOCKED
      and "lumen-drift-plan.timer" in msg, (st, msg))
shutil.rmtree(SCRATCH)
liveness.OPS_ROOT = SRC

# 4. install path: the only `enable --now` sits under the DRIFT_PLAN_ON=1 branch
live = open(os.path.join(SRC, BUNDLE, "install.sh")).read()
m = re.search(r'if \[ "\$DRIFT_PLAN_ON" = 1 \]; then\n\s+sudo systemctl enable --now '
              r'lumen-drift-plan\.timer\n\s*else', live)
# count executable lines only: the wq#5263 comment block (install.sh:25) names the command
code = [ln for ln in live.splitlines() if not ln.lstrip().startswith("#")]
n = sum("enable --now" in ln for ln in code)
check("enable gated", bool(m) and n == 1, "code enable --now count=%d gated=%s" % (n, bool(m)))

# 5. subtraction list grounds the intended-off claim (cited as line 56)
lines = open("/home/lumen/scratch/subtraction/timers-disable-list.txt").read().splitlines()
check("subtraction list:56", len(lines) >= 56 and lines[55].strip() == "lumen-drift-plan",
      lines[55] if len(lines) >= 56 else "short file")

print("RESULT:", "CONFIRMED" if not fails else "CONTRADICTED " + ",".join(fails))
sys.exit(1 if fails else 0)
