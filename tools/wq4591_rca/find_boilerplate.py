"""Locate 'bypass the intent' frames in the wq4591 attempt-2 transcript (masked)."""
import glob
import json
import os
import sys

sys.path.insert(0, "/home/lumen/foreman/harness")
import masking  # noqa: E402

SESSION = "539d080c-d10d-4b8d-ada6-fac1df405216"
path = glob.glob(os.path.expanduser(
    "~/.claude/projects/*/" + SESSION + ".jsonl"))[0]
for lineno, line in enumerate(open(path, encoding="utf-8"), 1):
    if "bypass the intent" not in line.lower():
        continue
    try:
        frame = json.loads(line)
    except ValueError:
        continue
    masked = masking.mask_text(line)[0]
    i = masked.lower().find("bypass the intent")
    print("line %s type=%s" % (lineno, frame.get("type")))
    print("  ctx: ...%s..." % masked[max(0, i - 260):i + 120].replace("\n", " \\n "))
