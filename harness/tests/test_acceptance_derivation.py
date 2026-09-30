"""wq#1705 — deterministic derivation must not mistake prose for a command.

A criterion is PINNED at dispatch and the worker cannot edit it, so a derivation
that turns an English sentence into a `cmd` hands the lane a gate nothing can
satisfy. wq#1705 was titled "make failed guard services self-heal or alert
instead of staying dead" and got exactly that: AC1 = `make failed guard services
self-heal or alert instead of staying dead`, against a repo with no Makefile.

Zero quota: `_derive_deterministic` takes no model call.
"""
import os
import re
import sys

HARNESS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HARNESS_DIR)
import acceptance  # noqa: E402

WQ1705_TITLE = "make failed guard services self-heal or alert instead of staying dead"


def _cmds(title, detail=""):
    return [c["cmd"] for c in acceptance._derive_deterministic(
        {"title": title, "detail": detail}) if c.get("proof_type") == "test"]


def test_the_wq1705_title_is_not_derived_as_a_make_target():
    assert _cmds(WQ1705_TITLE) == []


def test_a_prose_line_still_yields_the_citation_criterion():
    """Rejecting the bogus command must not leave the ticket ungated."""
    crit = acceptance._derive_deterministic({"title": WQ1705_TITLE, "detail": ""})
    assert [c["id"] for c in crit] == ["AC1"]
    assert crit[0]["proof_type"] == "citation"


def test_english_verb_openers_are_all_rejected_as_prose():
    for title in ("make the drift guard idempotent",
                  "go and fix the dispatcher stall",
                  "make sure the timer is enabled so it runs",
                  "npm is the wrong tool for this repo"):
        assert _cmds(title) == [], title


def test_a_real_bare_invocation_still_derives():
    assert _cmds("build fix\nmake test") == ["make test"]
    assert _cmds("build fix\npytest -q harness/tests") == ["pytest -q harness/tests"]
    assert _cmds("build fix\nmake lint test") == ["make lint test"]


def test_a_backticked_command_is_trusted_even_when_long():
    """An author who backticks a span is asserting it is a command; only the
    unquoted-line heuristic tightened."""
    detail = "run `pytest -q harness/tests/test_evidence_gate.py -k derivation -x`"
    assert _cmds("some title", detail) == [
        "pytest -q harness/tests/test_evidence_gate.py -k derivation -x"]


def test_wq3232_backticked_incomplete_python_dash_c_is_not_a_command():
    """wq#3231's ticket text used `python -c` inside backticks to name a debugging
    technique (a sitecustomize or a python -c wrapper setting a logger to DEBUG), not
    as a literal invocation -- -c always needs a trailing code argument. Deriving it
    as a test criterion mints an AC1 unsatisfiable by construction."""
    detail = ("consider a sitecustomize or a `python -c` wrapper that sets a logger "
               "to DEBUG for easier debugging")
    assert _cmds("some title", detail) == []
    ok, reason = acceptance.replayable("python -c")
    assert not ok
    assert "-c" in reason


def test_wq3235_dangling_flags_outside_the_c_e_m_set_are_rejected_too():
    """wq#3232 shipped a 3-item {-c,-e,-m} allowlist, so wq#3233 reproduced the same
    unsatisfiable-AC1 defect one flag over: `pytest -k` in prose derived AC1 with
    expect_exit 0, and `pytest -k` really exits 4 ("-k/--keyword expected one argument").
    Per wq#2290, this probe is drawn from OUTSIDE the population the first fix was built
    around -- it fails against that fix and passes only against a general check."""
    detail = ("the lane should have narrowed the run with a `pytest -k` selector instead "
              "of replaying the whole suite")
    assert _cmds("some title", detail) == []
    for cmd in ("pytest -k", "pytest --maxfail", "node -r", "mypy --config-file",
                "go -tags", "npm --prefix"):
        ok, reason = acceptance.replayable(cmd)
        assert not ok, cmd
        assert "dangling flag" in reason, cmd


def test_wq3235_boolean_trailing_flags_and_inline_values_still_replay():
    """The default-reject must not eat real invocations: a known-boolean trailing flag
    and a self-contained --flag=value are both complete commands."""
    for cmd in ("pytest -q harness/tests -x", "pytest --lf", "ruff check --fix",
                "pytest -q harness/tests --maxfail=1", "python3 -m pytest -q"):
        ok, reason = acceptance.replayable(cmd)
        assert ok, f"{cmd}: {reason}"
    assert _cmds("t", "run `pytest -q harness/tests -x`") == ["pytest -q harness/tests -x"]


def test_wq3235_the_dangling_flag_check_is_pre_pin_only():
    """An already-pinned criterion is replayed on its own exit code. Gating it here would
    newly reject a valid command ending in a boolean flag _ZERO_ARG_FLAGS omits, and a
    truly dangling flag fails the gate anyway -- with the tool's own error, not ours."""
    assert acceptance.replayable("ruff check --no-cache")[0] is False
    assert acceptance.replayable("ruff check --no-cache", pinned=True)[0] is True
    # the shape/security half of the check still applies to a pinned command
    assert acceptance.replayable("curl -k", pinned=True)[0] is False
    assert acceptance.replayable("pytest -q; rm -rf /", pinned=True)[0] is False


def test_wq3235_the_probe_input_really_is_outside_the_old_fixs_population():
    """The falsifiability guard itself (wq#2290): narrow the flag matcher back to wq#3232's
    shipped {-c,-e,-m} scope and the probe above must be ACCEPTED -- which is what makes it
    able to fail. A probe drawn from inside the old population would pass either way and
    prove nothing. Re-narrowing the check in future would break this test, on purpose."""
    saved = acceptance._FLAG_TOKEN_RE
    try:
        acceptance._FLAG_TOKEN_RE = re.compile(r"^-[cem]$")
        assert acceptance.replayable("pytest -k")[0] is True
        assert _cmds("t", "narrow the run with a `pytest -k` selector") == ["pytest -k"]
    finally:
        acceptance._FLAG_TOKEN_RE = saved
    assert acceptance.replayable("pytest -k")[0] is False


def test_looks_like_prose_boundaries():
    assert not acceptance._looks_like_prose(["make", "test"])
    assert not acceptance._looks_like_prose(["make", "a b c d e".replace(" ", "-")])
    assert acceptance._looks_like_prose(["make", "the", "thing"])
    assert acceptance._looks_like_prose("make one two three four five six".split())
