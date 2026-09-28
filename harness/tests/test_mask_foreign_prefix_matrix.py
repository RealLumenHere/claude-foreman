"""wq#2848 test-to-failure for the foreign-prefix class, across all three implementations.

Class: a rule's greedy body run is cut short (by another rule's prefix, or by its own
floor bookkeeping) BELOW that rule's own floor, so the whole `{N,}` match fails and a
real token passes through in the clear with no alarm.

Instance-only tests (one prefix, one offset) are what let wq#2720 ship this
regression, so this matrix covers EVERY rule x EVERY foreign prefix substring that
rule's own body charset can spell x offsets around the floor (0, 1, 3, 5, floor-len,
floor-1, floor, floor+1, end). It runs the same cases through masking.py, the
token-engine daemon mirror and the bin/mask-secrets sed mirror, so the three cannot
drift on this class again.

Assertion is strict: the fill character must not survive ANYWHERE in the output. The
output of a masked token is prefix + MASKED (a foreign substring may stay visible),
none of which contains the fill, so any surviving fill char is a leaked body char.

Credential-shaped literals are assembled at import by concatenation (masking.py:125-128
tripwire rule): no whole token shape appears in this source.
"""
import os
import string
import subprocess
import sys

import pytest

HERE = os.path.dirname(__file__)
sys.path.insert(0, os.path.join(HERE, ".."))
sys.path.insert(0, os.path.join(HERE, "..", "..", "token-engine"))
import cf_hold  # noqa: E402,F401
import fworker  # noqa: E402,F401  (side effect: puts the host's fleet_exec_policy on sys.path, as test_masking does)
import masking  # noqa: E402
import token_engine_daemon as ted  # noqa: E402

SED = os.path.join(HERE, "..", "..", "ops", "wq2316-mask-secrets-boundary", "mask-secrets")

_GH = "gh" + "p_"
_GHPAT = "github" + "_pat_"
_CFUT = "cf" + "ut"
_SK = "s" + "k-"
_XOX = "xo" + "xb-"
_AKIA = "AK" + "IA"

ALNUM = string.ascii_letters + string.digits
UPDIG = string.ascii_uppercase + string.digits

# label, token prefix, body charset, floor, exact-length?, fill char.
# sk-key fills with a DIGIT: its prefix is sk-(?:[a-zA-Z]+-)*, so a leading letters+hyphen
# run is kept visible by design (sk-ant-, sk-proj-) — identical before and after wq#2720,
# not part of this class.
RULES = [
    ("github", _GH, ALNUM, 30, False, "Q"),
    ("github_pat", _GHPAT, ALNUM + "_", 20, False, "Q"),
    ("cloudflare", _CFUT + "_", ALNUM + "_.-", 6, False, "Q"),
    ("sk-key", _SK, ALNUM + "_-", 16, False, "9"),
    ("slack", _XOX, ALNUM + "-", 8, False, "Q"),
    ("aws", _AKIA, UPDIG, 16, True, "Q"),
]
FOREIGN = [_AKIA, _CFUT, _CFUT + "_", _SK, _XOX, _GH, _GHPAT]

# Known residual, NOT part of this class: sk-key bodies that embed a cloudflare prefix leave
# a short tail (cloudflare runs first in PATTERNS). Identical on the pre-wq#2720 masker; tracked
# in its own ticket, so the sk-key cases here fill with a digit and skip that shape.


def _cases():
    out = []
    for label, pre, cs, floor, exact, fill in RULES:
        blen = floor if exact else floor + 12
        for f in FOREIGN:
            if not all(c in cs for c in f):
                continue
            hi = blen - len(f)
            offs = {0, 1, 3, 5, floor - len(f), floor - 1, floor, floor + 1, hi}
            for off in sorted(o for o in offs if 0 <= o <= hi):
                body = fill * off + f + fill * (hi - off)
                out.append(pytest.param(label, pre, body, fill, f, off,
                                        id=f"{label}-{f}-off{off}"))
    return out


def _sed(text):
    return subprocess.run(["bash", SED], input=text, capture_output=True, text=True,
                          check=True).stdout


IMPLS = [
    ("masking", lambda t: masking.mask_text(t)[0]),
    ("daemon", lambda t: ted.mask_rewrite(t)),
    ("sed", _sed),
]


@pytest.mark.parametrize("impl_name, impl", IMPLS, ids=[i[0] for i in IMPLS])
@pytest.mark.parametrize("label, pre, body, fill, f, off", _cases())
def test_a_foreign_prefix_substring_never_leaves_the_body_in_the_clear(
        impl_name, impl, label, pre, body, fill, f, off):
    masked = impl("x " + pre + body + " y")
    assert masking.MASK in masked, (
        f"{impl_name}: {label} token not masked at all (F={f!r} off={off})")
    assert fill not in masked, (
        f"{impl_name}: {label} body char survived unmasked (F={f!r} off={off})")
