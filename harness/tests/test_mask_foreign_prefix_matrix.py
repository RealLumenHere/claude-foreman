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
RULES = [
    ("github", _GH, ALNUM, 30, False, "Q"),
    ("github_pat", _GHPAT, ALNUM + "_", 20, False, "Q"),
    ("cloudflare", _CFUT + "_", ALNUM + "_.-", 6, False, "Q"),
    # wq#2899: sk-key now ALSO runs with a LETTER fill (SK_LETTER_RULE below), on top
    # of the digit fill here — this class truncates the run below sk-key's own floor
    # regardless of what the fill character is, since the foreign rule's already-masked
    # residue is what stops it either way (cloudflare and slack both run around sk-key
    # in PATTERNS, one on each side, so both glue directions apply). wq#2899 closed it
    # with a per-rule absorb alternative; wq#2913 closed it structurally instead — with
    # every rule reading the ORIGINAL text there is no already-masked residue inside a
    # body for anything to stop at. The digit fill stays too: it isolates this class
    # from the letter-only prefix-continuation shape SK_LETTER_RULE also covers.
    ("sk-key", _SK, ALNUM + "_-", 16, False, "9"),
    ("slack", _XOX, ALNUM + "-", 8, False, "Q"),
    ("aws", _AKIA, UPDIG, 16, True, "Q"),
]
FOREIGN = [_AKIA, _CFUT, _CFUT + "_", _SK, _XOX, _GH, _GHPAT]

# wq#2899: a second fill for sk-key ALONE, letter-led instead of digit-led — its
# prefix (sk-(?:[a-zA-Z]+-)*) is the one shape in RULES that can read a fill
# character as part of its OWN prefix group rather than its body, which is a
# distinct way to reach the same "run truncated below the floor" class the digit
# fill above cannot exercise.
SK_LETTER_RULE = ("sk-key", _SK, ALNUM + "_-", 16, False, "z")

# wq#2924: the letter fill runs against sk-key's OWN `sk-` too. It used to be
# excluded: under the old open `sk-(?:[a-zA-Z]+-)*` prefix, `sk-` + letters + `sk-`
# read as two whole qualifier units, so the letters went into the KEPT prefix and
# printed. The prefix is now one qualifier from a fixed vocabulary.


def _cases():
    out = []
    for label, pre, cs, floor, exact, fill in RULES + [SK_LETTER_RULE]:
        blen = floor if exact else floor + 12
        for f in FOREIGN:
            if not all(c in cs for c in f):
                continue
            hi = blen - len(f)
            offs = {0, 1, 3, 5, floor - len(f), floor - 1, floor, floor + 1, hi}
            idlabel = label if fill != "z" else label + "-letter"
            for off in sorted(o for o in offs if 0 <= o <= hi):
                body = fill * off + f + fill * (hi - off)
                out.append(pytest.param(label, pre, body, fill, f, off,
                                        id=f"{idlabel}+{f}+off{off}"))
    return out


def test_case_ids_read_silently():
    """wq#2936: a `-`-joined id (`sk-key-letter-` + a foreign prefix + `-off3-masking`)
    is itself credential-shaped, so every FAILED line that printed one paged the tripwire.
    `+` is in no rule's body charset, so it ends every run at the part boundary."""
    ids = "\n".join(f"[{p.id}-{name}]" for p in _cases() for name, _ in IMPLS)
    assert masking.mask_text(ids)[1] == []


def _sed(text):
    return subprocess.run(["bash", SED], input=text, capture_output=True, text=True,
                          check=True).stdout


IMPLS = [
    ("masking", lambda t: masking.mask_text(t)[0]),
    ("daemon", lambda t: ted.mask_rewrite(t)),
    ("sed", _sed),
]


_SK_QUALIFIERS = ["", "ant-", "or-", "proj-", "svcacct-", "admin-", "None-"]


@pytest.mark.parametrize("impl_name, impl", IMPLS, ids=[i[0] for i in IMPLS])
@pytest.mark.parametrize("qual", _SK_QUALIFIERS, ids=lambda q: q.rstrip("-") or "bare")
@pytest.mark.parametrize("gap", ["zz-", "zzzz" + _SK, "zzz-zz-", "zz" + _XOX])
def test_sk_key_keeps_only_a_known_qualifier(impl_name, impl, qual, gap):
    """wq#2924: a letters-then-dash run at the head of an sk- body is body, not prefix.

    Under the open `sk-(?:[a-zA-Z]+-)*` prefix every one of these gaps was read as a
    further qualifier and printed; a provider qualifier is still kept."""
    masked = impl("x " + _SK + qual + gap + "9" * 20 + " y")
    assert masked == "x " + _SK + qual + masking.MASK + " y", impl_name


@pytest.mark.parametrize("impl_name, impl", IMPLS, ids=[i[0] for i in IMPLS])
@pytest.mark.parametrize("label, pre, body, fill, f, off", _cases())
def test_a_foreign_prefix_substring_never_leaves_the_body_in_the_clear(
        impl_name, impl, label, pre, body, fill, f, off):
    masked = impl("x " + pre + body + " y")
    assert masking.MASK in masked, (
        f"{impl_name}: {label} token not masked at all (F={f!r} off={off})")
    assert fill not in masked, (
        f"{impl_name}: {label} body char survived unmasked (F={f!r} off={off})")
