"""wq#1705 — deterministic derivation must not mistake prose for a command.

A criterion is PINNED at dispatch and the worker cannot edit it, so a derivation
that turns an English sentence into a `cmd` hands the lane a gate nothing can
satisfy. wq#1705 was titled "make failed guard services self-heal or alert
instead of staying dead" and got exactly that: AC1 = `make failed guard services
self-heal or alert instead of staying dead`, against a repo with no Makefile.

Zero quota: `_derive_deterministic` takes no model call.
"""
import json
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


# The set wq#3235 actually shipped (PR#1500, merged d7003d3d80) -- one flat set for all 21
# allowlisted binaries. Kept verbatim so the guard below re-creates the real prior defect
# rather than a paraphrase of it.
WQ3235_FLAT_SET = {
    "-x", "-v", "-vv", "-vvv", "-q", "-qq", "-s", "-h", "-i", "-u", "-b", "-E", "-V",
    "--help", "--version", "--verbose", "--quiet", "--check", "--fix", "--list",
    "--exitfirst", "--last-failed", "--lf", "--ff", "--dry-run", "--no-header",
}


def _shared(flags):
    """Every binary keyed to one shared set -- wq#3235's shape, whatever the contents."""
    return {b: set(flags) for b in acceptance._ZERO_ARG_FLAGS_BY_BINARY}


def _flat_union():
    """The new per-binary table collapsed into a single cross-binary set: what would have
    happened had the fix widened the flat set instead of keying it."""
    flat = set()
    for flags in acceptance._ZERO_ARG_FLAGS_BY_BINARY.values():
        flat |= set(flags)
    return _shared(flat)


def test_wq3238_flag_arity_is_per_binary_not_universal():
    """wq#3237's finding: a flat cross-binary set gets arity wrong in BOTH directions.
    `-k` is boolean for make (`make -k` complains about missing targets, never about a
    missing -k argument) and argument-requiring for pytest ("argument -k: expected one
    argument", exit 4) -- one token, two arities, which one set cannot express."""
    assert acceptance.replayable("make -k")[0] is True
    assert acceptance.replayable("pytest -k")[0] is False
    # and the reverse pairing: pytest's -x is not a git flag at all
    assert acceptance.replayable("pytest -x")[0] is True
    assert acceptance.replayable("git log -x")[0] is False


def test_wq3238_git_dash_b_and_dash_u_are_dangling_for_git():
    """`git checkout -b` and `git branch -u` both exit 129 against real git -- -b wants a
    branch name, -u an upstream. wq#3235 listed both as universally boolean (they are, for
    python), so a command ending in either was accepted as a satisfiable criterion."""
    for cmd in ("git checkout -b", "git branch -u", "git commit -m"):
        ok, reason = acceptance.replayable(cmd)
        assert not ok, cmd
        assert "dangling flag for 'git'" in reason, (cmd, reason)
    # git query flags with no argument really are complete, and must still replay
    for cmd in ("git status -s", "git diff --stat", "git log --oneline",
                "git diff --exit-code"):
        ok, reason = acceptance.replayable(cmd)
        assert ok, f"{cmd}: {reason}"


def test_wq3238_the_model_derivation_path_drops_the_git_criterion():
    """The bug lived on derive()'s MODEL path, not the deterministic one:
    _derive_deterministic() drops every git candidate on its own `head == git` guard
    (acceptance.py, _derive_deterministic), but _validate_criteria() -> replayable() had
    no such guard, so a model-proposed git criterion was kept -- falsifying derive()'s
    docstring invariant that it is "never an unrunnable gate"."""
    proposed = [{"id": "AC1", "text": "branch is cut", "proof_type": "test",
                 "cmd": "git checkout -b", "expect_exit": 0},
                {"id": "AC2", "text": "suite passes", "proof_type": "test",
                 "cmd": "pytest -q harness/tests", "expect_exit": 0}]
    kept, rejected = acceptance._validate_criteria(proposed)
    assert [c["id"] for c in kept] == ["AC2"], kept
    assert any("AC1" in r and "dangling flag" in r for r in rejected), rejected
    doc = acceptance.derive({"id": 3238, "title": "t", "detail": "d"},
                            model_call=lambda _p: json.dumps({"criteria": proposed}))
    assert [c["cmd"] for c in doc["criteria"] if c["proof_type"] == "test"] == \
        ["pytest -q harness/tests"]


def test_wq3238_the_probes_really_can_fail_against_a_flat_set():
    """The wq#2290 falsifiability guard. wq#3235's regression suite probed 11 commands,
    none of them git and none testing a flag against a second binary, so it could not have
    caught this. Two re-creations, both of which must flip the probes above:

    (a) wq#3235's literal shipped set -- `git checkout -b` is accepted and survives
        _validate_criteria(), which is the defect as it actually merged;
    (b) this table collapsed into one cross-binary set -- `pytest -k` is accepted, because
        make's boolean -k leaks onto pytest, where -k demands an argument. (b) is what
        widening a flat set instead of keying it would have cost next."""
    saved = acceptance._ZERO_ARG_FLAGS_BY_BINARY
    collapsed = _flat_union()  # computed from the real table, before it is swapped out
    try:
        acceptance._ZERO_ARG_FLAGS_BY_BINARY = _shared(WQ3235_FLAT_SET)
        assert acceptance.replayable("git checkout -b")[0] is True
        assert acceptance.replayable("git branch -u")[0] is True
        assert acceptance._validate_criteria(
            [{"id": "AC1", "text": "t", "proof_type": "test",
              "cmd": "git checkout -b", "expect_exit": 0}])[0] != []
        acceptance._ZERO_ARG_FLAGS_BY_BINARY = collapsed
        assert acceptance.replayable("pytest -k")[0] is True
    finally:
        acceptance._ZERO_ARG_FLAGS_BY_BINARY = saved
    assert acceptance.replayable("git checkout -b")[0] is False
    assert acceptance.replayable("git branch -u")[0] is False
    assert acceptance.replayable("pytest -k")[0] is False


def test_wq3238_python_dash_m_arity_follows_the_module_not_the_interpreter():
    """`python3 -m pytest --lf` is parsed by pytest; keying arity on argv[0] would check
    --lf against python's table and reject a complete command."""
    assert acceptance._arity_binary(["python3", "-m", "pytest", "--lf"]) == "pytest"
    assert acceptance._arity_binary(["python3", "-m", "json.tool"]) == "python3"
    assert acceptance.replayable("python3 -m pytest --lf")[0] is True
    # ...and the interpreter's own trailing flags are still judged as python's
    assert acceptance.replayable("python3 -m")[0] is False
    assert acceptance.replayable("python3 -V")[0] is True
    # a bare trailing -i/-u/-b opens a REPL: complete, but it would hang out the replay
    # budget instead of passing, so it is deliberately not listed (see acceptance.py).
    for cmd in ("python3 -i", "python3 -u", "python3 -b"):
        assert acceptance.replayable(cmd)[0] is False, cmd


def test_wq3238_an_unknown_binary_defaults_to_reject():
    """Soundness does not depend on the table being complete: a binary with no entry
    rejects every trailing flag, so the candidate falls back to the citation criterion."""
    assert "tsc" in acceptance._ZERO_ARG_FLAGS_BY_BINARY
    assert acceptance.replayable("tsc --noEmit")[0] is True
    assert acceptance.replayable("tsc --project")[0] is False
    assert acceptance.replayable("./scripts/run.sh --only")[0] is False


def test_looks_like_prose_boundaries():
    assert not acceptance._looks_like_prose(["make", "test"])
    assert not acceptance._looks_like_prose(["make", "a b c d e".replace(" ", "-")])
    assert acceptance._looks_like_prose(["make", "the", "thing"])
    assert acceptance._looks_like_prose("make one two three four five six".split())
