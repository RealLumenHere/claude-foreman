"""Count and sample hook-deny / refusal frames in the wq4591 attempt-2 transcript (masked)."""
import glob
import json
import os
import sys

sys.path.insert(0, "/home/lumen/foreman/harness")
import masking  # noqa: E402

SESSION = "539d080c-d10d-4b8d-ada6-fac1df405216"
path = glob.glob(os.path.expanduser(
    "~/.claude/projects/*/" + SESSION + ".jsonl"))[0]
NEEDLES = ("blocked", "denied", "refus", "approval", "permission")
hits = []
for lineno, line in enumerate(open(path, encoding="utf-8"), 1):
    try:
        frame = json.loads(line)
    except ValueError:
        continue
    msg = frame.get("message")
    content = (msg.get("content") if isinstance(msg, dict) else None) or []
    for b in content:
        if not (isinstance(b, dict) and b.get("type") == "tool_result"
                and b.get("is_error")):
            continue
        c = b.get("content")
        text = c if isinstance(c, str) else " ".join(
            x.get("text", "") for x in c if isinstance(x, dict)) if isinstance(c, list) else ""
        low = text.lower()
        if any(n in low for n in NEEDLES):
            hits.append((lineno, masking.mask_text(text)[0][:220].replace("\n", " \\n ")))
print("total error-result frames matching:", len(hits))
for lineno, t in hits:
    print("line %s: %s" % (lineno, t))
