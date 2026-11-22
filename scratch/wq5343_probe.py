"""wq#5343 adversarial probe of wq#5336 (foreman bd1f8588).

Runs against the rev-pinned install.sh (git show <rev>:...) so the probe is re-runnable.
Usage: python3 scratch/wq5343_probe.py [foreman_rev]
"""
import importlib.util
import re
import subprocess
import sys

FOREMAN = "/home/lumen/foreman"
REV = sys.argv[1] if len(sys.argv) > 1 else "bd1f8588"
TEST = "ops/tests/test_wq5269_minerva_guard_intended_off.py"
INSTALL = "ops/wq3147-minerva-guard/install.sh"


def show(rev, path):
    return subprocess.run(["git", "-C", FOREMAN, "show", f"{rev}:{path}"],
                          check=True, capture_output=True, text=True).stdout


def load(src, name):
    spec = importlib.util.spec_from_loader(name, loader=None)
    mod = importlib.util.module_from_spec(spec)
    mod.__file__ = f"{FOREMAN}/{TEST}"
    exec(compile(src, mod.__file__, "exec"), mod.__dict__)
    return mod


new = load(show(REV, TEST), "t_new")
old_src = show(f"{REV}^", TEST)
text = show(REV, INSTALL)
U = new.UNIT
GATE = '  if [ "$INTENDED_ON" = 1 ]; then\n'
results = []


def case(name, got, want):
    ok = got == want
    results.append(ok)
    print(("PASS" if ok else "FAIL"), name, "got=", got, "want=", want)


# 0. Pre-fix static check on the pinned main install.sh: reproduce the reported red.
blk = text.split('= --verify ]; then', 1)[1].split("\nfi\n", 1)[0]
red = [fn for fn in ("venabled", "vactive", "vtimer_scheduled")
       if re.search(r"^\s*" + fn + r"\s+" + re.escape(U), blk, re.M)]
case("pre-fix regex is red on main (bug reproduced)", red,
     ["venabled", "vactive", "vtimer_scheduled"])
assert "venabled" in old_src  # old test really had the unscoped check

# 1. Unmodified bundle: gated asserts are excluded.
case("main bundle: no ungated asserts", new.ungated_timer_asserts(text), [])

# 2. Assert re-added after the gate's fi (ungated) -> flagged.
t = text.replace("  vdone\n", f"  vactive {U}\n  vdone\n", 1)
case("assert after gate fi flagged", new.ungated_timer_asserts(t), ["vactive"])

# 3. Assert inside a different conditional -> still flagged (only INTENDED_ON=1 exempt).
t = text.replace("  vdone\n", f'  if [ "$OTHER" = 1 ]; then\n    vtimer_scheduled {U}\n  fi\n  vdone\n', 1)
case("assert in non-INTENDED_ON if flagged", new.ungated_timer_asserts(t), ["vtimer_scheduled"])

# 4. Assert before the gate (top of block) -> flagged.
t = text.replace(GATE, f"  venabled {U}\n" + GATE, 1)
case("assert before gate flagged", new.ungated_timer_asserts(t), ["venabled"])

# 5. Nested if inside the gate: lazy match stops at the inner fi; a gated assert after it
#    is flagged (false positive, the safe direction).
t = text.replace(GATE, GATE + '    if true; then\n      :\n    fi\n', 1)
case("nested if in gate -> gated assert after inner fi flagged (safe FP)",
     new.ungated_timer_asserts(t), ["venabled", "vactive", "vtimer_scheduled"])

# 6. BLIND SPOT: an `else` branch of the INTENDED_ON gate runs exactly when the timer is
#    intended-off, yet the strip regex removes it with the gate -> NOT flagged statically.
t = text.replace(f"    vtimer_scheduled {U}\n  fi\n",
                 f"    vtimer_scheduled {U}\n  else\n    venabled {U}\n  fi\n", 1)
assert t != text
case("else-branch assert NOT flagged by static check (known blind spot)",
     new.ungated_timer_asserts(t), [])

# 6b. ...but the behavioural test of the same suite catches it (run_verify stubs disabled/inactive).
out = new.run_verify(t)
case("else-branch assert caught by run_verify MISS", f"not enabled: {U}" in out, True)

# 7. The shipped falsifiability test itself is real: the regex-strip, not a no-op.
case("stripped block differs from raw block",
     new.verify_block(text) != re.sub(r'^\s*if \[ "\$INTENDED_ON" = 1 \]; then\n.*?^\s*fi\n', "",
                                      new.verify_block(text), flags=re.M | re.S), True)

print(f"{sum(results)}/{len(results)} cases as expected")
sys.exit(0 if all(results) else 1)
