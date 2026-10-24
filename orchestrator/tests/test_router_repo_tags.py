"""wq#4623 (ISSUE-LOG #1565): a ticket names its repo with a leading REPO_TAGS `[tag]` or a
line-start `repo:` field; an unknown repo tag refuses instead of falling back to the lane's
repo — wq#4608 ([messenger], lane build) was spawned in a foreman worktree."""
import os
import sys

import pytest

ORCH_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ORCH_DIR)
import router  # noqa: E402

MESSENGER = "/home/lumen/messenger"
POKEMON = "/home/lumen/pokemon"

# wq#4608 as filed (title + detail head, verbatim from `wq show 4608`).
WQ4608 = {
    "lane": "build", "needs": "", "deliverable": None,
    "title": "[messenger] F-1: tailnet GET /wq route (read-only queue.db) for CCC",
    "detail": "Repo lumen/lumen-messenger (ALLOWed); file server_v2.py (live copy "
              "/home/lumen/messenger/server_v2.py, deploy = lumen-messenger.service). "
              "Add @app.get(\"/wq\") ...",
}


@pytest.fixture(autouse=True)
def hermetic(monkeypatch, tmp_path):
    monkeypatch.setattr(router.repo_sweep, "managed_repos",
                        lambda: {router.FOREMAN_ROOT, "/home/lumen/bin", POKEMON})
    # REPO_TAG_ROOT is a scratch dir: only repos made here count as "git repo" tags.
    monkeypatch.setattr(router, "REPO_TAG_ROOT", str(tmp_path))
    return tmp_path


def git_repo(root, name):
    os.makedirs(os.path.join(str(root), name, ".git"))


def t(**kw):
    base = {"lane": "build", "title": "x", "detail": "", "deliverable": None, "needs": ""}
    base.update(kw)
    return base


def test_wq4608_dispatches_into_messenger_not_foreman():
    assert router.repo_mismatch(WQ4608) is None
    assert router.repo_for_ticket(WQ4608) == MESSENGER


@pytest.mark.parametrize("tag", sorted(router.REPO_TAGS))
def test_every_table_tag_routes_to_its_repo(tag):
    tk = t(title=f"[{tag}] do the thing")
    assert router.repo_for_ticket(tk) == router.REPO_TAGS[tag]


def test_table_paths_are_the_ticket_spec():
    assert router.REPO_TAGS == {
        "ccc": "/home/lumen/ccc", "messenger": MESSENGER, "foreman": "/home/lumen/foreman",
        "bin": "/home/lumen/bin", "minerva": "/home/lumen/minerva"}


@pytest.mark.parametrize("value", ["messenger", "lumen/lumen-messenger",
                                   "lumen/lumen-messenger.git", "/home/lumen/messenger",
                                   "/home/lumen/messenger/server_v2.py", "`messenger`"])
def test_repo_field_forms(value):
    tk = t(title="F-1: tailnet route", detail=f"Context first.\nrepo: {value}\nmore")
    assert router.ticket_repo_tag(tk) == (MESSENGER, None)
    assert router.repo_for_ticket(tk) == MESSENGER


def test_repo_field_outranks_title_tag():
    tk = t(title="[ccc] wire the panel", detail="Repo: messenger")
    assert router.repo_for_ticket(tk) == MESSENGER


def test_repo_field_may_name_a_managed_lane_repo_by_path():
    tk = t(title="x", detail="repo: /home/lumen/pokemon/agent")
    assert router.ticket_repo_tag(tk) == (POKEMON, None)


def test_unknown_repo_field_refuses_instead_of_lane_default():
    tk = t(title="x", detail="repo: lumen/no-such-thing")
    repo, unknown = router.ticket_repo_tag(tk)
    assert repo is None and "no-such-thing" in unknown
    assert router.repo_for_ticket(tk) is None


def test_unknown_title_tag_that_is_a_git_repo_refuses(hermetic):
    # the pre-fix shape of wq#4608: a repo-named tag with no table row
    git_repo(hermetic, "dify")
    tk = t(title="[dify] bump the image")
    repo, unknown = router.ticket_repo_tag(tk)
    assert repo is None and "[dify]" in unknown
    assert router.repo_for_ticket(tk) is None


def test_topical_title_tags_are_not_repo_tags(hermetic):
    # [sec]/[rotation]/[judgment] are topical: no git repo by that name -> lane default
    for title in ("[sec] rotate", "[rotation] cf token", "[judgment] verify x", "[a/b] y"):
        tk = t(title=title)
        assert router.ticket_repo_tag(tk) == (None, None)
        assert router.repo_for_ticket(tk) == router.FOREMAN_ROOT


def test_lane_marker_tag_is_still_wq433_not_unknown(hermetic):
    # [pokemon] is a lane marker: repo_mismatch owns it, the unknown-tag rule must not
    git_repo(hermetic, "pokemon")
    tk = t(title="[pokemon] fix", lane="build")
    assert router.ticket_repo_tag(tk) == (None, None)
    assert router.repo_mismatch(tk)


def test_verify_tickets_are_exempt():
    tk = t(title="[verify] wq#4608 [messenger] route",
           detail="Verify repo: /home/lumen/messenger\nrepo: nonsense")
    assert router.ticket_repo_tag(tk) == (None, None)


def test_verify_repo_line_is_not_a_repo_field():
    tk = t(title="x", detail="Verify repo: /home/lumen/bin")
    assert router.ticket_repo_tag(tk) == (None, None)


def test_tag_disagreeing_with_a_repo_lane_is_a_mismatch():
    tk = t(title="[messenger] thing", lane="pokemon")
    assert "messenger" in router.repo_mismatch(tk)
    assert router.repo_for_ticket(tk) is None


def test_deliverable_still_outranks_title_tag():
    tk = t(title="[ccc] research", detail="Deliverable: /home/lumen/foreman/research/X.md")
    assert router.repo_for_ticket(tk) == router.FOREMAN_ROOT


def test_dispatch_cmd_names_the_unknown_tag(monkeypatch):
    monkeypatch.setattr(router, "wq_show", lambda tid: t(title="x", detail="repo: zzz"))
    with pytest.raises(SystemExit, match="unknown repo tag"):
        router.dispatch_cmd(1)
