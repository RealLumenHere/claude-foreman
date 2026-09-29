"""wq#3147: minerva_guard runs a rev's tests from a clean export and pages on red.

The fixture tests build a throwaway git repo so the verdict/paging logic is
exercised without the network or the live alert queue. test_real_ratchet_* is the
ticket's acceptance probe: minerva at a pinned commit (864fe16, ratchet green) plus
one committed unguarded sqlite3.connect must come back RED naming the ratchet.
"""
import os
import subprocess
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import minerva_guard as mg  # noqa: E402

MINERVA = "/home/lumen/minerva"
PINNED_GREEN = "864fe16"  # wq#3146 fix: ratchet green, full suite 188 passed
LEAK = (
    "import sqlite3\n\n\n"
    "def leaky(path):\n"
    "    conn = sqlite3.connect(path)\n"
    "    conn.execute('SELECT 1')\n"
    "    conn.close()\n"
)


def _git(repo, *args):
    subprocess.run(["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t",
                    *args], check=True, capture_output=True)


def _commit(repo, files, msg):
    for rel, body in files.items():
        p = repo / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", msg)
    return subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"],
                          capture_output=True, text=True, check=True).stdout.strip()


@pytest.fixture
def repo(tmp_path):
    r = tmp_path / "repo"
    r.mkdir()
    _git(r, "init", "-q", "-b", "main")
    return r


@pytest.fixture
def alerts(monkeypatch):
    sent = {"aq": [], "ntfy": [], "resolved": []}
    ids = iter(range(100, 200))

    def ingest(key, title, message, priority, dry_run=False):
        sent["aq"].append((key, title))
        return next(ids)

    monkeypatch.setattr(mg, "aq_ingest", ingest)
    monkeypatch.setattr(mg, "aq_resolve",
                        lambda rid, note, dry_run=False: sent["resolved"].append(rid))
    monkeypatch.setattr(mg, "ntfy",
                        lambda title, message, priority="default", dry_run=False:
                        sent["ntfy"].append(title))
    return sent


def test_green_then_red_then_green_pages_once_and_resolves(repo, tmp_path, alerts):
    state = str(tmp_path / "state.json")
    _commit(repo, {"tests/test_a.py": "def test_a():\n    assert True\n"}, "green")
    assert mg.main(["run", "--repo", str(repo), "--rev", "main", "--no-fetch",
                    "--state", state]) == 0
    assert alerts["aq"] == [] and mg.load_state(state)["verdict"] == "green"

    _commit(repo, {"tests/test_a.py": "def test_a():\n    assert False\n"}, "red")
    mg.main(["run", "--repo", str(repo), "--rev", "main", "--no-fetch", "--state", state])
    assert [k for k, _ in alerts["aq"]] == [mg.KEY_RED]
    assert mg.load_state(state)["failing"] == ["tests/test_a.py::test_a"]

    # Same failing set on the next run: no second page.
    mg.main(["run", "--repo", str(repo), "--rev", "main", "--no-fetch", "--state", state])
    assert len(alerts["aq"]) == 1

    # A second regression landing on an already-red main pages again.
    _commit(repo, {"tests/test_b.py": "def test_b():\n    assert False\n"}, "red2")
    mg.main(["run", "--repo", str(repo), "--rev", "main", "--no-fetch", "--state", state])
    assert len(alerts["aq"]) == 2

    _commit(repo, {"tests/test_a.py": "def test_a():\n    assert True\n",
                   "tests/test_b.py": "def test_b():\n    assert True\n"}, "fix")
    mg.main(["run", "--repo", str(repo), "--rev", "main", "--no-fetch", "--state", state])
    assert alerts["resolved"] == [101]
    assert mg.load_state(state)["red_aq_id"] is None


def test_primary_checkout_edits_are_not_what_is_tested(repo, tmp_path):
    """The rev's committed tree is tested, not the working tree: a dirty primary
    checkout must neither mask a red commit nor fake one."""
    _commit(repo, {"tests/test_a.py": "def test_a():\n    assert False\n"}, "red")
    (repo / "tests/test_a.py").write_text("def test_a():\n    assert True\n")
    assert mg.main(["check", "--repo", str(repo), "--rev", "main", "--no-fetch"]) == 1


def test_broken_guard_pages_once_and_keeps_red_edge(repo, tmp_path, alerts):
    state = str(tmp_path / "state.json")
    _commit(repo, {"tests/test_a.py": "def test_a():\n    assert False\n"}, "red")
    mg.main(["run", "--repo", str(repo), "--rev", "main", "--no-fetch", "--state", state])
    assert [k for k, _ in alerts["aq"]] == [mg.KEY_RED]

    for _ in range(2):
        mg.main(["run", "--repo", str(repo), "--rev", "no-such-ref", "--no-fetch",
                 "--state", state])
    assert [k for k, _ in alerts["aq"]] == [mg.KEY_RED, mg.KEY_BROKEN]
    assert mg.main(["check", "--repo", str(repo), "--rev", "no-such-ref",
                    "--no-fetch"]) == 2

    # Guard recovers onto the same red: broken row resolved, red not re-paged.
    mg.main(["run", "--repo", str(repo), "--rev", "main", "--no-fetch", "--state", state])
    assert [k for k, _ in alerts["aq"]] == [mg.KEY_RED, mg.KEY_BROKEN]
    assert alerts["resolved"] == [101]


def test_no_tests_collected_is_broken_not_green(repo):
    _commit(repo, {"tests/helper.py": "X = 1\n"}, "no tests")
    assert mg.main(["check", "--repo", str(repo), "--rev", "main", "--no-fetch"]) == 2


def test_parse_failures_reads_short_summary():
    out = ("..F\nFAILED tests/test_x.py::test_y - AssertionError: nope\n"
           "ERROR tests/test_z.py::test_w - fixture\n1 failed, 1 error\n")
    assert mg.parse_failures(out) == ["tests/test_x.py::test_y", "tests/test_z.py::test_w"]


@pytest.mark.skipif(not os.path.isdir(os.path.join(MINERVA, ".git")),
                    reason="needs the Lumen minerva repo")
def test_real_ratchet_goes_red_on_unguarded_connect(tmp_path):
    """Acceptance probe (wq#3147): clone minerva, pin PINNED_GREEN, commit one
    unguarded sqlite3.connect on a branch -> check is RED naming the ratchet; the
    pinned parent is GREEN, so the probe can fail either way."""
    clone = tmp_path / "minerva"
    subprocess.run(["git", "clone", "-q", "--no-checkout", MINERVA, str(clone)],
                   check=True, capture_output=True)
    _git(clone, "checkout", "-q", "-b", "probe", PINNED_GREEN)
    _commit(clone, {"wq3147_leak_probe.py": LEAK}, "probe: unguarded connect")

    r = subprocess.run([sys.executable, mg.__file__, "check", "--repo", str(clone),
                        "--rev", "probe", "--no-fetch"], capture_output=True, text=True)
    assert r.returncode == 1, r.stdout[-2000:]
    assert "test_sqlite_leak_ratchet.py::test_no_new_unguarded_sqlite_connections" in r.stdout
    assert "wq3147_leak_probe.py::leaky()" in r.stdout

    r = subprocess.run([sys.executable, mg.__file__, "check", "--repo", str(clone),
                        "--rev", PINNED_GREEN, "--no-fetch"], capture_output=True, text=True)
    assert r.returncode == 0, r.stdout[-2000:]
