"""wq#2760: sweep fleet text for DSN passwords already printed in the clear.

Uses harness/masking.py's url-auth rule (as widened by wq#2760) as the detector, so it
finds exactly what the mask now catches. Prints path:line, scheme, the value's length
and value_sha12 — never the value, which is the whole point (instinct 4).

    python3 ops/wq2760-dsn-mask/sweep_dsn_history.py [--context] [--all-rules] <root> [<root> ...]

`--all-rules` (wq#2806) sweeps with EVERY rule in masking.PATTERNS — the widened
kv-secret, xml-secret, the prefix rules — not just url-auth, so the config-file shapes
the wq#2754 rotation printed (`<password>`, `KEY: value`) are found the same way.
Hits carry the rule's label; `old` is reported for url-auth hits only.

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
# wq#2806: every rule in PATTERNS needs one of these substrings, so a line without any is
# skipped before the expensive rules run. Without it the --all-rules sweep of
# /home/lumen/logs (2.8 GB) had not finished after 40 minutes at 100% CPU.
PREFILTER = re.compile(r"(?i)secret|token|key|pass|salt|credential|://|gh[pours]_|github_pat_|cfut|sk-|xox[bpasr]-|AKIA")
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


def main(roots, show_context=False, all_rules=False):
    rules = list(masking.PATTERNS) if all_rules else [
        (label, rx, repl) for label, rx, repl in masking.PATTERNS if label == "url-auth"]
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
                    if not all_rules and ("://" not in line or "@" not in line):
                        continue
                    if all_rules and not PREFILTER.search(line):
                        continue
                    spans = set()   # two rules reading one value (the kv pair) is one hit
                    for label, rx, _repl in rules:
                      for m in masking._matches(rx, line):
                        if (m.start(), m.end()) in spans:
                            continue
                        spans.add((m.start(), m.end()))
                        value = m.group(0)[len(m.group(1)):]
                        if label == "url-auth":
                            old = any(o.start() == m.start() for o in OLD.finditer(line))
                            scheme = m.group(1).split("://", 1)[0]
                            tag = f"scheme={scheme} old={'yes' if old else 'no'}"
                        else:
                            tag = f"label={label} prefix={m.group(1)[-24:]!r}"
                        key = masking.value_sha12(value)
                        hit = f"{path}:{n} {tag} len={len(value)}"
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
    _all = "--all-rules" in sys.argv
    if _all:
        sys.argv.remove("--all-rules")
    args = sys.argv[1:]
    main([a for a in args if a != "--context"], show_context="--context" in args,
         all_rules=_all)
