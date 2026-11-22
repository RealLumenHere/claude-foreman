#!/usr/bin/env python3
"""wq#5313 probe (verify of wq#5266): run wq2819 iptables-drift-guard install.sh's INSTALL
path (not --verify) with systemctl/install/cp/mkdir stubbed on PATH, and record whether it
would enable the timer. The flag is a file constant, so the ON=1 case rewrites it in the copy.

The script under test is read from a pinned git rev, so the probe is re-runnable:
  python3 reports/wq5313_verify_wq5266_probe.py 8cec2566     # fixed: enables only at ON=1
  python3 reports/wq5313_verify_wq5266_probe.py 8cec2566~1   # pre-fix: enables always -> exit 1
Exit 0 iff the rev enables the timer ONLY when IPTABLES_DRIFT_GUARD_ON=1, and an env override
(IPTABLES_DRIFT_GUARD_ON=1 in the environment) does NOT flip the shipped intent.
"""
import os
import re
import subprocess
import sys
import tempfile

REV = sys.argv[1] if len(sys.argv) > 1 else "8cec2566"
script = subprocess.run(["git", "-C", "/home/lumen/foreman", "show",
                         REV + ":ops/wq2819-iptables-drift-guard/install.sh"],
                        capture_output=True, text=True, check=True).stdout


def run(flag=None, env_extra=None):
    text = script
    if flag is not None:
        text, n = re.subn(r"(?m)^IPTABLES_DRIFT_GUARD_ON=\d$", "IPTABLES_DRIFT_GUARD_ON=%d" % flag, text)
        if n != 1:
            return 99, ["<no flag line in rev>"]
    with tempfile.TemporaryDirectory() as tmp:
        log = os.path.join(tmp, "calls.log")
        for name in ("systemctl", "install", "cp", "mkdir"):
            p = os.path.join(tmp, name)
            with open(p, "w") as fh:
                fh.write('#!/usr/bin/env bash\necho "' + name + ' $*" >> "$STUB_LOG"\n')
            os.chmod(p, 0o755)
        sp = os.path.join(tmp, "install.sh")
        with open(sp, "w") as fh:
            fh.write(text)
        env = dict(os.environ, PATH=tmp + os.pathsep + os.environ["PATH"], STUB_LOG=log)
        env.pop("IPTABLES_DRIFT_GUARD_ON", None)
        env.update(env_extra or {})
        r = subprocess.run(["bash", sp], env=env, capture_output=True, text=True, timeout=60)
        calls = open(log).read().splitlines() if os.path.exists(log) else []
        return r.returncode, calls


ok = True
for label, flag, extra, want in (("shipped", None, None, False),
                                 ("shipped+env ON=1", None, {"IPTABLES_DRIFT_GUARD_ON": "1"}, False),
                                 ("flag=1", 1, None, True)):
    rc, calls = run(flag, extra)
    enables = any(c.startswith("systemctl enable") for c in calls)
    reloads = any(c == "systemctl daemon-reload" for c in calls)
    good = rc == 0 and enables == want and reloads
    ok = ok and good
    print(f"{REV} {label}: rc={rc} enables={enables} want={want} daemon-reload={reloads} {'ok' if good else 'BAD'}")
sys.exit(0 if ok else 1)
