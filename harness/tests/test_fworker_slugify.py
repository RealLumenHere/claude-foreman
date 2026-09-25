"""wq#2701: fworker.slugify never cuts a branch name the tripwire reads as a credential.

wq#2687's branch `feat/wq2687-<slug>-a1` carried `cfut` one hyphen into a word, so
every `git status` and run record paged it as a burned cloudflare token.
"""
import os
import re
import sys

HARNESS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HARNESS_DIR)
import fworker  # noqa: E402
import masking  # noqa: E402

WQ2687_TITLE = ("mask-secrets (bin, sed mirror): cfut_/kv-secret charset absorbs "
                "trailing punctuation, corrupting already-masked text")


def _old_slugify(text, maxlen=40):
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:maxlen] or "ticket"


def _branch(slug, attempt=1):
    return f"feat/wq2687-{slug}-a{attempt}"


def test_the_wq2687_title_used_to_cut_a_credential_shaped_branch():
    assert "cloudflare" in masking.mask_text(_branch(_old_slugify(WQ2687_TITLE)))[1]


def test_the_wq2687_title_now_cuts_a_silent_branch():
    slug = fworker.slugify(WQ2687_TITLE)
    for attempt in (1, 9, 99):
        assert masking.mask_text(_branch(slug, attempt))[1] == []
    assert slug.startswith("mask-secrets-bin-sed-mirror-")


def test_an_ordinary_title_slugs_exactly_as_before():
    for title in ("[rotation] burned credential 2 values set:d68eb822ead9 (kv-secret)",
                  "fix: mask report prefixes", "", "!!!"):
        assert fworker.slugify(title) == _old_slugify(title)
