#!/usr/bin/env python3
"""wq#5009 — park-and-resume for Frank-gated decisions: round state machine, decision
card protocol, answer routing. Pattern port of KKKKhazix/agent-autoloop (source notes:
scratch/research/extract_agent_autoloop.md, wq#4974). Design: design/WQ5009_PARK_RESUME.md.

The defect this removes: a worker that needs a decision only Frank can make either blocks
(session lost, the frank_gated queue becomes a graveyard) or waits (a slot and tokens
burn). Instead:

  worker   ends its turn with a ```foreman-park``` decision card and exits
  fworker  parks the ROUND: stores the session id + card under _runs/wq<id>/park.json,
           moves the ticket claimed -> frank_gated -> pending_review (wq decide carries the
           card), notifies Frank, exits EXIT_PARKED — uncharged, slot freed, refill kicked
  (nothing runs: no process, no tokens, state is one small file; survives reboot)
  Frank    answers:  park.py answer <id> "<choice>"      (or plain `wq answer`)
  sweep    routes the first answer onto the card, `wq review accept` -> ticket open
  dispatch claims it like any open ticket; fworker sees an ANSWERED round and resumes the
           SAME session with the answer as its next turn (fresh session + the decision in
           context if that session can no longer be resumed)

Round states (per ticket):  RUNNING -> PARKED -> ANSWERED -> RESUMED -> RUNNING ...
                            PARKED -> CANCELLED (ticket moved by hand / closed)
A ticket may park at most MAX_HOPS times (hop-count breaker): past that the card is not
honoured and the exit takes the ordinary path.

stdlib only; every wq call goes through the wq CLI (never the DB).
"""
import argparse
import glob
import json
import os
import re
import subprocess
import sys
import time
import uuid

FOREMAN_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RUNS_ROOT = os.environ.get("FOREMAN_RUNS_ROOT", os.path.join(FOREMAN_ROOT, "_runs"))
WQ_BIN = os.environ.get("FOREMAN_WQ_BIN", "/home/lumen/bin/wq")
NTFY_BIN = os.environ.get("FOREMAN_NTFY_BIN", "/home/lumen/bin/ntfy-send")
DISPATCH_BATCH_BIN = os.environ.get("FOREMAN_DISPATCH_BATCH_BIN", "/home/lumen/bin/dispatch-batch")
CORD_BIN = os.environ.get("FOREMAN_CORD_BIN", "/home/lumen/bin/cord")
CORD_TTL_MIN = int(os.environ.get("FOREMAN_PARK_CORD_TTL_MIN", str(24 * 60)))
MODE_ENV = "FOREMAN_PARK"                # on (default) | off
PARK_FILENAME = "park.json"
FENCE = "foreman-park"
PARKED_SENTINEL = "PARKED"
MAX_HOPS = int(os.environ.get("FOREMAN_PARK_MAX_HOPS", "8"))
REMIND_AFTER_S = int(os.environ.get("FOREMAN_PARK_REMIND_S", str(24 * 3600)))
MAX_REMINDERS = 3
NTFY_TOPIC = os.environ.get("FOREMAN_PARK_NTFY_TOPIC", "lumen-fleet")
LUMEN_SSH = os.environ.get("FOREMAN_PARK_SSH", "ssh lumen@100.117.66.102")

RUNNING, PARKED, ANSWERED, RESUMED, CANCELLED = (
    "RUNNING", "PARKED", "ANSWERED", "RESUMED", "CANCELLED")
TRANSITIONS = {
    RUNNING: {PARKED},
    PARKED: {ANSWERED, CANCELLED},
    ANSWERED: {RESUMED, CANCELLED},
    RESUMED: {PARKED, RUNNING, CANCELLED},
    CANCELLED: {PARKED, RUNNING},
}


class ParkError(ValueError):
    pass


def enabled():
    return (os.environ.get(MODE_ENV) or "on").strip().lower() != "off"


# --------------------------------------------------------------------------
# decision card protocol
# --------------------------------------------------------------------------

_FENCE_RE = re.compile(r"```" + FENCE + r"[ \t]*\n(.*?)\n```", re.S)
LIMITS = {"title": 120, "text": 2000, "option": 200, "options": 6, "key": 80}


def find_card_block(text):
    """Raw JSON text of the LAST ```foreman-park fenced block, or None."""
    if not isinstance(text, str):
        return None
    blocks = _FENCE_RE.findall(text)
    return blocks[-1] if blocks else None


def validate_ask(ask):
    """Normalised ask dict, or raise ParkError naming exactly what is wrong (the message
    is fed back to the worker verbatim)."""
    if not isinstance(ask, dict):
        raise ParkError("`ask` must be a JSON object")
    title, text = ask.get("title"), ask.get("text")
    options, recommend = ask.get("options"), ask.get("recommend")
    if not (isinstance(title, str) and title.strip()):
        raise ParkError("`ask.title` is required (one line: the decision)")
    if not (isinstance(text, str) and text.strip()):
        raise ParkError("`ask.text` is required (what you found and why Frank must decide)")
    if len(title) > LIMITS["title"] or len(text) > LIMITS["text"]:
        raise ParkError(f"`ask.title` <= {LIMITS['title']} chars, `ask.text` <= {LIMITS['text']}")
    if options is None:
        options = []
    if not (isinstance(options, list) and all(isinstance(o, str) and o.strip() for o in options)):
        raise ParkError("`ask.options` must be a list of non-empty strings")
    options = [o.strip() for o in options]
    if len(options) > LIMITS["options"] or any(len(o) > LIMITS["option"] for o in options):
        raise ParkError(f"at most {LIMITS['options']} options, each <= {LIMITS['option']} chars")
    if len({o.lower() for o in options}) != len(options):
        raise ParkError("`ask.options` must be distinct")
    if recommend is not None:
        if not isinstance(recommend, str) or not recommend.strip():
            raise ParkError("`ask.recommend` must be a non-empty string when given")
        recommend = recommend.strip()
        if options and recommend.lower() not in {o.lower() for o in options}:
            raise ParkError("`ask.recommend` must be one of `ask.options`")
    key = ask.get("key")
    if key is not None and not (isinstance(key, str) and 0 < len(key) <= LIMITS["key"]):
        raise ParkError(f"`ask.key` must be a string <= {LIMITS['key']} chars when given")
    return {"title": " ".join(title.split()), "text": text.strip(), "options": options,
            "recommend": recommend, "key": key}


def parse_card(text):
    """(ask, error). (None, None): the message carries no card at all. (None, error):
    a card block is present but unusable. (ask, None): a valid ask."""
    raw = find_card_block(text)
    if raw is None:
        return None, None
    try:
        doc = json.loads(raw)
    except ValueError as e:
        return None, f"the {FENCE} block is not valid JSON ({e})"
    if not isinstance(doc, dict) or "ask" not in doc:
        return None, f'the {FENCE} block must be a JSON object with an "ask" key'
    try:
        return validate_ask(doc["ask"]), None
    except ParkError as e:
        return None, str(e)


def rewrite_text(error):
    return (f"PARK CARD REJECTED: {error}. Re-send your final message with a corrected "
            f"```{FENCE} block: one JSON object "
            '{"ask": {"title": "...", "text": "...", "options": ["...", "..."], '
            '"recommend": "<one of options>"}}'
            f" followed by the lines `EXIT_SIGNAL: false` and `{PARKED_SENTINEL}`. If you "
            "no longer need a decision, carry on with the ticket instead.")


def new_card(ask, version=1):
    return {"id": uuid.uuid4().hex, "version": version, "kind": "ask", "state": "open",
            "title": ask["title"], "text": ask["text"], "options": ask["options"],
            "recommend": ask["recommend"], "key": ask.get("key"),
            "choice": None, "answer": None, "answered_by": None, "ref": None}


def match_answer(card, answer):
    """(choice, free_text). An answer that names an option — by its text (case-blind) or
    its 1-based number — is a choice; anything else is free text. Never guesses."""
    a = (answer or "").strip()
    opts = card.get("options") or []
    for o in opts:
        if a.lower() == o.lower():
            return o, None
    m = re.fullmatch(r"#?(\d{1,2})[.)]?", a)
    if m and 1 <= int(m.group(1)) <= len(opts):
        return opts[int(m.group(1)) - 1], None
    return None, a


def decision_prompt(card, ticket_id):
    """What `wq decide --prompt` carries (and so what every Frank-gate surface shows)."""
    lines = [card["title"], "", card["text"]]
    if card["options"]:
        lines += ["", "Options:"] + [f"  {i}. {o}" for i, o in enumerate(card["options"], 1)]
    lines += ["", f"Answer: {LUMEN_SSH} 'python3 {os.path.abspath(__file__)} answer "
                  f"{ticket_id} \"<option number, option text, or your own words>\"'",
              "A worker session is parked on this answer and resumes with it "
              "(no process, no tokens while it waits)."]
    return "\n".join(lines)


def resume_prompt(card, ticket_id):
    """The next turn of the parked session."""
    if card.get("choice"):
        said = f"Frank chose: {card['choice']}"
    else:
        said = f"Frank answered (free text): {card.get('answer')}"
    return (f"DECISION RECEIVED for wq#{ticket_id} — you parked this session on the "
            f"question \"{card['title']}\". {said}. Carry on with the ticket from where "
            f"you stopped, acting on that decision. The exit contract is unchanged; if a "
            f"further decision is needed you may park again.")


def context_block(card, ticket_id):
    """For a FRESH session when the parked one can no longer be resumed."""
    opts = "; ".join(card.get("options") or []) or "(open question)"
    said = card.get("choice") or card.get("answer")
    return (f"DECISION ALREADY MADE (wq#{ticket_id}, park-and-resume): an earlier session "
            f"on this ticket stopped to ask Frank — \"{card['title']}\": {card['text']} "
            f"Options were: {opts}. Frank's answer: {said}. Do not ask again; act on it. "
            f"The earlier session's work is in this worktree.")


# --------------------------------------------------------------------------
# round store
# --------------------------------------------------------------------------

def run_dir(ticket_id, runs_root=None):
    return os.path.join(runs_root or RUNS_ROOT, f"wq{ticket_id}")


def park_path(ticket_id, runs_root=None):
    return os.path.join(run_dir(ticket_id, runs_root), PARK_FILENAME)


def load(ticket_id, runs_root=None):
    try:
        with open(park_path(ticket_id, runs_root), encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) and d.get("state") else None
    except (OSError, ValueError):
        return None


def _save(ticket_id, rec, runs_root=None):
    path = park_path(ticket_id, runs_root)
    os.makedirs(os.path.dirname(path), mode=0o711, exist_ok=True)
    tmp = f"{path}.tmp.{os.getpid()}"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(rec, f, indent=1, sort_keys=True)
    os.replace(tmp, path)


def transition(rec, new_state, now=None, **note):
    old = rec.get("state", RUNNING)
    if new_state not in TRANSITIONS.get(old, ()):
        raise ParkError(f"illegal round transition {old} -> {new_state}")
    rec["state"] = new_state
    rec.setdefault("history", []).append(
        dict(note, at=time.time() if now is None else now, frm=old, to=new_state))
    rec["history"] = rec["history"][-40:]
    return rec


def hops(ticket_id, runs_root=None):
    rec = load(ticket_id, runs_root)
    return int(rec.get("hops", 0)) if rec else 0


# --------------------------------------------------------------------------
# wq / ntfy plumbing
# --------------------------------------------------------------------------

def _wq(*args, timeout=30):
    return subprocess.run([WQ_BIN, *map(str, args)], capture_output=True, text=True,
                          timeout=timeout)


def _wq_show(ticket_id):
    p = _wq("show", ticket_id, "--json")
    try:
        d = json.loads(p.stdout)
        return d if isinstance(d, dict) else {}
    except ValueError:
        return {}


def _notify(title, body, priority=None):
    try:
        cmd = [NTFY_BIN, "-t", NTFY_TOPIC, "-T", title]
        if priority:
            cmd += ["-p", priority]
        return subprocess.run(cmd + [body], capture_output=True, text=True,
                              timeout=20).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def _cord(*args, timeout=30):
    try:
        return subprocess.run([CORD_BIN, *args], capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError):
        return None


def _cord_request(card, ticket_id):
    """wq#5005: file a Cord drop so Frank can answer the card from his phone. Returns the
    drop id or None. The drop is filed with --wait 0 and read later by _cord_poll."""
    note = card["text"][:400] + "".join(f"\n{i}. {o}" for i, o in enumerate(card["options"], 1))
    p = _cord("request", f"wq#{ticket_id} decision: {card['title'][:60]}", "--note", note,
              "--as", "park", "--ttl", str(CORD_TTL_MIN), "--wait", "0")
    drop = (p.stdout.strip() if p and p.returncode == 0 else "")
    return drop or None


def _cord_poll(drop_id):
    """Answer text once Frank has filled the drop, else None. 1s await: a timeout exits
    non-zero, a filled drop prints the value (burn-on-read, so the caller must use it)."""
    p = _cord("await", drop_id, "--wait", "1", timeout=30)
    value = p.stdout.strip() if p and p.returncode == 0 else ""
    return value or None


def _card_notice(card, ticket_id):
    body = card["text"][:600]
    if card["options"]:
        body += "\n" + "\n".join(f"{i}. {o}" for i, o in enumerate(card["options"], 1))
    if card.get("recommend"):
        body += f"\nRecommended: {card['recommend']}"
    body += (f"\nAnswer: {LUMEN_SSH} 'python3 {os.path.abspath(__file__)} answer "
             f"{ticket_id} \"<choice>\"'")
    return f"DECISION wq#{ticket_id}: {card['title'][:80]}", body


# --------------------------------------------------------------------------
# park (called by fworker when a round ends on a valid card)
# --------------------------------------------------------------------------

def park_round(ticket_id, session_id, worktree, attempt, ask, runs_root=None, now=None,
               log=lambda m: None):
    """Park the round. Returns the park record, or raises ParkError (nothing changed that
    matters: the caller falls back to the ordinary exit path)."""
    now = time.time() if now is None else now
    if not session_id:
        raise ParkError("no session id to resume — cannot park")
    rec = load(ticket_id, runs_root) or {"state": RUNNING, "hops": 0, "round": 0,
                                         "ticket": int(ticket_id)}
    if rec["state"] in (PARKED, ANSWERED):
        raise ParkError(f"round already {rec['state']} (card {rec['card']['id']})")
    if int(rec.get("hops", 0)) >= MAX_HOPS:
        raise ParkError(f"hop limit: this ticket has already parked {rec['hops']} times "
                        f"(max {MAX_HOPS})")
    before = _wq_show(ticket_id).get("status")
    if before != "claimed":
        # only a ticket this worker still holds can park: a done/blocked/released ticket
        # carrying a card is a contradiction, and moving it to frank_gated would undo it
        raise ParkError(f"ticket is {before!r}, not claimed — nothing to park")
    card = new_card(ask)
    transition(rec, PARKED, now, card=card["id"])
    rec.update(hops=int(rec.get("hops", 0)) + 1, round=int(rec.get("round", 0)) + 1,
               session_id=session_id, worktree=worktree, attempt=attempt, card=card,
               parked_at=now, delivered_at=None, reminders=0, last_notice_at=None)
    _save(ticket_id, rec, runs_root)          # the round is durable BEFORE the ticket moves

    # claimed -> (release) -> frank_gated. The release is what clears the claim: a ticket
    # that keeps a stale claimed_by is skipped by auto-dispatch after Frank answers, and
    # `wq release` on a frank_gated row would reopen it, so the order is fixed. The gap
    # between the two calls is two wq invocations; the row is re-read afterwards and a
    # dispatcher that slipped into it is reported rather than silently overwritten.
    _wq("release", ticket_id)
    p = _wq("edit", ticket_id, "--status", "frank_gated", "--gate-type", "frank_decision")
    after = _wq_show(ticket_id)
    if p.returncode != 0 or after.get("status") != "frank_gated" or after.get("claimed_by"):
        transition(rec, CANCELLED, now, why="ticket did not reach frank_gated cleanly")
        _save(ticket_id, rec, runs_root)
        raise ParkError(f"ticket did not park cleanly (status={after.get('status')!r}, "
                        f"claimed_by={after.get('claimed_by')!r}): "
                        f"{(p.stdout + p.stderr).strip()[-200:]}")
    rec_text = card["recommend"] or "no recommendation given by the worker"
    p = _wq("decide", ticket_id, "--prompt", decision_prompt(card, ticket_id),
            "--rec", rec_text, "--no-probe", "park-and-resume decision card (wq#5009)")
    if p.returncode != 0:
        # still parked and still safe (frank_gated is unclaimable); sweep re-posts the card
        log(f"park: wq decide failed for wq#{ticket_id}, sweep will retry: "
            f"{(p.stdout + p.stderr).strip()[-200:]}")
    _wq("note", ticket_id, f"[park wq#5009] round {rec['round']} parked on card "
                           f"{card['id'][:8]}: {card['title']}")
    deliver(ticket_id, rec, runs_root, now)
    return rec


def deliver(ticket_id, rec, runs_root=None, now=None):
    now = time.time() if now is None else now
    title, body = _card_notice(rec["card"], ticket_id)
    if not rec.get("cord_id"):
        rec["cord_id"] = _cord_request(rec["card"], ticket_id)
    if rec.get("cord_id"):
        body += "\nOr answer in Cord (phone): fill the drop for this card."
    if _notify(title, body, priority="high"):
        if not rec.get("delivered_at"):
            rec["delivered_at"] = now
        rec["last_notice_at"] = now
        _save(ticket_id, rec, runs_root)
        return True
    return False


# --------------------------------------------------------------------------
# sweep — answer routing. Cheap, idempotent, no model call; run by auto-dispatch at the
# top of every pass, by `park.py answer`, and safe to run by hand.
# --------------------------------------------------------------------------

def sweep_one(ticket_id, runs_root=None, now=None, dry_run=False, log=lambda m: None):
    """Reconcile one parked round with its ticket. Returns the action taken."""
    now = time.time() if now is None else now
    rec = load(ticket_id, runs_root)
    if not rec:
        return "no-round"
    if rec["state"] != PARKED:
        return rec["state"].lower()
    t = _wq_show(ticket_id)
    status = t.get("status")
    if not status:
        return "ticket-unreadable"
    card = rec["card"]
    answer = (t.get("frank_response") or "").strip()

    if status == "pending_review" and answer:
        choice, free = match_answer(card, answer)
        if dry_run:
            return "would-answer"
        card.update(state="answered", choice=choice, answer=answer, answered_by="frank",
                    version=card["version"] + 1)
        transition(rec, ANSWERED, now, choice=choice, free_text=bool(free))
        rec["answered_at"] = now
        _save(ticket_id, rec, runs_root)      # first answer wins: recorded before reopen
        p = _wq("review", ticket_id, "accept", "--reason",
                "park-and-resume (wq#5009): answer routed to the parked session")
        if p.returncode != 0:
            log(f"park: wq review accept failed for wq#{ticket_id}: "
                f"{(p.stdout + p.stderr).strip()[-200:]}")
            return "answered-review-failed"
        _wq("note", ticket_id, f"[park wq#5009] round {rec['round']} answered: "
                               f"{choice or answer[:120]} — session {rec['session_id']} "
                               f"resumes on next dispatch")
        return "answered"

    if status in ("pending_review", "frank_gated"):
        if dry_run:
            return "waiting"
        if rec.get("cord_id") and not answer and status == "pending_review":
            cord_answer = _cord_poll(rec["cord_id"])
            if cord_answer:
                _wq("answer", ticket_id, cord_answer)
                return sweep_one(ticket_id, runs_root, now, dry_run, log)
        if status == "frank_gated":
            # the card never reached pending_review (decide failed) or a review rejected
            # the answer: (re)post it so Frank has a live question again
            _wq("decide", ticket_id, "--prompt", decision_prompt(card, ticket_id),
                "--rec", card["recommend"] or "no recommendation given by the worker",
                "--no-probe", "park-and-resume decision card (wq#5009)")
        last = rec.get("last_notice_at")
        due = (not rec.get("delivered_at")
               or (last and now - last >= REMIND_AFTER_S
                   and int(rec.get("reminders", 0)) < MAX_REMINDERS))
        if due:
            was_delivered = bool(rec.get("delivered_at"))
            if deliver(ticket_id, rec, runs_root, now) and was_delivered:
                rec["reminders"] = int(rec.get("reminders", 0)) + 1
                _save(ticket_id, rec, runs_root)
            return "re-notified"
        return "waiting"

    # open / claimed / done / blocked / awaiting_grant: someone resolved or moved the
    # ticket outside the card. The round is over; the session is not resumed.
    if dry_run:
        return "would-cancel"
    card["state"] = "resolved"
    transition(rec, CANCELLED, now, why=f"ticket is {status} with no routed answer")
    _save(ticket_id, rec, runs_root)
    return "cancelled"


def parked_tickets(runs_root=None, states=(PARKED,)):
    out = []
    for path in sorted(glob.glob(os.path.join(runs_root or RUNS_ROOT, "wq*", PARK_FILENAME))):
        m = re.search(r"wq(\d+)$", os.path.dirname(path))
        if not m:
            continue
        rec = load(int(m.group(1)), runs_root)
        if rec and rec["state"] in states:
            out.append(int(m.group(1)))
    return out


def sweep(runs_root=None, now=None, dry_run=False, log=lambda m: None):
    """{ticket_id: action} for every PARKED round, plus `ready`: tickets whose round is
    ANSWERED and waiting for a dispatch to resume it."""
    actions = {}
    for tid in parked_tickets(runs_root):
        try:
            actions[tid] = sweep_one(tid, runs_root, now, dry_run, log)
        except Exception as e:          # one bad round never stops the sweep
            actions[tid] = f"error: {e}"
    return {"actions": actions, "ready": parked_tickets(runs_root, (ANSWERED,))}


# --------------------------------------------------------------------------
# resume (called by fworker when it has claimed a ticket)
# --------------------------------------------------------------------------

def _resume_info(rec, ticket_id):
    card = rec["card"]
    return {"session_id": rec.get("session_id"), "prompt": resume_prompt(card, ticket_id),
            "context": context_block(card, ticket_id), "worktree": rec.get("worktree"),
            "attempt": rec.get("attempt"), "round": rec.get("round")}


def peek_resume(ticket_id, runs_root=None):
    """The resume info for a ticket whose round is ANSWERED, else None. Changes nothing:
    fworker peeks after the claim and only marks the round RESUMED once every spawn gate
    has passed, so a deferred dispatch can never swallow Frank's answer."""
    rec = load(ticket_id, runs_root)
    if not rec or rec["state"] != ANSWERED:
        return None
    return _resume_info(rec, ticket_id)


def mark_resumed(ticket_id, runs_root=None, now=None):
    """ANSWERED -> RESUMED, immediately before the spawn. True if it made the move."""
    rec = load(ticket_id, runs_root)
    if not rec or rec["state"] != ANSWERED:
        return False
    transition(rec, RESUMED, now, session=rec.get("session_id"))
    rec["resumed_at"] = time.time() if now is None else now
    _save(ticket_id, rec, runs_root)
    return True


def take_resume(ticket_id, runs_root=None, now=None):
    """peek + mark in one step (callers with no gates between the two)."""
    info = peek_resume(ticket_id, runs_root)
    if info:
        mark_resumed(ticket_id, runs_root, now)
    return info


def decision_context(ticket_id, runs_root=None):
    """A paragraph for any FRESH session on a ticket whose last card was answered — the
    parked session could not be resumed, crashed after resuming, or a later attempt
    started over. The decision must never depend on one session staying alive."""
    rec = load(ticket_id, runs_root)
    if not rec or rec["state"] not in (ANSWERED, RESUMED):
        return None
    card = rec.get("card") or {}
    if not (card.get("choice") or card.get("answer")):
        return None
    return context_block(card, ticket_id)


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def cmd_answer(a):
    rec = load(a.ticket)
    if not rec or rec["state"] != PARKED:
        print(f"wq#{a.ticket}: no parked round "
              f"({'state ' + rec['state'] if rec else 'no park record'})", file=sys.stderr)
        return 1
    t = _wq_show(a.ticket)
    if t.get("status") == "frank_gated":
        sweep_one(a.ticket)             # re-post the card so `wq answer` has a target
        t = _wq_show(a.ticket)
    if t.get("status") != "pending_review":
        print(f"wq#{a.ticket} is {t.get('status')!r}, not awaiting an answer", file=sys.stderr)
        return 1
    if (t.get("frank_response") or "").strip():
        print(f"wq#{a.ticket}: already answered ({t['frank_response'][:80]!r}) — first "
              f"answer wins", file=sys.stderr)
    else:
        p = _wq("answer", a.ticket, a.text)
        if p.returncode != 0:
            print((p.stdout + p.stderr).strip(), file=sys.stderr)
            return 1
    action = sweep_one(a.ticket, log=lambda m: print(m, file=sys.stderr))
    rec = load(a.ticket)
    print(f"wq#{a.ticket}: {action} — "
          f"{rec['card'].get('choice') or rec['card'].get('answer')!r}; session "
          f"{rec.get('session_id')} resumes on the next dispatch pass")
    if action == "answered" and a.dispatch:
        p = subprocess.run([DISPATCH_BATCH_BIN, str(a.ticket)], capture_output=True, text=True)
        print((p.stdout + p.stderr).strip()[-400:])
        return p.returncode
    return 0 if action == "answered" else 1


def cmd_list(a):
    rows = parked_tickets(states=(PARKED, ANSWERED))
    for tid in rows:
        rec = load(tid)
        c = rec["card"]
        age_h = (time.time() - rec.get("parked_at", time.time())) / 3600
        print(f"wq#{tid:<6} {rec['state']:<9} round {rec['round']} hop {rec['hops']}/{MAX_HOPS} "
              f"{age_h:5.1f}h  {c['title'][:70]}"
              + (f"  -> {c.get('choice') or c.get('answer')}" if rec["state"] == ANSWERED else ""))
    if not rows:
        print("no parked rounds")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description="park-and-resume rounds (wq#5009)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("answer", help="Frank: answer a parked decision card")
    s.add_argument("ticket", type=int)
    s.add_argument("text")
    s.add_argument("--dispatch", action="store_true",
                   help="also run dispatch-batch for the ticket now (skips auto-dispatch's "
                        "gates: only when you want it resumed this minute)")
    s = sub.add_parser("sweep", help="route answers; safe to run any time")
    s.add_argument("--dry-run", action="store_true")
    sub.add_parser("list", help="parked and answered rounds")
    s = sub.add_parser("show", help="one ticket's park record")
    s.add_argument("ticket", type=int)
    a = ap.parse_args(argv)
    if a.cmd == "answer":
        return cmd_answer(a)
    if a.cmd == "sweep":
        print(json.dumps(sweep(dry_run=a.dry_run, log=lambda m: print(m, file=sys.stderr)),
                         indent=1, sort_keys=True))
        return 0
    if a.cmd == "list":
        return cmd_list(a)
    print(json.dumps(load(a.ticket), indent=1, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
