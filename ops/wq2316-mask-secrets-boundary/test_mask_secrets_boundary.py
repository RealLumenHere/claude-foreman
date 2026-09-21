"""wq#2316: the bundled bin/mask-secrets masks real tokens and leaves plain words alone.

Runs the sed script as a subprocess, so these assert the shipped artefact's behaviour
rather than a Python re-implementation of it — the whole failure this ticket fixes was
that the sed copy drifted from harness/masking.py while both claimed to be in lockstep.

Credential-shaped literals are built by concatenation on purpose (masking.py:125-128):
a real-looking `ghp_` + 36 in tracked source is the exact class of text that filed half
the rotation backlog (wq#2299).
"""
import pathlib
import subprocess

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
    assert mask("CF cfut_abcdef123456") == "CF cfut_MASKED"
    assert mask("hook xoxb-12345678901") == "hook xoxb-MASKED"
    assert mask("id " + "AKIA" + "Q" * 16) == "id AKIAMASKED"


def test_kv_secret_rule_is_unchanged():
    """Left unanchored on purpose — it keys on an English word, so a left boundary
    would miss `githubToken: ...`. Carrying masking.py's shape-based label and tail
    lookahead across is wq#2313."""
    assert mask('API_TOKEN="abcdef1234567890"') == 'API_TOKEN="MASKED"'
    assert mask("password: hunter2hunter2") == "password: MASKED"


def test_two_tokens_on_one_line_both_mask():
    """The boundary char is consumed by the match, so the /g rescan must still find a
    second token separated from the first by a single delimiter."""
    line = "a=ghp_" + _GH_BODY + ",b=ghp_" + _GH_BODY
    assert mask(line) == "a=ghp_MASKED,b=ghp_MASKED"
