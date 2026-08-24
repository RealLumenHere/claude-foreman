#!/usr/bin/env python3
"""wq#951 -- does claude-code honour BASH_MAX_OUTPUT_LENGTH? Live, two-arm test.

Reading the 2.1.233 binary says yes (UNCAPPED_BASH.md §2): the Bash tool's
truncator calls a getter over `process.env.BASH_MAX_OUTPUT_LENGTH` with default
30000 and upper limit 150000, and no lower bound. This proves it end to end.

Both arms run the SAME command through a real headless session under the real
worker-settings.json, so guard.sh is live. The command ends in `2>&1`, which
trips guard.sh's SKIP_CAP background clause -- so the `head -c` rewrite is NOT
applied and anything that truncates the result can only be claude-code itself.
(That the redirect trips a clause meant for `cmd &` is itself the §3 finding;
here it is just a convenient way to hold the guard's rewrite off.)

  arm A  BASH_MAX_OUTPUT_LENGTH unset  -> expect the full 8000 chars (< default 30000)
  arm B  BASH_MAX_OUTPUT_LENGTH=2000   -> expect ~2000 chars + a truncation notice

Reproduce:  python3 research/native_bash_cap_test.py
Cost:       two short Haiku sessions.
"""

import json
import os
import subprocess
import sys
import tempfile

CLAUDE = "/home/lumen/.npm-global/bin/claude"
SETTINGS = "/home/lumen/foreman/harness/worker-settings.json"
MODEL = "claude-haiku-4-5-20251001"
PAYLOAD = 8000

PROMPT = (
    "Run exactly this one Bash command and then stop. Do not explain, do not run "
    "anything else, do not use any other tool:\n\n"
    f"""python3 -c "print('x'*{PAYLOAD})" 2>&1\n\n"""
    "After it returns, reply with the single word DONE."
)


def run_arm(label, cap, workdir):
    env = dict(os.environ)
    env.pop("BASH_MAX_OUTPUT_LENGTH", None)
    if cap is not None:
        env["BASH_MAX_OUTPUT_LENGTH"] = str(cap)
    cmd = [CLAUDE, "-p", PROMPT, "--model", MODEL, "--max-turns", "3",
           "--settings", SETTINGS, "--permission-mode", "acceptEdits",
           "--allowedTools", "Bash",
           "--output-format", "stream-json", "--verbose"]
    log = os.path.join(workdir, f"{label}.jsonl")
    with open(log, "w") as fh:
        p = subprocess.run(cmd, stdout=fh, stderr=subprocess.PIPE, text=True,
                           cwd=workdir, env=env, timeout=300)
    if p.returncode != 0:
        print(f"  {label}: claude exited {p.returncode}: {p.stderr.strip()[:200]}")
    return log


def measure(log):
    """(command, result_length, truncation_notice) for the first Bash call in a run."""
    cmds = {}
    with open(log, errors="replace") as fh:
        for line in fh:
            try:
                frame = json.loads(line)
            except ValueError:
                continue
            m = frame.get("message") or {}
            content = m.get("content") if isinstance(m, dict) else None
            for b in content if isinstance(content, list) else []:
                if b.get("type") == "tool_use" and b.get("name") == "Bash":
                    cmds[b.get("id")] = (b.get("input") or {}).get("command", "")
                elif b.get("type") == "tool_result" and b.get("tool_use_id") in cmds:
                    c = b.get("content")
                    text = c if isinstance(c, str) else json.dumps(c)
                    notice = next((ln for ln in text.splitlines()
                                   if "truncat" in ln.lower()), "")
                    return cmds[b["tool_use_id"]], len(text), notice
    return None, None, None


def main():
    with tempfile.TemporaryDirectory(prefix="wq951-native-") as work:
        print(f"payload: {PAYLOAD} chars, guard.sh live (SKIP_CAP via `2>&1`)\n")
        for label, cap in (("A-unset", None), ("B-2000", 2000)):
            log = run_arm(label, cap, work)
            command, size, notice = measure(log)
            shown = "unset" if cap is None else str(cap)
            if size is None:
                print(f"  {label:<8} BASH_MAX_OUTPUT_LENGTH={shown:<6} "
                      f"NO BASH RESULT (see {log})")
                continue
            rewritten = "head -c" in (command or "")
            print(f"  {label:<8} BASH_MAX_OUTPUT_LENGTH={shown:<6} "
                  f"result={size:>6} chars  guard-rewrote={rewritten}")
            if notice:
                print(f"           notice: {notice.strip()[:110]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
