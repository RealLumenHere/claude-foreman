"""wq#2760: sweep fleet text for DSN passwords already printed in the clear.

Uses harness/masking.py's url-auth rule (as widened by wq#2760) as the detector, so it
finds exactly what the mask now catches. Prints path:line, scheme, the value's length
and value_sha12 — never the value, which is the whole point (instinct 4).

    python3 ops/wq2760-dsn-mask/sweep_dsn_history.py [--context] <root> [<root> ...]

`--context` adds every hit's line, masked by masking.mask_text and cut to 160
characters around the hit, so a hit can be dated and attributed without its value.

`old` marks a hit the pre-wq#2760 url-auth would also have caught: every hit is a
miss for bin/mask-secrets (no URL rule until now), `old=no` is a miss for masking.py
too. Values that are this masker's own output (`MASKED`) are not hits.
"""
import os
import re
import sys

HARNESS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "harness")
sys.path.insert(0, HARNESS)
import masking  # noqa: E402

URL_AUTH = next(rx for label, rx, _ in masking.PATTERNS if label == "url-auth")
# the rule as it stood before wq#2760: user {1,64}, body without `@`
OLD = re.compile(URL_AUTH.pattern.replace(r"]{0,64}:)", r"]{1,64}:)")
                 .replace(r"[^\s/'\"`]{8,}", r"[^\s@/'\"`]{8,}"))
assert OLD.pattern.count("{1,64}") == 1 and r"[^\s@/" in OLD.pattern, "old rule drifted"
SKIP_DIRS ={".git", "node_modules", "__pycache__", ".venv", "venv"}
MAX_BYTES = 64 << 20


def files(root):
    if os.path.isfile(root):
        yield root
        return
    for d, dirs, names in os.walk(root):
        dirs[:] = [x for x in dirs if x not in SKIP_DIRS]
        for n in names:
            yield os.path.join(d, n)


def context(line, start):
    """The line masked, then cut around the hit; masked FIRST so the cut can't split a
    value into a fragment the rule no longer recognises."""
    masked = masking.mask_text(line.rstrip("\n"))[0]
    head = masked[:60].replace("\n", " ")
    around = masked[max(0, start - 60):start + 100]
    return f"{head} ... {around}" if start > 60 else masked[:160]


def main(roots, show_context=False):
    seen = {}
    for root in roots:
        for path in files(root):
            try:
                if os.path.getsize(path) > MAX_BYTES:
                    continue
                fh = open(path, errors="replace")
            except OSError:
                continue
            with fh:
                for n, line in enumerate(fh, 1):
                    if "://" not in line or "@" not in line:
                        continue
                    for m in masking._matches(URL_AUTH, line):
                        value = m.group(0)[len(m.group(1)):]
                        old = any(o.start() == m.start() for o in OLD.finditer(line))
                        scheme = m.group(1).split("://", 1)[0]
                        key = masking.value_sha12(value)
                        hit = f"{path}:{n} scheme={scheme} len={len(value)} old={'yes' if old else 'no'}"
                        if show_context:
                            hit += "\n    " + context(line, m.start())
                        seen.setdefault(key, []).append(hit)
    for key, where in sorted(seen.items()):
        print(f"{key} hits={len(where)} first={where[0]}")
        if show_context:
            for w in where[1:]:
                print(f"  also={w}")
    print(f"distinct_values={len(seen)}")


if __name__ == "__main__":
    args = sys.argv[1:]
    main([a for a in args if a != "--context"], show_context="--context" in args)
