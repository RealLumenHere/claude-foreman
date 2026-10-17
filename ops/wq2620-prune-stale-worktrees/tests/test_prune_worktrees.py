"""wq#2620 — prune_worktrees.py against real git repos built in a tmp dir. No
mocking of git itself: every check the tool makes (merge-base, status, worktree
list) runs for real against throwaway repos, so a change to git's own output
shapes would fail these tests too.
"""
import datetime
import json
import os
import subprocess
import sys
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import prune_worktrees as pw  # noqa: E402


# The format bin/wq:145 and bin/slock:43 actually write into the queue db.
# Spelled out here rather than imported from `pw` on purpose (wq#2669): these
# fixtures have to fail if the pruner's own constant drifts away from what the
# writers store. The original suite used ISO-8601 here, which matched the
# pruner's then-buggy parser and kept the expiry tests green while the branch
# they cover was unreachable against a real db.
WQ_TS_FMT = "%Y-%m-%d %H:%M:%S"


def wq_ts(**delta):
    """A timestamp in wq's stored format, offset from now by `delta`."""
    t = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(**delta)
    return t.strftime(WQ_TS_FMT)


def git(*args, cwd, env=None):
    r = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, env=env)
    assert r.returncode == 0, f"git {args} failed: {r.stderr}"
    return r.stdout


def dated_env(days_ago):
    """Env that stamps the commit git makes with it `days_ago` days in the past —
    author and committer both, since `%ct` reads the committer date."""
    ts = str(int(time.time()) - int(days_ago * 86400))
    env = dict(os.environ)
    env["GIT_AUTHOR_DATE"] = ts
    env["GIT_COMMITTER_DATE"] = ts
    return env


def make_repo(tmp_path, name="origin-src"):
    """A bare 'origin' plus a working clone on 'main', HEAD pointed at it."""
    bare = tmp_path / f"{name}.git"
    work = tmp_path / name
    subprocess.run(["git", "init", "--bare", "-q", "-b", "main", str(bare)], check=True)
    subprocess.run(["git", "clone", "-q", str(bare), str(work)], check=True)
    git("config", "user.email", "t@example.com", cwd=work)
    git("config", "user.name", "T", cwd=work)
    (work / "f.txt").write_text("1\n")
    git("add", "f.txt", cwd=work)
    git("commit", "-q", "-m", "init", cwd=work)
    git("push", "-q", "origin", "main", cwd=work)
    return work


def add_worktree(repo, root, name, branch, ahead=False, dirty=False, base_branch="main"):
    """Cut a linked worktree at root/name on a new branch off `base_branch`."""
    os.makedirs(root, exist_ok=True)
    wt = os.path.join(root, name)
    git("worktree", "add", "-q", "-b", branch, wt, base_branch, cwd=repo)
    if ahead:
        with open(os.path.join(wt, "g.txt"), "w") as f:
            f.write("x\n")
        git("add", "g.txt", cwd=wt)
        git("commit", "-q", "-m", "unique work", cwd=wt)
    if dirty:
        with open(os.path.join(wt, "f.txt"), "a") as f:
            f.write("uncommitted\n")
    return wt


def land_late(repo, root, name, branch, commit_days_ago, land_days_ago, ff=False):
    """A worktree whose unique commit is `commit_days_ago` old but which only
    LANDED on origin/main `land_days_ago` ago — the wq#2934 divergence, built
    out of real git dates and real ancestry rather than a patched clock.

    `ff=True` fast-forwards instead of merging, leaving the branch tip as
    origin/main's own tip: no commit anywhere dates that landing, so
    `landing_age_days` has only `done_at` (or the tip) left to read."""
    os.makedirs(root, exist_ok=True)
    wt = os.path.join(root, name)
    git("worktree", "add", "-q", "-b", branch, wt, "main", cwd=repo)
    with open(os.path.join(wt, "g.txt"), "w") as f:
        f.write("ticket work\n")
    cenv = dated_env(commit_days_ago)
    git("add", "g.txt", cwd=wt, env=cenv)
    git("commit", "-q", "-m", "ticket work, then blocked", cwd=wt, env=cenv)
    if ff:
        git("merge", "-q", "--ff-only", branch, cwd=repo)
    else:
        git("merge", "-q", "--no-ff", "-m", f"merge {branch}", branch,
            cwd=repo, env=dated_env(land_days_ago))
    git("push", "-q", "origin", "main", cwd=repo)
    git("fetch", "-q", "origin", cwd=repo)
    return wt


def wq_db_with_done_at(tmp_path, monkeypatch, ticket_id, done_at):
    """A synthetic queue db where `ticket_id` is done at `done_at`."""
    import sqlite3
    db = tmp_path / "queue.db"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE tasks(id INTEGER PRIMARY KEY, status TEXT, "
                 "lease_expires TEXT, done_at TEXT)")
    conn.execute("INSERT INTO tasks(id, status, lease_expires, done_at) "
                 "VALUES (?, 'done', NULL, ?)", (ticket_id, done_at))
    conn.commit()
    conn.close()
    monkeypatch.setattr(pw, "WQ_DB", str(db))
    return db


@pytest.fixture
def repo(tmp_path):
    return make_repo(tmp_path)


def test_merged_clean_worktree_is_prunable(tmp_path, repo):
    root = tmp_path / "worktrees"
    add_worktree(repo, root, "wq9001-a1", "feat/wq9001-a1-done")
    results = pw.run([str(root)], apply=False, fetch=False, grace_days=0)
    assert len(results) == 1
    assert results[0]["action"] == "prune"


def test_branch_with_unique_commits_is_skipped(tmp_path, repo):
    root = tmp_path / "worktrees"
    add_worktree(repo, root, "wq9002-a1", "feat/wq9002-a1-wip", ahead=True)
    [r] = pw.run([str(root)], apply=False, fetch=False, grace_days=0)
    assert r["action"] == "skip"
    assert "commits not in" in r["reason"]


def test_squash_merged_branch_is_prunable(tmp_path, repo):
    """wq#4105: a squash-merge-then-delete workflow never puts the branch's own
    commits onto origin/main (only a new commit with the same total diff), so
    the plain ancestor test in `is_merged` never matches. The patch-id fallback
    must still find it merged."""
    root = tmp_path / "worktrees"
    branch = "feat/wq9005-a1-squashed"
    wt = add_worktree(repo, root, "wq9005-a1", branch, ahead=True)
    # Squash-merge: apply the branch's diff onto main as a brand new commit,
    # never merging or fast-forwarding the branch itself in.
    diff = subprocess.run(["git", "diff", "main", branch], cwd=repo,
                           capture_output=True, text=True, check=True).stdout
    subprocess.run(["git", "apply", "-"], cwd=repo, input=diff, text=True, check=True)
    git("add", "-A", cwd=repo)
    git("commit", "-q", "-m", f"squash-merge {branch} (#1)", cwd=repo)
    git("push", "-q", "origin", "main", cwd=repo)
    git("fetch", "-q", "origin", cwd=repo)

    [r] = pw.run([str(root)], apply=False, fetch=False, grace_days=0)
    assert r["action"] == "prune"
    assert "squash-equivalent" in r["reason"]


def test_dirty_worktree_without_backup_is_skipped_even_if_merged(tmp_path, repo):
    root = tmp_path / "worktrees"
    add_worktree(repo, root, "wq9003-a1", "feat/wq9003-a1-dirty", dirty=True)
    [r] = pw.run([str(root)], apply=False, fetch=False, grace_days=0, backup_root=None)
    assert r["action"] == "skip"
    assert "uncommitted" in r["reason"]


def test_dirty_merged_worktree_is_backed_up_then_prunable(tmp_path, repo):
    """wq#4105: a merged-but-dirty worktree is backup-then-prune, not an
    indefinite skip — matching the by-hand backup-then-delete done for the
    241 stale worktrees found 2026-10-16."""
    root = tmp_path / "worktrees"
    backup_root = tmp_path / "backups"
    wt = add_worktree(repo, root, "wq9004-a1", "feat/wq9004-a1-dirty", dirty=True)
    [r] = pw.run([str(root)], apply=False, fetch=False, grace_days=0,
                 backup_root=str(backup_root))
    assert r["action"] == "prune"
    assert "backed up" in r["reason"]
    backups = list(backup_root.iterdir())
    assert len(backups) == 1
    diff = (backups[0] / "diff.patch").read_text()
    assert "uncommitted" in diff


def test_locked_worktree_is_skipped(tmp_path, repo):
    root = tmp_path / "worktrees"
    wt = add_worktree(repo, root, "wq9004-a1", "feat/wq9004-a1-locked")
    git("worktree", "lock", wt, cwd=repo)
    [r] = pw.run([str(root)], apply=False, fetch=False, grace_days=0)
    assert r["action"] == "skip"
    assert "locked" in r["reason"]


def test_stray_directory_without_gitfile_is_ignored(tmp_path):
    root = tmp_path / "worktrees"
    os.makedirs(root / "not-a-worktree")
    assert pw.run([str(root)], apply=False, fetch=False, grace_days=0) == []


def test_primary_checkout_is_never_a_candidate(tmp_path, repo):
    """A full clone's .git is a DIRECTORY, not a linked-worktree file — must never
    be picked up even if someone points --roots straight at a parent containing one."""
    assert pw.linked_worktree_repo(str(repo)) is None


def test_live_wq_claim_blocks_pruning(tmp_path, repo, monkeypatch):
    root = tmp_path / "worktrees"
    add_worktree(repo, root, "wq9005-a1", "feat/wq9005-a1-live")
    db = tmp_path / "queue.db"
    import sqlite3
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE tasks(id INTEGER PRIMARY KEY, status TEXT, lease_expires TEXT)")
    conn.execute("INSERT INTO tasks(id, status, lease_expires) VALUES (9005, 'claimed', NULL)")
    conn.commit()
    conn.close()
    monkeypatch.setattr(pw, "WQ_DB", str(db))
    [r] = pw.run([str(root)], apply=False, fetch=False, grace_days=0)
    assert r["action"] == "skip"
    assert "live wq claim" in r["reason"]


def test_expired_lease_does_not_block_pruning(tmp_path, repo, monkeypatch):
    root = tmp_path / "worktrees"
    add_worktree(repo, root, "wq9006-a1", "feat/wq9006-a1-expired")
    db = tmp_path / "queue.db"
    import sqlite3
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE tasks(id INTEGER PRIMARY KEY, status TEXT, lease_expires TEXT)")
    conn.execute("INSERT INTO tasks(id, status, lease_expires) VALUES (9006, 'claimed', '2000-01-01 00:00:00')")
    conn.commit()
    conn.close()
    monkeypatch.setattr(pw, "WQ_DB", str(db))
    [r] = pw.run([str(root)], apply=False, fetch=False, grace_days=0)
    assert r["action"] == "prune"


def test_parse_ts_reads_the_format_wq_actually_writes():
    """wq#2669 regression. `_parse_ts` must read what bin/wq and bin/slock store
    and nothing else — a parser that also accepts ISO-8601 would let a fixture in
    the wrong format keep passing, which is how wq#2644's bug stayed hidden."""
    assert pw._parse_ts(wq_ts(hours=-1)) < pw._now_utc_naive()
    assert pw._parse_ts(wq_ts(hours=1)) > pw._now_utc_naive()
    assert pw._parse_ts("2000-01-01T00:00:00Z") is None
    assert pw._parse_ts("not-a-timestamp") is None
    assert pw._parse_ts(None) is None


def test_unparseable_lease_fails_closed_with_a_reason_that_says_so(tmp_path, repo, monkeypatch):
    """A lease the parser cannot read must skip (never delete on a value we do
    not understand) but must say `unparseable` — the wq#2644 failure mode was a
    silent skip whose reason string read like a legitimate live claim."""
    root = tmp_path / "worktrees"
    add_worktree(repo, root, "wq9016-a1", "feat/wq9016-a1-garbage-lease")
    db = tmp_path / "queue.db"
    import sqlite3
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE tasks(id INTEGER PRIMARY KEY, status TEXT, lease_expires TEXT)")
    conn.execute("INSERT INTO tasks(id, status, lease_expires) VALUES (9016, 'claimed', 'whenever')")
    conn.commit()
    conn.close()
    monkeypatch.setattr(pw, "WQ_DB", str(db))
    [r] = pw.run([str(root)], apply=False, fetch=False, grace_days=0)
    assert r["action"] == "skip"
    assert "unparseable" in r["reason"]


def test_missing_wq_db_fails_closed(tmp_path, repo, monkeypatch):
    root = tmp_path / "worktrees"
    add_worktree(repo, root, "wq9007-a1", "feat/wq9007-a1-nodb")
    monkeypatch.setattr(pw, "WQ_DB", str(tmp_path / "does-not-exist.db"))
    [r] = pw.run([str(root)], apply=False, fetch=False, grace_days=0)
    assert r["action"] == "skip"
    assert "fail closed" in r["reason"]


def test_live_slock_blocks_scratch_worktree(tmp_path, repo, monkeypatch):
    root = tmp_path / "scratch" / "worktrees"
    wt_name = "myrepo-feat-wq9008-thing"
    add_worktree(repo, root, wt_name, "feat/wq9008-thing")
    owners = root / ".owners"
    os.makedirs(owners)
    (owners / wt_name).write_text("agent=some-session-1\ncreated=now\n")
    db = tmp_path / "queue.db"
    import sqlite3
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE slocks(scope TEXT, holder TEXT, expires_at TEXT)")
    conn.execute("CREATE TABLE slocks_ro(scope TEXT, holder TEXT, expires_at TEXT)")
    conn.execute("INSERT INTO slocks(scope, holder, expires_at) VALUES ('lane:x', 'some-session-1', NULL)")
    conn.commit()
    conn.close()
    monkeypatch.setattr(pw, "WQ_DB", str(db))
    [r] = pw.run([str(root)], apply=False, fetch=False, grace_days=0)
    assert r["action"] == "skip"
    assert "live slock" in r["reason"]


def scratch_worktree_with_slock(tmp_path, repo, monkeypatch, wt_name, branch, expires_at):
    """A scratch worktree owned by `some-session-1`, holding one exclusive slock
    that expires at `expires_at`. Returns the root to sweep."""
    root = tmp_path / "scratch" / "worktrees"
    add_worktree(repo, root, wt_name, branch)
    owners = root / ".owners"
    os.makedirs(owners, exist_ok=True)
    (owners / wt_name).write_text("agent=some-session-1\ncreated=now\n")
    db = tmp_path / "queue.db"
    import sqlite3
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE slocks(scope TEXT, holder TEXT, expires_at TEXT)")
    conn.execute("CREATE TABLE slocks_ro(scope TEXT, holder TEXT, expires_at TEXT)")
    conn.execute("INSERT INTO slocks(scope, holder, expires_at) VALUES ('lane:x', 'some-session-1', ?)",
                 (expires_at,))
    conn.commit()
    conn.close()
    monkeypatch.setattr(pw, "WQ_DB", str(db))
    return root


def test_unexpired_slock_blocks_scratch_worktree(tmp_path, repo, monkeypatch):
    """The NULL-expiry case above never reaches the parser. This one does."""
    root = scratch_worktree_with_slock(
        tmp_path, repo, monkeypatch, "myrepo-feat-wq9017-thing", "feat/wq9017-thing",
        wq_ts(hours=1))
    [r] = pw.run([str(root)], apply=False, fetch=False, grace_days=0)
    assert r["action"] == "skip"
    assert "live slock" in r["reason"]


def test_expired_slock_does_not_block_scratch_worktree(tmp_path, repo, monkeypatch):
    """wq#2669: the branch wq#2644 proved was dead. An expired slock, in the
    format bin/slock writes, must not exempt a merged scratch worktree."""
    root = scratch_worktree_with_slock(
        tmp_path, repo, monkeypatch, "myrepo-feat-wq9018-thing", "feat/wq9018-thing",
        wq_ts(hours=-1))
    [r] = pw.run([str(root)], apply=False, fetch=False, grace_days=0)
    assert r["action"] == "prune"
    assert "no live slock" in r["reason"]


def test_scratch_worktree_with_no_owner_record_is_prunable(tmp_path, repo, monkeypatch):
    root = tmp_path / "scratch" / "worktrees"
    add_worktree(repo, root, "myrepo-feat-orphan", "feat/orphan")
    monkeypatch.setattr(pw, "WQ_DB", str(tmp_path / "does-not-exist.db"))
    [r] = pw.run([str(root)], apply=False, fetch=False, grace_days=0)
    assert r["action"] == "prune"
    assert "no owner record" in r["reason"]


def test_apply_actually_removes_and_dry_run_does_not(tmp_path, repo):
    root = tmp_path / "worktrees"
    wt = add_worktree(repo, root, "wq9009-a1", "feat/wq9009-a1-done")

    [r] = pw.run([str(root)], apply=False, fetch=False, grace_days=0)
    assert r["action"] == "prune"
    assert r["bytes"] > 0
    assert os.path.isdir(wt), "dry-run must never remove anything"

    [r] = pw.run([str(root)], apply=True, fetch=False, grace_days=0)
    assert r["action"] == "prune"
    assert r["removed"] is True
    assert r["bytes"] > 0, "size must be measured before removal, not after"
    assert not os.path.isdir(wt)


def test_merged_worktree_within_grace_period_is_skipped(tmp_path, repo):
    """wq#2909: a branch that merged moments ago must not be pruned under the
    default grace period, even though it already passes clean+merged."""
    root = tmp_path / "worktrees"
    add_worktree(repo, root, "wq9020-a1", "feat/wq9020-a1-just-merged")
    [r] = pw.run([str(root)], apply=False, fetch=False)  # default grace_days=7
    assert r["action"] == "skip"
    assert "grace period" in r["reason"]


def test_merged_worktree_past_grace_period_is_prunable(tmp_path, repo):
    """Same shape, real git dates: committed 20 days ago AND landed 10 days ago,
    so the landing clock itself is past a 7-day grace period. Replaces a version
    that reached 8 days by monkeypatching the age function, which never
    exercised git ancestry at all (the wq#2934 test-suite gap)."""
    root = tmp_path / "worktrees"
    land_late(repo, root, "wq9021-a1", "feat/wq9021-a1-old-merge",
              commit_days_ago=20, land_days_ago=10)
    [r] = pw.run([str(root)], apply=False, fetch=False)
    assert r["action"] == "prune", r["reason"]


def test_late_landing_gets_the_full_grace_period(tmp_path, repo):
    """wq#2934 regression, the contradiction wq#2942 fixes. A branch whose only
    commit is 10 days old — work done, then blocked — but which lands RIGHT NOW
    must get a fresh 7-day revert window. Under the tip-commit clock it read as
    10 days old and was prunable the instant the merge landed: zero protection.
    The reason must name the landing commit, not the tip, or the right answer
    arrived via the wrong clock."""
    root = tmp_path / "worktrees"
    land_late(repo, root, "wq9023-a1", "feat/wq9023-a1-blocked-then-landed",
              commit_days_ago=10, land_days_ago=0)
    [r] = pw.run([str(root)], apply=False, fetch=False)  # default grace_days=7
    assert r["action"] == "skip", r["reason"]
    assert "grace period" in r["reason"]
    assert "landing commit" in r["reason"]


def test_landing_age_diverges_from_tip_commit_age(tmp_path, repo):
    """The two clocks, side by side on one branch: the tip reads 10 days, the
    landing reads ~0. wq#2909 used the first; only the second answers 'how long
    has a revert had to find this worktree'."""
    root = tmp_path / "worktrees"
    branch = "feat/wq9024-a1-two-clocks"
    land_late(repo, root, "wq9024-a1", branch, commit_days_ago=10, land_days_ago=0)
    tip_age = (pw._now_utc_naive() - pw.commit_ts(str(repo), branch)).total_seconds() / 86400
    age, source = pw.landing_age_days(str(repo), branch, "main", "wq9024-a1")
    assert 9.9 < tip_age < 10.1
    assert age < 0.1, (age, source)
    assert source.startswith("landing commit")


def test_fast_forward_landing_falls_back_to_wq_done_at(tmp_path, repo, monkeypatch):
    """The one case git cannot date: a fast-forward whose branch tip is still
    origin/main's tip, so no commit anywhere records the landing. For a
    `wqNNNN-aN` worktree the ticket's own `done_at` covers it — here the work
    was committed 10 days ago but the ticket only closed now."""
    root = tmp_path / "worktrees"
    land_late(repo, root, "wq9025-a1", "feat/wq9025-a1-ff-just-closed",
              commit_days_ago=10, land_days_ago=0, ff=True)
    wq_db_with_done_at(tmp_path, monkeypatch, 9025, wq_ts(minutes=-5))
    [r] = pw.run([str(root)], apply=False, fetch=False)
    assert r["action"] == "skip", r["reason"]
    assert "grace period" in r["reason"]
    assert "done_at" in r["reason"]


def test_fast_forward_landing_with_an_old_done_at_is_prunable(tmp_path, repo, monkeypatch):
    """Same fast-forward, same absent git evidence — but the ticket closed 10
    days ago, so the grace period really has elapsed. Without this the test
    above would also pass a fix that simply never prunes."""
    root = tmp_path / "worktrees"
    land_late(repo, root, "wq9026-a1", "feat/wq9026-a1-ff-long-closed",
              commit_days_ago=10, land_days_ago=0, ff=True)
    wq_db_with_done_at(tmp_path, monkeypatch, 9026, wq_ts(days=-10))
    [r] = pw.run([str(root)], apply=False, fetch=False)
    assert r["action"] == "prune", r["reason"]


def test_done_at_wins_over_an_older_landing_commit(tmp_path, repo, monkeypatch):
    """Both clocks readable and disagreeing: landed 10 days ago, but the ticket
    was reopened and only closed now. The LATEST evidence wins, because the
    smallest age is the most protection — a reopened ticket has had no week."""
    root = tmp_path / "worktrees"
    land_late(repo, root, "wq9027-a1", "feat/wq9027-a1-reopened",
              commit_days_ago=20, land_days_ago=10)
    wq_db_with_done_at(tmp_path, monkeypatch, 9027, wq_ts(minutes=-5))
    [r] = pw.run([str(root)], apply=False, fetch=False)
    assert r["action"] == "skip", r["reason"]
    assert "done_at" in r["reason"]


def test_late_landing_protects_a_non_fworker_scratch_worktree(tmp_path, repo, monkeypatch):
    """Out of the population the fix was designed around (wq#2630): a
    lumen-pr scratch worktree, whose name is not `wqNNNN-aN`, so no `done_at`
    can ever be read for it. The landing-commit clock has to carry that case on
    its own or the fix only ever protected fworker worktrees."""
    root = tmp_path / "scratch" / "worktrees"
    wt_name = "myrepo-feat-wq9028-late"
    land_late(repo, root, wt_name, "feat/wq9028-late",
              commit_days_ago=10, land_days_ago=0)
    owners = root / ".owners"
    os.makedirs(owners, exist_ok=True)
    (owners / wt_name).write_text("agent=some-session-1\ncreated=now\n")
    monkeypatch.setattr(pw, "WQ_DB", str(tmp_path / "no-such.db"))
    [r] = pw.run([str(root)], apply=False, fetch=False)
    assert r["action"] == "skip", r["reason"]
    assert "grace period" in r["reason"]
    assert "landing commit" in r["reason"]


def test_unreadable_commit_date_fails_closed(tmp_path, repo, monkeypatch):
    """No source can date the landing: the real fail-closed branch, reached by
    breaking the commit-date read rather than by stubbing out the age function."""
    root = tmp_path / "worktrees"
    add_worktree(repo, root, "wq9022-a1", "feat/wq9022-a1-noage")
    monkeypatch.setattr(pw, "commit_ts", lambda repo, rev: None)
    monkeypatch.setattr(pw, "WQ_DB", str(tmp_path / "no-such.db"))
    [r] = pw.run([str(root)], apply=False, fetch=False, grace_days=0)
    assert r["action"] == "skip"
    assert "landing date unreadable" in r["reason"]
    assert "fail closed" in r["reason"]


def test_full_cli_dry_run_reports_json(tmp_path, repo, capsys):
    root = tmp_path / "worktrees"
    add_worktree(repo, root, "wq9010-a1", "feat/wq9010-a1-done")
    log = tmp_path / "log.jsonl"
    rc = pw.main(["--roots", str(root), "--no-fetch", "--grace-days", "0",
                  "--json", "--log", str(log)])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["summary"]["prunable"] == 1
    assert out["summary"]["mode"] == "dry-run"
    assert log.exists()
    logged = json.loads(log.read_text().strip().splitlines()[-1])
    assert logged["summary"]["prunable"] == 1
