"""harness/repo_sweep.py — wq#800. Zero-quota: real git repos in tmp_path,
a fake wq that records add calls, no network, no claude.

Tests:
  1. All repos in sync → exit 0, no tickets.
  2. Seeded divergence → exit 1, exactly one ticket filed.
  3. Re-run with existing ticket → no duplicate (idempotent).
  4. Content-diverged repo → files a CONTENT_DIVERGED ticket.
  5. Behind-only repo → files a BEHIND_ONLY ticket.
  6. Non-git directory → reported as NOT_A_REPO, no crash.
"""
import os
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


def make_fake_wq(tmp_path):
    """A fake wq that records 'add' calls and rejects duplicates."""
    fake = str(tmp_path / "fake-wq")
    log = str(tmp_path / "wq-adds.log")
    with open(fake, "w") as fh:
        fh.write(textwrap.dedent(f"""\
            #!/usr/bin/env python3
            import sys, os, json
            LOG = "{log}"
            args = sys.argv[1:]
            if args[0] != "add":
                sys.exit(0)
            title = None
            for i, a in enumerate(args):
                if a == "--title" and i + 1 < len(args):
                    title = args[i + 1]
            # Check for duplicates
            entries = []
            if os.path.exists(LOG):
                with open(LOG) as f:
                    entries = [json.loads(l) for l in f if l.strip()]
            if any(e["title"] == title for e in entries):
                print("DUPLICATE — already exists")
                sys.exit(1)
            with open(LOG, "a") as f:
                f.write(json.dumps({{"title": title, "args": args}}) + "\\n")
            print(f"added wq ticket: {{title}}")
        """))
    os.chmod(fake, 0o755)
    return fake, log


def read_wq_log(log):
    import json
    if not os.path.exists(log):
        return []
    with open(log) as fh:
        return [json.loads(l) for l in fh if l.strip()]


# --- Tests ---

def run_sweep(*args):
    p = subprocess.run([sys.executable, SWEEP, *args], capture_output=True, text=True)
    return p.returncode, p.stdout + p.stderr


def test_all_in_sync_no_tickets(tmp_path):
    repo = make_in_sync_repo(tmp_path)
    fake_wq, log = make_fake_wq(tmp_path)
    env = os.environ.copy()
    env["REPO_SWEEP_WQ"] = fake_wq
    p = subprocess.run([sys.executable, SWEEP, "--repo", repo],
                       capture_output=True, text=True, env=env)
    assert p.returncode == 0, p.stdout + p.stderr
    assert "IN_SYNC" in p.stdout
    assert read_wq_log(log) == []


def test_safe_reset_files_one_ticket(tmp_path):
    repo = make_safe_reset_repo(tmp_path)
    fake_wq, log = make_fake_wq(tmp_path)
    env = os.environ.copy()
    env["REPO_SWEEP_WQ"] = fake_wq
    p = subprocess.run([sys.executable, SWEEP, "--repo", repo],
                       capture_output=True, text=True, env=env)
    assert p.returncode == 1, p.stdout + p.stderr
    assert "SAFE_RESET" in p.stdout
    assert "ticket filed" in p.stdout
    entries = read_wq_log(log)
    assert len(entries) == 1
    assert "SAFE_RESET" in entries[0]["title"]
    assert "diverged" in entries[0]["title"]  # basename of the repo


def test_redundant_ahead_files_a_ticket_not_an_error(tmp_path):
    repo = make_redundant_ahead_repo(tmp_path)
    fake_wq, log = make_fake_wq(tmp_path)
    env = os.environ.copy()
    env["REPO_SWEEP_WQ"] = fake_wq
    p = subprocess.run([sys.executable, SWEEP, "--repo", repo],
                       capture_output=True, text=True, env=env)
    assert p.returncode == 1, p.stdout + p.stderr
    assert "REDUNDANT_AHEAD" in p.stdout
    assert "ERROR" not in p.stdout, "an unmapped exit code silently files no ticket"
    entries = read_wq_log(log)
    assert len(entries) == 1
    assert "REDUNDANT_AHEAD" in entries[0]["title"]


def test_idempotent_no_duplicate_ticket(tmp_path):
    repo = make_safe_reset_repo(tmp_path)
    fake_wq, log = make_fake_wq(tmp_path)
    env = os.environ.copy()
    env["REPO_SWEEP_WQ"] = fake_wq
    # First run files the ticket
    subprocess.run([sys.executable, SWEEP, "--repo", repo],
                   capture_output=True, text=True, env=env)
    assert len(read_wq_log(log)) == 1
    # Second run: duplicate, should not crash and should not double-file
    p = subprocess.run([sys.executable, SWEEP, "--repo", repo],
                       capture_output=True, text=True, env=env)
    assert p.returncode == 1  # still diverged
    assert "already open" in p.stdout
    assert len(read_wq_log(log)) == 1  # still just one entry


def test_content_diverged_files_ticket(tmp_path):
    repo = make_content_diverged_repo(tmp_path)
    fake_wq, log = make_fake_wq(tmp_path)
    env = os.environ.copy()
    env["REPO_SWEEP_WQ"] = fake_wq
    p = subprocess.run([sys.executable, SWEEP, "--repo", repo],
                       capture_output=True, text=True, env=env)
    assert p.returncode == 1, p.stdout + p.stderr
    assert "CONTENT_DIVERGED" in p.stdout
    entries = read_wq_log(log)
    assert len(entries) == 1
    assert "CONTENT_DIVERGED" in entries[0]["title"]


def test_behind_only_files_ticket(tmp_path):
    repo = make_behind_only_repo(tmp_path)
    fake_wq, log = make_fake_wq(tmp_path)
    env = os.environ.copy()
    env["REPO_SWEEP_WQ"] = fake_wq
    p = subprocess.run([sys.executable, SWEEP, "--repo", repo],
                       capture_output=True, text=True, env=env)
    assert p.returncode == 1, p.stdout + p.stderr
    assert "BEHIND_ONLY" in p.stdout
    entries = read_wq_log(log)
    assert len(entries) == 1
    assert "BEHIND_ONLY" in entries[0]["title"]


def test_not_a_git_repo_does_not_crash(tmp_path):
    notrepo = str(tmp_path / "notgit")
    os.makedirs(notrepo)
    fake_wq, log = make_fake_wq(tmp_path)
    env = os.environ.copy()
    env["REPO_SWEEP_WQ"] = fake_wq
    p = subprocess.run([sys.executable, SWEEP, "--repo", notrepo],
                       capture_output=True, text=True, env=env)
    assert "NOT_A_REPO" in p.stdout
    assert read_wq_log(log) == []  # no ticket for non-repo


def test_dry_run_does_not_file_tickets(tmp_path):
    repo = make_safe_reset_repo(tmp_path)
    fake_wq, log = make_fake_wq(tmp_path)
    env = os.environ.copy()
    env["REPO_SWEEP_WQ"] = fake_wq
    p = subprocess.run([sys.executable, SWEEP, "--repo", repo, "--dry-run"],
                       capture_output=True, text=True, env=env)
    assert p.returncode == 1
    assert "dry run" in p.stdout
    assert read_wq_log(log) == []


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
