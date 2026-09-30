"""wq#3222: no test may reach the live alert queue or the live ntfy pager.

Two layers, both installed by conftest.py at import (before any harness module is
imported, so a reload or a sys.modules pop-and-reimport reads the same values):

1. env: every harness/dispatcher module resolves its aq/ntfy binary from an env var
   (FOREMAN_AQ_BIN, FOREMAN_NTFY_BIN, ...). They are pointed at a path that does not
   exist, so the fail-open paths (`os.path.exists(AQ_BIN)`, `except OSError`) stay quiet,
   and subprocess fworkers inherit the same values. The old _no_live_aq monkeypatched
   two module globals per test, which a reloaded module silently escapes.
2. exec: Popen._execute_child is wrapped. A spawn whose argv resolves to the live aq
   or ntfy-send is refused with ENOENT (never executed) and recorded against
   PYTEST_CURRENT_TEST; conftest fails that test and the session. This is the
   overnight-1 recorder (wq#3222) as a permanent guard, matching by resolved path rather
   than by basename, so a test's own `tmp_path/aq` fake is not a hit.

install() also drops WORKER_SESSION_ENV, the per-ticket vars a suite run inside an
fworker session inherits, which pointed tests at that worker's live run state.

Only this process is wrapped: a grandchild that hardcodes /home/lumen/bin/aq instead of
reading the env var is out of reach here.
"""
import errno
import inspect
import os
import shutil
import subprocess
import sys

LIVE_AQ = "/home/lumen/bin/aq"
LIVE_BINS = (LIVE_AQ, "/home/lumen/bin/ntfy-send")
LIVE_AQ_DB = "/home/lumen/agents/alert-queue/alerts.db"
NO_AQ = "/nonexistent/foreman-tests/aq"
NO_NTFY = "/nonexistent/foreman-tests/ntfy-send"
ENV = {
    "FOREMAN_AQ_BIN": NO_AQ,
    "REPO_SWEEP_AQ": NO_AQ,
    "FOREMAN_NTFY_BIN": NO_NTFY,
    "LANGFUSE_LAG_NTFY_SEND": NO_NTFY,
    "LANGFUSE_TRACE_RATE_NTFY_SEND": NO_NTFY,
}

# What fworker exports to the worker it spawns (fworker.py build_env and the spawn
# path): a suite run from inside a worker session inherits them, so the stop gate read
# that worker's own pinned acceptance.json and bumped its live stop-gate.json counter
# (test_evidence_gate/test_stop_gate_worker_traps failed only when run by a worker).
WORKER_SESSION_ENV = ("FOREMAN_TICKET_ID", "FOREMAN_STOP_GATE_STATE", "FOREMAN_WORKTREE_ROOT",
                      "FOREMAN_ATTEMPT", "FOREMAN_GRANT_FILE", "FOREMAN_GRANT_SHA",
                      "FOREMAN_FIRE_PHASE", "FOREMAN_FIRE_PLAN_PATH")

hits = []  # (PYTEST_CURRENT_TEST, argv)
_real_execute_child = subprocess.Popen._execute_child
_SIG = inspect.signature(_real_execute_child)
_live_real = {os.path.realpath(p): p for p in LIVE_BINS}
_live_names = {os.path.basename(p) for p in LIVE_BINS}


def _resolves_live(token, path_env):
    if os.sep not in token:
        if token not in _live_names:
            return None
        token = shutil.which(token, path=path_env)
        if not token:
            return None
    return _live_real.get(os.path.realpath(token))


def live_target(args, executable=None, shell=False, env=None):
    """The LIVE_BINS entry this spawn would run, or None. Checks the executable, the
    first three argv words (`timeout 5 aq ...`, `bash /home/lumen/bin/aq`) and, under
    shell=True, every word of the command line."""
    if isinstance(args, (str, bytes, os.PathLike)):
        args = [args]
    words = [os.fsdecode(a) for a in args]
    if shell:
        words = " ".join(words).replace(";", " ").replace("|", " ").replace("&", " ").split()
    else:
        words = words[:3]
    if executable:
        words.insert(0, os.fsdecode(executable))
    path_env = (env or os.environ).get("PATH")
    for w in words:
        live = _resolves_live(w, path_env)
        if live:
            return live
    return None


def _close_child_ends(self, a):
    """What the real _execute_child's `finally` does with the child's pipe ends; Popen's
    own cleanup on a failed spawn only closes the parent's."""
    for child, parent in (("p2cread", "p2cwrite"), ("c2pwrite", "c2pread"),
                          ("errwrite", "errread")):
        if a.get(child, -1) != -1 and a.get(parent, -1) != -1:
            os.close(a[child])
    if hasattr(self, "_devnull"):
        os.close(self._devnull)
        del self._devnull


def scratch_aq_db(env=None):
    """True when the spawn's AQ_DB names a queue other than the live one: the real aq CLI
    on a tmp_path alerts.db (dispatcher/tests/test_dispatch_health_idle.py) opens no
    live row, so it is allowed. ntfy-send has no such switch."""
    db = (os.environ if env is None else env).get("AQ_DB")
    return bool(db) and os.path.realpath(db) != os.path.realpath(LIVE_AQ_DB)


def _guarded_execute_child(self, *args, **kw):
    a = _SIG.bind(self, *args, **kw).arguments
    hit = live_target(a["args"], a["executable"], a.get("shell"), a.get("env"))
    if hit == LIVE_AQ and scratch_aq_db(a.get("env")):
        hit = None
    if hit:
        argv = a["args"]
        hits.append((os.environ.get("PYTEST_CURRENT_TEST", "<outside a test>"),
                     [argv] if isinstance(argv, (str, bytes)) else list(argv)))
        _close_child_ends(self, a)
        raise FileNotFoundError(errno.ENOENT,
                                "refused by harness/tests/live_bin_guard.py (wq#3222): "
                                "tests must not reach the live binary", hit)
    return _real_execute_child(self, *args, **kw)


def session_finish(session):
    """Called from both conftests' pytest_sessionfinish (a repo-root run loads both); a
    spawn outside a harness test has no per-test fixture to fail, so the session fails."""
    if not hits or getattr(session, "_live_bin_guard_reported", False):
        return
    session._live_bin_guard_reported = True
    tr = session.config.pluginmanager.get_plugin("terminalreporter")
    if tr:
        tr.write_line(f"live_bin_guard: {len(hits)} live aq/ntfy-send spawn(s) refused: "
                      f"{hits}", red=True)
    session.exitstatus = 1  # pytest.ExitCode.TESTS_FAILED


def install():
    """Env first, then re-point any module imported before this ran (a root `pytest`
    collects dispatcher/ ahead of harness/tests), then wrap the spawn."""
    os.environ.update(ENV)
    for k in WORKER_SESSION_ENV:
        os.environ.pop(k, None)
    fake = {"aq": NO_AQ, "ntfy-send": NO_NTFY}
    for mod in list(sys.modules.values()):
        for attr in ("AQ_BIN", "NTFY_BIN", "NTFY_SEND"):
            try:
                val = getattr(mod, attr, None)
            except Exception:  # lazy-module __getattr__ side effects
                continue
            if isinstance(val, str) and os.path.realpath(val) in _live_real:
                setattr(mod, attr, fake[os.path.basename(val)])
    if subprocess.Popen._execute_child is not _guarded_execute_child:
        subprocess.Popen._execute_child = _guarded_execute_child
