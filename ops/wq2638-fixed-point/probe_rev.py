"""wq#2638 acceptance probe: score any revision of masking.py on the fixed-point
property, in both adjacency directions.

    python3 ops/wq2638-fixed-point/probe_rev.py            # this tree's harness/
    python3 ops/wq2638-fixed-point/probe_rev.py <dir>      # a dir holding masking.py

To score an older revision, write it out first and pass the directory:

    git show <rev>:harness/masking.py > /tmp/<rev>/masking.py
    python3 ops/wq2638-fixed-point/probe_rev.py /tmp/<rev>

(`live_code` is imported from this tree either way; it is not part of the rules.)

Rows: FIRE = a live value must be masked AND alarm; QUIET = this module's own output
must be left alone with no hit; every row must also be a fixed point. The cases are
drawn from outside any one lane's fixtures — the two breaks wq#2350 named, the
composition cases, and three the ticket does not name: a live token glued BEHIND
masked output, a live body that happens to contain the literal, and a
letters-and-dashes value sitting behind an `sk-` prefix.

Scored 2026-09-25, wq#2638 second pass:
    cf32737   (pre-fix)                     8 bad of 14
    e681514   (first pass, bounded bodies)  4 bad of 14
    this fix  (match-level test only)       1 bad of 14
The one remaining is `ghp_MASKED<live token>`, which fails identically on all three —
a pre-existing word-boundary gap, filed as wq#2694 and asserted (not fixed) in
harness/tests/test_masking.py.

Every fixture is SPLIT at the boundary its rule reads, so this file's own source does
not match the rules it exercises (test_the_mask_suite_does_not_arm_its_own_tripwire).
"""
import os
import sys

HARNESS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..",
                       "harness")
sys.path.insert(0, HARNESS)
if len(sys.argv) > 1:
    sys.path.insert(0, sys.argv[1])
import masking  # noqa: E402

LIVE = "REALSECRETVALUE123456"
CF = "cfut_" + "v1.0-abcdefghijklmnopqrstuvwxyz0123456789"
GHP = "ghp_" + "A1b2C3d4E5f6G7h8J9k0A1b2C3d4E5f6G7h8"

CASES = [
    # (kind, name, text, the live value that must not survive a FIRE row)
    ("QUIET", "self output", "cfut_" + "MASKED", None),
    ("QUIET", "self output under a label", "PAT: github_pat_" + "MASKED", None),
    ("QUIET", "prose after the literal", "read back as cfut_" + "MASKED.", None),
    ("QUIET", "prose, two dots", "read back as cfut_" + "MASKED..", None),
    ("QUIET", "composed url-auth", "https://x:ghp_" + "MASKED", None),
    ("FIRE", "live glued in front (cloudflare)", "cfut_" + LIVE + "cfut_MASKED", LIVE),
    ("FIRE", "live glued in front (sk-key)",
     "sk-ant-" + LIVE + "7890" + "sk-ant-MASKED", LIVE),
    ("FIRE", "live glued in front (kv)", 'GITEA_TOKEN="' + LIVE + "cfut_MASKED", LIVE),
    ("FIRE", "live glued in front (url-auth)",
     "https://lumen:" + LIVE + "MASKED@host", LIVE),
    ("FIRE", "live glued behind (cloudflare)", "cfut_MASKED" + CF, CF[len("cfut_"):]),
    ("FIRE", "live glued behind (github)", "ghp_MASKED" + GHP, GHP[len("ghp_"):]),
    ("FIRE", "live body contains the literal",
     "cfut_" + "abcdef" + "MASKED" + "ghijklmnopqrstuv", "ghijklmnopqrstuv"),
    ("FIRE", "live body opens with the literal",
     "cfut_" + "MASKED" + "ghijklmnopqrstuv", "ghijklmnopqrstuv"),
    ("FIRE", "letters-and-dashes value behind an sk- prefix",
     "sk-ant-" + "LIVESECRETLETTERSXYZ-" + "sk-ant-MASKED", "LIVESECRETLETTERSXYZ"),
]

bad = 0
for kind, name, text, live in CASES:
    out, hits = masking.mask_text(text)
    twice = masking.mask_text(out)
    notes = []
    if kind == "FIRE" and not hits:
        notes.append("NO ALARM")
    if kind == "QUIET" and (hits or out != text):
        notes.append("RE-ARM")
    if live and live in out:
        notes.append("VALUE SURVIVES")
    if twice != (out, []):
        notes.append("NOT A FIXED POINT")
    bad += bool(notes)
    print(f"{'BAD ' if notes else 'ok  '} {name:44} {','.join(notes) or 'pass':22}"
          f" hits={hits} out={out!r}")
print(f"{bad} bad row(s) of {len(CASES)}")
sys.exit(1 if bad > 1 else 0)   # 1 known-bad row (wq#2694) is the current baseline
