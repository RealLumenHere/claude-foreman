"""wq#2913: the three mirrors mask the SAME characters of the same corpus.

masking.py is the tripwire that pages and files rotation tickets, token_engine_daemon
is the broker's detect-and-fail copy (it runs as `tokeneng`, off the harness import
path, so it cannot import the first), and ops/wq2316-mask-secrets-boundary/mask-secrets
is the pipe instinct 4 tells every agent to use. Between them they have drifted on
nearly every masking ticket — wq#2313, wq#2687, wq#2726 -> wq#2732, wq#2802, wq#2848 —
always silently, and always in the direction that misses a mask.

test_mask_foreign_prefix_matrix already runs the three over one defect CLASS. This runs
them over a corpus and compares the answer character by character, which is the only
comparison that can catch a drift nobody has thought to build a case for: a difference
of one masked character anywhere in the corpus fails, whatever rule produced it.

The comparison is on the output BYTES. A looser one was tried first — map each output
back to the set of original offsets it redacted, via difflib, so a copy that split the
same coverage into a different number of literals would still pass. difflib aligns on
characters, not on spans, so the letters of `MASKED` in one copy's output absorbed
unrelated body characters of the original and the oracle reported a one-offset
disagreement between two copies that had masked identically. An oracle that reports a
difference where there is none also hides one where there is: byte equality needs no
oracle at all, and the three copies keep the same prefixes by construction (wq#2913
gave the daemon's last three rules group 1, and the wrapper IS masking.py), so there is
no cosmetic difference left for a looser comparison to forgive.

test_the_corpus_pins_both_directions is what stops the comparison passing vacuously:
byte equality between three copies that all mask nothing is still equality. The
per-rule leak metric lives where it belongs, in test_mask_foreign_prefix_matrix and in
kb/reports/wq2868_tail_probe.py; here the direction only has to be non-vacuous.

Credential-shaped literals are assembled by concatenation (masking.py's tripwire rule):
no whole token shape appears in this source.
"""
import os
import subprocess
import sys

import pytest

HERE = os.path.dirname(__file__)
sys.path.insert(0, os.path.join(HERE, ".."))
sys.path.insert(0, os.path.join(HERE, "..", "..", "token-engine"))
import cf_hold  # noqa: E402,F401
import fworker  # noqa: E402,F401  (side effect: puts the host's fleet_exec_policy on sys.path)
import masking  # noqa: E402
import token_engine_daemon as ted  # noqa: E402

WRAPPER = os.path.join(HERE, "..", "..", "ops", "wq2316-mask-secrets-boundary",
                       "mask-secrets")

_GH = "gh" + "p_"
_GHPAT = "github" + "_pat_"
_CFUT = "cf" + "ut_"
_SK = "s" + "k-"
_XOX = "xo" + "xb-"
_AKIA = "AK" + "IA"

_ALNUM36 = "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8"
_UP16 = "Q7R8S9T0U1V2W3X4"
_SK24 = "abcdefghijklmnopqrstuvwx"
_PW = "s3cret-value-9xK"

# One line per shape the three copies have historically disagreed about, plus the
# false positives they must all leave alone. Glued seams are deliberate: they are where
# a stop-based design used to hand one rule's leftovers to another.
CORPUS = [
    # --- plain values, one rule each
    "remote " + _GH + _ALNUM36,
    "pat " + _GHPAT + _ALNUM36 + "abcd",
    "cf " + _CFUT + "abcdef12",
    "sk " + _SK + "ant-" + _SK24,
    "slack " + _XOX + "12345678901",
    "aws " + _AKIA + _UP16,
    # --- the glued seams (wq#2702/wq#2720/wq#2802/wq#2848/wq#2899)
    _GH + _ALNUM36 + _AKIA + _UP16 + _XOX + "12345678901",
    _GH + _ALNUM36 + _CFUT + "abcdef12",
    _SK + _CFUT + "abcdef12" + _SK24,
    _SK + _XOX + "12345678901" + _SK24,
    # --- the wq#2913 tail: a full foreign shape past the floor, then a bare tail
    _GH + _ALNUM36 + _AKIA + _UP16 + "abcdefgh",
    # --- values carrying this module's own output (wq#2638/wq#2694)
    _CFUT + "abcdef12" + _CFUT + masking.MASK,
    _GH + masking.MASK + _GH + _ALNUM36,
    # --- labelled values (wq#2297/wq#2806)
    "NTFY_PASS=" + "hunter2000",
    'MCP_PG_READER_PASSWORD="' + _PW + '"',
    "<password>" + _PW + "</password>",
    'api key: "' + _PW + '"',
    # --- false positives every copy must leave alone (wq#2292/wq#2400/wq#2632/wq#2806)
    "repo highs_solver / risk-assessment-20260921-rollout",
    "see notes_on_" + _XOX + "two english words",
    "        key = apply_key_allowlist(reg)",
    "LANGFUSE_PUBLIC_KEY=" + "abcdef0123456789",
    "first pass: ordinary prose",
    _GH + masking.MASK + ".",
]


def _wrapper(text):
    out = subprocess.run(["bash", WRAPPER], input=text, capture_output=True, text=True,
                         check=True)
    assert out.stderr == "", out.stderr
    return out.stdout


IMPLS = [
    ("masking", lambda t: masking.mask_text(t)[0]),
    ("daemon", ted.mask_rewrite),
    ("wrapper", _wrapper),
]


@pytest.mark.parametrize("line", CORPUS, ids=range(len(CORPUS)))
def test_every_mirror_masks_the_same_bytes(line):
    want, whose = None, None
    for name, impl in IMPLS:
        got = impl(line)
        if want is None:
            want, whose = got, name
            continue
        # Name the disagreement by OFFSET, never by the characters at it: what only one
        # copy masked is by definition what the other printed in the clear, and a
        # failure message is itself a stream the tripwire reads.
        if got != want:
            at = next((i for i, (a, b) in enumerate(zip(got, want)) if a != b),
                      min(len(got), len(want)))
            pytest.fail(f"{name} and {whose} diverge at output offset {at} "
                        f"(lengths {len(got)} vs {len(want)})")


# The lines above this index are built around a real shape; the rest are the false
# positives every copy must leave byte-identical.
_FIRST_FALSE_POSITIVE = CORPUS.index(
    "repo highs_solver / risk-assessment-20260921-rollout")


@pytest.mark.parametrize("index", range(len(CORPUS)))
def test_the_corpus_pins_both_directions(index):
    """A differential passes vacuously if every copy masks nothing, so pin the direction
    too — for all three copies, not just for masking.py, since a mirror that went blind
    in lockstep would satisfy the comparison above."""
    line = CORPUS[index]
    for name, impl in IMPLS:
        out = impl(line)
        if index < _FIRST_FALSE_POSITIVE:
            assert masking.MASK in out, f"{name}: nothing masked in a real shape"
            assert len(out) < len(line), f"{name}: nothing was actually removed"
        else:
            assert out == line, f"{name}: rewrote a false positive"


def test_the_corpus_holds_no_shape_this_files_own_read_would_page_on():
    """Every other test here feeds these lines to a masker; this one feeds the FILE to
    the tripwire. A credential-shaped literal in tracked source files a rotation ticket
    on its own (wq#2299), and the corpus above is built out of shapes chosen to match.
    """
    with open(__file__, errors="replace") as fh:
        _masked, records = masking.scan(fh.read())
    assert [r["label"] for r in records] == [], records
