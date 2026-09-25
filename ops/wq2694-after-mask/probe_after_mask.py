#!/usr/bin/env python3
"""wq#2694 acceptance probe: a live value glued BEHIND masked output.

Loads harness/masking.py twice — as of BASE (the last main before the fix, pinned) and
as in the working tree — and reports, per rule and direction, whether the live value
survives masking and what alarms. Then scores what the `_AFTER_MASK` exception costs on
text nobody wrote for it: the hit set of both versions over kb/ISSUE-LOG.md pinned at
KB_REV (outside this repo, 900 rows of mask-alarm prose quoting masked values) and over
every tracked file under harness/ and ops/ at BASE.

Prints labels, counts and lengths only — never a matched value. Re-runnable:
    python3 ops/wq2694-after-mask/probe_after_mask.py
Exit 0 iff every adjacency row masks and alarms on the new code AND the corpus hit sets
differ only where the exception applies (a match starting right after `MASKED`).
"""
import importlib.util
import os
import subprocess
import sys
import tempfile

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
HARNESS = os.path.join(REPO, "harness")
BASE = "92586bf"                 # main at dispatch, before wq#2694
KB, KB_REV = "/home/lumen/kb", "8d248d2"

sys.path.insert(0, HARNESS)      # masking imports live_code


def _git(repo, *args):
    return subprocess.run(["git", "-C", repo, *args], check=True,
                          capture_output=True, text=True, errors="replace").stdout


def _load(name, source):
    with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False) as fh:
        fh.write(source)
    spec = importlib.util.spec_from_file_location(name, fh.name)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    os.unlink(fh.name)
    return mod


OLD = _load("masking_base", _git(REPO, "show", BASE + ":harness/masking.py"))
NEW = _load("masking_new", open(os.path.join(HARNESS, "masking.py")).read())

# Synthetic bodies, split so this file does not arm the tripwire it probes.
B36 = "A1b2C3d4E5f6G7h8I9j0" + "K1l2M3n4O5p6Q7r8"
URL_USER, URL_HOST = "https://" + "lumen:", "@git.example"
RULES = [("github", "ghp_", B36), ("github", "github_pat_", B36 + "_" + B36),
         ("cloudflare", "cfut_", B36), ("sk-key", "sk-ant-", B36),
         ("slack", "xoxb-", "1234-" + B36), ("aws", "AKIA", "ABCDEFGHIJ234567"),
         ("kv-secret", 'GITEA_TOKEN="', B36), ("url-auth", URL_USER, B36)]


def adjacency():
    ok = True
    print("rule        direction                 BASE           NEW")
    for label, prefix, body in RULES:
        end = URL_HOST if prefix == URL_USER else ""
        residue = ("ghp_" if prefix == URL_USER else prefix) + NEW.MASK
        for case, text in (("own output in front", residue + prefix + body + end),
                           ("bare literal in front", NEW.MASK + prefix + body + end)):
            row = []
            for mod in (OLD, NEW):
                masked, hits = mod.mask_text(text)
                row.append(("LEAK" if body in masked else "masked") + str(hits))
            good = row[1] == "masked" + str([label])
            ok &= good
            print(f"{label:11} {case:25} {row[0]:14} {row[1]}{'' if good else '  FAIL'}")
    return ok


def _hits(mod, text):
    return {(h["label"], h["value_len"], h["prefix"]) for h in mod.scan(text)[1]}


def corpus():
    docs = {"kb/ISSUE-LOG.md@" + KB_REV: _git(KB, "show", KB_REV + ":ISSUE-LOG.md")}
    for path in _git(REPO, "ls-files", "--with-tree=" + BASE, "harness", "ops").split():
        if path.endswith((".py", ".md", ".sh", ".txt", ".json", ".jsonl")):
            docs[path] = _git(REPO, "show", f"{BASE}:{path}")
    seams = sum(t.count(NEW.MASK) for t in docs.values())
    changed = {p: (_hits(OLD, t), _hits(NEW, t)) for p, t in docs.items()}
    changed = {p: h for p, h in changed.items() if h[0] != h[1]}
    print(f"\ncorpus: {len(docs)} files, {seams} occurrences of the mask literal")
    print(f"files whose hit set changed: {len(changed)}")
    for path, (old, new) in changed.items():
        print(f"  {path}: lost {sorted(old - new)} gained {sorted(new - old)}")
    return not changed


if __name__ == "__main__":
    a, c = adjacency(), corpus()
    print("\nPASS" if a and c else "\nFAIL")
    sys.exit(0 if a and c else 1)
