"""Tests for the reconciled stale-env-audit (wq#3215 docker/gate work rebased onto the
live d35e196 --aq build, wq#3245).

Unit tests exercise the pure comparison / compose-parsing / aq-planning logic directly
(fast, no root, no live units, no docker). The compose tests read a COMMITTED fixture
(fixtures/docker-compose.fixture.yml) rather than a live compose file, so the evidence is
re-runnable and cannot be changed under the test by another lane.

`test_transient_unit_control` is the test-to-failure: it drives a real
`systemd-run -p EnvironmentFile=...` transient unit through clean -> drift -> restart and
asserts --fail-on-drift's exit code tracks it. It needs root (to create a transient unit
and to read another uid's /proc), so it is skipped unless RUN_LIVE_SYSTEMD_TESTS=1.
"""
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import time

import pytest
import yaml

from importlib.machinery import SourceFileLoader

HERE = os.path.dirname(os.path.abspath(__file__))
BIN = os.path.join(HERE, "stale-env-audit")
FIXTURE = os.path.join(HERE, "fixtures", "docker-compose.fixture.yml")
loader = SourceFileLoader("stale_env_audit", BIN)
spec = importlib.util.spec_from_loader("stale_env_audit", loader)
sea = importlib.util.module_from_spec(spec)
loader.exec_module(sea)


def fixture_service(name):
    with open(FIXTURE) as f:
        return yaml.safe_load(f)["services"][name]


# --------------------------------------------------------------------------
# env file parsing / drift
# --------------------------------------------------------------------------

def test_parse_envfile_quotes_and_comments():
    with tempfile.NamedTemporaryFile("w", suffix=".env", delete=False) as f:
        f.write('# comment\nFOO=bar\nBAZ="quo ted"\n; also comment\nEMPTY=\n')
        path = f.name
    try:
        assert sea.parse_envfile(path) == {"FOO": "bar", "BAZ": "quo ted", "EMPTY": ""}
    finally:
        os.unlink(path)


def test_parse_envfile_compose_strips_inline_comment_systemd_does_not():
    """wq#3280: agent/.env had 'OLLAMA_MODEL=gemma4:latest  # was qwen3:8b' and the docker
    audit flagged litellm every hour. compose strips the trailing comment, systemd does not."""
    with tempfile.NamedTemporaryFile("w", suffix=".env", delete=False) as f:
        f.write('A=gemma4:latest  # was qwen3:8b\nB="keep # inside" # trailing\n'
                "C='x#y'\nD=nohash#attached\nE=\n")
        path = f.name
    try:
        assert sea.parse_envfile(path, compose=True) == {
            "A": "gemma4:latest", "B": "keep # inside", "C": "x#y", "D": "nohash#attached", "E": ""}
        assert sea.parse_envfile(path)["A"] == "gemma4:latest  # was qwen3:8b"
        _, _ = sea.expected_from([path], compose=True)
    finally:
        os.unlink(path)


def test_parse_envfile_missing_file_returns_none():
    assert sea.parse_envfile("/nonexistent/path.env") is None


def test_find_drift_differs_and_missing():
    proc_env = {"FOO": "old", "KEEP": "same"}
    expected = {"FOO": "new", "KEEP": "same", "GONE": "was-set", "IGNORED_EMPTY": ""}
    differs, missing = sea.find_drift(proc_env, expected)
    assert differs == ["FOO"]
    assert missing == ["GONE"]


def test_find_drift_clean():
    assert sea.find_drift({"FOO": "bar"}, {"FOO": "bar"}) == ([], [])


def test_expected_from_later_file_wins_and_records_source():
    files = []
    for body in ("SHARED=first\nONLY_A=a\n", "SHARED=second\n"):
        with tempfile.NamedTemporaryFile("w", suffix=".env", delete=False) as f:
            f.write(body)
            files.append(f.name)
    try:
        expected, src = sea.expected_from(files)
        assert expected == {"SHARED": "second", "ONLY_A": "a"}
        assert src["SHARED"] == files[1] and src["ONLY_A"] == files[0]
    finally:
        for p in files:
            os.unlink(p)


def test_newer_files_unknown_start_time_claims_nothing():
    # A container whose StartedAt docker would not report: "I could not look" must not be
    # reported as "the file is newer" either way (wq#2985).
    assert sea.newer_files({"K": FIXTURE}, ["K"], None) == []


def test_newer_files_detects_a_file_edited_after_start():
    assert sea.newer_files({"K": FIXTURE}, ["K"], 0) == ["docker-compose.fixture.yml"]


# --------------------------------------------------------------------------
# compose parsing (committed fixture)
# --------------------------------------------------------------------------

def test_compose_env_files_string_form_is_resolved_against_working_dir():
    assert sea._compose_env_files(fixture_service("envfile-string"), "/srv/proj") == ["/srv/proj/.env"]


def test_compose_env_files_list_form_keeps_absolute_paths_absolute():
    assert sea._compose_env_files(fixture_service("envfile-list"), "/srv/proj") == [
        "/srv/proj/.env", "/etc/absolute/extra.env"]


def test_compose_env_files_mapping_form():
    # compose spec 2.24+ allows `- path: ...` entries; the pre-wq3245 draft read these as
    # dicts and silently produced nothing.
    assert sea._compose_env_files(fixture_service("envfile-mappings"), "/srv/proj") == [
        "/srv/proj/relative/sub.env", "/etc/absolute/mapped.env"]


def test_compose_env_files_absent_is_empty_not_a_guessed_path():
    assert sea._compose_env_files(fixture_service("no-envfile"), "/srv/proj") == []


def test_compose_inline_names_mapping_and_list_forms():
    assert sea._compose_inline_names(fixture_service("envfile-string")) == {"INLINE_MAPPING_VAR"}
    assert sea._compose_inline_names(fixture_service("envfile-list")) == {
        "INLINE_LIST_VAR", "INLINE_LIST_PASSTHROUGH"}
    assert sea._compose_inline_names(fixture_service("envfile-mappings")) == set()


def test_parse_docker_time_tolerates_nanosecond_timestamps():
    # docker reports .State.StartedAt with 9 fractional digits, which fromisoformat rejects.
    assert sea.parse_docker_time("2026-09-30T12:00:00.123456789Z") == 1790769600
    assert sea.parse_docker_time("2026-09-30T12:00:00Z") == 1790769600


def test_parse_docker_time_unparseable_is_none_not_zero():
    # 0 would read as "started at the epoch", i.e. every env file is newer than the
    # container — a silent all-drift verdict. Unknown has to stay unknown.
    assert sea.parse_docker_time("0001-01-01T00:00:00Z") is not None
    assert sea.parse_docker_time("") is None
    assert sea.parse_docker_time("not-a-time") is None


# --------------------------------------------------------------------------
# --fail-on-drift
# --------------------------------------------------------------------------

def res_of(examined, drift=(), overrides=()):
    return {"examined": list(examined), "drift": list(drift), "overrides": list(overrides)}


def row(unit, differs=(), missing=(), inline=()):
    return {"kind": "systemd", "unit": unit, "differs": list(differs), "missing": list(missing),
            "file_newer_than_process": [], "also_set_inline": list(inline)}


def test_fail_on_drift_matches_name_and_variable():
    res = res_of(["messenger.service", "n8n-watcher.service"], drift=[
        row("messenger.service", differs=["LANGFUSE_SECRET_KEY"]),
        row("n8n-watcher.service", missing=["OTHER_KEY"]),
    ])
    bad, unexamined = sea.fail_on_drift(res, ["messenger.service"], ["LANGFUSE_SECRET_KEY"])
    assert [r["unit"] for r in bad] == ["messenger.service"] and unexamined == []

    bad, unexamined = sea.fail_on_drift(res, ["messenger.service"], ["UNRELATED_KEY"])
    assert bad == [] and unexamined == []


def test_fail_on_drift_reports_a_name_it_never_examined():
    # wq#3245: the pre-reconciliation binary answered exit 0 for a unit it had never
    # looked at, which is what made wq#3224's gate untrustworthy.
    bad, unexamined = sea.fail_on_drift(res_of(["messenger.service"]),
                                        ["messenger.service", "typo.service"], ["ANY"])
    assert bad == [] and unexamined == ["typo.service"]


def test_run_fail_on_drift_exit_codes():
    drifting = res_of(["a.service"], drift=[row("a.service", differs=["K"])])
    assert sea.run_fail_on_drift(drifting, ["a.service"], ["K"]) == 1
    assert sea.run_fail_on_drift(res_of(["a.service"]), ["a.service"], ["K"]) == 0
    assert sea.run_fail_on_drift(res_of([]), ["a.service"], ["K"]) == 2
    # drift on one name and no view of another: drift is the louder signal, exit 1
    assert sea.run_fail_on_drift(drifting, ["a.service", "b.service"], ["K"]) == 1


def test_fail_on_drift_still_fails_on_a_variable_pinned_inline():
    # Not fleet-alert drift (no restart can close it) but the rotation did not take, and the
    # operator running the rotation is the one person who can act on that.
    r = row("x.service", inline=["A"])
    res = res_of(["x.service"], overrides=[r])
    bad, _ = sea.fail_on_drift(res, ["x.service"], ["A"])
    assert bad == [r]
    assert sea.run_fail_on_drift(res, ["x.service"], ["A"]) == 1


# --------------------------------------------------------------------------
# inline overrides are not alertable drift (wq#3215 item 4, enforced in wq#3245)
# --------------------------------------------------------------------------

def test_split_inline_removes_pinned_variables_from_the_drift_sets():
    differs, missing, overridden = sea.split_inline(["PINNED", "REAL"], ["GONE"], {"PINNED"})
    assert differs == ["REAL"] and missing == ["GONE"] and overridden == ["PINNED"]


def test_split_inline_leaves_nothing_alertable_when_every_mismatch_is_pinned():
    differs, missing, overridden = sea.split_inline(["A"], ["B"], {"A", "B"})
    assert differs == [] and missing == [] and overridden == ["A", "B"]


def test_make_row_routes_a_fully_pinned_mismatch_to_overrides_shape():
    r = sea.make_row("docker", "c1", ["A"], [], {"A"}, {"A": FIXTURE}, None)
    assert r["differs"] == [] and r["missing"] == [] and r["also_set_inline"] == ["A"]


def test_compose_control_vars_cover_the_variable_that_alerted_live():
    # The first live --docker run opened aq rows for six dify containers on COMPOSE_PROFILES
    # alone; compose resolves it at `up` time, so no recreate can ever reconcile it.
    assert "COMPOSE_PROFILES" in sea.COMPOSE_CONTROL_VARS


# --------------------------------------------------------------------------
# alert queue planning (live d35e196 --aq semantics, preserved)
# --------------------------------------------------------------------------

def test_plan_aq_adds_drifting_and_resolves_a_unit_it_examined_and_found_clean():
    drift = [row("a.service", differs=["K"])]
    to_add, to_resolve = sea.plan_aq(drift, {"a.service": 1, "b.service": 2},
                                     ["a.service", "b.service"])
    assert to_add == drift and to_resolve == [(2, "b.service")]


def test_plan_aq_keeps_a_row_for_a_unit_it_could_not_examine():
    # Absence of evidence is not evidence of clean: a unit whose /proc was unreadable this
    # run keeps its row.
    to_add, to_resolve = sea.plan_aq([], {"b.service": 2}, examined=[])
    assert to_add == [] and to_resolve == []


def test_plan_aq_one_unreadable_unit_no_longer_blocks_every_resolution():
    # wq#3245: the d35e196 rule gated resolution on fleet-wide `unreadable == 0`, so one
    # short-lived unit kept 7 obsolete rows open. b.service was examined and is clean.
    to_add, to_resolve = sea.plan_aq([], {"b.service": 2}, examined=["b.service", "c.service"])
    assert to_resolve == [(2, "b.service")]


def test_plan_aq_resolves_a_unit_whose_only_mismatch_is_now_an_inline_override():
    # The row was opened before wq#3245 stopped counting pinned variables as drift; the unit
    # is examined and absent from `drift`, so the row closes on the next run.
    to_add, to_resolve = sea.plan_aq([], {"docker-plugin_daemon-1": 9},
                                     examined=["docker-plugin_daemon-1"])
    assert to_resolve == [(9, "docker-plugin_daemon-1")]


def test_fix_hint_covers_every_row_kind():
    assert set(sea.FIX_HINT) == {"systemd", "docker"}


# --------------------------------------------------------------------------
# CLI contract — the wq#3245 root cause
# --------------------------------------------------------------------------

def run_cli(*argv):
    return subprocess.run([sys.executable, BIN, *argv], capture_output=True, text=True)


def test_unknown_flag_is_a_hard_error():
    # The d35e196 build scanned argv by substring and ignored anything it did not know, so
    # a caller built against a newer contract got a silent whole-fleet exit 0.
    r = run_cli("--no-such-flag")
    assert r.returncode == 2 and "unrecognized arguments" in r.stderr


def test_flags_are_not_abbreviable():
    r = run_cli("--js")
    assert r.returncode == 2 and "unrecognized arguments" in r.stderr


def test_fail_on_drift_requires_vars():
    r = run_cli("--fail-on-drift", "a.service")
    assert r.returncode == 2 and "go together" in r.stderr


def test_vars_requires_fail_on_drift():
    r = run_cli("--vars", "K")
    assert r.returncode == 2 and "go together" in r.stderr


def test_fail_on_drift_refuses_to_also_rewrite_the_alert_queue():
    r = run_cli("--fail-on-drift", "a.service", "--vars", "K", "--aq")
    assert r.returncode == 2 and "scoped read" in r.stderr


def test_help_lists_every_documented_flag():
    r = run_cli("--help")
    assert r.returncode == 0
    for flag in ("--json", "--docker", "--aq", "--fail-on-drift", "--vars"):
        assert flag in r.stdout


def test_fail_on_drift_on_a_unit_that_does_not_exist_is_inconclusive_not_clean():
    """The wq#3228 repro, inverted. On the live d35e196 binary this exact argv printed
    '19 clean, 0 with drift' and exited 0 — a whole-fleet audit masquerading as a scoped
    confirmation. It must now refuse to vouch for a name it never examined."""
    r = run_cli("--fail-on-drift", "wq3245-nonexistent-unit.service", "--vars", "NOPE_VAR")
    assert r.returncode == 2, r.stdout + r.stderr
    assert "never examined" in r.stdout
    assert "wq3245-nonexistent-unit.service" in r.stdout


# --------------------------------------------------------------------------
# test to failure (needs root + live systemd)
# --------------------------------------------------------------------------

@pytest.mark.skipif(os.environ.get("RUN_LIVE_SYSTEMD_TESTS") != "1",
                    reason="needs root + live systemd; set RUN_LIVE_SYSTEMD_TESTS=1")
def test_transient_unit_control():
    envf = tempfile.NamedTemporaryFile("w", suffix=".env", delete=False)
    envf.write("WQ3245_PROBE=one\n")
    envf.close()
    os.chmod(envf.name, 0o644)
    unit = "wq3245-probe-%d" % int(time.time())
    gate = ["--fail-on-drift", "%s.service" % unit, "--vars", "WQ3245_PROBE"]
    try:
        subprocess.run(["systemd-run", "--unit=%s" % unit, "-p", "EnvironmentFile=%s" % envf.name,
                        "-p", "Restart=no", "/bin/sleep", "600"], check=True)
        time.sleep(1)
        assert run_cli(*gate).returncode == 0            # examined and clean

        with open(envf.name, "w") as f:
            f.write("WQ3245_PROBE=two\n")
        r1 = run_cli(*gate)
        assert r1.returncode == 1, r1.stdout             # the gate can actually fail
        assert "still drifting" in r1.stdout
        assert unit in json.dumps(json.loads(run_cli("--json").stdout)["drift"])

        subprocess.run(["systemctl", "restart", "%s.service" % unit], check=True)
        time.sleep(1)
        assert run_cli(*gate).returncode == 0            # and clears once the restart takes
    finally:
        subprocess.run(["systemctl", "stop", "%s.service" % unit], capture_output=True)
        os.unlink(envf.name)


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
