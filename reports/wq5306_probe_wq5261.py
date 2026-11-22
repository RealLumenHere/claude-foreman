"""wq#5306 falsification probe for wq#5261 (e3750105).

Builds a copy of the bundle pinned at e3750105 in a temp dir, then:
  A) unmodified copy  -> expect VERIFY OK (exit 0)
  B) tampered .service source -> expect VERIFY FAIL (exit 1): unit-file check still bites
  C) tampered .timer source   -> expect VERIFY FAIL (exit 1)
Proves the verify still fails closed on drift after enabled/active were dropped.
"""
import os, subprocess, tempfile

REV = "e3750105"
REPO = "/home/lumen/foreman"
B = "ops/wq2243-remote-credential-sweep"
FILES = [f"{B}/install.sh", f"{B}/remote-credential-sweep.service",
         f"{B}/remote-credential-sweep.timer", "ops/_verify.sh"]


def build(root, tamper=None):
    for f in FILES:
        data = subprocess.run(["git", "-C", REPO, "show", f"{REV}:{f}"],
                              capture_output=True, check=True).stdout
        if tamper and f.endswith(tamper):
            data += b"\n# drift\n"
        p = os.path.join(root, f)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "wb") as fh:
            fh.write(data)
    return os.path.join(root, f"{B}/install.sh")


def run(tamper, want):
    with tempfile.TemporaryDirectory() as d:
        inst = build(d, tamper)
        r = subprocess.run(["bash", inst, "--verify"], capture_output=True, text=True)
        last = (r.stdout.strip().splitlines() or [""])[-1]
        ok = r.returncode == want
        print(f"{tamper or 'none':10} exit={r.returncode} want={want} {'PASS' if ok else 'FAIL'} :: {last}")
        return ok


res = [run(None, 0), run(".service", 1), run(".timer", 1)]
raise SystemExit(0 if all(res) else 1)
