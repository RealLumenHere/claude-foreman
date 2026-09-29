#!/usr/bin/env python3
"""wq#3157: A/B probe over the wq#3153 sed-pattern guard in _KV_POINTER.

Two-sided by construction, so it can fail in either direction:

  BYPASS   a single-occurrence credential whose value merely OPENS with `.*#`
           must still fire kv-secret. Before wq#3157 every one of these was
           silent; that silence is what wq#3156 contradicted.
  SUPPRESS a genuine two-sided sed substitution (the pattern half, then the
           SAME label again on the replacement side) must stay suppressed.
           This half is what wq#3153 was right about — a fix that just reverts
           the guard fails here, so "always fire" is not a passing answer.

Every value is assembled at runtime and no literal in this file carries a
credential-shaped body (CLAUDE.md tripwire rule). Inputs are literals, not a
live file, so the probe is re-runnable from any checkout.

Run:  python3 ops/wq3157-sed-guard/probe_sed_guard.py
Exit: 0 all expectations met, 1 otherwise.
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "harness"))
import masking  # noqa: E402

BODY = "a1b2c3d4" * 3          # 24 opaque chars, well over both floors
LBL = "my_api_" + "token"
ENV = "API" + "_TOKEN"
DBP = "DB" + "_PASSWORD"
SKY = "SECRET" + "_KEY"
AUT = "AUTH" + "_TOKEN"

# (name, text, must_fire)
CASES = [
    # --- control: the same lines WITHOUT the 3-char prefix ------------------
    ("control-kv", LBL + ' = "' + BODY + '"', True),
    ("control-env", ENV + "=" + BODY, True),
    # --- BYPASS half: value merely opens with a wildcard + sed delimiter ----
    ("bypass-kv-hash", LBL + ' = ".*#' + BODY + '"', True),
    ("bypass-env-hash", ENV + "=.*#" + BODY, True),
    ("bypass-env-slash", DBP + "=.+/" + BODY, True),
    ("bypass-env-at", SKY + "=.*@" + BODY, True),
    ("bypass-env-colon", AUT + "=.*:" + BODY, True),
    # --- SUPPRESS half: genuine two-sided sed, the wq#3150 shapes ----------
    ("sed-hash", 'sed -i "s#^API_BEARER' + "_TOKEN=.*#API_BEARER"
     + '_TOKEN=$TOKEN#" "$REPO/.env"', False),
    ("sed-slash", "sed -i 's/^GITEA" + "_PASSWORD=.+/GITEA"
     + "_PASSWORD=${NEW}/' .env", False),
    # the anchor variant the `\^?` in the guard is for
    ("sed-anchored", "sed -i 's#API" + "_TOKEN=.*#^API" + "_TOKEN=$NEW#' .env", False),
]
# NOT probed, deliberately: `s|…|…|` and `s,…,…,`. `|` and `,` are outside _KV_BODY, so
# the body stops at the delimiter and the match dies on the floor whatever the guard
# says — a case that passes for a reason unrelated to the thing under test. The `|,`
# entries in the delimiter class are parity with wq#3154's lane and insurance against a
# future widening of _KV_BODY, and this comment is the record that they are inert today.


def main():
    bad = []
    for name, text, must_fire in CASES:
        hits = masking.mask_text(text)[1]
        fired = "kv-secret" in hits
        ok = fired is must_fire
        print(f"{'ok  ' if ok else 'FAIL'} {name:17s} fired={fired} want={must_fire} {hits}")
        if not ok:
            bad.append(name)

    # wq#2806's cost lesson: a guard that re-runs a grammar per word boundary is
    # how the kv rule got slow. This one is a bounded backreference, so a long
    # line with many labels must stay cheap.
    line = (LBL + " = " + BODY + " ") * 400
    t0 = time.perf_counter()
    masking.mask_text(line)
    ms = (time.perf_counter() - t0) * 1000
    print(f"cost {len(line)} chars -> {ms:.1f} ms")
    if ms > 2000:
        bad.append("cost")

    print("PASS" if not bad else "FAIL: " + ", ".join(bad))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
