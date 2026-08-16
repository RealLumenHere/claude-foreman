"""OOM forensics — RELIABILITY_SPEC.md §E: "persistent journal for OOM forensics."

exit 137 alone is sufficient evidence for the OOM verdict (classifier.py §C6) — this
module never overrides that classification, same corroboration-only status as CPU/
wall-clock (§E: "never a ceiling"). What it adds is WHY, for the §C6 response ("raise
MemoryMax or shrink"): a human/orchestrator reading attempt-N.json wants to know
whether the kernel OOM-killer actually fired (this cgroup vs. system-wide pressure vs.
some other SIGKILL source entirely) without going and running journalctl by hand.

Best-effort throughout: a journal query that fails (no persistent journal, journalctl
missing, permission denied, timeout) must never fail or slow down an attempt — it logs
and returns an empty/unknown result, same as any other corroboration signal.
"""
from __future__ import annotations

import datetime as dt
import subprocess

OOM_PATTERNS = ("Out of memory", "oom-kill", "oom_kill", "Killed process")


def journal_is_persistent() -> bool:
    """True if /var/log/journal (persistent storage) exists and has entries — cheap
    check so a caller can note in forensics output whether an empty search result means
    "no OOM event" or "no journal to search in the first place" (e.g. a volatile-only
    /run/log/journal that rotated out the window already)."""
    try:
        r = subprocess.run(["journalctl", "--header"], capture_output=True, text=True, timeout=5)
        return "/var/log/journal/" in r.stdout
    except (subprocess.SubprocessError, OSError):
        return False


def oom_journal_evidence(since_epoch, pid=None, timeout=5):
    """Best-effort kernel-log search for an OOM-killer event since `since_epoch`
    (attempt start time), optionally narrowed to a specific PID's line. Returns a dict:
    {"persistent": bool, "matched": bool, "lines": [...]} — never raises.
    """
    result = {"persistent": journal_is_persistent(), "matched": False, "lines": []}
    try:
        since = dt.datetime.fromtimestamp(since_epoch, dt.timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        cmd = ["journalctl", "-k", "--since", since, "--no-pager", "-o", "cat"]
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        if r.returncode not in (0, 1):  # 1 = no entries matched, not an error here
            return result
        hits = [
            line for line in r.stdout.splitlines()
            if any(p in line for p in OOM_PATTERNS) and (pid is None or str(pid) in line)
        ]
        result["matched"] = bool(hits)
        result["lines"] = hits[:20]  # cap: forensics context, not a full dump
    except (subprocess.SubprocessError, OSError, ValueError, OverflowError):
        pass
    return result
