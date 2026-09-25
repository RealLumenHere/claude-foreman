#!/usr/bin/env python3
"""wq#2632 — what the kv-secret call test costs and what it buys.

The wq#2288 rule ended the value on `(?![A-Za-z0-9._-]|\\()`. wq#2322's verify-pass
found that this does not narrow the rule, it silently DROPS the match: the body run is
greedy and cannot give back a character into its own charset, so a value glued to a `(`
produces no mask, no label and no record at all. wq#2632 replaced it with a lookahead on
the VALUE's shape (`masking._KV_NOT_CALLED`) — a call is a segmented identifier, a
credential body is an undifferentiated run.

This script is the evidence that ruling rests on, kept runnable so the next pass can
re-derive it instead of taking the numbers on trust:

    python3 ops/wq2632-kv-paren/probe_paren.py harness dispatcher ops token-engine

It reports, for the shipped rule and for each variant:

  * corpus hits over every .py under the given roots — the precision budget,
  * the paren-adjacent matches the old `\\(` half suppressed, so the ruling can be
    checked against the real false-positive population rather than an invented one,
  * whether the trailing `(?![A-Za-z0-9._-])` half changes any verdict (it does not:
    it is inert against a greedy body, which is exactly why the `\\(` half next to it
    dropped whole matches).

Run 2026-09-25 over harness/ dispatcher/ ops/ token-engine/: shipped rule 36 corpus
hits, wq#2288 rule 36, no-call-test 53. Every one of the difference is
`<label> = <snake_case, dotted or camelCase identifier>(` and every one stays
suppressed, so requiring a separator or a hump costs no precision at all. The pre-change
corpus (485 files, before this ticket's own fixtures joined it) gave the same shape with
11 suppressions instead of 17.
"""
import os
import pathlib
import re
import sys

HARNESS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "harness")
sys.path.insert(0, HARNESS)
import masking  # noqa: E402

HEAD = r'\b((?:' + masking._KV_LABEL + r')\b["\s]*[:=]["\s]*)' + masking._KV_POINTER
BODY = r'[A-Za-z0-9._-]{12,}'

VARIANTS = {
    # what wq#2632 ships, rebuilt from the module's own parts so the probe cannot
    # drift from the rule it is measuring
    "shipped":       re.compile(HEAD + masking._KV_NOT_CALLED + BODY, re.IGNORECASE),
    "wq2288":        re.compile(HEAD + BODY + r'(?![A-Za-z0-9._-]|\()', re.IGNORECASE),
    "no_call_test":  re.compile(HEAD + BODY + r'(?![A-Za-z0-9._-])', re.IGNORECASE),
    # the shipped rule with the inert trailing half put back, to show it is inert
    "shipped_plus_charset": re.compile(
        HEAD + masking._KV_NOT_CALLED + BODY + r'(?![A-Za-z0-9._-])', re.IGNORECASE),
}


def sources(roots):
    for root in roots:
        for path in sorted(pathlib.Path(root).rglob("*.py")):
            if "__pycache__" not in str(path):
                yield path


def hits(rx, files):
    found = set()
    for path in files:
        text = path.read_text(encoding="utf-8", errors="replace")
        for n, line in enumerate(text.splitlines(), 1):
            for m in masking._matches(rx, line):
                found.add((str(path), n, m.group(0)))
    return found


def main(argv):
    roots = argv[1:] or ["harness", "dispatcher", "ops", "token-engine"]
    files = list(sources(roots))
    found = {name: hits(rx, files) for name, rx in VARIANTS.items()}

    print(f"files scanned: {len(files)}")
    for name in VARIANTS:
        print(f"  {name:22} {len(found[name])} hits")
    print("trailing charset half inert:",
          found["shipped"] == found["shipped_plus_charset"])

    suppressed = found["no_call_test"] - found["wq2288"]
    print(f"\nparen-adjacent matches the wq#2288 `\\(` half suppressed: {len(suppressed)}")
    for path, n, text in sorted(suppressed):
        kept = "STILL SUPPRESSED" if (path, n, text) not in found["shipped"] else "REGAINED"
        print(f"  {kept:17} {path}:{n}: {text[:90]}")

    gained = found["shipped"] - found["wq2288"]
    print(f"\nhits the narrowing adds over the corpus: {len(gained)}")
    for path, n, text in sorted(gained):
        print(f"  {path}:{n}: {text[:90]}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
