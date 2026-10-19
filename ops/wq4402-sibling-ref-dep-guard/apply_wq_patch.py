#!/usr/bin/env python3
"""Idempotent patch for /home/lumen/bin/wq — refuse/warn when a ticket's title/detail
names a sibling wq#NNNN as the thing it wraps but --deps is empty (wq#4402).

wq#4396 (INTEGRATE: remote-cli as Limpet shared terminal backend) existed only to wrap
wq#4395's deliverable, but was filed with an EMPTY --deps field 1 second after wq#4395
and claimed 4 seconds after it -- both fanned out in parallel, the dependency expressed
only in prose. The result: the wrapper was written against an invented wire protocol
while the real sibling artifact sat on disk 26s before the commit, shipping 8/8 green
with 0/4 tools working (reports/4397_verify_wq4396_remote_cli_protocol_mismatch.md,
reports/4398_judgment_wq4397_remote_cli_protocol.md, ISSUE-LOG row 1473). deps_met()
(wq:1065-1070) already refuses a claim unless every dep is status=done -- nothing was
unenforced, the dependency was simply never recorded, so there was nothing to enforce.

This patch adds the guard at CREATION, not claim: `wq add` scans title+detail for a
'wq#NNNN'/'wqNNNN'/'wq NNNN' reference to an OPEN (not done) sibling task. A reference
to an already-DONE sibling is a normal back-reference/citation and never fires the
guard. If --deps is empty and an open-sibling reference is found: the 'build' lane
(where wq#4396 itself was filed) REFUSES with exit 1 and a hint to add --deps or
--force; every other lane prints a WARNING and proceeds -- a mention is not always a
dependency, so lanes outside build get an advisory instead of a hard stop.

Each block is guarded by a marker so re-running is a no-op. Takes a timestamped .bak
first.

    python3 apply_wq_patch.py [--wq /home/lumen/bin/wq] [--check]
"""
import argparse
import shutil
import sys
import time

MARK_HELPERS = "# --- wq#4402 sibling-ref dep guard ---"
MARK_ADD = "# wq#4402: sibling-ref dep guard"

HELPERS = r'''
# --- wq#4402 sibling-ref dep guard ---
_WQ_REF_RE = re.compile(r'\bwq\s*#?\s*(\d+)\b', re.IGNORECASE)
def undeclared_sibling_refs(c, title, detail):
    """wq#4402: scans title+detail for a 'wq#NNNN'/'wqNNNN'/'wq NNNN' reference to an
    OPEN (not done) task and returns [(id, status), ...] for each. A back-reference to
    an already-done sibling is a normal citation, not an undeclared dependency, so it
    is excluded here -- the guard must not fire on routine references to closed work."""
    text = f"{title or ''}\n{detail or ''}"
    seen, out = set(), []
    for m in _WQ_REF_RE.finditer(text):
        rid = m.group(1)
        if rid in seen: continue
        seen.add(rid)
        row = c.execute("SELECT status FROM tasks WHERE id=?", (rid,)).fetchone()
        if row and row[0] != 'done':
            out.append((rid, row[0]))
    return out
def print_sibling_ref_notice(refs, refuse):
    """wq#4402: refuse (build lane) or warn (elsewhere) -- same prose, different exit."""
    ids = ', '.join(f"wq#{rid} [{status}]" for rid, status in refs)
    lead = "REFUSED" if refuse else "WARNING"
    print(f"{lead}: title/detail names {ids} as if this ticket wraps/depends on it, but "
          f"--deps is empty (wq#4402 -- wq#4396 shipped 8/8 green with 0/4 tools working "
          f"this exact way: the dependency was only ever prose, deps_met() had nothing "
          f"to enforce).")
    if refuse:
        print("  Add --deps with the referenced id(s), or --force if this really is just "
              "a back-reference and not a dependency.")
    else:
        print("  If this ticket actually wraps that task's deliverable, add --deps so "
              "deps_met() can enforce it before claim.")
# --- end wq#4402 helpers ---
'''

ADD_HOOK = '''    # wq#4402: sibling-ref dep guard -- a title/detail naming an OPEN sibling wq# with
    # no --deps is either refused (build lane) or warned (elsewhere); a done sibling is
    # a routine back-reference and never fires this
    if not (a.deps or "").strip():
        sib_refs = undeclared_sibling_refs(c, a.title, a.detail)
        if sib_refs:
            refuse = (a.lane == "build" and not a.force)
            print_sibling_ref_notice(sib_refs, refuse)
            if refuse:
                c.close(); sys.exit(1)
'''

ANCHOR_HELPERS = "def cmd_add(a):"
ANCHOR_ADD = "    a.deps = normalize_deps(a.deps)\n    if not a.force:\n        nt = norm_title(a.title)"


def patch(src):
    changed = []
    if MARK_HELPERS not in src:
        assert src.count(ANCHOR_HELPERS) == 1, "cmd_add anchor not unique"
        src = src.replace(ANCHOR_HELPERS, HELPERS.lstrip("\n") + "\n" + ANCHOR_HELPERS)
        changed.append("helpers")
    if MARK_ADD not in src:
        assert src.count(ANCHOR_ADD) == 1, "cmd_add insert anchor not unique"
        src = src.replace(ANCHOR_ADD, "    a.deps = normalize_deps(a.deps)\n" + ADD_HOOK + "    if not a.force:\n        nt = norm_title(a.title)")
        changed.append("cmd_add hook")
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
    bak = f"{a.wq}.bak.{time.strftime('%Y%m%d_%H%M%S')}_pre_wq4402"
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
