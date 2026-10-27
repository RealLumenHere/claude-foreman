"""Summarize the last frames of the wq4591 attempt-2 transcript (names only, no content)."""
import glob
import json
import os

SESSION = "539d080c-d10d-4b8d-ada6-fac1df405216"
path = glob.glob(os.path.expanduser(
    "~/.claude/projects/*/" + SESSION + ".jsonl"))[0]
frames = []
for lineno, line in enumerate(open(path, encoding="utf-8"), 1):
    try:
        frame = json.loads(line)
    except ValueError:
        continue
    msg = frame.get("message")
    content = (msg.get("content") if isinstance(msg, dict) else None) or []
    kinds = []
    for b in content:
        if isinstance(b, dict) and b.get("type") == "tool_use":
            ti = b.get("input") or {}
            arg = (ti.get("command") or ti.get("file_path") or "")[:60]
            kinds.append("use:%s(%s)" % (b.get("name"), arg.replace("\n", " ")))
        elif isinstance(b, dict) and b.get("type") == "tool_result":
            kinds.append("result:err=%s" % bool(b.get("is_error")))
    frames.append((lineno, frame.get("type"), "; ".join(kinds)[:140]))
print("total lines:", frames[-1][0] if frames else 0)
for f in frames[-12:]:
    print(f)
