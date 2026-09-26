"""wq#2740 probe: does account() accept a body-only prose match with no prefix adjacency?

Pinned, re-runnable inputs (never a live mutable file):
  A. kb ISSUE-LOG.md at rev 8d248d2 (foreign repo, outside this repo's tracked tree --
     the population account()'s `tracked` default is drawn from).
  B. this repo's tracked tree as mask_replay builds it, for the zero-hits claim.

Prints hashes/offsets and counts only; never the glued prefix+body form, which would
re-arm the cloudflare tripwire on this run (the whole point of the row under judgment).
"""
import hashlib
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))))  # the repo this ops dir lives in, wherever it is checked out
from harness import mask_replay  # noqa: E402

PREFIX = "cfut_"
BODY = "-prefixed"          # sha256[:12] == a745825001bc, the value under judgment
GLUED = PREFIX + BODY
KB_REV = "8d248d2"

print("body sha256[:12] =", hashlib.sha256(BODY.encode()).hexdigest()[:12])
print("glued len        =", len(GLUED), "body len =", len(BODY),
      "floor =", mask_replay.TRUNCATED_FLOOR)

kb = subprocess.run(["git", "-C", "/home/lumen/kb", "show", f"{KB_REV}:ISSUE-LOG.md"],
                    capture_output=True, text=True, check=True).stdout
pinned = {f"kb@{KB_REV}:ISSUE-LOG.md": kb}
print(f"\nA. pinned foreign blob: body occurrences = {kb.count(BODY)}, "
      f"glued occurrences = {kb.count(GLUED)}")
print("   account(body, pinned, prefix=%r) -> %r"
      % (PREFIX, mask_replay.account(BODY, pinned, PREFIX)))
print("   account(body, pinned)            -> %r"
      % (mask_replay.account(BODY, pinned),))

tracked = mask_replay.tracked_text()
body_files = [p for p, t in tracked.items() if BODY in t]
glued_files = [p for p, t in tracked.items() if GLUED in t]
print(f"\nB. tracked tree: {len(tracked)} files, body in {len(body_files)}, "
      f"glued in {len(glued_files)}")
print("   account(body, tracked, prefix=%r) -> %r"
      % (PREFIX, mask_replay.account(BODY, tracked, PREFIX)))
