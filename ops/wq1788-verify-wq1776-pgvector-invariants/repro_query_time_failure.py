#!/usr/bin/env python3
"""wq#1788 escalation repro — does check_chunk_doc_invariants.py honour §4.3?

Independent re-derivation of the wq#1785 finding, written without reference to
wq#1785's own transcript. Stubs `psycopg2` so no live Postgres (and no real
`/home/lumen/agent/.env`) is touched, then drives `main([])` through the two
failure windows §4.3 covers:

  A (control)   — Postgres unreachable at connect() time.
  B             — Postgres reachable at connect(), gone (or refusing) by the
                  time the first invariant query runs.

§4.3 ("Postgres unavailable must `WARN` and exit 0 ... must never take the
fleet-facing scan down with it") and the script's own docstring make no
distinction between the two: both are "Postgres unavailable", B is just the
race window. Each case prints OBSERVED rc / stdout / whether it raised.

Run: python3 repro_query_time_failure.py
"""
import contextlib
import importlib.util
import io
import os
import pathlib
import sys
import traceback
import types

SCRIPT = (pathlib.Path(__file__).resolve().parent.parent
          / "wq1776-pgvector-chunk-doc-invariants"
          / "check_chunk_doc_invariants.py")


class OperationalError(Exception):
    """Stand-in for psycopg2.OperationalError."""


def install_psycopg2_stub(fail_on_query):
    """Register a fake psycopg2 whose cursors optionally fail on the real SQL.

    connect() always succeeds, and the `SET default_transaction_read_only`
    statement it issues always succeeds, so the failure lands exactly where the
    finding claims: inside measure().
    """
    class Cursor:
        def execute(self, sql, *a):
            if fail_on_query and "research_chunks" in sql:
                raise OperationalError(
                    "server closed the connection unexpectedly")
            self._sql = sql

        def fetchone(self):
            return (0, 0, 0)

        def fetchall(self):
            return [("research_chunks_doc_id_fkey",
                     "FOREIGN KEY (doc_id) REFERENCES research_docs(id) "
                     "ON DELETE CASCADE")]

    class Conn:
        def cursor(self):
            return Cursor()

        def commit(self):
            pass

        def close(self):
            pass

    mod = types.ModuleType("psycopg2")
    mod.OperationalError = OperationalError
    mod.connect = lambda **kw: Conn()
    sys.modules["psycopg2"] = mod


def install_unreachable_psycopg2_stub():
    mod = types.ModuleType("psycopg2")
    mod.OperationalError = OperationalError
    def _boom(**kw):
        raise OperationalError("could not connect to server: Connection refused")
    mod.connect = _boom
    sys.modules["psycopg2"] = mod


def load_script():
    # Point the module's env-file constant at a path that does not exist, so
    # _load_env() is a no-op and the real agent .env is never opened.
    os.environ["LUMEN_AGENT_ENV"] = "/nonexistent/wq1788-repro.env"
    for k in ("HOST", "PORT", "DB", "USER", "PASSWORD"):
        os.environ[f"LUMEN_PGVECTOR_{k}"] = "5432" if k == "PORT" else "stub"
    spec = importlib.util.spec_from_file_location("cci_under_test", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def run_case(name, expectation, stub):
    stub()
    mod = load_script()
    out = io.StringIO()
    rc, exc = None, None
    try:
        with contextlib.redirect_stdout(out):
            rc = mod.main([])
    except BaseException:
        exc = traceback.format_exc().strip().splitlines()[-1]
    print(f"--- case {name}")
    print(f"    §4.3 expects : {expectation}")
    print(f"    rc           : {rc}")
    print(f"    stdout       : {out.getvalue().strip() or '(nothing)'}")
    print(f"    raised       : {exc or '(nothing)'}")
    print(f"    HONOURS §4.3 : {exc is None and rc == 0 and 'UNAVAILABLE' in out.getvalue()}")
    return exc is None and rc == 0 and "UNAVAILABLE" in out.getvalue()


def main():
    print(f"script under test: {SCRIPT}")
    a = run_case("A: unreachable at connect() (control)",
                 "UNAVAILABLE line, exit 0",
                 install_unreachable_psycopg2_stub)
    b = run_case("B: connect() succeeds, query fails",
                 "UNAVAILABLE line, exit 0",
                 lambda: install_psycopg2_stub(fail_on_query=True))
    print()
    print(f"VERDICT: control passes={a}  query-window passes={b}")
    return 0 if (a and b) else 1


if __name__ == "__main__":
    sys.exit(main())
