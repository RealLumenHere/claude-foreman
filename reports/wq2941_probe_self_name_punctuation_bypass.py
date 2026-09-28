#!/usr/bin/env python3
"""wq#2941 probe: wq#2933's kv-secret self-name guard (harness/masking.py
`_KV_NOT_SELF`) leaks real secrets behind a single ',' ';' or '?'.

Those three characters sit in the guard's trailing-punctuation class
`[.,:;!?]` but are NOT in `_KV_BODY`. At that gap the negative lookahead
`(?![A-Za-z0-9._\\-+/=:!#$%^*@~])` can be satisfied by backtracking the
punctuation match to zero width and reading the punctuation character
ITSELF as proof "the body ends here" -- without ever inspecting what
follows it. `.`, `:` and `!` don't have this hole because they are also
members of `_KV_BODY`, so the same zero-width backtrack lands on a body
character and the lookahead correctly fails.

Run: python3 reports/wq2941_probe_self_name_punctuation_bypass.py
Today (wq#2933 as shipped, PR #1244): FAILS, exit 1 -- every case below
leaks a real, unmasked, 32-char opaque secret with zero suppression record.
Once fixed: PASS, exit 0.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "harness"))
import masking  # noqa: E402

# 32 chars, no recognized fixed-prefix shape (not sk-/ghp_/AKIA/...) so only
# the kv-secret rule is in play -- isolates the self-name guard specifically.
# Split into runs each under 8 -- the ALL_CAPS env-identifier form of kv-secret
# (`_KV_ENV_LABEL`, which "REAL_SECRET" matches) floors at 8, not the general
# label's 12. Pre-existing, found red on
# test_the_mask_suite_does_not_arm_its_own_tripwire while gating wq#2922;
# concatenation keeps the runtime value byte-identical.
REAL_SECRET = "Zx8qLp" "3vWn7t" "Rk2mYb" "9cQe4s" "Hj6uFd" "1a"

CASES = [
    "API_KEY: API_KEY," + REAL_SECRET,
    "API_KEY: API_KEY;" + REAL_SECRET,
    "API_KEY: API_KEY?" + REAL_SECRET,
    "GITEA_PASSWORD=GITEA_PASSWORD;" + REAL_SECRET,
    "AUTH_TOKEN=AUTH_TOKEN," + REAL_SECRET,
    "DB_SECRET: DB_SECRET?" + REAL_SECRET,
]


def main():
    failures = []
    for line in CASES:
        masked, hits = masking.mask_text(line)
        leaked = REAL_SECRET in masked
        recorded = [r["suppressed"] for r in masking.suppressions(line)]
        if leaked or "kv-secret" not in hits:
            failures.append((line, hits, recorded, leaked))
    if failures:
        print(f"FAIL: {len(failures)}/{len(CASES)} real secrets survived unmasked:")
        for line, hits, recorded, leaked in failures:
            print(f"  line={line!r}")
            print(f"    hits={hits} suppressions={recorded} leaked={leaked}")
        return 1
    print(f"PASS: all {len(CASES)} cases masked and recorded")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
