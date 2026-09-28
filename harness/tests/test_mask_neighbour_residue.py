"""wq#2944: a prefix rule's body running straight through a NEIGHBOUR's credential.

sk-key's body charset spans `_` and `-`, so `sk-` + `cfut_<body>` is one legal sk-key match
and one cloudflare match; cloudflare's charset does the same for a slack or github token.
wq#2926 read the old sequential `_floored_absorb` path, where the owning rule's alarm was
silenced by the own-output screen. After the union pass (wq#2913) the same shape is ONE burn
by design: `_redundant()` drops the container and the inner rule names the value
(test_mask_replay.py::test_wq3021_corpus_burned_nothing pins that a container over a foreign
whole credential files no second rotation). What has to stay true, in all three
implementations, is what this file pins: no live character of either value survives, something
was masked, and the neighbour's label alarms. The owning rule's label is deliberately NOT
asserted; asserting it is the double-burn regression.

Credential-shaped literals are assembled by concatenation at import, and the case ids
are `+`-joined, for the reasons test_mask_foreign_prefix_matrix.py gives (wq#2936).
"""
import os
import re
import subprocess
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))
sys.path.insert(0, os.path.join(HERE, "..", "..", "token-engine"))
import cf_hold  # noqa: E402,F401
import fworker  # noqa: E402,F401  (side effect: puts the host's fleet_exec_policy on sys.path)
import masking  # noqa: E402
import token_engine_daemon as ted  # noqa: E402

SED = os.path.join(HERE, "..", "..", "ops", "wq2316-mask-secrets-boundary", "mask-secrets")

_GH = "gh" + "p_"
_GHPAT = "github" + "_pat_"
_CFUT = "cf" + "ut_"
_SK = "s" + "k-"
_XOX = "xo" + "xb-"
_AKIA = "AK" + "IA"

# The gap filler is `z`/`Z`-free of the foreign bodies below (all `7`), so an assertion
# can tell whose characters survived.
# label, own prefix, its body charset, its floor
OWN = [
    ("github", _GH, "[A-Za-z0-9]", 30),
    ("cloudflare", _CFUT, "[A-Za-z0-9_.-]", 6),
    ("sk-key", _SK, "[A-Za-z0-9_-]", 16),
    ("slack", _XOX, "[A-Za-z0-9-]", 8),
]
# name, the neighbour's prefix, a body that reaches ITS floor so it really masks
FOREIGN = [
    ("cfut", _CFUT, "7" * 6),
    ("ghp", _GH, "7" * 30),
    ("ghpat", _GHPAT, "7" * 20),
    ("xoxb", _XOX, "7" * 8),
    ("akia", _AKIA, "7" * 16),
]
# The gap wq#2899's absorb has to cross. `none` is wq#2926's zero-length case; `tag` and
# `tag2` are the letters-then-dash live tags sk-key's own prefix group swallows.
GAPS = [("none", ""), ("tag", "ant-"), ("tag2", "or-v1-"), ("dash", "z-"),
        ("long", "z" * 18 + "-")]

_LONG = "7" * 40  # reaches every own rule's floor on its own
_WORD = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_")


def _spannable(charset, gap):
    """Every gap character legal in this rule's own body — otherwise the rule cannot
    reach the residue at all and has no business masking it (a `.` gap stops sk-key,
    a `-` gap stops github)."""
    return all(re.fullmatch(charset, c) for c in gap)


def _cases():
    out = []
    for label, pre, charset, floor in OWN:
        for fname, fpre, fbody in FOREIGN:
            if fpre.startswith(pre) or pre.startswith(fpre):
                continue  # own-label repeat: wq#2638's composing, not a foreign residue
            for gname, gap in GAPS:
                if not _spannable(charset, gap + fpre):
                    # The own rule's body charset must run through the neighbour's
                    # prefix or the two never overlap (github stops at `_`, slack at
                    # `_`, sk-key and cloudflare do not).
                    continue
                if len(gap) >= floor:
                    continue  # the gap alone reaches the floor: two independent masks
                # `_B` only lets the NEIGHBOUR start where the character in front of it
                # is not a word character, so anything else leaves no residue to absorb
                # and the case tests nothing (`ghp_` + `cfut_` masks neither, in all
                # three implementations alike).
                if (gap + pre)[-1] in _WORD:
                    continue
                out.append(pytest.param(label, pre, gap, fpre, _LONG,
                                        id=f"{label}+{fname}+gap{gname}"))
    return out


CASES = _cases()


def _sed(text):
    return subprocess.run(["bash", SED], input=text, capture_output=True, text=True,
                          check=True).stdout


IMPLS = [
    ("masking", lambda t: masking.mask_text(t)[0]),
    ("daemon", lambda t: ted.mask_rewrite(t)),
    ("sed", _sed),
]


def test_the_case_ids_read_silently():
    """wq#2936: a FAILED line prints the id, and a `-`-joined id is itself credential
    shaped. `+` is in no rule's body charset, so it ends every run at the part boundary."""
    ids = "\n".join(f"[{p.id}-{name}]" for p in CASES for name, _ in IMPLS)
    assert masking.mask_text(ids)[1] == []


def test_the_matrix_covers_the_contradicted_shape():
    """The two shapes wq#2926 reproduced are in CASES, so a regression cannot pass by
    the matrix having quietly skipped them."""
    ids = {p.id for p in CASES}
    assert "sk-key+cfut+gaptag" in ids      # sk-ant- in front of a cloudflare token
    assert "sk-key+cfut+gapnone" in ids     # the same drop with no tag at all
    assert "sk-key+xoxb+gapnone" in ids
    assert len(CASES) >= 10


@pytest.mark.parametrize("impl_name, impl", IMPLS, ids=[i[0] for i in IMPLS])
@pytest.mark.parametrize("label, pre, gap, fpre, fbody", CASES)
def test_no_live_character_survives_a_neighbours_credential_in_the_body(
        impl_name, impl, label, pre, gap, fpre, fbody):
    """Nothing of the neighbour's body survives, in any of the three implementations."""
    masked = impl("x " + pre + gap + fpre + fbody + " y")
    assert masking.MASK in masked, f"{impl_name}: {label} nothing masked at all"
    assert "7" not in masked, f"{impl_name}: {label} neighbour's body in the clear"
    # The neighbour's kept prefix (`<fixed prefix>MASKED`) may stand in front of the mask:
    # a fixed prefix is the table's declared NON-secret part. Only live characters count.


_FOREIGN_LABEL = {"cfut": "cloudflare", "xoxb": "slack", "ghp": "github",
                  "ghpat": "github", "akia": "aws"}


@pytest.mark.parametrize("label, pre, gap, fpre, fbody", CASES)
def test_the_neighbours_credential_still_alarms(label, pre, gap, fpre, fbody):
    """Only masking.py reports labels; the mirrors rewrite text only. The neighbour's
    label is the one that carries the rotation, and it must survive the container screen."""
    fname = next(n for n, p, _b in FOREIGN if p == fpre)
    hits = masking.mask_text("x " + pre + gap + fpre + fbody + " y")[1]
    assert _FOREIGN_LABEL[fname] in hits, hits


@pytest.mark.parametrize("label, pre, _charset, _floor", OWN, ids=[o[0] for o in OWN])
def test_a_rule_still_refuses_its_own_output(label, pre, _charset, _floor):
    """wq#2298's loop stays closed: a rule's own replacement read back is silent."""
    assert masking.mask_text(pre + masking.MASK)[1] == []
    assert masking.scan(pre + masking.MASK)[1] == []


@pytest.mark.parametrize("fname, fpre, _fbody", FOREIGN, ids=[f[0] for f in FOREIGN])
def test_url_auth_still_reads_a_foreign_prefix_as_its_own_output(fname, fpre, _fbody):
    """url-auth and kv-secret have arbitrary-text bodies, so a password another rule
    already masked leaves a tail of `<foreign prefix>MASKED` that IS wholly accounted
    for, and re-alarming on it is wq#2298's loop."""
    assert masking.mask_text("https://user:" + fpre + masking.MASK + "@host")[1] == []
    assert masking.mask_text("token: " + fpre + masking.MASK)[1] == []
