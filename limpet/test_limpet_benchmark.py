"""wq#3997 -- tests for the RE-agent benchmark eval instrument.

Covers: loading the committed AgentRE-Bench fixture suite, loader rejections,
grading in all three modes (crucially a FAILING grade, so the instrument is
shown to discriminate, not just confirm), the resumable runner, the periodic
loop, and the CLI baseline.

The pass/fail probe reads the real committed fixture (``benchmark.load_suite()``
against the in-tree ``benchmark.d/``) -- an immutable, rev-pinned input -- and
grades it with an agent drawn from OUTSIDE the scorer's happy path (a
wrong-answer agent), so the probe can fail and the "score 0" baseline is real
evidence, not a tautology (acceptance-probe doctrine, wq#2630).
"""
import json

import pytest

import benchmark as b


def _oracle(task):
    return dict(task.expect)


def _wrong(task):
    return {k: "DEFINITELY-NOT-THE-ANSWER" for k in task.expect}


def _write_suite(dir_path, body):
    dir_path.mkdir(parents=True, exist_ok=True)
    (dir_path / "s.toml").write_text(body, encoding="utf-8")
    return dir_path


# --- loader ---------------------------------------------------------------

def test_load_committed_fixture():
    suite = b.load_suite()
    ids = {t.id for t in suite}
    assert {"re-fw-soc-id", "re-fw-partition", "re-bin-license"} <= ids
    assert all(t.benchmark == "agentre-bench" for t in suite)  # default from [benchmark]
    assert {t.target_class for t in suite} == {"firmware", "binary"}
    # the fixture is firmware-leaning, matching the appliance emphasis
    assert sum(t.target_class == "firmware" for t in suite) >= 3


def test_loader_rejects_duplicate_id(tmp_path):
    body = (
        '[[task]]\nid="x"\nname="a"\nbenchmark="z"\ntarget_class="firmware"\n'
        'prompt="p"\nexpect={k="v"}\n'
        '[[task]]\nid="x"\nname="b"\nbenchmark="z"\ntarget_class="firmware"\n'
        'prompt="p"\nexpect={k="v"}\n'
    )
    with pytest.raises(b.BenchmarkError, match="defined in both"):
        b.load_suite(_write_suite(tmp_path, body))


def test_loader_rejects_bad_grade_and_empty_expect(tmp_path):
    bad_grade = ('[[task]]\nid="x"\nname="a"\nbenchmark="z"\ntarget_class="firmware"\n'
                 'prompt="p"\ngrade="fuzzy"\nexpect={k="v"}\n')
    with pytest.raises(b.BenchmarkError, match="grade"):
        b.load_suite(_write_suite(tmp_path / "g", bad_grade))
    empty = ('[[task]]\nid="x"\nname="a"\nbenchmark="z"\ntarget_class="firmware"\n'
             'prompt="p"\nexpect={}\n')
    with pytest.raises(b.BenchmarkError, match="non-empty"):
        b.load_suite(_write_suite(tmp_path / "e", empty))


# --- grading --------------------------------------------------------------

def test_grade_pass_and_fail_on_committed_fixture():
    """The falsifiable probe: the same real task graded right vs wrong."""
    task = next(t for t in b.load_suite() if t.id == "re-fw-soc-id")

    good = b.grade_task(task, _oracle(task))
    assert good["passed"] is True
    assert good["matched"] == good["total"] and not good["missing"] and not good["wrong"]

    bad = b.grade_task(task, _wrong(task))
    assert bad["passed"] is False            # probe CAN and DOES fail
    assert set(bad["wrong"]) == set(task.expect)
    assert bad["matched"] == 0

    missing = b.grade_task(task, None)        # agent answered nothing
    assert missing["passed"] is False
    assert set(missing["missing"]) == set(task.expect)


def test_grade_contains_mode():
    task = b.BenchTask(id="c", name="n", benchmark="z", target_class="binary",
                       prompt="p", expect={"u": "admin"}, grade=b.GRADE_CONTAINS)
    assert b.grade_task(task, {"u": "the admin account"})["passed"] is True
    assert b.grade_task(task, {"u": "root"})["passed"] is False


def test_grade_exact_mode_rejects_extra_key():
    task = b.BenchTask(id="x", name="n", benchmark="z", target_class="binary",
                       prompt="p", expect={"a": "1"}, grade=b.GRADE_EXACT)
    assert b.grade_task(task, {"a": "1"})["passed"] is True
    res = b.grade_task(task, {"a": "1", "b": "2"})
    assert res["passed"] is False and "b" in res["wrong"]


# --- runner ---------------------------------------------------------------

def test_run_suite_oracle_vs_null_baseline():
    suite = b.load_suite()
    full = b.run_suite(suite, _oracle)
    assert full["score"] == 1.0
    assert full["passed_n"] == full["total_n"] == len(suite)
    assert full["by_target_class"]["firmware"]["passed"] >= 3

    baseline = b.run_suite(suite, b._null_agent)
    assert baseline["score"] == 0.0           # no agent core -> honest 0
    assert baseline["passed_n"] == 0


def test_run_suite_records_agent_crash_as_error_and_continues():
    suite = b.load_suite()

    def crasher(task):
        if task.id == "re-fw-soc-id":
            raise RuntimeError("tool blew up")
        return _oracle(task)

    agg = b.run_suite(suite, crasher)
    errs = [r for r in agg["records"] if r["type"] == "task_error"]
    assert len(errs) == 1 and errs[0]["id"] == "re-fw-soc-id"
    assert errs[0]["passed"] is False
    assert agg["passed_n"] == len(suite) - 1   # every other task still scored


def test_run_suite_is_resumable_on_unchanged_digest():
    suite = b.load_suite()
    first = b.run_suite(suite, _oracle)

    called = []

    def tracking(task):
        called.append(task.id)
        return _oracle(task)

    second = b.run_suite(suite, tracking, prior=first["records"])
    assert called == []                        # every unchanged task skipped
    assert second["score"] == 1.0

    # a task whose digest changed re-runs; others stay skipped
    stale = [dict(r) for r in first["records"]]
    stale[0]["digest"] = "0" * 64
    b.run_suite(suite, tracking, prior=stale)
    assert called == [suite[0].id]


# --- periodic -------------------------------------------------------------

def test_periodic_bounds_iterations_and_injects_sleep():
    suite = b.load_suite()
    slept, results = [], []
    count = b.periodic(suite, _oracle, interval_seconds=3600, iterations=3,
                       sleep=slept.append, on_result=results.append)
    assert count == 3
    assert slept == [3600, 3600]               # final iteration does not sleep
    assert len(results) == 3 and all(r["score"] == 1.0 for r in results)


# --- CLI ------------------------------------------------------------------

def test_cli_tasks_json(capsys):
    assert b.main(["tasks", "--json"]) == 0
    rows = json.loads(capsys.readouterr().out)
    assert any(r["id"] == "re-fw-soc-id" for r in rows)


def test_cli_run_null_and_oracle_with_results_file(tmp_path, capsys):
    out = tmp_path / "results.jsonl"
    assert b.main(["run", "--agent", "oracle", "--results", str(out)]) == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary["score"] == 1.0 and summary["schema"] == b.SCHEMA
    lines = [json.loads(l) for l in out.read_text().splitlines()]
    assert lines and all(rec["schema"] == b.SCHEMA for rec in lines)

    assert b.main(["run", "--agent", "null"]) == 0
    assert json.loads(capsys.readouterr().out)["score"] == 0.0
