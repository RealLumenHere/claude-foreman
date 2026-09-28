#!/usr/bin/env python3
r"""wq#2945 probe: wq#2931's url-auth placeholder exclusion (harness/masking.py,
landed as 05e65ce, PR http://100.121.17.5:3000/lumen/claude-foreman/pulls/1245
as 79ddd88) is not scoped to "the whole userinfo body IS a placeholder" -- it is
scoped to "a `<name>`-shaped substring appears ANYWHERE before the first `@`".

The new lookahead is ``r"(?![^\s/'\"`@]*<[A-Za-z_][A-Za-z0-9_-]*>)"``. That class
has no anchor requiring the placeholder to consume the whole run up to `@`, so
a REAL secret glued directly (no separator) to any bracket-shaped annotation --
e.g. an app-added "<truncated>" marker, or a hand-redaction that left part of
the real value in place -- exempts the entire match. Before wq#2931 (parent
commit a361e80), the exclusion was anchored to the first character only
(``(?!\\*[${%<*.]|...)``) and did not have this hole: the same input masked.

`<`/`>` genuinely can't appear inside a real credential's OWN alphabet (true,
per the PR's own RFC 3986 citation) -- but nothing stops real credential chars
from appearing immediately BEFORE an unrelated `<...>` annotation on the same
line, and the fix's job was to skip pure placeholders, not any line containing
one.

Run: python3 reports/wq2945_probe_url_auth_placeholder_glued_secret.py
Today (wq#2931 as landed, 05e65ce): FAILS, exit 1 -- the secret-shaped body
leaks in the clear with zero mask, zero alarm.
Once fixed (exclusion scoped to "placeholder IS the whole body up to `@`"):
PASS, exit 0.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "harness"))
import masking  # noqa: E402

# Built at runtime, split at the prefix seam (instinct 4): not a real credential,
# but token-alphabet-shaped and 20 chars, well over the rule's 8-char body floor.
_PREFIX = "gh" + "p_"
REAL_SECRET = _PREFIX + "A1b2C3d4E5f6G7h8"

CASES = [
    # a truncation marker glued directly behind a real value, no separator
    "https://lumen:" + REAL_SECRET + "<truncated>@100.64.0.9",
    # a botched hand-redaction: real prefix left in place, tag glued on after it
    "remote https://x:" + REAL_SECRET + "<redacted-rest>@git.example",
]


def main():
    failures = []
    for line in CASES:
        masked, hits = masking.mask_text(line)
        leaked = REAL_SECRET in masked
        if leaked or "url-auth" not in hits:
            failures.append((line, hits, leaked))
    if failures:
        print(f"FAIL: {len(failures)}/{len(CASES)} real secrets survived unmasked:")
        for line, hits, leaked in failures:
            print(f"  line={line!r}")
            print(f"    hits={hits} leaked={leaked}")
        return 1
    print(f"PASS: all {len(CASES)} cases masked and recorded")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
