"""wq#3997 -- adopt a published RE-agent benchmark as Limpet's eval instrument.

Design: ``design/wq3997_limpet_re_benchmark.md``. The live-search pass
(``scratch/research/limpet_live_search_2026-10-14.md`` A1) surfaced three
published agentic-RE benchmarks -- CrackMeBench (arXiv 2605.10597),
AgentRE-Bench (agentre-bench.ai), and a survey (arXiv 2604.14317). Limpet had
no external eval instrument; this module is one, with AgentRE-Bench adopted as
the default source because its unit of evaluation -- an agent planning, driving
tools, and producing graded findings end to end -- is exactly what the
appliance ships (CrackMeBench scores single-binary software-license reversing,
further from the firmware/hardware emphasis).

The loader is benchmark-*agnostic* on purpose: it reads ``[[task]]`` tables
from ``benchmark.d/*.toml``, each tagged with the ``benchmark`` it is drawn
from and a ``target_class``. Which benchmark the appliance is measured against
is then a question of which drop-in files ship, not of code -- the same
one-schema-many-files posture as the target-device catalog (wq#3940). The
committed ``benchmark.d/agentre_fixture.toml`` is a small OFFLINE FIXTURE
modeling the AgentRE-Bench task schema, not the vendored published dataset:
fetching that needs network egress the build sandbox does not have
(``fleet-search web`` down fleet-wide, wq#2595), so it is a queued follow-up.

Limpet's agent core is still design-only (``design/wq3806_limpet_architecture.md``
Section 6; same note in ``critic.py``/``model_router.py``), so this module does
not invoke a model. ``run_suite`` takes the agent as an injected callable --
keeping the scoring pure and offline-testable instead of speculatively wiring a
model client that does not exist yet. With no agent core, the honest baseline
is zero: the CLI's default ``null`` agent answers nothing and the suite scores
0, which is itself the forcing function the ticket wants.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import time
import tomllib
from dataclasses import dataclass

SCHEMA = "limpet-bench/1"
BENCH_DIR = pathlib.Path(__file__).parent / "benchmark.d"

# Grading modes a task may declare. All normalize strings (strip + casefold)
# before comparing so a correct answer is not failed on whitespace or case.
GRADE_SUBSET = "subset"      # every expected key present and equal in the answer
GRADE_EXACT = "exact"        # answer dict equals the expected dict exactly
GRADE_CONTAINS = "contains"  # each expected value is a substring of the answer's
GRADE_MODES = (GRADE_SUBSET, GRADE_EXACT, GRADE_CONTAINS)


class BenchmarkError(ValueError):
    """A malformed suite file or task the loader refuses to accept."""


@dataclass(frozen=True)
class BenchTask:
    """One gradeable RE-agent task drawn from a published benchmark.

    ``expect`` is the ground-truth answer map the agent is graded against;
    ``prompt`` is what the agent is asked. ``benchmark`` names the source
    (e.g. ``agentre-bench``) so results can be reported per-benchmark, and
    ``target_class`` (firmware | binary | ...) lets the appliance report its
    score on the firmware/hardware slice separately.
    """

    id: str
    name: str
    benchmark: str
    target_class: str
    prompt: str
    expect: dict
    grade: str = GRADE_SUBSET
    weight: float = 1.0
    source_file: str = ""

    def digest(self) -> str:
        """Stable hash of the gradeable content, so an edited task re-runs and
        an unchanged one is skipped on a resumed run (macbench row_digest idiom)."""
        payload = json.dumps(
            {"id": self.id, "prompt": self.prompt, "expect": self.expect,
             "grade": self.grade, "weight": self.weight},
            sort_keys=True,
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _norm(value) -> str:
    return str(value).strip().casefold()


def load_suite(bench_dir: pathlib.Path = BENCH_DIR) -> list[BenchTask]:
    """Every ``[[task]]`` from every ``*.toml`` in ``bench_dir``, in filename order.

    A file's optional top-level ``[benchmark] name = ...`` supplies the default
    ``benchmark`` for its tasks; a task may still override it. Duplicate task
    ids across the whole suite are a bug, not a merge -- fail loud.
    """
    tasks: list[BenchTask] = []
    seen: dict[str, str] = {}
    for toml_path in sorted(pathlib.Path(bench_dir).glob("*.toml")):
        with toml_path.open("rb") as fh:
            data = tomllib.load(fh)
        default_bench = data.get("benchmark", {}).get("name", "")
        for entry in data.get("task", []):
            entry = dict(entry)
            entry.setdefault("benchmark", default_bench)
            try:
                task = BenchTask(source_file=toml_path.stem, **entry)
            except TypeError as exc:
                raise BenchmarkError(f"{toml_path.name}: malformed task: {exc}") from exc
            if task.grade not in GRADE_MODES:
                raise BenchmarkError(
                    f"{toml_path.name}: task {task.id!r} grade {task.grade!r} "
                    f"must be one of {GRADE_MODES}")
            if not isinstance(task.expect, dict) or not task.expect:
                raise BenchmarkError(
                    f"{toml_path.name}: task {task.id!r} must declare a non-empty [task.expect]")
            if task.id in seen:
                raise BenchmarkError(
                    f"task id {task.id!r} defined in both {seen[task.id]} and {toml_path.name}")
            seen[task.id] = toml_path.name
            tasks.append(task)
    return tasks


def grade_task(task: BenchTask, answer: dict | None) -> dict:
    """Grade one task's ``answer`` against its ground truth. No model call.

    Returns ``{"id", "benchmark", "target_class", "passed", "matched",
    "total", "missing", "wrong"}``. ``passed`` is true iff every expected
    answer is present and correct per the task's ``grade`` mode; ``matched``
    /``total`` expose partial progress for reporting but do not soften
    ``passed`` -- a benchmark task is credited only when fully solved.
    """
    answer = answer or {}
    if not isinstance(answer, dict):
        raise BenchmarkError(f"task {task.id!r}: agent answer must be an object, got {type(answer).__name__}")

    missing: list[str] = []
    wrong: dict[str, dict] = {}
    expect = task.expect

    if task.grade == GRADE_EXACT:
        extra = sorted(set(answer) - set(expect))
        for k in extra:
            wrong[k] = {"expected": None, "got": answer[k]}

    for key, want in expect.items():
        if key not in answer:
            missing.append(key)
            continue
        got = answer[key]
        if task.grade == GRADE_CONTAINS:
            ok = _norm(want) in _norm(got)
        else:  # subset / exact both require equality on the expected keys
            ok = _norm(want) == _norm(got)
        if not ok:
            wrong[key] = {"expected": want, "got": got}

    matched = len(expect) - len(missing) - len([k for k in wrong if k in expect])
    passed = not missing and not wrong
    return {
        "id": task.id, "benchmark": task.benchmark, "target_class": task.target_class,
        "passed": passed, "matched": matched, "total": len(expect),
        "missing": missing, "wrong": wrong,
    }


def _done_digests(prior: list[dict] | None) -> dict[str, str]:
    """Map task id -> digest for every ``task_done`` record in a prior run log."""
    done: dict[str, str] = {}
    for rec in prior or []:
        if rec.get("type") == "task_done" and rec.get("id"):
            done[rec["id"]] = rec.get("digest", "")
    return done


def run_suite(suite: list[BenchTask], agent, *, prior: list[dict] | None = None) -> dict:
    """Run ``agent`` over every task in ``suite`` and score it.

    ``agent`` is ``callable(task: BenchTask) -> dict`` returning the agent's
    answer map (an injected boundary -- Limpet's agent core is design-only).
    An exception from ``agent`` is caught and recorded as a ``task_error``
    (scored as unsolved), so one crashing task never aborts the suite -- same
    continue-on-failure posture as the macbench walk.

    Resumable: a task whose ``digest`` matches a ``task_done`` record in
    ``prior`` is skipped and its prior record carried forward, so a re-run only
    re-scores tasks that changed.

    Returns ``{"schema", "records": [...], "score", "passed_n", "total_n",
    "by_benchmark": {...}, "by_target_class": {...}}``. ``records`` is suitable
    for appending as JSON lines.
    """
    done = _done_digests(prior)
    prior_by_id = {r.get("id"): r for r in (prior or []) if r.get("type") == "task_done"}
    records: list[dict] = []

    for task in suite:
        if done.get(task.id) == task.digest():
            records.append(prior_by_id[task.id])
            continue
        base = {"schema": SCHEMA, "id": task.id, "benchmark": task.benchmark,
                "target_class": task.target_class, "digest": task.digest()}
        try:
            answer = agent(task)
            result = grade_task(task, answer)
        except Exception as exc:  # a crashing agent/answer scores as unsolved, not a suite abort
            records.append({**base, "type": "task_error", "passed": False,
                            "error": f"{type(exc).__name__}: {exc}"})
            continue
        records.append({**base, "type": "task_done", "passed": result["passed"],
                        "matched": result["matched"], "total": result["total"],
                        "missing": result["missing"], "wrong": result["wrong"]})

    return _aggregate(records)


def _aggregate(records: list[dict]) -> dict:
    scored = [r for r in records if r.get("type") in ("task_done", "task_error")]
    passed_n = sum(1 for r in scored if r.get("passed"))
    total_n = len(scored)

    def _slice(key: str) -> dict:
        out: dict[str, dict] = {}
        for r in scored:
            bucket = out.setdefault(r.get(key, ""), {"passed": 0, "total": 0})
            bucket["total"] += 1
            if r.get("passed"):
                bucket["passed"] += 1
        return out

    return {
        "schema": SCHEMA, "records": records,
        "score": round(passed_n / total_n, 4) if total_n else 0.0,
        "passed_n": passed_n, "total_n": total_n,
        "by_benchmark": _slice("benchmark"),
        "by_target_class": _slice("target_class"),
    }


def periodic(suite, agent, *, interval_seconds: int = 86400, iterations=None,
             sleep=time.sleep, on_result=None) -> int:
    """Re-run the suite every ``interval_seconds``, the registry-daemon shape
    (``daemon.py``): ``iterations`` bounds the loop for tests (None = forever)
    and ``sleep`` is injectable so tests don't wait. ``on_result`` (if given)
    is called with each run's aggregate -- the deploy wiring points it at an
    appender for the ``schema: limpet-bench/1`` JSON-lines results file. Each
    run reuses the previous run's records as ``prior`` so unchanged tasks are
    skipped. Returns the number of runs completed."""
    count = 0
    prior: list[dict] | None = None
    while iterations is None or count < iterations:
        agg = run_suite(suite, agent, prior=prior)
        prior = agg["records"]
        if on_result is not None:
            on_result(agg)
        count += 1
        if iterations is not None and count >= iterations:
            break
        sleep(interval_seconds)
    return count


# --- CLI ------------------------------------------------------------------
# Two built-in agents stand in until the agent core exists: `null` answers
# nothing (the honest baseline -- an appliance with no agent scores 0), and
# `oracle` returns each task's ground truth (a harness self-test -- proves the
# loader + fixture + scorer agree, scoring 1.0). A real agent is a library
# call into run_suite, not a CLI flag.

def _null_agent(task: BenchTask) -> dict:
    return {}


def _oracle_agent(task: BenchTask) -> dict:
    return dict(task.expect)


_AGENTS = {"null": _null_agent, "oracle": _oracle_agent}


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="limpet-bench",
                                description="LIMPET RE-agent benchmark eval instrument (wq#3997)")
    p.add_argument("--bench-dir", help="override benchmark.d suite dir")
    sub = p.add_subparsers(dest="command", required=True)

    st = sub.add_parser("tasks", help="list the loaded benchmark tasks")
    st.add_argument("--json", action="store_true", help="machine-readable output")

    sr = sub.add_parser("run", help="run the suite once against a built-in agent")
    sr.add_argument("--agent", choices=sorted(_AGENTS), default="null",
                    help="null (baseline, answers nothing) or oracle (harness self-test)")
    sr.add_argument("--results", help="append each record as a JSON line to this file")

    args = p.parse_args(argv)
    bench_dir = pathlib.Path(args.bench_dir) if args.bench_dir else BENCH_DIR
    try:
        suite = load_suite(bench_dir)
    except BenchmarkError as exc:
        print(f"error: {exc}")
        return 2

    if args.command == "tasks":
        rows = [{"id": t.id, "benchmark": t.benchmark, "target_class": t.target_class,
                 "name": t.name, "grade": t.grade} for t in suite]
        if args.json:
            print(json.dumps(rows, indent=2, sort_keys=True))
        else:
            for t in suite:
                print(f"{t.id:<16} [{t.benchmark}/{t.target_class}] {t.name}")
        return 0

    agg = run_suite(suite, _AGENTS[args.agent])
    if args.results:
        with open(args.results, "a", encoding="utf-8") as fh:
            for rec in agg["records"]:
                fh.write(json.dumps(rec, sort_keys=True) + "\n")
    summary = {k: v for k, v in agg.items() if k != "records"}
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    import sys

    raise SystemExit(main(sys.argv[1:]))
