"""wq#2669 acceptance probe — does `_parse_ts` read what the writers write?

The unit tests seed their own databases, so they can only confirm the pruner
agrees with itself. This probe takes its input from outside that population: it
reads the timestamp format constant out of `bin/wq` and `bin/slock` at a pinned
git revision, formats a lease with *that* string, and asks the pruner whether
the claim is live. If the pruner's `_DB_TS_FMT` ever drifts from what the
writers store — the wq#2644 defect — this fails.

Pinned to a revision, not the working tree, so the evidence is re-runnable:
a live read of /home/lumen/bin would change under the probe.

    python3 probe_wq2669_writer_format.py        # exits non-zero on failure
"""
import datetime
import os
import re
import sqlite3
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import prune_worktrees as pw  # noqa: E402

BIN_REPO = "/home/lumen/bin"
PINNED_REV = "6c1c1be"
FMT_RE = re.compile(r"^FMT\s*=\s*\"(.+)\"", re.M)


def writer_fmt(blob):
    """The FMT constant of a wq-family binary, read at PINNED_REV."""
    src = subprocess.run(["git", "-C", BIN_REPO, "show", f"{PINNED_REV}:{blob}"],
                         capture_output=True, text=True, check=True).stdout
    m = FMT_RE.search(src)
    assert m, f"no FMT constant in {blob}@{PINNED_REV}"
    return m.group(1)


def main():
    wq_fmt = writer_fmt("wq")
    slock_fmt = writer_fmt("slock")
    print(f"bin/wq@{PINNED_REV}    FMT = {wq_fmt!r}")
    print(f"bin/slock@{PINNED_REV} FMT = {slock_fmt!r}")
    assert wq_fmt == slock_fmt, "writers disagree — the pruner cannot use one format"

    past = (datetime.datetime.now(datetime.timezone.utc)
            - datetime.timedelta(minutes=90)).strftime(wq_fmt)
    print(f"lease written by the writers' own format: {past!r} (90 min ago)")

    failures = []
    with tempfile.TemporaryDirectory() as td:
        db = os.path.join(td, "queue.db")
        conn = sqlite3.connect(db)
        conn.execute("CREATE TABLE tasks(id INTEGER PRIMARY KEY, status TEXT, lease_expires TEXT)")
        conn.execute("INSERT INTO tasks VALUES (99999, 'claimed', ?)", (past,))
        conn.execute("CREATE TABLE slocks(scope TEXT, holder TEXT, expires_at TEXT)")
        conn.execute("CREATE TABLE slocks_ro(scope TEXT, holder TEXT, expires_at TEXT)")
        conn.execute("INSERT INTO slocks VALUES ('lane:probe', 'probe-agent', ?)", (past,))
        conn.commit()
        conn.close()
        pw.WQ_DB = db

        claim = pw.wq_claim_is_live(99999)
        slock = pw.slock_is_live("probe-agent")

    print(f"wq_claim_is_live(99999) -> {claim}")
    print(f"slock_is_live('probe-agent') -> {slock}")
    if claim[0] is not False:
        failures.append("expired lease reported live")
    if slock[0] is not False:
        failures.append("expired slock reported live")

    if failures:
        print("FAIL: " + "; ".join(failures))
        return 1
    print("PASS: both expired records read as expired")
    return 0


if __name__ == "__main__":
    sys.exit(main())
