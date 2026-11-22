#!/usr/bin/env python3
"""wq#5304 probe (verify of wq#5262): run wq2265 install.sh's INSTALL path (not --verify)
with systemctl/install/ls stubbed on PATH, and record whether it would enable the timer.

The script under test is read from a pinned git rev, so the probe is re-runnable:
  python3 reports/wq5304_verify_wq5262_probe.py 1ba40722     # fixed: enables only on ON=1
  python3 reports/wq5304_verify_wq5262_probe.py 1ba40722~1   # pre-fix: enables always
Exit 0 iff the rev enables the timer ONLY when SIMPILL_WATCH_ON=1.
"""
import os
import subprocess
import sys
import tempfile

REV = sys.argv[1] if len(sys.argv) > 1 else "1ba40722"
script = subprocess.run(["git", "-C", "/home/lumen/foreman", "show",
                         REV + ":ops/wq2265-simpill-watch/install.sh"],
                        capture_output=True, text=True, check=True).stdout


def run(env_extra):
    with tempfile.TemporaryDirectory() as tmp:
        log = os.path.join(tmp, "calls.log")
        for name in ("systemctl", "install", "ls"):
            p = os.path.join(tmp, name)
            with open(p, "w") as fh:
                fh.write('#!/usr/bin/env bash\necho "' + name + ' $*" >> "$STUB_LOG"\n')
            os.chmod(p, 0o755)
        sp = os.path.join(tmp, "install.sh")
        with open(sp, "w") as fh:
            fh.write(script)
        env = dict(os.environ, PATH=tmp + os.pathsep + os.environ["PATH"], STUB_LOG=log)
        env.pop("SIMPILL_WATCH_ON", None)
        env.update(env_extra)
        r = subprocess.run(["bash", sp], env=env, capture_output=True, text=True, timeout=60)
        calls = open(log).read().splitlines() if os.path.exists(log) else []
        return r.returncode, calls


ok = True
for label, extra in (("default", {}), ("ON=0", {"SIMPILL_WATCH_ON": "0"}),
                     ("ON=yes", {"SIMPILL_WATCH_ON": "yes"}), ("ON=1", {"SIMPILL_WATCH_ON": "1"})):
    rc, calls = run(extra)
    enables = any(c.startswith("systemctl enable") for c in calls)
    want = label == "ON=1"
    ok = ok and enables == want and rc == 0
    print(f"{REV} {label}: rc={rc} enables={enables} want={want}")
sys.exit(0 if ok else 1)
