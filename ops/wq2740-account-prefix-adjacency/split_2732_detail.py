"""wq#2740: close the arming source behind rotation row sha256:a745825001bc.

wq#2732's `detail` names the cloudflare pattern by writing its prefix and a nine-char
word contiguously, so every worker who reads that ticket (as doctrine requires) re-arms
the cloudflare tripwire on prose. The house mitigation for a fixture that arms the
tripwire is to split it at the prefix seam (ISSUE-LOG rows 814/822): mask rules stop
firing, and mask_replay's seam pass still accounts for the whole value.

Reads the raw field from the queue db, rewrites it through `wq edit` (never a direct
UPDATE, so wq's own bookkeeping runs), and verifies. Prints counts, never the value.
"""
import os
import sqlite3
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))))
from harness import mask_replay  # noqa: E402

DB = "/home/lumen/agents/work-queue/queue.db"
GLUED = "cfut_" + "-prefixed"
SPLIT = "'cfut_' + '" + "-prefixed" + "'"


def detail():
    con = sqlite3.connect(DB, timeout=30)
    try:
        return con.execute("SELECT detail FROM tasks WHERE id=?", (2732,)).fetchone()[0]
    finally:
        con.close()


before = detail()
print("before: glued occurrences =", before.count(GLUED))
if before.count(GLUED) != 1:
    sys.exit("expected exactly one occurrence; nothing changed")
after = before.replace(GLUED, SPLIT)
p = subprocess.run(["/home/lumen/bin/wq", "edit", "2732", "--detail", after],
                   capture_output=True, text=True)
print("wq edit rc =", p.returncode, "|", (p.stdout + p.stderr).strip()[:200])
now = detail()
print("after: glued occurrences =", now.count(GLUED), "| split present =", SPLIT in now,
      "| length delta =", len(now) - len(before))
for name, pats in (("current", mask_replay.CURRENT_PATTERNS),
                   ("legacy", mask_replay.LEGACY_PATTERNS)):
    hits = mask_replay._hits(pats, now, skip_masked_output=True)
    print(f"{name} rules on the new detail:",
          [(label, len(value)) for label, _, value in hits])
print("seam-closed accounting survives:",
      mask_replay.account("-prefixed", {"wq#2732.detail": now}, "cfut_"))
