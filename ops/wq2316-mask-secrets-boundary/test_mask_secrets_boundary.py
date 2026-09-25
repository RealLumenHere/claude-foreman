"""wq#2316: the bundled bin/mask-secrets masks real tokens and leaves plain words alone.

Runs the sed script as a subprocess, so these assert the shipped artefact's behaviour
rather than a Python re-implementation of it — the whole failure this ticket fixes was
that the sed copy drifted from harness/masking.py while both claimed to be in lockstep.

Credential-shaped literals are built by concatenation on purpose (masking.py:125-128):
a real-looking `ghp_` + 36 in tracked source is the exact class of text that filed half
the rotation backlog (wq#2299).
"""
import importlib.util
import pathlib
import subprocess
import sys

import pytest

SCRIPT = pathlib.Path(__file__).with_name("mask-secrets")

# Bodies at the floors the rules actually carry: gh?_ is 36 base62, github_pat_ 82.
_GH_BODY = "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8"        # 36
_PAT_BODY = "11ABCDEFG0" + "abcdefghij" * 3               # 40, underscores legal too
_SK_BODY = "abcdefghijklmnopqrstuvwx"                      # 24 letters (wq#2300 shape)


def mask(text):
    """Pipe one line through the bundled script and return what came back."""
    out = subprocess.run(
        ["bash", str(SCRIPT)], input=text, capture_output=True, text=True, check=True
    )
    return out.stdout.rstrip("\n")


# ---- the harm this ticket fixes: plain words that merely CONTAIN a prefix ----

@pytest.mark.parametrize("line", [
    # wq#2294: the repo name that fired a pri-1 rotation alarm for a credential that
    # never existed, and whose masking blocked the first pass of the wq#2294 census.
    "repo highs_solver",
    "clone https://github.com/example/highs_solver.git",
    # `sk-` inside an ordinary hyphenated word, with a long enough tail to clear {16,}.
    "risk-assessment-20260921-rollout",
    # `cfut_` is a prefix too; inside a word it is not a credential.
    "reading mycfut_settings_override",
    # wq#2400: the same word, one separator over. This entry was drafted for this list
    # and dropped before landing because the boundary was spelled `[^A-Za-z0-9]`, which
    # reads `_` as a delimiter and so masked it — and the dropped draft went on to fire
    # the slack alarm from the run's snapshot, filing a rotate-today ticket for two
    # English words. `_` only ever continues an identifier, so it is now inside the
    # class in both copies of the house set.
    "see notes_on_xoxb-plainword",
    "reading my_cfut_settings_override",
    "log_sk-report-generator-2026",
])
def test_plain_words_are_untouched(line):
    assert mask(line) == line


def test_the_masked_output_does_not_re_arm(line="ghp_MASKED and sk-ant-MASKED"):
    """A masked artefact read again must not look like a fresh token.

    The old {6,} floor matched this tool's OWN output (`ghp_MASKED` is 6 legal body
    chars), so every re-read of a masked file re-fired the alarm. The {30,} floor and
    the boundary together end that loop.
    """
    assert mask(line) == line


# ---- real tokens still mask ----

def test_github_prefixes_mask():
    for prefix in ("ghp_", "gho_", "ghu_", "ghs_", "ghr_"):
        assert mask("token=" + prefix + _GH_BODY) == "token=" + prefix + "MASKED"


def test_github_pat_masks():
    # Deliberately not a `"pat": "<token>"` line: the kv-secret rule runs last and is
    # still unanchored here, so it would mask the already-masked value a second time
    # (`"pat": "MASKED"`) and hide which rule fired. That cascade is wq#2313's to fix.
    line = "url https://github_pat_" + _PAT_BODY + "@example.test/x"
    assert mask(line) == "url https://github_pat_MASKED@example.test/x"


def test_token_at_line_start_masks():
    """The `^` half of the boundary alternation — sed -E has no \\b to lean on."""
    assert mask("ghp_" + _GH_BODY) == "ghp_MASKED"


def test_sk_prefix_keeps_no_body_characters():
    """wq#2300: the greedy `sk-[a-zA-Z-]*` printed 8 characters of the secret.

    Group 1 ran as far as the {16,} body would allow, so a body opening with a long
    letter run was partly kept. Repeating WHOLE dash-terminated letter groups instead
    pins the prefix at `sk-ant-` and masks the entire body.
    """
    assert mask("auth sk-ant-" + _SK_BODY) == "auth sk-ant-MASKED"
    assert mask("auth sk-" + _SK_BODY) == "auth sk-MASKED"
    # `[a-zA-Z]+-` repeats WHOLE letter groups, so `v1-` is body, not prefix: the
    # kept prefix stops at `sk-or-`. masking.py's own comment calls this one
    # `sk-or-v1-`, but both copies compile to the same stop — lockstep holds, and
    # keeping less of the prefix can only ever keep less of the secret.
    assert mask("auth sk-or-v1-" + _SK_BODY) == "auth sk-or-MASKED"


def test_cloudflare_slack_aws_still_mask():
    # wq#2400: split at each rule's boundary, like the aws line below always was. Left
    # whole, these three lines armed the pri-1 alarm on anyone who READ this file — and
    # this is the file wq#2316's own run was editing when it filed wq#2400.
    assert mask("CF cfut_" + "abcdef123456") == "CF cfut_MASKED"
    assert mask("hook xoxb-" + "12345678901") == "hook xoxb-MASKED"
    assert mask("id " + "AKIA" + "Q" * 16) == "id AKIAMASKED"


def test_trailing_punctuation_after_masked_literal_survives():
    """wq#2687: cfut_ and kv-secret are the only two rules whose body charset
    includes `.`, so a greedy run starting on already-masked prose swallowed the
    sentence-final period. The sentinel (wq#2732) stops the run because the literal
    is not in the charset while the rules run, so the period is never reachable.

    Only the already-masked case is in scope. A `.` directly after a RAW value is
    genuinely part of the run — masking.py eats it too, and matching it is the
    lockstep this file exists to hold (test_matches_masking_py_exactly below)."""
    assert mask("reads back as cfut_MASKED.") == "reads back as cfut_MASKED."


def test_kv_secret_rule_is_unchanged():
    """Left unanchored on purpose — it keys on an English word, so a left boundary
    would miss `githubToken: ...`. Carrying masking.py's shape-based label and tail
    lookahead across is wq#2313."""
    # Split after the label, where this rule reads its boundary (masking.py's KV_LINE
    # convention) — same reason as wq#2400's splits above.
    assert mask('API_TOKEN="' + 'abcdef1234567890"') == 'API_TOKEN="MASKED"'
    assert mask("password: " + "hunter2hunter2") == "password: MASKED"


def test_two_tokens_on_one_line_both_mask():
    """The boundary char is consumed by the match, so the /g rescan must still find a
    second token separated from the first by a single delimiter."""
    line = "a=ghp_" + _GH_BODY + ",b=ghp_" + _GH_BODY
    assert mask(line) == "a=ghp_MASKED,b=ghp_MASKED"


# ---- wq#2697: a live value glued behind this tool's own output ----

# ids= on both lists: pytest's default id is the parameter itself, which would print
# the credential-shaped fixture into every failure summary this file ever produces.
_GLUED = pytest.mark.parametrize("live, masked", [
    ("ghp_" + _GH_BODY, "ghp_MASKED"),
    ("AKIA" + "Q" * 16, "AKIAMASKED"),
    ("xoxb-" + "12345678901", "xoxb-MASKED"),
    ("sk-ant-" + _SK_BODY, "sk-ant-MASKED"),
], ids=["github", "aws", "slack", "sk-key"])


@pytest.mark.parametrize("residue", ["ghp_MASKED", "MASKED"],
                         ids=["after-github", "after-bare"])
@_GLUED
def test_a_value_glued_behind_the_mask_literal_masks(residue, live, masked):
    """The char in front of the live value is the `D` of `MASKED`, an identifier char,
    so `(^|[^A-Za-z0-9_])` refused the position and the value came out in the clear.
    Port of masking.py's `_AFTER_MASK` (wq#2638, ISSUE-LOG row 788): directly after
    the literal is a boundary.
    """
    assert mask(residue + live) == residue + masked


@_GLUED
def test_cloudflare_residue_does_not_swallow_the_seam(live, masked):
    """Was the last divergence from masking.py: cfut's body class spans the literal,
    so the rule re-swallowed the seam and the live value behind it into one match.
    The sentinel closes it (wq#2732) — the literal is not in the charset while the
    rules run, so the cfut_ run dies under its floor and the value masks on its own,
    which is what `_body()`'s lookahead does."""
    assert mask("cfut_MASKED" + live) == "cfut_MASKED" + masked


@pytest.mark.parametrize("line", ["ghp_MASKEDghp_MASKED", "AKIAMASKEDMASKED x"])
def test_own_output_glued_head_to_tail_stays_untouched(line):
    """The widened boundary must not turn a repeated artefact into a fresh match."""
    assert mask(line) == line


# ---- wq#2726/wq#2732: a value whose LAST character is not alphanumeric ----

# The population wq#2687's alnum-tail bound was written around was bodies ending in
# alnum, and every fixture above is drawn from it — which is why 30 green tests shipped
# a leak (wq#2630: a probe that cannot fail confirms and never falsifies). These are
# the complement: cfut and kv-secret are the two rules whose charset holds `.`/`_`/`-`,
# so they are the two whose values can end in one.
@pytest.mark.parametrize("tail", [".", "_", "-"], ids=["dot", "under", "dash"])
def test_a_cloudflare_value_ending_in_punctuation_masks_whole(tail):
    """Forcing the body's last char to be alnum made POSIX leftmost-longest back off
    to the longest alnum-ending prefix, printing the real final character next to the
    mask (`cfut` + `MASKED_`, which loses the prefix underscore as well)."""
    assert mask("tok cfut_" + "abcd1efgh" + tail + " end") == "tok cfut_MASKED end"


@pytest.mark.parametrize("body", [
    "abcdefghijk",      # 12 with the tail: exactly the rule's floor
    "abcdefghijklmnop",  # comfortably over it
], ids=["at-floor", "over-floor"])
@pytest.mark.parametrize("tail", [".", "_", "-"], ids=["dot", "under", "dash"])
def test_a_kv_value_ending_in_punctuation_masks_whole(body, tail):
    """At the floor this was the worse half: no prefix of the run both cleared {11,}
    and ended in alnum, so the rule did not fire at all and the shortest value it is
    designed to catch went through bin/mask-secrets completely in the clear."""
    assert mask("TOKEN: " + body + tail + " end") == "TOKEN: MASKED end"


# ---- the oracle: harness/masking.py, which this script is a lockstep copy of ----

_MASKING = pathlib.Path(__file__).resolve().parents[2] / "harness" / "masking.py"


def _mask_text():
    """Load the canonical masker by path, so the difference this file reports is the
    sed script's and not an import-path accident. Skips when the bundle is installed
    away from the repo (install.sh copies only mask-secrets)."""
    if not _MASKING.exists():
        pytest.skip("harness/masking.py not alongside this bundle")
    # masking.py imports its sibling live_code, so harness/ has to be importable.
    if str(_MASKING.parent) not in sys.path:
        sys.path.insert(0, str(_MASKING.parent))
    spec = importlib.util.spec_from_file_location("_masking_oracle", _MASKING)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.mask_text


# Every shape the sed mirror claims to carry, INCLUDING the non-alnum tails the
# committed suite had no case for. The kv lines stay on `TOKEN:`/`password:`, where
# masking.py's shape-based label vocabulary and this copy's keyword list agree — the
# label half is the divergence wq#2313 owns and this test is not about it.
_LOCKSTEP = [
    "tok ghp_" + _GH_BODY + " end",
    "tok github_pat_" + _PAT_BODY + " end",
    "tok sk-ant-" + _SK_BODY + " end",
    "tok xoxb-" + "12345678901" + " end",
    "tok " + "AKIA" + "Q" * 16 + " end",
    "tok cfut_" + "abcdef123456" + " end",
    "tok cfut_" + "abcd1efgh." + " end",
    "tok cfut_" + "abcd1efgh_" + " end",
    "tok cfut_" + "abcd1efgh-" + " end",
    "TOKEN: " + "abcdefghijk_" + " end",
    "TOKEN: " + "abcdefghijklmnop." + " end",
    "password: " + "hunter2hunter2" + " end",
    # our own output, alone and with a live value glued to it
    "reads back as cfut_MASKED.",
    "ghp_MASKED" + "AKIA" + "Q" * 16,
    "cfut_MASKED" + "ghp_" + _GH_BODY,
    "ghp_MASKEDghp_MASKED",
    # the false positives both sides must refuse
    "highs_solver and notes_on_xoxb-two words",
    "my_cfut_settings is a filename",
]


@pytest.mark.parametrize("line", _LOCKSTEP, ids=range(len(_LOCKSTEP)))
def test_matches_masking_py_exactly(line):
    """The acceptance probe wq#2687 was missing. Its oracle is not derived from the
    sed rules, so it can and does falsify: run against the alnum-tail script this
    replaced, the punctuation-tail lines and all four glued-seam lines fail."""
    assert mask(line) == _mask_text()(line)[0]
