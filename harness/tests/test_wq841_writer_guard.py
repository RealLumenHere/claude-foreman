"""wq#841 (a2): the reopened_count WRITER, and what happens when it goes missing.

wq#841 made rework a durable column instead of an erasable notes substring. The column is
durable; the code that writes it is not — `/home/lumen/bin/wq` is untracked and several lanes
rewrite it in place from older copies. On 2026-08-23 12:17Z one of them did exactly that and
every reopened_count reference vanished, leaving a column nothing bumps: rework_rate frozen,
`reopened_signal_is_structural` still reporting 1, no complaint anywhere.

These tests pin the two things that catch it: a checker that can tell a patched wq from a
stripped one, an --apply that puts the writer back idempotently and refuses half-patched input,
and the gauge that turns all of it into a number Grafana can alert on.

Nothing here writes to the live binary — every case runs against a copy under tmp_path.
"""
import importlib.util
import os
import shutil
import sqlite3
import subprocess
import sys

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
ENSURE = os.path.join(REPO, "ops", "wq841-rework-durability", "ensure_wq_reopened.py")
FLOW = os.path.join(REPO, "reports", "opsaudit_a13_flow_metrics.py")


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


ensure = load(ENSURE, "ensure_wq_reopened")
flow = load(FLOW, "opsaudit_a13_flow_metrics")


def unpatched_sources():
    """Every real wq on this box that has no writer in it: the live one when a lane has just
    rewritten it away, plus the .bak.* copies each such lane leaves behind. Using a real file
    rather than a checked-in fixture means the applier is exercised against the shapes wq
    actually takes, which is where it has to work."""
    live = ensure.WQ_BIN
    candidates = [live] if os.path.exists(live) else []
    bak_dir = os.path.dirname(live)
    if os.path.isdir(bak_dir):
        base = os.path.basename(live) + ".bak."
        candidates += sorted((os.path.join(bak_dir, n) for n in os.listdir(bak_dir)
                              if n.startswith(base)), reverse=True)
    return [c for c in candidates
            if os.path.isfile(c) and not os.path.islink(c) and not ensure.check(c)["present"]]


@pytest.fixture
def stale_wq(tmp_path):
    """A runnable copy of a wq with no reopened_count writer — the state the 12:17Z revert left
    behind. Anchors must still match, or the applier could not have healed that revert either."""
    for src in unpatched_sources():
        text = open(src, encoding="utf-8").read()
        try:
            ensure.apply_hunks(text)
        except ValueError:
            continue                      # too old to carry today's anchors; try the next one
        path = tmp_path / "wq_stale"
        shutil.copy2(src, path)
        path.chmod(0o755)
        return str(path)
    pytest.skip("no unpatched wq with current anchors available to patch")


def reopen_roundtrip(wq_path, tmp_path, name="rt"):
    """add -> done -> reopen against a scratch DB; returns the ticket's reopened_count."""
    db = str(tmp_path / f"{name}.db")
    env = dict(os.environ, WQ_DB=db, WQ_AGENT="wq841-guard-test")

    def run(*args):
        p = subprocess.run([sys.executable, wq_path, *args], capture_output=True, text=True,
                           env=env, timeout=60)
        assert p.returncode == 0, f"{args}: {p.stdout}{p.stderr}"
        return p

    tid = run("add", "--lane", "auto", "--title", "writer guard").stdout.strip().split()[2]
    # --no-artifact is wq#759's escape hatch and predates nothing older than it; copies from
    # before that gate close without it.
    done = subprocess.run([sys.executable, wq_path, "done", tid, "--notes", "closed once",
                           "--no-artifact", "guard test"], capture_output=True, text=True,
                          env=env, timeout=60)
    if done.returncode != 0:
        run("done", tid, "--notes", "closed once")
    out = run("edit", tid, "--status", "open").stdout
    c = sqlite3.connect(db)
    (count,) = c.execute("SELECT reopened_count FROM tasks WHERE id=?", (tid,)).fetchone()
    c.close()
    return count, out


def test_check_sees_a_stripped_writer(stale_wq):
    state = ensure.check(stale_wq)
    assert state["present"] is False
    assert state["partial"] is False


def test_check_never_raises_on_a_missing_file(tmp_path):
    state = ensure.check(str(tmp_path / "does_not_exist"))
    assert state["present"] is False and state["readable"] is False


def test_apply_restores_the_writer_and_it_actually_counts(stale_wq, tmp_path):
    code, msg = ensure.apply(stale_wq, backup=False)
    assert code == 0, msg
    assert ensure.check(stale_wq)["present"] is True
    count, out = reopen_roundtrip(stale_wq, tmp_path)
    assert count == 1, out
    assert "REOPENED" in out, f"a reopen must announce itself: {out!r}"


def test_apply_is_idempotent(stale_wq):
    ensure.apply(stale_wq, backup=False)
    with open(stale_wq, encoding="utf-8") as fh:
        after_first = fh.read()
    code, msg = ensure.apply(stale_wq, backup=False)
    assert code == 0 and "already present" in msg
    with open(stale_wq, encoding="utf-8") as fh:
        assert fh.read() == after_first


def test_apply_refuses_a_half_patched_file(stale_wq, tmp_path):
    """The 12:48Z state: helpers back, call sites not. Patching that again would define
    bump_reopened twice. Refuse and leave it alone — a human restores a known copy."""
    ensure.apply(stale_wq, backup=False)
    with open(stale_wq, encoding="utf-8") as fh:
        text = fh.read()
    find, repl = ensure.HUNKS[3]          # the _finish call site
    half = tmp_path / "wq_half"
    half.write_text(text.replace(repl, find), encoding="utf-8")
    before = half.read_text(encoding="utf-8")
    code, msg = ensure.apply(str(half), backup=False)
    assert code == 1 and "HALF-patched" in msg
    assert half.read_text(encoding="utf-8") == before, "a refusal must not write"


def test_apply_leaves_a_backup_it_can_roll_back_to(stale_wq):
    code, msg = ensure.apply(stale_wq, backup=True)
    assert code == 0, msg
    bak = msg.split("backup ")[1].split(")")[0]
    assert ensure.check(bak)["present"] is False, "the backup must be the pre-patch text"
    os.remove(bak)


def test_gauge_reads_1_when_present_and_0_when_the_writer_is_gone(stale_wq):
    assert flow.writer_present(stale_wq) == 0
    ensure.apply(stale_wq, backup=False)
    assert flow.writer_present(stale_wq) == 1


def test_gauge_reads_minus_1_when_it_cannot_check_at_all(monkeypatch, tmp_path):
    """0 means "the writer is gone", -1 means "I could not tell". A broken checker reporting 0
    would fire the wrong alarm, and reporting 1 would hide a real one."""
    monkeypatch.setattr(flow, "ENSURE_WQ", str(tmp_path / "no_such_checker.py"))
    assert flow.writer_present() == -1


def test_prom_output_always_carries_the_writer_gauge():
    """Absence is the failure mode this whole ticket is about, so the series must never just
    stop being emitted."""
    p = subprocess.run([sys.executable, FLOW, "--prom"], capture_output=True, text=True, timeout=120)
    assert p.returncode == 0, p.stderr
    body = [ln for ln in p.stdout.splitlines() if ln.startswith("lumen_queue_reopened_writer_present ")]
    assert len(body) == 1, p.stdout
    assert body[0].split()[1] in {"1", "0", "-1"}
