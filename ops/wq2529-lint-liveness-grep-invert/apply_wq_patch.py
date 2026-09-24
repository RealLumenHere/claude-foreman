#!/usr/bin/env python3
"""Idempotent patch for /home/lumen/bin/wq — lint out grep -v/-qv/-vq in Liveness: lines
(wq#2529).

wq#1975 shipped 'Liveness: git show HEAD:<doc> | grep -qv <needle>'. grep -qv (and any
grep invocation whose short-opt cluster carries the invert-match 'v', or --invert-match)
succeeds whenever ANY line lacks the needle, so the check can never go false and a
Frank-gated ticket built on it can never self-close. Found by orch-batch2-sec re-verify
2026-09-24; wq#1975's own Liveness: line was hand-corrected to grep -q, but the CLI had
no guard stopping the next ticket from shipping the same bug. This patch adds that guard
to `wq add` and `wq edit`: a Liveness: line containing an inverted grep is refused, not
silently filed, with the fix (drop -v, use grep -q/-qc/-L) printed in the error. Each
block is guarded by a marker so re-running is a no-op. Takes a timestamped .bak first.

    python3 apply_wq_patch.py [--wq /home/lumen/bin/wq] [--check]
"""
import argparse
import shutil
import sys
import time

MARK_HELPERS = "# --- wq#2529 liveness grep-invert lint ---"
MARK_ADD = "# wq#2529: liveness grep-invert lint"
MARK_EDIT = "# wq#2529: liveness grep-invert lint (edit)"

HELPERS = r'''
# --- wq#2529 liveness grep-invert lint ---
def _liveness_grep_invert_line(text):
    """wq#2529: wq#1975 shipped 'Liveness: git show HEAD:<doc> | grep -qv <needle>' — -v
    inverts the match, so the probe succeeds as soon as ANY line lacks the needle and can
    never go false (the ticket could never self-close). Returns the offending Liveness:
    line, or None. Flags any grep invocation whose short-opt cluster carries 'v' (-v,
    -qv, -vq, -Ev, ...) or the --invert-match long form; a plain 'v' inside an unrelated
    long option (e.g. --version) does not count."""
    for line in (text or "").splitlines():
        if "Liveness:" not in line:
            continue
        tokens = line.split()
        for i, tok in enumerate(tokens):
            if tok != "grep" and not tok.endswith("/grep"):
                continue
            for nxt in tokens[i + 1:]:
                if nxt in ("|", ";", "&&"):
                    break
                if nxt.startswith("--"):
                    if nxt == "--invert-match":
                        return line
                    continue
                if nxt.startswith("-") and "v" in nxt[1:]:
                    return line
    return None

def _reject_liveness_grep_invert(text):
    """wq#2529: hard-refuse (no --force) a Liveness: line whose grep uses -v/-qv/-vq —
    the fix is always grep -q (or grep -c/-L), never grep -v, for a probe that must be
    able to go both true and false as the underlying condition changes."""
    bad = _liveness_grep_invert_line(text)
    if bad is None:
        return
    eprint(f"REFUSED: wq#2529 — Liveness: line uses grep -v (inverted match), which is "
           f"true whenever ANY line lacks the needle and can never go false:")
    eprint(f"  {bad.strip()}")
    eprint("  Fix: drop the -v. Use 'grep -q <needle>' if the probe should go true once "
           "the needle IS present, or restructure with 'grep -qL <needle>' / a negated "
           "'! grep -q' if it should go true once the needle is ABSENT. grep -v/-qv/-vq "
           "is never the right shape for a Liveness: probe.")
    sys.exit(1)
# --- end wq#2529 helpers ---
'''

ADD_HOOK = '''    # wq#2529: liveness grep-invert lint — refuse before the on-ramp check below ever
    # gets to persist a probe that can never go false (wq#1975's bug, now a hard gate)
    _reject_liveness_grep_invert(
        (a.detail or "") + (("\\nLiveness: " + a.liveness) if getattr(a, "liveness", None) else ""))
'''
EDIT_HOOK = '''    # wq#2529: liveness grep-invert lint (edit) — same guard as `wq add`, applied to any
    # --detail that carries (or edits) a Liveness: line
    if a.detail is not None:
        _reject_liveness_grep_invert(a.detail)
'''

ANCHOR_HELPERS = "def cmd_add(a):"
ANCHOR_ADD = ("    # wq#1478: creation gate — a Frank-gate whose premise is already met is not filed;\n"
              "    # wq#1676: one with no Liveness: line at all is refused unless --no-probe/--accept-probe\n"
              "    a.detail = _liveness_creation_check(a.title, a.detail, a.lane, frank_gated, force=a.force, verb=\"add\",")
ANCHOR_EDIT = ("    for col,val in ((\"title\",a.title),(\"detail\",a.detail),(\"pri\",a.pri),(\"lane\",a.lane),\n"
               "                    (\"status\",a.status),")


def patch(src):
    changed = []
    if MARK_HELPERS not in src:
        assert src.count(ANCHOR_HELPERS) == 1, "cmd_add anchor not unique"
        src = src.replace(ANCHOR_HELPERS, HELPERS.lstrip("\n") + "\n" + ANCHOR_HELPERS)
        changed.append("helpers")
    if MARK_ADD not in src:
        assert src.count(ANCHOR_ADD) == 1, "cmd_add insert anchor not unique"
        src = src.replace(ANCHOR_ADD, ADD_HOOK + ANCHOR_ADD)
        changed.append("cmd_add hook")
    if MARK_EDIT not in src:
        assert src.count(ANCHOR_EDIT) == 1, "cmd_edit insert anchor not unique"
        src = src.replace(ANCHOR_EDIT, EDIT_HOOK + ANCHOR_EDIT)
        changed.append("cmd_edit hook")
    return src, changed


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--wq", default="/home/lumen/bin/wq")
    ap.add_argument("--check", action="store_true", help="report whether the patch is applied; write nothing")
    a = ap.parse_args(argv)
    src = open(a.wq).read()
    new, changed = patch(src)
    if a.check:
        print("applied" if not changed else f"missing: {', '.join(changed)}")
        return 0 if not changed else 1
    if not changed:
        print("already applied — nothing to do"); return 0
    bak = f"{a.wq}.bak.{time.strftime('%Y%m%d_%H%M%S')}_pre_wq2529"
    shutil.copy2(a.wq, bak)
    compile(new, a.wq, "exec")
    with open(a.wq, "w") as f:
        f.write(new)
    import os
    os.chmod(a.wq, 0o755)
    print(f"patched {a.wq}: {', '.join(changed)} (backup {bak})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
