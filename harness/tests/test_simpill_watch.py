"""harness/simpill_watch.py — wq#2242. No network: a fake GitHub API, a fake `wq`, and real git
repos in tmp_path for the clone/fetch/fork-remote path.

The PROOF shape: a synthetic event files exactly one ticket, a repeat files nothing, a first-seen
repo is baselined, a merged PR and the push it produced are one event, a failed filing retries.
"""
import json
import os
import subprocess
import sys

import pytest

HARNESS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HARNESS_DIR)
import simpill_watch as sw  # noqa: E402
import repo_sweep  # noqa: E402

SHA1 = "a" * 40
SHA2 = "b" * 40


def make_cfg(tmp_path, **kw):
    cfg = {"owner": "SkinnnyJay", "clone_root": str(tmp_path / "clones"), "fork_owner": None,
           "us": [], "repos": ["simpill-utils"]}
    cfg.update(kw)
    return cfg


class FakeWq:
    def __init__(self, rc=0, out="task 9001 added"):
        self.calls, self.rc, self.out = [], rc, out

    def __call__(self, args):
        self.calls.append(args)
        return self.rc, self.out


def push(repo, sha, msg="fix: thing"):
    return sw._event({}, repo, "push", sha, msg, f"https://github.com/x/{repo}/commit/{sha}", msg)


def test_first_sight_baselines_and_files_nothing(tmp_path):
    cfg, state, wq = make_cfg(tmp_path), {}, FakeWq()
    assert sw.process_repo(cfg, "simpill-utils", [push("simpill-utils", SHA1)], state, wq) == []
    assert wq.calls == [] and state["simpill-utils"]["baselined"] is True


def test_one_new_event_files_exactly_one_ticket_and_a_repeat_files_nothing(tmp_path):
    cfg, state, wq = make_cfg(tmp_path), {}, FakeWq()
    sw.process_repo(cfg, "simpill-utils", [push("simpill-utils", SHA1)], state, wq)
    events = [push("simpill-utils", SHA2), push("simpill-utils", SHA1)]
    assert sw.process_repo(cfg, "simpill-utils", events, state, wq) == [f"simpill-utils:push:{SHA2}"]
    assert len(wq.calls) == 1
    assert sw.process_repo(cfg, "simpill-utils", events, state, wq) == []
    assert len(wq.calls) == 1


def test_ticket_carries_lane_priority_needs_tag_and_the_contract(tmp_path):
    cfg = make_cfg(tmp_path, fork_owner="lumen-fork")
    args = sw.ticket_args(cfg, push("simpill-utils", SHA2, "fix: thing"))
    flag = lambda name: args[args.index(name) + 1]  # noqa: E731
    assert args[0] == "add" and flag("--lane") == "simpill" and flag("--pri") == "0"
    clone = str(tmp_path / "clones" / "simpill-utils")
    assert flag("--needs") == f"repo({clone})"
    detail = flag("--detail")
    for needle in ("EVALUATE", "TEST", "FIX", "DRAFT PR", "fork", clone,
                   "https://github.com/lumen-fork/simpill-utils.git", f"simpill-utils:push:{SHA2}"):
        assert needle in detail
    assert "[simpill] SkinnnyJay/simpill-utils push bbbbbbbb" in flag("--title")


def test_merged_pr_and_the_push_it_produced_are_one_ticket(tmp_path):
    cfg, state, wq = make_cfg(tmp_path), {}, FakeWq()
    sw.process_repo(cfg, "simpill-utils", [], state, wq)
    pr = sw._event(cfg, "simpill-utils", "pr_merged", 7, "add x", "u", "s",
                   covers=[sw.event_key("simpill-utils", "push", SHA2)])
    filed = sw.process_repo(cfg, "simpill-utils", [pr, push("simpill-utils", SHA2)], state, wq)
    assert filed == ["simpill-utils:pr_merged:7"] and len(wq.calls) == 1
    assert sw.process_repo(cfg, "simpill-utils", [pr, push("simpill-utils", SHA2)], state, wq) == []


def test_failed_filing_is_retried_next_poll(tmp_path):
    cfg, state = make_cfg(tmp_path), {}
    sw.process_repo(cfg, "simpill-utils", [], state, FakeWq())
    ev = [push("simpill-utils", SHA2)]
    assert sw.process_repo(cfg, "simpill-utils", ev, state, FakeWq(rc=1, out="boom")) == []
    assert sw.process_repo(cfg, "simpill-utils", ev, state, FakeWq()) == [f"simpill-utils:push:{SHA2}"]


def test_wq_duplicate_answer_counts_as_filed(tmp_path):
    cfg, state = make_cfg(tmp_path), {}
    sw.process_repo(cfg, "simpill-utils", [], state, FakeWq())
    dup = FakeWq(rc=1, out="DUPLICATE — task 5 [simpill/open] already has this title")
    assert sw.process_repo(cfg, "simpill-utils", [push("simpill-utils", SHA2)], state, dup)
    assert sw.process_repo(cfg, "simpill-utils", [push("simpill-utils", SHA2)], state, FakeWq()) == []


def test_dry_run_files_and_records_nothing(tmp_path):
    cfg, state, wq = make_cfg(tmp_path), {}, FakeWq()
    sw.process_repo(cfg, "simpill-utils", [], state, wq)
    assert sw.process_repo(cfg, "simpill-utils", [push("simpill-utils", SHA2)], state, wq, dry_run=True)
    assert wq.calls == [] and state["simpill-utils"]["seen"] == []


def test_seen_list_is_capped(tmp_path):
    seen = []
    sw._remember(seen, [f"k{i}" for i in range(sw.SEEN_CAP + 50)])
    assert len(seen) == sw.SEEN_CAP and seen[-1] == f"k{sw.SEEN_CAP + 49}"


class FakeApi:
    """Just enough of api.github.com for collect_events."""

    def __init__(self, head=SHA2, prs=(), issues=(), reviews=()):
        self.head, self.prs, self.issues, self.reviews = head, prs, issues, reviews

    def __call__(self, path):
        if path.endswith("/repos/SkinnnyJay/simpill-utils"):
            return {"default_branch": "main"}
        if "/commits/main" in path:
            return {"sha": self.head, "html_url": "u", "commit": {"message": "feat: y\n\nbody"}}
        if "/pulls?state=closed" in path:
            return list(self.prs)
        if "/pulls?state=open" in path:
            return [{"number": 3, "user": {"login": "lumen-fork"}, "html_url": "p"}]
        if path.endswith("/reviews"):
            return list(self.reviews)
        if "/issues?" in path:
            return list(self.issues)
        raise AssertionError(path)


def test_collect_events_covers_push_merged_pr_and_issue_but_not_prs_listed_as_issues(tmp_path):
    cfg = make_cfg(tmp_path)
    api = FakeApi(
        prs=[{"number": 7, "title": "t", "html_url": "u", "merged_at": "2026-09-20", "merge_commit_sha": SHA2,
              "user": {"login": "jay"}},
             {"number": 8, "title": "closed unmerged", "html_url": "u", "merged_at": None, "user": {"login": "j"}}],
        issues=[{"number": 11, "title": "bug", "body": "", "html_url": "u", "user": {"login": "j"}},
                {"number": 12, "title": "a pr", "pull_request": {}, "user": {"login": "j"}}])
    keys = [e["key"] for e in sw.collect_events(cfg, "simpill-utils", api)]
    assert keys == ["simpill-utils:pr_merged:7", f"simpill-utils:push:{SHA2}", "simpill-utils:issue:11"]


def test_issues_and_reviews_are_filtered_to_us_when_handles_are_configured(tmp_path):
    cfg = make_cfg(tmp_path, us=["lumen-fork"])
    api = FakeApi(
        issues=[{"number": 1, "title": "x", "body": "ping @lumen-fork", "html_url": "u", "user": {"login": "j"}},
                {"number": 2, "title": "y", "body": "nothing", "html_url": "u", "user": {"login": "j"}}],
        reviews=[{"id": 55, "state": "CHANGES_REQUESTED", "html_url": "r", "user": {"login": "jay"}}])
    keys = [e["key"] for e in sw.collect_events(cfg, "simpill-utils", api)]
    assert "simpill-utils:issue:1" in keys and "simpill-utils:issue:2" not in keys
    assert "simpill-utils:review:55" in keys


def test_run_once_end_to_end_with_events_override(tmp_path):
    cfg, state, wq = make_cfg(tmp_path), {}, FakeWq()
    calls = []
    git = lambda args, check=True: calls.append(args)  # noqa: E731
    base = {"simpill-utils": [push("simpill-utils", SHA1)]}
    assert sw.run_once(cfg, state, git=git, wq=wq, events_override=base) == ([], 0)
    new = {"simpill-utils": [push("simpill-utils", SHA2), push("simpill-utils", SHA1)]}
    assert sw.run_once(cfg, state, git=git, wq=wq, events_override=new) == ([f"simpill-utils:push:{SHA2}"], 0)
    assert sw.run_once(cfg, state, git=git, wq=wq, events_override=new) == ([], 0)
    assert len(wq.calls) == 1 and calls  # clones were synced every poll


def test_one_repo_erroring_does_not_stop_the_others(tmp_path):
    cfg = make_cfg(tmp_path, repos=["a", "b"])
    state, wq, msgs = {}, FakeWq(), []

    def api(path):
        if "/a" in path:
            raise OSError("down")
        raise KeyError("default_branch")

    filed, errors = sw.run_once(cfg, state, api=api, git=lambda *a, **k: None, wq=wq, log=msgs.append)
    assert filed == [] and errors == 2 and len(msgs) == 2


def git(*args, cwd=None):
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True,
                          env={**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
                               "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}).stdout


def test_sync_clone_clones_then_fetches_and_wires_the_fork_remote(tmp_path, monkeypatch):
    up = tmp_path / "up"
    up.mkdir()
    git("init", "-q", "-b", "main", cwd=up)
    (up / "f").write_text("1")
    git("add", ".", cwd=up)
    git("commit", "-qm", "one", cwd=up)
    monkeypatch.setattr(sw, "upstream_url", lambda cfg, repo: str(up))
    cfg = make_cfg(tmp_path, fork_owner="lumen-fork")
    path = sw.sync_clone(cfg, "simpill-utils")
    assert path == str(tmp_path / "clones" / "simpill-utils") and os.path.isdir(path + "/.git")
    assert git("remote", "get-url", "fork", cwd=path).strip() == "https://github.com/lumen-fork/simpill-utils.git"
    (up / "f").write_text("2")
    git("commit", "-qam", "two", cwd=up)
    sw.sync_clone(cfg, "simpill-utils")
    assert git("log", "--format=%s", cwd=path).split() == ["two", "one"]
    assert git("remote", cwd=path).split() == ["fork", "origin"]


def test_no_fork_owner_means_no_fork_remote(tmp_path, monkeypatch):
    up = tmp_path / "up"
    up.mkdir()
    git("init", "-q", "-b", "main", cwd=up)
    (up / "f").write_text("1")
    git("add", ".", cwd=up)
    git("commit", "-qm", "one", cwd=up)
    monkeypatch.setattr(sw, "upstream_url", lambda cfg, repo: str(up))
    path = sw.sync_clone(make_cfg(tmp_path), "simpill-utils")
    assert git("remote", cwd=path).split() == ["origin"]


def test_shipped_config_is_valid_and_its_clones_are_managed_repos():
    cfg = sw.load_config()
    assert {"simpill-utils", "interview-prep", "acp-llm-cli"} <= set(cfg["repos"])
    for repo in cfg["repos"]:
        assert sw.clone_path(cfg, repo) in repo_sweep.managed_repos()


@pytest.mark.parametrize("patch", [{"owner": ""}, {"repos": ["a/b"]}, {"repos": "x"}, {"fork_owner": 3}, {"us": "j"}])
def test_bad_config_is_rejected(tmp_path, patch):
    p = tmp_path / "c.json"
    p.write_text(json.dumps(make_cfg(tmp_path, **patch)))
    with pytest.raises(ValueError):
        sw.load_config(str(p))


def test_unreadable_config_manages_no_clones(tmp_path):
    assert sw.managed_clones(str(tmp_path / "missing.json")) == set()


def test_swept_repos_do_not_include_the_watchers_clones():
    swept = sorted(repo_sweep._lane_repos() | set(repo_sweep.EXTRA_REPOS))
    assert not any(p.startswith(sw.load_config()["clone_root"]) for p in swept)


def test_main_cli_synthetic_event_proof(tmp_path, monkeypatch, capsys):
    """CLI shape of the PROOF: baseline, then one synthetic event -> one ticket, repeat -> none."""
    cfgp = tmp_path / "c.json"
    cfgp.write_text(json.dumps(make_cfg(tmp_path)))
    events = tmp_path / "e.json"
    wq = FakeWq()
    monkeypatch.setattr(sw, "STATE", str(tmp_path / "state.json"))
    monkeypatch.setattr(sw, "_git", lambda *a, **k: None)
    monkeypatch.setattr(sw, "_wq", wq)

    def write(*idents):
        events.write_text(json.dumps({"simpill-utils": [
            {"kind": "push", "ident": i, "title": "t", "url": "u", "summary": "s"} for i in idents]}))

    write(SHA1)
    assert sw.main(["--config", str(cfgp), "--events", str(events)]) == 0
    assert wq.calls == []
    write(SHA1, SHA2)
    assert sw.main(["--config", str(cfgp), "--events", str(events)]) == 0
    assert sw.main(["--config", str(cfgp), "--events", str(events)]) == 0
    assert len(wq.calls) == 1
    assert capsys.readouterr().out.count("filed: simpill-utils:push:" + SHA2) == 1
