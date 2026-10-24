"""wq#4623: RepoResolve skips + pages a ticket whose repo tag dispatch has no row for, rather
than letting `repo_for_ticket(t) or g.repo` fall back to the lane's repo (wq#4608 got a
foreman worktree for a messenger ticket)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_gates_replay import _cfg, _decide, denv, fd, router, world  # noqa: E402,F401


def test_unknown_repo_tag_skips_and_pages_once(world, monkeypatch):
    monkeypatch.setattr(router, "repo_for_ticket", lambda t: None)
    world.add(1, "build", [])
    world.tickets[1]["detail"] = "repo: lumen/no-such-thing"
    ready, skips = _decide(world, _cfg(["build"]), 1, "build")
    assert ready is None
    assert [(s["stage"], s["reason"]) for s in skips] == [("capability", "repo_tag_unknown")]
    assert len(world.pings) == 1 and "no-such-thing" in world.pings[0][0]


def test_known_repo_tag_dispatches_on_that_repo(world, monkeypatch):
    monkeypatch.setattr(router, "repo_for_ticket", lambda t: router.REPO_TAGS["messenger"])
    world.add(1, "build", [])
    ready, skips = _decide(world, _cfg(["build"]), 1, "build")
    assert skips == [] and ready["repo"] == "/home/lumen/messenger"


def test_no_repo_tag_keeps_the_lane_default(world, monkeypatch):
    monkeypatch.setattr(router, "repo_for_ticket", lambda t: None)
    world.add(1, "build", [])
    ready, skips = _decide(world, _cfg(["build"]), 1, "build")
    assert skips == [] and ready["repo"] == "/repo/build"
