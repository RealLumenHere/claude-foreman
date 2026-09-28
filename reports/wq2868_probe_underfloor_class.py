#!/usr/bin/env python3
"""wq#2868 judgment probe — independent adjudication of wq#2848's floor-fallback fix.

Deliberately drawn from OUTSIDE the population wq#2848's own matrix test covers
(wq#2630: a probe that cannot fail confirms nothing), namely:

  A  the INVARIANT the floor-fallback claims: for an open-floor rule, no foreign
     shape at any offset can stop the run below the rule's own floor, so the match
     can never fail outright.  Offsets swept continuously 0..floor+6, not the nine
     sampled offsets of test_mask_foreign_prefix_matrix.
  B  the SAME under-floor class reached through the OTHER guard `_body()` carries —
     the unconditional MASK-literal guard.  wq#2848 deferred the foreign guard past
     the floor but left this one active inside it, so a real body containing the
     literal under the floor still fails the whole rule.  Not wq#2848's scope; the
     point of the probe is to show whether the class is closed or only narrowed.
  C  kv-secret as host — the rule wq#2720 deliberately gave NO foreign stop.
  D  url-auth as host — the rule that takes no bounded body at all.
  E  a four-credential chain (github -> aws -> slack -> cloudflare glued with no
     separator), longer than the single-foreign-prefix cases the matrix covers, to
     test that _MAX_PASSES actually drains a chain of stops.
  F  the residual wq#2848 scoped out and filed as wq#2899 (rule order), quantified
     here at a `-`-preceded offset rather than avoided by a digit fill.

Prints counts and case ids only, never a body.  Credential-shaped literals are
assembled at runtime and split at the prefix seam, so no whole shape appears in
this source (masking.py's tripwire rule).
"""
import os
import string
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
# An argv path lets the same probe run against a historical masking.py extracted with
# `git show <rev>:harness/masking.py`, which is how the pre/post comparison in the
# judgment report was produced (the fix is only adjudicable against what it replaced).
# isdir, not just "argv has a word": the regression test imports this module under
# pytest, where argv[1] is a pytest flag.
_ARG = sys.argv[1] if len(sys.argv) > 1 and os.path.isdir(sys.argv[1]) else None
sys.path.insert(0, _ARG or os.path.join(HERE, "..", "harness"))
import masking  # noqa: E402

GH = "gh" + "p_"
GHPAT = "github" + "_pat_"
CFUT = "cf" + "ut_"
SK = "s" + "k-"
XOX = "xo" + "xb-"
AKIA = "AK" + "IA"

ALNUM = string.ascii_letters + string.digits
UPDIG = string.ascii_uppercase + string.digits

# label, prefix, body charset, floor, open-floor?, fill char (never in any prefix)
RULES = [
    ("github", GH, ALNUM, 30, True, "Q"),
    ("github_pat", GHPAT, ALNUM + "_", 20, True, "Q"),
    ("cloudflare", CFUT, ALNUM + "_.-", 6, True, "Q"),
    ("sk-key", SK, ALNUM + "_-", 16, True, "9"),
    ("slack", XOX, ALNUM + "-", 8, True, "Q"),
    ("aws", AKIA, UPDIG, 16, False, "Q"),
]
FOREIGN = [AKIA, CFUT, CFUT[:-1], SK, XOX, GH, GHPAT]


def leaked(text, fill):
    """(masked, leak?) — a surviving fill char is a leaked body char: the output of a
    masked token is prefix + MASKED, and no prefix and not the literal holds the fill."""
    masked, _ = masking.mask_text(text)
    return masked, fill in masked


def section_a():
    """The floor-fallback invariant, swept continuously."""
    fails, total = [], 0
    for label, pre, cs, floor, open_floor, fill in RULES:
        blen = floor + 14
        for f in FOREIGN:
            if not all(c in cs for c in f):
                continue
            for off in range(0, min(floor + 7, blen - len(f)) + 1):
                body = fill * off + f + fill * (blen - len(f) - off)
                total += 1
                masked, _ = masking.mask_text("x " + pre + body + " y")
                # The invariant under test is matchability, not full coverage: the
                # rule must FIRE.  Residue is section F's business.
                if masking.MASK not in masked:
                    fails.append(f"{label}/{f}/off{off}")
    return "A invariant(rule fires at all)", total, fails


def section_b():
    """The same under-floor class through the MASK-literal guard wq#2848 left inside
    the floor.  A real body holding the literal below the floor: does the rule fire?"""
    fails, total = [], 0
    for label, pre, cs, floor, open_floor, fill in RULES:
        if not all(c in cs for c in masking.MASK):
            continue
        blen = floor + 14
        for off in range(0, floor):
            body = fill * off + masking.MASK + fill * (blen - len(masking.MASK) - off)
            total += 1
            # The injected literal is in the input, so "did MASK appear" says nothing
            # here; a surviving fill char is the only usable signal.
            masked, hits = masking.mask_text("x " + pre + body + " y")
            if fill in masked:
                fails.append(f"{label}/off{off}/hits={hits or 'SILENT'}")
    return "B under-floor via MASK guard (leak = fail)", total, fails


def section_c():
    """kv-secret host, no foreign stop by design (wq#2720's own note)."""
    label = "api_" + "key"
    fails, total = [], 0
    for f in FOREIGN:
        for off in range(0, 14):
            body = "Q" * off + f + "Q" * (18 - len(f) - off)
            if off + len(f) > 18:
                continue
            total += 1
            masked, leak = leaked(label + ": " + body, "Q")
            if leak:
                fails.append(f"kv/{f}/off{off}")
    return "C kv-secret host (no foreign stop)", total, fails


def section_d():
    """url-auth host — unbounded body, screened by _OWN_TAIL instead."""
    fails, total = [], 0
    for f in FOREIGN:
        for off in range(0, 10):
            body = "Q" * off + f + "Q" * (20 - len(f) - off)
            total += 1
            masked, leak = leaked("https://u:" + body + "@h.example/p", "Q")
            if leak:
                fails.append(f"url/{f}/off{off}")
    return "D url-auth host", total, fails


def section_e():
    """A four-credential chain, no separators: every one must be masked and named."""
    # Fill chars are chosen to appear in NO prefix this module keeps, so a surviving
    # fill is unambiguously a body char and not the `AKIA`/`xoxb-` a mask legitimately
    # leaves visible (a first cut of this probe used 'A' and 'b' and read the kept
    # prefixes back as residue).
    chain = (GH + "Q" * 30 + AKIA + "7" * 16 + XOX + "z" * 8 + CFUT + "y" * 6)
    # Sentinels, not "x .. y": one of E's fill chars has to be a letter and the
    # wrapper's own letters would read back as residue (a first cut reported 'y').
    masked, hits = masking.mask_text("[[ " + chain + " ]]")
    fails = []
    for want in ("github", "aws", "slack", "cloudflare"):
        if want not in hits:
            fails.append(f"chain missing hit {want}")
    for ch in ("Q", "7", "z", "y"):
        if ch in masked.replace(masking.MASK, ""):
            fails.append(f"chain residue {ch!r}")
    return "E four-credential chain", 1, fails


def section_f():
    """wq#2899's scoped-out residual: a foreign prefix at a `-`-preceded offset in an
    sk-key body, where the foreign rule's own `_B` boundary IS satisfied in the raw
    text, so rule order decides who gets it first."""
    fails, total = [], 0
    for f in (CFUT, CFUT[:-1], XOX):
        for off in (4, 10, 16, 20):
            body = "9" * off + "-" + f + "9" * 10
            total += 1
            masked, leak = leaked("x " + SK + body + " y", "9")
            if leak:
                fails.append(f"sk/-{f}/off{off}")
    return "F rule-order residual (sk-key, dash-preceded)", total, fails


def main():
    print("masking.py", masking.MASK, "rules", len(masking.PATTERNS),
          "max_passes", getattr(masking, "_MAX_PASSES", "n/a"))
    bad = 0
    for fn in (section_a, section_b, section_c, section_d, section_e, section_f):
        name, total, fails = fn()
        print(f"{name}: {total} cases, {len(fails)} failing")
        for row in fails[:8]:
            print("   ", row)
        if len(fails) > 8:
            print(f"    ... and {len(fails) - 8} more")
        bad += len(fails)
    print("TOTAL FAILING", bad)
    return 0


if __name__ == "__main__":
    sys.exit(main())
