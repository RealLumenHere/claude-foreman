"""Masked denial-frame extractor for wq#4683 RCA.

Reads the wq4591 attempt-2 transcript through the same mask-at-read path
failure_record.py uses (wq#2275), printing only permission-denial frames:
line number, tool, command (masked, truncated), error head (masked, truncated).
Never prints a raw frame.
"""
import glob
import json
import os
import sys

sys.path.insert(0, "/home/lumen/foreman/harness")
from action_gate import is_permission_denial  # noqa: E402
import masking  # noqa: E402
from failure_record import _result_text  # noqa: E402

SESSION = "539d080c-d10d-4b8d-ada6-fac1df405216"


def main():
    paths = glob.glob(os.path.expanduser(
        "~/.claude/projects/*/" + SESSION + ".jsonl"))
    if not paths:
        print("transcript not found")
        return 1
    pending = {}
    for lineno, line in enumerate(open(paths[0], encoding="utf-8"), 1):
        try:
            frame = json.loads(line)
        except ValueError:
            continue
        msg = frame.get("message")
        content = (msg.get("content") if isinstance(msg, dict) else None) or []
        if frame.get("type") == "assistant":
            for b in content:
                if isinstance(b, dict) and b.get("type") == "tool_use":
                    pending[b.get("id")] = (lineno, b.get("name") or "",
                                            b.get("input") or {})
        elif frame.get("type") == "user":
            for b in content:
                if not (isinstance(b, dict) and b.get("type") == "tool_result"
                        and b.get("is_error")):
                    continue
                use_line, tool, ti = pending.pop(
                    b.get("tool_use_id"), (None, "", {}))
                text = _result_text(b)
                if not (is_permission_denial(text)
                        or "bypass the intent" in text.lower()):
                    continue
                cmd = ti.get("command") or ti.get("file_path") or ""
                print("USE line %s TOOL %s" % (use_line, tool))
                print("  CMD: %s" % masking.mask_text(cmd)[0][:400].replace("\n", " \\n "))
                print("  ERR line %s: %s" % (
                    lineno, masking.mask_text(text)[0][:500].replace("\n", " \\n ")))
                print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
