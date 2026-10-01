#!/usr/bin/env python3
"""wq#944 — lint the two channels that carry PRODUCT-LIVENESS to dispatched audits.

wq#909 put the rule in playbooks/fleet-audit.md and pointed at it from
playbooks/session-start.md. No dispatched audit prompt reads session-start.md: an
fworker-dispatched one-shot audit runs the numbered bootstrap written into its own
prompt text and nothing else. wq#944 rewired it through the two channels those
sessions do read, and this lint is what keeps them wired:

  1. instincts.yaml — every audit prompt's bootstrap step 1 is
     `claude_context.sh --core`. That injected `head -c 4096` of the file when this
     lint was written; since wq#958 it injects what `instincts-render` emits, which
     is every rule by default (minerva/context/claude_context.sh:56). The lint checks
     the rule is in what the channel delivers, not merely in the file (wq#3222: the
     4096-byte check failed once the file grew past it, though the rule still arrived).
  2. the dated audit prompts under /home/lumen/prompts/ — each must name the
     playbook inside its own SESSION BOOTSTRAP block, which is the only part of a
     prompt a dispatched session is guaranteed to act on.

Exit 0 = both channels intact. Exit 1 = a channel is broken, with the reason.
"""
import os
import re
import subprocess
import sys

INSTINCTS = "/home/lumen/instincts.yaml"
PROMPTS_DIR = "/home/lumen/prompts"
PLAYBOOK = "/home/lumen/instructions/playbooks/fleet-audit.md"

# minerva/context/claude_context.sh:56 — `/home/lumen/bin/instincts-render`.
RENDER_BIN = os.environ.get("INSTINCTS_RENDER_BIN", "/home/lumen/bin/instincts-render")

MARKER = "PRODUCT-LIVENESS"
PLAYBOOK_REF = "playbooks/fleet-audit.md"
BOOTSTRAP_RE = re.compile(r"^#+\s*session bootstrap\b", re.I | re.M)
NEXT_HEADING_RE = re.compile(r"^#+\s", re.M)


def check_instincts(path=INSTINCTS, render=None):
    """The rule must be in what --core delivers and point at the playbook."""
    render = render or RENDER_BIN
    try:
        done = subprocess.run([render, "--file", path], capture_output=True, text=True,
                              timeout=60)
    except (OSError, subprocess.TimeoutExpired) as e:
        return [f"{path}: {render} did not run ({e})"]
    if done.returncode != 0:
        return [f"{path}: {render} exited {done.returncode}, so --core delivers no rules"]
    window = done.stdout
    if MARKER not in window:
        return [
            f"{path}: no {MARKER} rule in what {render} emits — "
            f"claude_context.sh --core injects only that, so it reaches no audit session"
        ]
    line = next(ln for ln in window.splitlines() if MARKER in ln)
    if PLAYBOOK_REF not in line:
        return [f"{path}: the {MARKER} rule does not point at {PLAYBOOK_REF}"]
    return []


def bootstrap_block(text):
    """The numbered bootstrap section of a prompt, or None if it has none."""
    m = BOOTSTRAP_RE.search(text)
    if not m:
        return None
    rest = text[m.end():]
    nxt = NEXT_HEADING_RE.search(rest)
    return rest[: nxt.start()] if nxt else rest


def audit_prompts(prompts_dir=PROMPTS_DIR):
    """Dispatchable audit prompts: audit-shaped name, .md, has a bootstrap, not a backup."""
    out = []
    for fn in sorted(os.listdir(prompts_dir)):
        if not fn.endswith(".md") or "audit" not in fn.lower():
            continue
        path = os.path.join(prompts_dir, fn)
        with open(path, errors="ignore") as f:
            text = f.read()
        if bootstrap_block(text) is not None:
            out.append((path, text))
    return out


def check_prompts(prompts_dir=PROMPTS_DIR):
    problems = []
    found = audit_prompts(prompts_dir)
    if not found:
        return [f"{prompts_dir}: no audit prompt with a SESSION BOOTSTRAP section found"]
    for path, text in found:
        if PLAYBOOK_REF not in bootstrap_block(text):
            problems.append(
                f"{path}: SESSION BOOTSTRAP does not read {PLAYBOOK_REF} — "
                f"a dispatched session will never see the PRODUCT-LIVENESS rule"
            )
    return problems


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    instincts = argv[0] if len(argv) > 0 else INSTINCTS
    prompts_dir = argv[1] if len(argv) > 1 else PROMPTS_DIR
    problems = check_instincts(instincts) + check_prompts(prompts_dir)
    for p in problems:
        print(f"FAIL {p}")
    if problems:
        print(f"\n{len(problems)} broken channel(s). Fix per {PLAYBOOK} §3.")
        return 1
    print(f"OK PRODUCT-LIVENESS reaches audit sessions via {instincts} and {prompts_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
