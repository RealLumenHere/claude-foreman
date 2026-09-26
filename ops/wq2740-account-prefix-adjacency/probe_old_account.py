"""wq#2740: the new tests must FAIL against the pre-fix account(), or they prove nothing.

Rebuilds the pre-wq#2740 body-only direct path (mask_replay.py:436-439 at fa8784d) and
runs the same two assertions through it.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))))
from harness import mask_replay, masking  # noqa: E402


def old_account(value, tracked, prefix=""):
    for path, text in tracked.items():
        at = text.find(value)
        if at >= 0:
            return f"{path}:{text.count(chr(10), 0, at) + 1}"
    for path, text in tracked.items():
        if not mask_replay._SEAM.search(text):
            continue
        joined = mask_replay._seam_joined(text)
        at = joined.find(value)
        if at >= 0:
            return f"{path}:{joined.count(chr(10), 0, at) + 1}"
    cut = masking.untruncated(value, mask_replay.TRUNCATED_FLOOR)
    if cut:
        return old_account(cut, tracked)
    whole = masking.untruncated_whole(prefix, value, mask_replay.TRUNCATED_FLOOR)
    return old_account(whole, tracked) if whole else None


prefix, body = "cfut_", "-prefixed"
prose = {"doc.md": "none of the vendor" + body + " patterns fire here\n"}
floor_body = "abcdefghijkl"
wrapped = {"reports/r.md": "TOKEN:\n" + floor_body + "\n"}

print("old account(short body, prose, prefix) ->", repr(old_account(body, prose, prefix)),
      "(new: None -- test discriminates)")
print("old account(floor body, wrapped, label) ->",
      repr(old_account(floor_body, wrapped, "TOKEN: ")),
      "(new: same -- floor guard is not a no-op assertion)")
print("new account(short body, prose, prefix) ->",
      repr(mask_replay.account(body, prose, prefix)))
