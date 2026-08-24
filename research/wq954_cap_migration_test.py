#!/usr/bin/env python3
"""wq#954 -- does the migrated cap actually cap? Live, three-arm test.

wq#951's research/native_bash_cap_test.py proved the premise (claude-code honours
BASH_MAX_OUTPUT_LENGTH). This proves the end state: with the `head -c` rewrite and
SKIP_CAP deleted from guard.sh, the env var alone caps every command shape -- including
the shell-state verbs the old pipeline cap had to exempt.

Every arm runs THIS BRANCH's guard.sh (a temp settings file, not the deployed
/home/lumen/foreman one) so the deletion is what is under test.

  arm A  plain command, cap unset    -> 8000 chars, guard did not rewrite
  arm B  plain command, cap 2000     -> ~2000 chars  (wq#729's case, same outcome)
  arm C  `cd /tmp && ...`, cap 2000  -> ~2000 chars  (the payoff: SKIP_CAP's 725 KB
                                        residual is capped now, and the `cd` survives)

Arm C is the whole ticket. Under the old guard it came back at the full 8000: `cd`
tripped SKIP_CAP, because piping it would have subshelled the directory change away.

Reproduce:  python3 research/wq954_cap_migration_test.py          # all three arms
            python3 research/wq954_cap_migration_test.py C-cd     # just one
Cost:       one short Haiku session per arm.
"""

import json
import os
import subprocess
import sys
import tempfile

CLAUDE = "/home/lumen/.npm-global/bin/claude"
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GUARD = os.path.join(REPO, "harness", "guard.sh")
SETTINGS_TEMPLATE = os.path.join(REPO, "harness", "worker-settings.json")
MODEL = "claude-haiku-4-5-20251001"
PAYLOAD = 8000

# `pwd` is in the payload command so arm C proves the `cd` actually took effect in the
# same shell -- a subshelled `cd` would report the session cwd, not /tmp.
PLAIN = f"""python3 -c "print('x'*{PAYLOAD})" """
STATEFUL = f"""cd /tmp && pwd && python3 -c "print('x'*{PAYLOAD})" """

ARMS = [
    ("A-unset", None, PLAIN),
    ("B-plain", 2000, PLAIN),
    ("C-cd", 2000, STATEFUL),
]


def prompt_for(command):
    return ("Run exactly this one Bash command and then stop. Do not explain, do not run "
            "anything else, do not use any other tool:\n\n"
            f"{command}\n\n"
            "After it returns, reply with the single word DONE.")


def branch_settings(workdir):
    """worker-settings.json with every deployed guard path repointed at this branch."""
    with open(SETTINGS_TEMPLATE) as fh:
        raw = fh.read()
    raw = raw.replace("/home/lumen/foreman/harness/guard.sh", GUARD)
    path = os.path.join(workdir, "branch-settings.json")
    with open(path, "w") as fh:
        fh.write(raw)
    return path


def run_arm(label, cap, command, settings, workdir):
    env = dict(os.environ)
    env.pop("BASH_MAX_OUTPUT_LENGTH", None)
    if cap is not None:
        env["BASH_MAX_OUTPUT_LENGTH"] = str(cap)
    cmd = [CLAUDE, "-p", prompt_for(command), "--model", MODEL, "--max-turns", "3",
           "--settings", settings, "--permission-mode", "acceptEdits",
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
    """(command, result_length, truncation_notice, result_text) for the first Bash call."""
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
                    return cmds[b["tool_use_id"]], len(text), notice, text
    return None, None, None, None


def main():
    failures = []
    with tempfile.TemporaryDirectory(prefix="wq954-cap-") as work:
        settings = branch_settings(work)
        print(f"payload: {PAYLOAD} chars, guard.sh under test: {GUARD}\n")
        # `... wq954_cap_migration_test.py C-cd` re-runs one arm, not all three.
        wanted = set(sys.argv[1:])
        for label, cap, command in ARMS:
            if wanted and label not in wanted:
                continue
            log = run_arm(label, cap, command, settings, work)
            issued, size, notice, text = measure(log)
            shown = "unset" if cap is None else str(cap)
            if size is None:
                print(f"  {label:<8} BASH_MAX_OUTPUT_LENGTH={shown:<6} NO BASH RESULT ({log})")
                failures.append(f"{label}: no Bash result")
                continue
            # The guard must hand the command through verbatim -- no wrapper, no pipe.
            rewritten = "head -c" in issued or issued.strip() != command.strip()
            print(f"  {label:<8} BASH_MAX_OUTPUT_LENGTH={shown:<6} "
                  f"result={size:>6} chars  guard-rewrote={rewritten}")
            if notice:
                print(f"           notice: {notice.strip()[:70]}")
            if rewritten:
                failures.append(f"{label}: guard rewrote the command: {issued!r}")
            if cap is None and size < PAYLOAD:
                failures.append(f"{label}: uncapped arm truncated at {size}")
            if cap is not None and size > cap + 500:
                failures.append(f"{label}: cap {cap} did not hold, got {size}")
            if label == "C-cd":
                # `pwd` echoes /tmp somewhere in the kept head of the result. Search the
                # whole text, not the first line: when a result is large the CLI prefixes
                # it with a `<persisted-output>` notice naming a /home/lumen spool file.
                if "/tmp" not in text:
                    failures.append(f"C-cd: the `cd` did not take effect: {text[:120]!r}")
                else:
                    print("           `cd` survived: pwd reported /tmp")
    print()
    for f in failures:
        print(f"FAIL {f}")
    print("PASS: the cap moved and nothing opts out" if not failures
          else f"{len(failures)} failure(s)")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
