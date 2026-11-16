"""wq#5009 — park-and-resume rounds (harness/park.py): decision-card protocol, round state
machine, answer routing through the real `wq` CLI against a scratch queue DB."""
import itertools
import json
import os
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import park

WQ_BIN = "/home/lumen/bin/wq"
ASK = {"title": "Rotate the Gitea token now or after the freeze?",
       "text": "Old token still authenticates. Rotating restarts messenger.",
       "options": ["rotate now", "after freeze", "leave it"], "recommend": "after freeze"}


def card_text(ask=ASK, tail="\nEXIT_SIGNAL: false\nPARKED"):
    return "I need a decision.\n```foreman-park\n" + json.dumps({"ask": ask}) + "\n```" + tail


# ------------------------------------------------------------------ card protocol

def test_no_card_is_not_an_error():
    assert park.parse_card("all landed.\nEXIT_SIGNAL: true\nTICKET_CLOSED") == (None, None)
    assert park.parse_card(None) == (None, None)
    assert park.parse_card("```json\n{\"ask\": {}}\n```") == (None, None)   # wrong fence


def test_valid_card_is_normalised():
    ask, err = park.parse_card(card_text())
    assert err is None and ask["options"] == ASK["options"] and ask["recommend"] == "after freeze"
    ask, err = park.parse_card(card_text({"title": "  Pick   one ", "text": " why "}))
    assert err is None and ask == {"title": "Pick one", "text": "why", "options": [],
                                   "recommend": None, "key": None}


def test_last_card_block_wins():
    first = dict(ASK, title="first draft")
    text = card_text(first, tail="") + "\nrevised:\n" + card_text()
    assert park.parse_card(text)[0]["title"] == ASK["title"]


@pytest.mark.parametrize("raw,needle", [
    ("{not json", "not valid JSON"),
    ('["ask"]', 'JSON object with an "ask" key'),
    ('{"notice": {}}', 'JSON object with an "ask" key'),
    ('{"ask": "do it"}', "must be a JSON object"),
    ('{"ask": {"text": "x"}}', "`ask.title` is required"),
    ('{"ask": {"title": "x"}}', "`ask.text` is required"),
    ('{"ask": {"title": "x", "text": "y", "options": "a,b"}}', "list of non-empty strings"),
    ('{"ask": {"title": "x", "text": "y", "options": ["a", ""]}}', "list of non-empty strings"),
    ('{"ask": {"title": "x", "text": "y", "options": ["a", "A"]}}', "must be distinct"),
    ('{"ask": {"title": "x", "text": "y", "options": ["a"], "recommend": "b"}}',
     "must be one of"),
    ('{"ask": {"title": "x", "text": "y", "options": ["1","2","3","4","5","6","7"]}}',
     "at most 6 options"),
    ('{"ask": {"title": "' + "t" * 121 + '", "text": "y"}}', "<= 120 chars"),
])
def test_invalid_cards_name_the_fault(raw, needle):
    ask, err = park.parse_card("```foreman-park\n" + raw + "\n```")
    assert ask is None and needle in err
    assert needle in park.rewrite_text(err) and "foreman-park" in park.rewrite_text(err)


@pytest.mark.parametrize("answer,choice,free", [
    ("after freeze", "after freeze", None),
    ("AFTER FREEZE ", "after freeze", None),
    ("2", "after freeze", None), ("#3", "leave it", None), ("1.", "rotate now", None),
    ("4", None, "4"), ("0", None, "0"),                       # out of range: never guessed
    ("after the freeze, and tell me first", None, "after the freeze, and tell me first"),
    ("rotate", None, "rotate"),                               # no prefix matching
])
def test_answer_matching_never_guesses(answer, choice, free):
    assert park.match_answer(park.new_card(ASK), answer) == (choice, free)


def test_prompts_carry_the_decision():
    card = park.new_card(ASK)
    dp = park.decision_prompt(card, 77)
    assert "2. after freeze" in dp and "park.py answer 77" in dp and dp.startswith(ASK["title"])
    card.update(choice="leave it")
    assert "Frank chose: leave it" in park.resume_prompt(card, 77)
    card.update(choice=None, answer="ask me monday")
    assert "free text): ask me monday" in park.resume_prompt(card, 77)
    assert "Do not ask again" in park.context_block(card, 77)


# ------------------------------------------------------------------ state machine

def test_round_transitions_exhaustive():
    states = (park.RUNNING, park.PARKED, park.ANSWERED, park.RESUMED, park.CANCELLED)
    legal = {(park.RUNNING, park.PARKED), (park.PARKED, park.ANSWERED),
             (park.PARKED, park.CANCELLED), (park.ANSWERED, park.RESUMED),
             (park.ANSWERED, park.CANCELLED), (park.RESUMED, park.PARKED),
             (park.RESUMED, park.RUNNING), (park.RESUMED, park.CANCELLED),
             (park.CANCELLED, park.PARKED), (park.CANCELLED, park.RUNNING)}
    for a, b in itertools.product(states, states):
        rec = {"state": a}
        if (a, b) in legal:
            assert park.transition(rec, b, now=1.0)["state"] == b
            assert rec["history"][-1]["frm"] == a
        else:
            with pytest.raises(park.ParkError):
                park.transition(rec, b)
            assert rec["state"] == a


# ------------------------------------------------------------------ real wq round trip

@pytest.fixture
def q(tmp_path, monkeypatch):
    monkeypatch.setenv("WQ_DB", str(tmp_path / "wq.db"))
    monkeypatch.setenv("WQ_AGENT", "park-test-1")
    # `wq decide` fires a detached judge-shadow job (wq#2799) per call; from a unit test
    # that is a real systemd unit carrying fixture text. Its own kill switch:
    monkeypatch.setenv("FOREMAN_JUDGE_SHADOW", "0")
    runs = str(tmp_path / "_runs")
    notices = tmp_path / "notices.log"
    ntfy = tmp_path / "ntfy"
    ntfy.write_text(f"#!/bin/sh\nprintf 'CALL %s\\n' \"$4\" >> {notices}\n")   # $4 = title
    ntfy.chmod(0o755)
    monkeypatch.setattr(park, "RUNS_ROOT", runs)
    monkeypatch.setattr(park, "NTFY_BIN", str(ntfy))
    monkeypatch.setattr(park, "WQ_BIN", WQ_BIN)

    class Q:
        runs_root, notice_log = runs, notices

        def wq(self, *a):
            return subprocess.run([WQ_BIN, *map(str, a)], capture_output=True, text=True)

        def add_claimed(self, title="park round trip"):
            out = self.wq("add", "--lane", "build", "--title", title, "--detail", "d").stdout
            tid = int(out.strip().split()[2])
            assert self.wq("claim", "--id", tid).returncode == 0
            return tid

        def show(self, tid):
            return json.loads(self.wq("show", tid, "--json").stdout)

        def sent(self):
            return notices.read_text().splitlines() if notices.exists() else []
    return Q()


def parked(q, tid=None, ask=ASK, sid="sess-abc"):
    tid = tid or q.add_claimed()
    rec = park.park_round(tid, sid, "/wt/x", 1, park.validate_ask(ask))
    return tid, rec


def test_park_moves_the_ticket_and_posts_the_card(q):
    tid, rec = parked(q)
    t = q.show(tid)
    assert t["status"] == "pending_review" and t["gate_type"] == "frank_decision"
    assert "2. after freeze" in t["decision_prompt"] and t["orch_recommendation"] == "after freeze"
    assert t["claimed_by"] is None                  # a stale claim would block the resume
    assert rec["state"] == park.PARKED and rec["round"] == 1 and rec["hops"] == 1
    assert rec["session_id"] == "sess-abc" and rec["delivered_at"]
    assert len(q.sent()) == 1 and f"DECISION wq#{tid}" in q.sent()[0]
    # durable and private: the whole parked state is this one file
    path = park.park_path(tid)
    assert oct(os.stat(path).st_mode & 0o777) == "0o600"
    assert park.load(tid)["card"]["state"] == "open"
    # unclaimable while parked
    assert q.wq("claim", "--id", tid, "--agent", "someone-else").returncode != 0 or \
        q.show(tid)["status"] == "pending_review"


def test_unanswered_round_just_waits(q):
    tid, _ = parked(q)
    assert park.sweep()["actions"] == {tid: "waiting"}
    assert park.sweep(dry_run=True) == {"actions": {tid: "waiting"}, "ready": []}
    assert park.take_resume(tid) is None
    assert len(q.sent()) == 1                    # no spam


def test_answer_routes_reopens_and_resumes_the_same_session_once(q):
    tid, _ = parked(q)
    assert q.wq("answer", tid, "2").returncode == 0
    assert park.sweep(dry_run=True)["actions"] == {tid: "would-answer"}
    assert q.show(tid)["status"] == "pending_review"            # dry run changed nothing
    res = park.sweep()
    assert res == {"actions": {tid: "answered"}, "ready": [tid]}
    assert q.show(tid)["status"] == "open" and q.show(tid)["claimed_by"] is None
    rec = park.load(tid)
    assert rec["state"] == park.ANSWERED and rec["card"]["choice"] == "after freeze"
    assert rec["card"]["version"] == 2 and rec["card"]["answered_by"] == "frank"
    assert park.sweep()["actions"] == {}                        # idempotent

    r = park.take_resume(tid)
    assert r["session_id"] == "sess-abc" and "Frank chose: after freeze" in r["prompt"]
    assert r["worktree"] == "/wt/x" and r["attempt"] == 1
    assert park.take_resume(tid) is None                        # never resumed twice
    assert park.load(tid)["state"] == park.RESUMED and park.sweep()["ready"] == []


def test_peek_does_not_consume_and_fresh_sessions_still_get_the_decision(q):
    """A dispatch that is deferred after the claim must not swallow the answer, and a
    session that cannot be resumed must not lose it."""
    tid, _ = parked(q)
    assert park.decision_context(tid) is None                  # nothing decided yet
    q.wq("answer", tid, "3")
    park.sweep()
    for _ in range(3):
        assert park.peek_resume(tid)["session_id"] == "sess-abc"
    assert park.load(tid)["state"] == park.ANSWERED
    assert "Frank's answer: leave it" in park.decision_context(tid)
    assert park.mark_resumed(tid) is True and park.mark_resumed(tid) is False
    assert park.peek_resume(tid) is None
    assert "Frank's answer: leave it" in park.decision_context(tid)   # survives the resume


def test_free_text_answer_is_kept_verbatim(q):
    tid, _ = parked(q)
    q.wq("answer", tid, "neither — retire the token entirely")
    park.sweep()
    card = park.load(tid)["card"]
    assert card["choice"] is None and card["answer"] == "neither — retire the token entirely"
    assert "free text): neither" in park.take_resume(tid)["prompt"]


def test_a_second_park_after_resume_is_round_two(q):
    tid, _ = parked(q)
    q.wq("answer", tid, "1")
    park.sweep()
    park.take_resume(tid)
    assert q.wq("claim", "--id", tid).returncode == 0
    _, rec = parked(q, tid, sid="sess-abc")
    assert rec["round"] == 2 and rec["hops"] == 2 and rec["card"]["choice"] is None
    assert q.show(tid)["status"] == "pending_review"


def test_hop_limit_refuses_the_ninth_park(q, monkeypatch):
    monkeypatch.setattr(park, "MAX_HOPS", 3)
    tid = q.add_claimed()
    for n in (1, 2, 3):
        parked(q, tid)
        q.wq("answer", tid, "1")
        assert park.sweep()["actions"] == {tid: "answered"}
        assert park.take_resume(tid)["round"] == n
        assert q.wq("claim", "--id", tid).returncode == 0
    with pytest.raises(park.ParkError) as e:
        parked(q, tid)
    assert "hop limit" in str(e.value)
    assert q.show(tid)["status"] == "claimed"                   # ticket untouched


def test_cannot_park_twice_or_without_a_session(q):
    tid, _ = parked(q)
    with pytest.raises(park.ParkError):
        park.park_round(tid, "sess-abc", "/wt/x", 1, park.validate_ask(ASK))
    other = q.add_claimed("no session")
    with pytest.raises(park.ParkError):
        park.park_round(other, None, "/wt/y", 1, park.validate_ask(ASK))
    assert q.show(other)["status"] == "claimed" and park.load(other) is None


def test_only_a_claimed_ticket_can_park(q):
    """A done or released ticket that still emits a card must not be dragged back into
    frank_gated."""
    tid = q.add_claimed("already closed")
    assert q.wq("done", tid, "--notes", "see README.md").returncode == 0 or True
    status = q.show(tid)["status"]
    if status == "claimed":                         # done gate refused the fixture notes
        assert q.wq("release", tid).returncode == 0
        status = q.show(tid)["status"]
    assert status in ("done", "open")
    with pytest.raises(park.ParkError) as e:
        park.park_round(tid, "sess", "/wt", 1, park.validate_ask(ASK))
    assert "not claimed" in str(e.value)
    assert q.show(tid)["status"] == status and park.load(tid) is None


def test_ticket_moved_by_hand_cancels_the_round(q):
    tid, _ = parked(q)
    assert q.wq("edit", tid, "--status", "open", "--force").returncode == 0
    assert park.sweep()["actions"] == {tid: "cancelled"}
    assert park.load(tid)["state"] == park.CANCELLED and park.take_resume(tid) is None
    assert park.load(tid)["card"]["state"] == "resolved"


def test_rejected_answer_reposts_the_card(q):
    tid, _ = parked(q)
    q.wq("answer", tid, "garbled")
    assert q.wq("review", tid, "reject", "--reason", "not an answer").returncode == 0
    assert q.show(tid)["status"] == "frank_gated"
    assert park.sweep()["actions"][tid] in ("waiting", "re-notified")
    assert q.show(tid)["status"] == "pending_review"            # question is live again
    q.wq("answer", tid, "3")
    assert park.sweep()["actions"] == {tid: "answered"}
    assert park.load(tid)["card"]["choice"] == "leave it"


def test_undelivered_card_is_resent_and_reminders_are_capped(q, monkeypatch):
    monkeypatch.setattr(park, "NTFY_BIN", "/bin/false")
    tid, rec = parked(q)
    assert rec["delivered_at"] is None and q.sent() == []
    monkeypatch.setattr(park, "NTFY_BIN", str(q.notice_log.parent / "ntfy"))
    assert park.sweep(now=rec["parked_at"] + 60)["actions"] == {tid: "re-notified"}
    assert len(q.sent()) == 1 and park.load(tid)["delivered_at"]
    t = rec["parked_at"] + 60
    for _ in range(6):                                          # a week of daily sweeps
        t += park.REMIND_AFTER_S + 1
        park.sweep(now=t)
    assert len(q.sent()) == 1 + park.MAX_REMINDERS
    assert park.sweep(now=t + 10)["actions"] == {tid: "waiting"}


def test_parked_state_needs_no_process_and_survives_a_cold_read(q, tmp_path):
    """'Event-driven — no process, no tokens while parked': after park_round returns
    there is nothing running; a brand-new interpreter finds the round from disk alone."""
    tid, _ = parked(q)
    code = ("import sys, json; sys.path.insert(0, %r); import park; "
            "park.RUNS_ROOT = %r; print(json.dumps(park.parked_tickets()))"
            % (os.path.dirname(park.__file__), q.runs_root))
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert json.loads(out.stdout) == [tid]


def test_cli_answer_is_the_event(q, capsys):
    tid, _ = parked(q)
    assert park.main(["answer", str(tid), "rotate now"]) == 0
    assert "answered" in capsys.readouterr().out
    assert q.show(tid)["status"] == "open" and park.load(tid)["card"]["choice"] == "rotate now"
    assert park.main(["answer", str(tid), "leave it"]) == 1     # first answer won
    assert park.load(tid)["card"]["choice"] == "rotate now"
    assert park.main(["list"]) == 0 and f"wq#{tid}" in capsys.readouterr().out
    assert park.main(["answer", "99999", "x"]) == 1


def test_one_corrupt_round_does_not_stop_the_sweep(q):
    tid, _ = parked(q)
    bad = os.path.join(q.runs_root, "wq424242")
    os.makedirs(bad)
    open(os.path.join(bad, "park.json"), "w").write("{broken")
    q.wq("answer", tid, "1")
    assert park.sweep()["actions"] == {tid: "answered"}


def test_enabled_switch(monkeypatch):
    monkeypatch.delenv("FOREMAN_PARK", raising=False)
    assert park.enabled()
    monkeypatch.setenv("FOREMAN_PARK", "off")
    assert not park.enabled()


# ------------------------------------------------------------------ Stop hook

def _hook(tmp_path, monkeypatch, text):
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "hooks"))
    import stop_gate
    t = tmp_path / "t.jsonl"
    t.write_text(json.dumps({"type": "assistant", "message": {"content": [
        {"type": "text", "text": text}]}}) + "\n")
    monkeypatch.setenv("FOREMAN_EXIT_GATE_PROTOCOL", "1")
    monkeypatch.setenv("FOREMAN_TICKET_ID", "5009")
    monkeypatch.setenv("FOREMAN_STOP_GATE_STATE", str(tmp_path / "sg.json"))
    monkeypatch.setenv("FOREMAN_WORKTREE_ROOT", str(tmp_path))
    monkeypatch.delenv("FOREMAN_EXIT_GATE", raising=False)
    monkeypatch.setattr(stop_gate, "_ticket_is_done", lambda t_: False)
    monkeypatch.setattr(stop_gate, "_ticket_status", lambda t_: "claimed")
    monkeypatch.setattr(stop_gate, "_worktree_dirty_no_landing", lambda w: True)
    monkeypatch.setattr(stop_gate.acceptance, "read_acceptance", lambda *a, **k: None)
    return stop_gate, stop_gate.decide({"session_id": "s", "cwd": str(tmp_path),
                                        "transcript_path": str(t)})


def test_stop_hook_lets_a_parking_session_stop(tmp_path, monkeypatch):
    """Dirty worktree, ticket still claimed, EXIT_SIGNAL false: every convergence nudge
    would fire — and none may, because this stop is going to be resumed."""
    sg, verdict = _hook(tmp_path, monkeypatch, card_text())
    assert verdict == (sg.ALLOW, "")


def test_stop_hook_still_nudges_a_malformed_card_and_obeys_the_off_switch(tmp_path, monkeypatch):
    bad = "need a decision\n```foreman-park\n{\"ask\": {\"title\": \"x\"}}\n```\nPARKED"
    sg, verdict = _hook(tmp_path, monkeypatch, bad)
    assert verdict[0] == sg.BLOCK
    monkeypatch.setenv("FOREMAN_PARK", "off")
    sg, verdict = _hook(tmp_path, monkeypatch, card_text())
    assert verdict[0] == sg.BLOCK
