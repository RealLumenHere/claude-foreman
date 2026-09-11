"""harness/repo_sweep.py — wq#800, wq#1674. Zero-quota: real git repos in tmp_path,
a fake aq over a scratch alerts.db, no network, no claude.

wq#1674 moved the sweep off `wq add` and onto a keyed aq row, so the tests below
assert the alert-queue contract instead of a ticket count: one row per
(repo, verdict), count++ on re-fire, and the edge-clear that ends an incident.

Tests:
  1. All repos in sync → exit 0, no aq rows.
  2. Seeded divergence → exit 1, exactly one keyed aq row.
  3. Re-run while still diverged → same row, count++ (the F5 no-refile fix).
  4. Content-diverged / behind-only repos → the right keyed row.
  5. Non-git directory → reported as NOT_A_REPO, no row.
  6. Repo returning to IN_SYNC → its open row is resolved (the edge-clear).
  7. Verdict change → the stale row is resolved, the new one opened.
  8. aq unavailable → the sweep still reports and still exits 1.
"""
import os
import sqlite3
import subprocess
import sys
import textwrap

HARNESS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SWEEP = os.path.join(HARNESS_DIR, "repo_sweep.py")
RECONCILE = os.path.join(HARNESS_DIR, "repo_reconcile.py")

sys.path.insert(0, HARNESS_DIR)
import repo_sweep  # noqa: E402


def git(repo, *args):
    p = subprocess.run(["git", "-C", repo, *args], capture_output=True, text=True)
    assert p.returncode == 0, f"git {args}: {p.stdout}{p.stderr}"
    return p.stdout.strip()


def write(repo, name, text):
    with open(os.path.join(repo, name), "w") as fh:
        fh.write(text)


def commit(repo, name, text, msg):
    write(repo, name, text)
    git(repo, "add", name)
    git(repo, "commit", "-m", msg)


def make_in_sync_repo(tmp_path, name="synced"):
    """A repo where local main == origin/main."""
    origin = str(tmp_path / f"{name}-origin.git")
    work = str(tmp_path / name)
    subprocess.run(["git", "init", "--bare", "--initial-branch=main", origin],
                   check=True, capture_output=True)
    subprocess.run(["git", "clone", origin, work], check=True, capture_output=True)
    git(work, "config", "user.email", "test@lumen.local")
    git(work, "config", "user.name", "test")
    commit(work, "file.txt", "hello\n", "init")
    git(work, "push", "origin", "main")
    return work


def make_safe_reset_repo(tmp_path, name="diverged"):
    """Local main ahead but tree-identical to origin/main (the wq#686 shape)."""
    origin = str(tmp_path / f"{name}-origin.git")
    work = str(tmp_path / name)
    subprocess.run(["git", "init", "--bare", "--initial-branch=main", origin],
                   check=True, capture_output=True)
    subprocess.run(["git", "clone", origin, work], check=True, capture_output=True)
    git(work, "config", "user.email", "test@lumen.local")
    git(work, "config", "user.name", "test")
    commit(work, "file.txt", "base\n", "base")
    git(work, "push", "origin", "main")
    # Squashed PR on origin
    git(work, "checkout", "-b", "squash")
    commit(work, "file.txt", "final\n", "feat: squashed PR")
    git(work, "push", "origin", "squash:main")
    # Local main with same content, different history
    git(work, "checkout", "main")
    commit(work, "file.txt", "mid\n", "direct 1")
    commit(work, "file.txt", "final\n", "direct 2")
    git(work, "fetch", "origin")
    return work


def make_content_diverged_repo(tmp_path, name="content-div"):
    """Local main ahead with genuinely different content from origin."""
    origin = str(tmp_path / f"{name}-origin.git")
    work = str(tmp_path / name)
    subprocess.run(["git", "init", "--bare", "--initial-branch=main", origin],
                   check=True, capture_output=True)
    subprocess.run(["git", "clone", origin, work], check=True, capture_output=True)
    git(work, "config", "user.email", "test@lumen.local")
    git(work, "config", "user.name", "test")
    commit(work, "file.txt", "base\n", "base")
    git(work, "push", "origin", "main")
    git(work, "checkout", "-b", "squash")
    commit(work, "file.txt", "remote-version\n", "feat: squashed PR")
    git(work, "push", "origin", "squash:main")
    git(work, "checkout", "main")
    commit(work, "file.txt", "local-version\n", "direct 1")
    git(work, "fetch", "origin")
    return work


def make_redundant_ahead_repo(tmp_path, name="redundant"):
    """The wq#975 shape: local main ahead with a duplicate of landed content, AND behind
    later remote commits — so the sweep sees exit 11, which it must not read as ERROR."""
    origin = str(tmp_path / f"{name}-origin.git")
    work = str(tmp_path / name)
    subprocess.run(["git", "init", "--bare", "--initial-branch=main", origin],
                   check=True, capture_output=True)
    subprocess.run(["git", "clone", origin, work], check=True, capture_output=True)
    git(work, "config", "user.email", "test@lumen.local")
    git(work, "config", "user.name", "test")
    commit(work, "file.txt", "base\n", "base")
    git(work, "push", "origin", "main")
    git(work, "checkout", "-b", "landed")
    commit(work, "file.txt", "final\n", "feat: squashed PR")
    commit(work, "other.txt", "later\n", "work the duplicate never saw")
    git(work, "push", "origin", "landed:main")
    git(work, "checkout", "main")
    commit(work, "file.txt", "final\n", "direct-to-main duplicate")
    git(work, "fetch", "origin")
    return work


def make_behind_only_repo(tmp_path, name="behind"):
    """Local main behind origin/main."""
    origin = str(tmp_path / f"{name}-origin.git")
    work = str(tmp_path / name)
    subprocess.run(["git", "init", "--bare", "--initial-branch=main", origin],
                   check=True, capture_output=True)
    subprocess.run(["git", "clone", origin, work], check=True, capture_output=True)
    git(work, "config", "user.email", "test@lumen.local")
    git(work, "config", "user.name", "test")
    commit(work, "file.txt", "base\n", "base")
    git(work, "push", "origin", "main")
    git(work, "checkout", "-b", "squash")
    commit(work, "file.txt", "newer\n", "feat: new work")
    git(work, "push", "origin", "squash:main")
    git(work, "checkout", "main")
    git(work, "fetch", "origin")
    return work


ALERTS_SCHEMA = """
CREATE TABLE IF NOT EXISTS alerts(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  first_ts TEXT NOT NULL, last_ts TEXT NOT NULL,
  source TEXT NOT NULL, key TEXT NOT NULL,
  title TEXT, message TEXT, priority TEXT DEFAULT 'default',
  count INTEGER DEFAULT 1, status TEXT DEFAULT 'open',
  owner TEXT DEFAULT 'unassigned', note TEXT, resolved_ts TEXT);
"""


def make_fake_aq(tmp_path):
    """A fake aq over a scratch alerts.db with the real add/resolve contract:
    `add` dedups onto the open (source, key) row and increments count (see
    governor/foreman_usaged.py), `resolve` closes one row by id."""
    db = str(tmp_path / "alerts.db")
    c = sqlite3.connect(db)
    c.executescript(ALERTS_SCHEMA)
    c.commit()
    c.close()
    fake = str(tmp_path / "fake-aq")
    with open(fake, "w") as fh:
        fh.write(textwrap.dedent(f"""\
            #!/usr/bin/env python3
            import sys, sqlite3, datetime
            DB = "{db}"
            now = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            args = sys.argv[1:]
            c = sqlite3.connect(DB)
            if args[0] == "add":
                source, key, title, message = args[1], args[2], args[3], args[4]
                row = c.execute("SELECT id FROM alerts WHERE source=? AND key=? "
                                "AND status='open'", (source, key)).fetchone()
                if row:
                    c.execute("UPDATE alerts SET count=count+1, last_ts=?, title=?, "
                              "message=? WHERE id=?", (now, title, message, row[0]))
                else:
                    c.execute("INSERT INTO alerts(first_ts,last_ts,source,key,title,message) "
                              "VALUES(?,?,?,?,?,?)", (now, now, source, key, title, message))
            elif args[0] == "resolve":
                c.execute("UPDATE alerts SET status='resolved', resolved_ts=?, note=? "
                          "WHERE id=?", (now, args[2] if len(args) > 2 else '', args[1]))
            else:
                sys.exit(2)
            c.commit(); c.close()
        """))
    os.chmod(fake, 0o755)
    return fake, db


def aq_rows(db, status="open"):
    c = sqlite3.connect(db)
    c.row_factory = sqlite3.Row
    rows = c.execute("SELECT * FROM alerts WHERE status=? ORDER BY id", (status,)).fetchall()
    c.close()
    return rows


# --- Tests ---

def sweep_env(fake_aq, db):
    env = os.environ.copy()
    env["REPO_SWEEP_AQ"] = fake_aq
    env["AQ_DB"] = db
    return env


def run_sweep(*args, env=None):
    p = subprocess.run([sys.executable, SWEEP, *args], capture_output=True, text=True, env=env)
    return p.returncode, p.stdout + p.stderr


def test_all_in_sync_no_aq_rows(tmp_path):
    repo = make_in_sync_repo(tmp_path)
    fake_aq, db = make_fake_aq(tmp_path)
    rc, out = run_sweep("--repo", repo, env=sweep_env(fake_aq, db))
    assert rc == 0, out
    assert "IN_SYNC" in out
    assert aq_rows(db) == []


def test_safe_reset_records_one_keyed_row(tmp_path):
    repo = make_safe_reset_repo(tmp_path)
    fake_aq, db = make_fake_aq(tmp_path)
    rc, out = run_sweep("--repo", repo, env=sweep_env(fake_aq, db))
    assert rc == 1, out
    assert "SAFE_RESET" in out
    assert "aq row recorded" in out
    rows = aq_rows(db)
    assert len(rows) == 1
    assert rows[0]["source"] == "repo-sweep"
    assert rows[0]["key"] == f"{repo}:SAFE_RESET"
    assert rows[0]["count"] == 1
    # The row carries the ticket aq_escalate will file, verbatim (wq#1674).
    assert rows[0]["title"] == f"[repo-sweep] {os.path.basename(repo)}: SAFE_RESET"
    assert rows[0]["message"].startswith(f"Automated sweep detected SAFE_RESET in {repo}.")


def test_redundant_ahead_is_a_verdict_not_an_error(tmp_path):
    repo = make_redundant_ahead_repo(tmp_path)
    fake_aq, db = make_fake_aq(tmp_path)
    rc, out = run_sweep("--repo", repo, env=sweep_env(fake_aq, db))
    assert rc == 1, out
    assert "REDUNDANT_AHEAD" in out
    assert "ERROR" not in out, "an unmapped exit code silently records nothing"
    rows = aq_rows(db)
    assert len(rows) == 1
    assert rows[0]["key"] == f"{repo}:REDUNDANT_AHEAD"


def test_refire_increments_one_row_instead_of_refiling(tmp_path):
    """The wq#1674 fix for F5: nine sweeps over nine days used to mean nine
    tickets, because wq add's dup guard only sees rows that are not done."""
    repo = make_safe_reset_repo(tmp_path)
    fake_aq, db = make_fake_aq(tmp_path)
    env = sweep_env(fake_aq, db)
    for _ in range(3):
        rc, out = run_sweep("--repo", repo, env=env)
        assert rc == 1, out
    rows = aq_rows(db)
    assert len(rows) == 1, [dict(r) for r in rows]
    assert rows[0]["count"] == 3


def test_content_diverged_records_row(tmp_path):
    repo = make_content_diverged_repo(tmp_path)
    fake_aq, db = make_fake_aq(tmp_path)
    rc, out = run_sweep("--repo", repo, env=sweep_env(fake_aq, db))
    assert rc == 1, out
    assert "CONTENT_DIVERGED" in out
    rows = aq_rows(db)
    assert len(rows) == 1
    assert rows[0]["key"] == f"{repo}:CONTENT_DIVERGED"


def test_behind_only_records_row(tmp_path):
    repo = make_behind_only_repo(tmp_path)
    fake_aq, db = make_fake_aq(tmp_path)
    rc, out = run_sweep("--repo", repo, env=sweep_env(fake_aq, db))
    assert rc == 1, out
    assert "BEHIND_ONLY" in out
    rows = aq_rows(db)
    assert len(rows) == 1
    assert rows[0]["key"] == f"{repo}:BEHIND_ONLY"


def test_in_sync_again_resolves_the_open_row(tmp_path):
    """The edge-clear. Without it `count` is lifetime, not per-incident, and
    aq_escalate — which suppresses per aq id — never tickets a later divergence."""
    repo = make_behind_only_repo(tmp_path)
    fake_aq, db = make_fake_aq(tmp_path)
    env = sweep_env(fake_aq, db)
    rc, _ = run_sweep("--repo", repo, env=env)
    assert rc == 1
    assert len(aq_rows(db)) == 1
    git(repo, "merge", "--ff-only", "origin/main")
    rc, out = run_sweep("--repo", repo, env=env)
    assert rc == 0, out
    assert "1 stale aq row(s) resolved" in out
    assert aq_rows(db) == []
    resolved = aq_rows(db, status="resolved")
    assert len(resolved) == 1
    assert "IN_SYNC" in (resolved[0]["note"] or "")


def test_verdict_change_resolves_the_stale_row(tmp_path):
    repo = make_behind_only_repo(tmp_path)
    fake_aq, db = make_fake_aq(tmp_path)
    env = sweep_env(fake_aq, db)
    c = sqlite3.connect(db)
    c.execute("INSERT INTO alerts(first_ts,last_ts,source,key,title,message) "
              "VALUES('t','t','repo-sweep',?,'stale','stale')", (f"{repo}:SAFE_RESET",))
    c.commit()
    c.close()
    rc, out = run_sweep("--repo", repo, env=env)
    assert rc == 1, out
    rows = aq_rows(db)
    assert len(rows) == 1, [dict(r) for r in rows]
    assert rows[0]["key"] == f"{repo}:BEHIND_ONLY"
    assert aq_rows(db, status="resolved")[0]["key"] == f"{repo}:SAFE_RESET"


def test_not_a_git_repo_does_not_crash(tmp_path):
    notrepo = str(tmp_path / "notgit")
    os.makedirs(notrepo)
    fake_aq, db = make_fake_aq(tmp_path)
    rc, out = run_sweep("--repo", notrepo, env=sweep_env(fake_aq, db))
    assert "NOT_A_REPO" in out
    assert aq_rows(db) == []


def test_dry_run_touches_nothing(tmp_path):
    repo = make_safe_reset_repo(tmp_path)
    fake_aq, db = make_fake_aq(tmp_path)
    rc, out = run_sweep("--repo", repo, "--dry-run", env=sweep_env(fake_aq, db))
    assert rc == 1
    assert "dry run" in out
    assert aq_rows(db) == []


def test_aq_failure_is_reported_not_swallowed(tmp_path):
    """A broken alert queue must not turn a diverged repo into a clean sweep."""
    repo = make_safe_reset_repo(tmp_path)
    _, db = make_fake_aq(tmp_path)
    env = sweep_env(str(tmp_path / "no-such-aq"), db)
    rc, out = run_sweep("--repo", repo, env=env)
    assert rc == 1, out
    assert "SAFE_RESET" in out
    assert "aq FAILED" in out
    assert aq_rows(db) == []


# --- repo_hint (wq#1094: dispatcher reads the repo back off a filed ticket) -----------

def test_repo_hint_round_trips_a_filed_ticket_detail(tmp_path, monkeypatch):
    repo = "/home/lumen/questfree"
    monkeypatch.setattr(repo_sweep, "managed_repos", lambda: {repo, "/home/lumen/pokemon"})
    title = f"[repo-sweep] {os.path.basename(repo)}: BEHIND_ONLY"
    detail = (f"Automated sweep detected BEHIND_ONLY in {repo}.\n"
              f"Output: BEHIND_ONLY {repo} main ahead=0 behind=1\n\n"
              f"To inspect: python3 {RECONCILE} status --repo {repo}\n"
              f"To fix (if SAFE_RESET or REDUNDANT_AHEAD): "
              f"python3 {RECONCILE} reconcile --repo {repo} --apply")
    assert repo_sweep.repo_hint(title, detail) == repo


def test_repo_hint_none_for_non_sweep_ticket():
    assert repo_sweep.repo_hint("just a normal ticket", "detail: /home/lumen/pokemon") is None


def test_repo_hint_none_when_detail_missing_or_malformed():
    title = "[repo-sweep] pokemon: SAFE_RESET"
    assert repo_sweep.repo_hint(title, "") is None
    assert repo_sweep.repo_hint(title, "some unrelated first line") is None


def test_repo_hint_refuses_a_repo_outside_the_managed_set(monkeypatch):
    monkeypatch.setattr(repo_sweep, "managed_repos", lambda: {"/home/lumen/pokemon"})
    title = "[repo-sweep] evil: SAFE_RESET"
    detail = "Automated sweep detected SAFE_RESET in /etc/shadow-adjacent-repo.\nmore text"
    assert repo_sweep.repo_hint(title, detail) is None


def test_escalated_ticket_format_round_trips_through_repo_hint(tmp_path, monkeypatch):
    """wq#1674: repo_sweep now writes the ticket text into an aq row and
    aq_escalate passes it through, so the dispatcher's read-back has to survive
    the extra hop. The provenance aq_escalate appends must stay AFTER the detail."""
    repo = "/home/lumen/pokemon"
    monkeypatch.setattr(repo_sweep, "managed_repos", lambda: {repo})
    title = repo_sweep.ticket_title(repo, "BEHIND_ONLY")
    detail = repo_sweep.ticket_detail(repo, "BEHIND_ONLY", "ahead=0 behind=2")
    assert repo_sweep.repo_hint(title, detail) == repo
    escalated = detail + "\n\nAuto-escalated by aq_escalate (wq#1674 repo-sweep rule)."
    assert repo_sweep.repo_hint(title, escalated) == repo


def test_aq_key_splits_back_into_repo_and_verdict():
    key = repo_sweep.aq_key("/home/lumen/bin", "BEHIND_ONLY")
    assert key.rsplit(":", 1) == ["/home/lumen/bin", "BEHIND_ONLY"]


def test_fleet_inventory_is_a_managed_repo():
    # wq#1453-1456: needs:repo(/home/lumen/fleet/inventory) must resolve (router.needs_repo_hint).
    assert "/home/lumen/fleet/inventory" in repo_sweep.managed_repos()
