"""capability_replay.py — BROKER_WIRING.md §4 row (iv), live drill half (wq#1438).

The row asks for "a --dry-run replay of wq#1401/1402/1403/1407 landing in the predicted
column with zero spawns". All four are `blocked`, so a real dispatcher pass refuses them
at `not_open` before stage 3.7 is reached — the replay harness asks stage 3.7 directly.

Two layers here. The synthetic rows carry the same shape as the real four and pin the
predicted column deterministically (no queue, no fleet). The live-queue test is the
drill itself, skipped when the queue is absent so this file still runs anywhere.
"""
import os
import sys

import pytest

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
DISPATCHER_DIR = os.path.dirname(TESTS_DIR)
REPO = os.path.dirname(DISPATCHER_DIR)
sys.path.insert(0, DISPATCHER_DIR)
sys.path.insert(0, os.path.join(REPO, "harness"))
import capability_lint  # noqa: E402
import capability_replay  # noqa: E402

LIVE_DB = "/home/lumen/agents/work-queue/queue.db"
ROSTER = capability_lint.Roster(names=["lumen", "isis", "thor"], ips={})


def fresh_sweep():
    """wq#1482: a fresh, hitless read_dir_sweep.json — this file's corpus is about
    the allow-list gate (wq#1441), not the sweep gate, so replay() should see the
    same "sweep just ran clean" state `foreman_dispatcher` sees once the timer has
    run at least once."""
    import datetime
    policy = capability_lint.load_policy()
    ts = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    roots = {r: {"files": 0, "hits": []}
             for r in (policy.get("allow") or {}).get("read_dirs") or []}
    return {"ts": ts, "roots": roots}

# The four tickets' real shapes: a cited path outside every add_dirs, and — for 1401
# only — a v2 verb in the same detail. Text trimmed to the parts stage 3.7 reads.
CORPUS = [
    (1401, "Maintenance-window scheduler",
     "doc /home/lumen/instructions/playbooks/maintenance-window.md ... and REBOOTS "
     "only if boot-safety triple-checked; reboot the node afterwards",
     "dispatchable"),   # wq#2131: 'reboot' mid-sentence is a prose mention -> advisory
    # wq#1441: deny is the default. /home/lumen/agent carries live tokens (agent/.env
    # + .env.bak.*) and backups/ is not a swept read root — neither gets an auto grant.
    # wq#2131: that is an advisory now, not a needs_unresolved hold; wq#2184: and preflight
    # honours the same advisory bucket, so the ticket dispatches with no grant for the dir.
    # A cited path UNDER allow.read_dirs (.npm-global/bin, research_prompts) lands
    # dispatchable with the dir tag.
    (1402, "agent notes", "read the config under /home/lumen/agent", "dispatchable"),
    (1403, "wq proposal backups",
     "write to /home/lumen/backups/wq-proposals nightly", "dispatchable"),
    (1407, "Fix preflight claude spawn under systemd PATH",
     "PATH lacks /home/lumen/.npm-global/bin so every auto-reprove fails",
     "dispatchable"),
    (9999, "allowed read root",
     "read /home/lumen/research_prompts and summarise", "dispatchable"),
]


@pytest.fixture(autouse=True)
def _apply_dir(monkeypatch):
    monkeypatch.setenv("FOREMAN_NEEDS_LINT_MODE", "apply-dir")


@pytest.mark.parametrize("tid,title,detail,column", CORPUS,
                         ids=[str(c[0]) for c in CORPUS])
def test_each_ticket_lands_in_the_predicted_column(tid, title, detail, column):
    t = {"id": tid, "title": title, "detail": detail, "needs": None,
         "archetype": "standard-engineering", "status": "blocked"}
    r = capability_replay.replay(t, repo="/home/lumen/foreman", roster=ROSTER,
                                 sweep=fresh_sweep())
    assert r["column"] == column
    if tid in (1402, 1403):
        assert r["advisory"] and not r["apply"]   # advisory path: NO grant, nothing written
    elif column == "dispatchable":
        # the dir tag is what makes the path reachable; the replay names what the
        # dispatcher WOULD write, and writes nothing itself.
        assert r["apply"] and all(a.startswith("dir(") for a in r["apply"])


def test_1401_names_the_v2_verb_that_holds_it():
    """The doc's §4 row-iv prediction ("1401 -> auto dir") predates the lint's v2-verb
    bucket. §2.4 is the governing text: a ticket that needs `reboot` stays
    needs_unresolved with the missing verb named, "which is the honest state" — when
    the verb is an instruction (wq#2131: fenced code, a list item, an imperative
    line). The dir tag is still derived — it is simply not applied to a ticket being
    skipped."""
    tid, title, detail, _ = CORPUS[0]
    r = capability_replay.replay({"id": tid, "title": title,
                                  "detail": detail + "\n- reboot the node afterwards",
                                  "needs": None, "archetype": "standard-engineering"},
                                 repo="/home/lumen/foreman", roster=ROSTER,
                                 sweep=fresh_sweep())
    assert r["column"] == "needs_unresolved" and "reboot" in r["detail"]
    assert r["tags"] == ["dir(/home/lumen/instructions/playbooks)"]
    assert "apply" not in r and not r["lint_clear"]


def test_1401_prose_mention_of_the_verb_dispatches_with_an_advisory():
    tid, title, detail, _ = CORPUS[0]
    r = capability_replay.replay({"id": tid, "title": title, "detail": detail,
                                  "needs": None, "archetype": "standard-engineering"},
                                 repo="/home/lumen/foreman", roster=ROSTER,
                                 sweep=fresh_sweep())
    assert r["column"] == "dispatchable" and r["lint_clear"]
    assert r["apply"] == ["dir(/home/lumen/instructions/playbooks)"]
    assert len(r["advisory"]) == 1 and "'reboot'" in r["advisory"][0]


def test_secret_citations_stay_refused_in_the_replay():
    for tid, detail in [(1466, "rotate /home/lumen/.secrets/gatus-external.env"),
                        (1491, "the password lives in /home/lumen/.fleet_pw"),
                        (1619, "edit /home/lumen/agent/.env"),
                        (1690, "copy /home/lumen/.netrc over")]:
        r = capability_replay.replay({"id": tid, "title": "t", "detail": detail,
                                      "needs": None, "archetype": "standard-engineering"},
                                     repo="/home/lumen/foreman", roster=ROSTER,
                                     sweep=fresh_sweep())
        assert r["column"] == "capability_denied_by_policy", (tid, r)
        assert not r["lint_clear"]


def test_the_replay_prints_the_advisory_bucket(capsys, monkeypatch, tmp_path):
    import sqlite3
    db = tmp_path / "q.db"
    con = sqlite3.connect(db)
    con.execute("create table tasks (id integer, lane text, status text, title text, "
                "detail text, needs text, archetype text)")
    con.execute("insert into tasks values (7, 'foreman', 'open', 't', "
                "'compare with /home/lumen/instructions/playbooks/maintenance-window.md "
                "and the old flow needed a reboot', NULL, 'standard-engineering')")
    con.commit()
    con.close()
    monkeypatch.setattr(capability_lint, "load_roster", lambda *a, **k: ROSTER)
    monkeypatch.setattr(capability_lint, "load_sweep", lambda *a, **k: fresh_sweep())
    monkeypatch.setattr(capability_replay.router, "repo_for_ticket",
                        lambda t: "/home/lumen/foreman")
    assert capability_replay.main(["--db", str(db), "7"]) == 0
    out = capsys.readouterr().out
    assert "dispatchable" in out and "+advisory" in out and "lint_clear=True" in out
    assert "advisory: 'reboot' is a v2 verb" in out
    assert capability_replay.main(["--db", str(db), "--json", "7"]) == 0
    assert '"advisory": ["\'reboot\'' in capsys.readouterr().out


def test_the_replay_never_writes(monkeypatch):
    """No third writer: the dispatcher owns the `needs` column (§2.1/§3)."""
    import capability
    monkeypatch.setattr(capability, "_wq",
                        lambda *a: pytest.fail(f"replay shelled out to wq {a}"))
    for tid, title, detail, _ in CORPUS:
        capability_replay.replay({"id": tid, "title": title, "detail": detail,
                                  "needs": None, "archetype": None},
                                 repo="/home/lumen/foreman", roster=ROSTER)


@pytest.mark.skipif(not os.path.exists(LIVE_DB), reason="no live queue on this host")
def test_live_replay_of_the_four_stuck_tickets():
    """The drill on the real rows. Under wq#1441's explicit allow list 1402/1403 get no
    auto grant for the dirs they cite (not swept read roots); wq#2131 makes that an
    advisory instead of a needs_unresolved hold, so what the replay may say about them
    is `cited_path_unreachable` (preflight, the worker's real reach) or `dispatchable`
    — never `needs_unresolved` for an advisory-only ticket. Nothing is spawned and
    nothing is written — this module has no code path that can do either."""
    tickets = capability_replay.load_tickets([1401, 1402, 1403, 1407], LIVE_DB)
    assert {t["id"] for t in tickets} == {1401, 1402, 1403, 1407}
    got = {t["id"]: capability_replay.replay(t, sweep=fresh_sweep()) for t in tickets}
    for tid in (1401, 1402, 1403, 1407):
        assert got[tid]["column"] in ("dispatchable", "cited_path_unreachable"), (tid, got[tid])
        assert got[tid]["unresolved"] == [] and got[tid]["lint_clear"], (tid, got[tid])
    assert any("allow.read_dirs" in a for a in got[1402]["advisory"])
