"""deferral_gate.py — a deferred AC bullet gets a ticket, or it does not get deferred (wq#2597).

wq#2583's AC had three parts. Its close satisfied one and said so, verbatim, in its own done
notes: "Queue re-route of blocked tickets + SPAWN_LOOP tickets left as follow-up (out of scope
for a worktree-isolated code PR)." No follow-up ticket was ever filed. The queue therefore
recorded a fully-done ticket, the 54 mis-routed rows sat there for 5 days, and the gap was
found only because wq#2590 spent a verify-pass on it and wq#2597 spent a judgment ticket on
top — two dispatches to recover work that one `wq add` would have kept.

"Out of scope for this PR" is a legitimate engineering call, and this module does not argue
with it. It argues with the *silent* version. The rule it enforces:

    A closure whose notes defer work must name the ticket that carries the deferred work.

Enforcement is file-the-ticket, not refuse-the-close, and that direction is deliberate. A
refusal here strands a ticket that is genuinely finished-as-scoped under a worker that has
already stopped (harness/fworker.py:724-742 documents what a fatal gate at the end of a run
costs), and it punishes the honest note — the worker who wrote the deferral down plainly gets
a failed attempt while one who wrote nothing sails through. Auto-filing inverts that: the
honest note produces a tracked ticket, and the cost of a false positive is one retirable row.

Used by fworker.wq_done(); pure enough to call from any other close path.
"""
import re

# Deferral language, narrow on purpose. Each pattern is something a closure says when work it
# was asked to do is NOT in the artifact — not merely a remark about the work's edges.
DEFERRAL_PATTERNS = (
    r"left (?:as|to|for|in) (?:a )?(?:follow[-\s]?up|later|separate ticket)",
    r"out of scope (?:for|of)\b",
    r"\bdefer(?:red|ring)?\s+(?:to|as|until|the)\b",
    r"follow[-\s]?up (?:ticket|work|sweep|pass|is )?(?:needed|required|pending|tbd)?\b",
    r"\b(?:remains?|still)\s+(?:to be|un)(?:done|run|applied|finished)\b",
    r"not (?:yet )?(?:done|run|applied|implemented|swept)\b",
    r"\bTODO\b",
)
_DEFERRAL = re.compile("|".join(DEFERRAL_PATTERNS), re.IGNORECASE)

# Any wq ticket reference other than the one being closed: the closure is already pointing at
# where the rest of the work lives, which is all this gate wants.
_TICKET_REF = re.compile(r"\bwq#?(\d{2,})\b", re.IGNORECASE)

MARKER = "deferral-gate"


def _sentences(notes):
    return [s.strip() for s in re.split(r"(?<=[.;\n])\s+", notes or "") if s.strip()]


def cited_followups(notes, ticket_id):
    """Ticket ids the notes point at, excluding the ticket being closed."""
    return [int(m) for m in _TICKET_REF.findall(notes or "") if int(m) != int(ticket_id)]


def scan(notes, ticket_id):
    """The deferring sentence, or None.

    None when there is no deferral language, and also when the notes already cite another
    ticket — a closure that says "the sweep is wq#2601" has done exactly what the rule asks.
    """
    if not notes or not _DEFERRAL.search(notes):
        return None
    if cited_followups(notes, ticket_id):
        return None
    for sentence in _sentences(notes):
        if _DEFERRAL.search(sentence):
            return sentence
    return notes.strip()


def followup_ticket(ticket_id, deferral, lane="build", pri=2, probe=None):
    """The `wq add` argv for the deferred work. Split out so a caller can log or review it
    before it runs, and so the test suite asserts the argv rather than a mock's call count."""
    head = re.sub(r"\s+", " ", deferral)[:70]
    detail = (
        f"Auto-filed by harness/deferral_gate.py when wq#{ticket_id} closed: its notes "
        f"deferred work and cited no ticket to carry it (wq#2597, after wq#2583 dropped its "
        f"own AC part 2 exactly this way).\n\n"
        f"Deferred in wq#{ticket_id}'s closing notes:\n  \"{deferral}\"\n\n"
        f"Read wq#{ticket_id}'s AC and notes first: this row exists because the deferral was "
        f"real, but only the closing session knew the scope. If the deferred work turns out "
        f"to be already done or not worth doing, retire this row with that finding — do not "
        f"leave it open as a placeholder.")
    argv = ["add", "--lane", lane, "--pri", str(pri),
            "--title", f"[followup] wq#{ticket_id} deferred work: {head}",
            "--detail", detail, "--deps", str(ticket_id)]
    # wq#1676 refuses an untagged row. The parent's own probe is the right one when it has
    # one: a deferred AC bullet is the parent's AC, so the parent's closure test still reads
    # on it. Otherwise let wq derive one from the text.
    argv += ["--liveness", probe] if probe else ["--accept-probe"]
    return argv


def parse_new_id(stdout):
    """`wq add` prints `added task <id> [<lane>]` (/home/lumen/bin/wq:1551)."""
    m = re.search(r"added task (\d+)", stdout or "")
    return int(m.group(1)) if m else None


def annotate(notes, followup_id=None, error=None):
    """The closing notes, with the gate's outcome in them. A closure that deferred work is
    readable as such from the row alone afterwards — including when the filing failed, which
    is the case a silent gate would turn back into wq#2583."""
    if followup_id:
        return f"{notes.rstrip()} [{MARKER}] deferred work filed as wq#{followup_id}."
    return f"{notes.rstrip()} [{MARKER}] DEFERRAL-UNFILED: {error or 'wq add refused'}."


def enforce(ticket_id, notes, run_wq, lane="build", pri=2, probe=None, log=None):
    """Returns the notes to close with — unchanged when nothing was deferred.

    `run_wq(argv) -> object with .returncode/.stdout/.stderr` is injected so the caller owns
    the wq identity, and so no test of this gate can file a row on the live queue.
    """
    deferral = scan(notes, ticket_id)
    if not deferral:
        return notes
    argv = followup_ticket(ticket_id, deferral, lane=lane, pri=pri, probe=probe)
    p = run_wq(argv)
    if p.returncode != 0 and probe:
        # A stale/invalid parent probe must not cost the follow-up row; fall back to derivation.
        p = run_wq(followup_ticket(ticket_id, deferral, lane=lane, pri=pri))
    new_id = parse_new_id(getattr(p, "stdout", ""))
    if p.returncode != 0 or not new_id:
        err = ((getattr(p, "stderr", "") or "") + " " +
               (getattr(p, "stdout", "") or "")).strip()[:180] or "wq add refused"
        if log:
            log(f"wq#{ticket_id}: deferral gate could not file the follow-up: {err}")
        return annotate(notes, error=err)
    if log:
        log(f"wq#{ticket_id}: notes deferred work -> filed wq#{new_id} ({deferral[:60]})")
    return annotate(notes, followup_id=new_id)
