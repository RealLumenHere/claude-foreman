"""wq#2030 verify-pass probe: PR#648's `_NEEDS_DECISION` regex matches bare
`frank` as a *substring*, with no word boundary, so any decision_reason that
merely contains the common English word "frankly" (or "Frankenstein",
"Frankfurt", etc.) is misclassified as `defer-needs-decision` -- the ONE label
that surfaces by default in Frank's review queue -- even when the reason is
squarely a theoretical/unproven defer that the ticket exists to filter OUT.

Run from repo root: python3 reports/2030_probe_needs_decision_frank_substring.py
"""
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..",
                                 "ops", "wq2024-label-defer-types"))
from defer_type_labels import classify_defer_type

# Each of these is an unambiguous defer-theoretical reason by the ticket's own
# phrasing ("paper-focused", "proof of concept only", "promising but unproven
# in production") -- it should NEVER classify as defer-needs-decision. The
# only reason it does is the literal substring "frank" inside an unrelated
# word.
CASES = [
    ("A paper-focused proof of concept only, frankly not production ready.",
     "defer-theoretical"),
    ("Frankenstein-style hack stitched from two papers, promising but "
     "unproven in production.", "defer-theoretical"),
    ("This is frankly too experimental and academic to ship as-is.",
     "defer-theoretical"),
]

if __name__ == "__main__":
    failures = 0
    for reason, expected_not in CASES:
        got = classify_defer_type(reason)
        flag = "BUG" if got == "defer-needs-decision" else "ok "
        if got == "defer-needs-decision":
            failures += 1
        print(f"{flag}  classify({reason!r}) -> {got!r} "
              f"(must not be 'defer-needs-decision'; ticket-shaped label is "
              f"{expected_not!r})")
    print()
    print(f"{failures}/{len(CASES)} cases misrouted into Frank's default queue")
