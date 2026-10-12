"""Episode bookkeeping across wq + aq (wq#3734): a DUPLICATE refusal from wq is the
open ticket for the episode, and resolving an episode closes its aq row."""
import selfheal_common as common
from selfheal_fakes import Proc


def outlets(replies):
    ran = []

    def runner(argv, **kw):
        ran.append(argv)
        return replies(argv)
    return common.Outlets(wq="wq", aq="aq", ntfy="ntfy", runner=runner), ran


def test_wq_duplicate_refusal_returns_the_open_ticket():
    # wq exits 1 and names the open ticket that already carries this title
    out, _ = outlets(lambda a: Proc("DUPLICATE — task 3625 [build/open] already has "
                                    "this title:\n  [selfheal] x\nUse --force to add "
                                    "anyway.", returncode=1))
    assert out.file_ticket("[selfheal] x", "d", "build", "p1") == "3625"


def test_wq_other_failures_still_return_none():
    out, _ = outlets(lambda a: Proc("REFUSED: archetype", returncode=1))
    assert out.file_ticket("t", "d", "build", "p1") is None
    out, _ = outlets(lambda a: None)
    assert out.file_ticket("t", "d", "build", "p1") is None
    out, _ = outlets(lambda a: Proc("added task 42 [build/p1]"))
    assert out.file_ticket("t", "d", "build", "p1") == "42"


def test_alert_returns_aq_row_id_and_resolve_alert_calls_aq():
    out, ran = outlets(lambda a: Proc("queued id=2671277\n"))
    assert out.alert("k", "t", "m") == "2671277"
    out.resolve_alert("2671277", "cleared")
    assert ran[-1] == ["aq", "resolve", "2671277", "cleared"]
    out, _ = outlets(lambda a: None)
    assert out.alert("k", "t", "m") is None


def test_duplicate_episode_is_recorded_so_the_next_sweep_does_not_refile(make_ctx):
    """The 2026-10-08 loop: wq refused the duplicate, file_once kept nothing, and every
    30-min sweep re-filed + re-bumped aq + re-paged urgent (x14)."""
    def replies(argv):
        if "wq" in argv:
            return Proc("DUPLICATE — task 3625 [build/open] already has this title",
                        returncode=1)
        return Proc("queued id=2671277")
    out, ran = outlets(replies)
    state = {}
    for i in range(3):
        ctx = make_ctx({}, state, now=1_000_000.0 + 1800 * i)
        ctx.out = out
        ctx.file_once("providers", "provider-gateway-key", "t", "d", page="urgent")
    # once, not once per sweep (as root, wq runs behind `runuser -u lumen --`)
    assert [next(x for x in a if x in ("wq", "aq", "ntfy")) for a in ran] == \
        ["wq", "aq", "ntfy"]
    assert state["tickets"]["provider-gateway-key"] == {
        "id": "3625", "at": 1_000_000, "title": "t", "aq": "2671277"}


def test_resolve_closes_the_episodes_aq_row(make_ctx):
    ctx = make_ctx({})
    ctx.file_once("nodes", "node-offline:ptah", "t", "d")
    assert ctx.state["tickets"]["node-offline:ptah"]["aq"] == "7001"
    ctx.resolve("nodes", "node-offline:ptah", "ptah back")
    assert ctx.out.resolved == ["7001"]
    assert ctx.out.pages == [("low", "fleet-selfheal: node-offline:ptah cleared")]


def test_resolve_without_msg_still_closes_the_row_and_dry_run_closes_nothing(make_ctx):
    ctx = make_ctx({})
    ctx.file_once("providers", "provider-gateway-key", "t", "d")
    ctx.resolve("providers", "provider-gateway-key")
    assert ctx.out.resolved == ["7001"] and ctx.out.pages == []
    state = {"tickets": {"k": {"id": "1", "aq": "5"}}, "alerts": {"a": "6"}}
    ctx = make_ctx({}, state, dry_run=True)
    ctx.resolve("c", "k")
    ctx.clear_alert("a", "n")
    assert ctx.out.resolved == [] and state["alerts"] == {"a": "6"}
