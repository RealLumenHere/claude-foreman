#!/usr/bin/env python3
"""wq#3051 judgment probe — the orphan backstop's trailing run re-opening on a prefix.

wq#3037 CONTRADICTED wq#3018: `masking._orphan_matches()`'s combined-floor test
counts the characters behind an already-masked placeholder as a continuation of the
body in FRONT of it, even when those characters are a fresh known token prefix. The
docstring justified that by claiming "this rule's charset cannot run through" a second
prefix; for five of the six `_ORPHAN_SHAPES` rows the prefix's own letters are a subset
of its own body charset, so the charset runs straight through. The result is a hit — and
`token_engine_daemon.read_worktree_file`'s `Deny("body_mask_tripwire", ...)` — on text
holding no credential at all.

Five sections, and they pull in opposite directions on purpose:

  1 reopen     the over-fire corpus. `<prefix><under-floor fill>MASKED<prefix>`, built
               from OUTSIDE the population wq#3018 checked itself against (wq#2630):
               same-family where the charset admits the prefix, cross-family (AKIA,
               which every charset but slack's admits) everywhere else. Bar: no hit,
               and `mask_text` returns its input byte-for-byte, which is what makes any
               hit attributable to this backstop alone rather than to a PATTERNS match.
               This section FAILS on the code wq#3037 measured; that is the point.
  2 underfloor wq#2868 section B verbatim, the 96 real under-floor leaks the backstop
               exists for. Bar: still 0 SILENT. A tightening that quiets section 1 by
               quieting this too has re-opened wq#2922.
  5 secondlink the same chain with a full-length body on the second link. Bar: it
               still fires and is still redacted. The fix declines to COUNT that second
               mention toward the first one's floor on the grounds that it is read
               elsewhere; this is where that reasoning breaks if it is wrong.
  3 daemon     the same bars against the mirrored copy in token_engine_daemon.
  4 outside    committed files OUTSIDE wq#3018's own 14-file quiet sweep, read
               rev-PINNED (`git show <rev>:<path>`) so the evidence is re-runnable —
               including wq#3037's own report, whose subject matter is precisely the
               masked-output adjacency this rule mis-reads. Bar: no hit.

Run: python3 reports/wq3051_probe_orphan_prefix_reopen.py [path/to/harness]
"""
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
# Same argv convention as wq2868's probe: a dir lets this run against a historical
# masking.py, which is how the before/after in the judgment report was produced.
_ARG = sys.argv[1] if len(sys.argv) > 1 and os.path.isdir(sys.argv[1]) else None
sys.path.insert(0, os.path.join(REPO, "token-engine"))
sys.path.insert(0, os.path.join(REPO, "harness"))
# In FRONT of harness/, not instead of it: an extracted masking.py still imports
# live_code from the working tree's harness dir.
if _ARG:
    sys.path.insert(0, _ARG)
sys.path.insert(0, HERE)
import masking  # noqa: E402
import wq2868_probe_underfloor_class as under  # noqa: E402


def _daemon():
    """The mirrored copy, imported the way wq#3018's probe does it — `fworker` first,
    for the side effect of putting the daemon's root-owned `fleet_exec_policy` next to
    it on sys.path."""
    import fworker  # noqa: F401
    import token_engine_daemon as ted
    return ted

# Split at the prefix seam, never written glued (instinct 4 / wq#2795), and the glued
# shapes below are built at runtime so no committed byte of this file is one.
GH = "gh" + "p_"
GHPAT = "github" + "_pat_"
CFUT = "cf" + "ut_"
SK = "s" + "k-"
XOX = "xo" + "xb-"
AKIA = "AK" + "IA"
FILL = "Q"          # in every body charset here, in none of the prefixes

# label, prefix, floor, fill length, the prefixes this rule's own charset can run
# through. github's `[A-Za-z0-9]` excludes `_` and `-`, so the only prefix reachable
# inside a github body is AKIA — the wq#2702 glue read() phase 2 already treats as a
# prefix. slack's `[A-Za-z0-9-]` excludes `_`, and AKIA's capitals are in it.
#
# Fill is floor-1, the longest first half that still cannot reach the floor on its own,
# except cloudflare: `_PRE_CFUT` is `cfut_?`, so the rule can keep the bare `cfut` and
# hand the `_` to the BODY, which buys the first half a character back. floor-2 there
# keeps the corpus free of any real PATTERNS match — the precondition section 1
# enforces by requiring mask_text() to return its input unchanged.
REOPEN = [
    ("github", GH, 30, 29, [AKIA]),
    ("github_pat", GHPAT, 20, 19, [GHPAT, AKIA]),
    ("cloudflare", CFUT, 6, 4, [CFUT, AKIA]),
    ("sk-key", SK, 16, 15, [SK, AKIA]),
    ("slack", XOX, 8, 7, [XOX, AKIA]),
    ("aws", AKIA, 16, 15, [AKIA]),
]


def _cases():
    """(name, text) over the over-fire corpus.

    The fill keeps the first half UNDER the rule's floor, so it can never reach the
    floor on its own and every hit is the combined-floor test's doing; the trailing
    prefix then carries the sum over it, which is the whole false-positive mechanism.
    Two tails per row — end of string, and a space and a word behind it — because
    `_OWN_TAIL.match` is anchored and a following separator is what silences the class
    by accident.
    """
    for label, pre, floor, fill_len, reopeners in REOPEN:
        for second in reopeners:
            if fill_len + len(second) < floor:
                continue        # the sum cannot clear the floor; not this class
            for tail_name, tail in (("eos", ""), ("word", " y")):
                yield (f"{label}/{second}/{tail_name}",
                       "x " + pre + FILL * fill_len + masking.MASK + second + tail)


def reopen():
    """1 — ordinary text: an already-masked placeholder glued to a second prefix."""
    fails, total = [], 0
    for name, text in _cases():
        total += 1
        masked, hits = masking.mask_text(text)
        if masked != text:
            fails.append(f"{name}/REDACTED")      # a real PATTERNS match: bad corpus
        elif hits:
            fails.append(f"{name}/ALARM:{','.join(sorted(set(hits)))}")
    return "1 reopen: no-credential text must not alarm", total, fails


def second_link():
    """5 — the falsifier for the fix's own argument.

    Declining to count the trailing prefix is only sound because that prefix, sitting
    directly behind the mask literal, is where `_B`'s `_AFTER_MASK` lookbehind admits
    it — so the second mention is read as a match in its OWN right and nothing is
    lost. That is a claim, and this is the case that can break it: the same chain with
    a full-length body on the second link. Bar: the rule still fires AND the second
    body is actually redacted. A silent second link means the tightening in
    `_orphan_matches` threw away real coverage, not a double count.

    The shape is not invented. `harness/tests/fixtures/wq3037_values.py` pins three
    values a real worker run recorded from exactly this chain (`github_pat_abc` glued
    to `github_pat_` and an English phrase, ISSUE-LOG row 1001 / wq#3040): the first
    link is the under-floor orphan hit section 1 now declines, the second link is this.
    """
    ted = _daemon()
    fails, total = [], 0
    for label, pre, floor, fill_len, reopeners in REOPEN:
        text = ("x " + pre + FILL * fill_len + masking.MASK
                + pre + FILL * floor + " y")
        total += 1
        masked, hits = masking.mask_text(text)
        if label.split("_")[0] not in " ".join(hits):
            fails.append(f"{label}/SILENT:{hits}")
        elif masked.count(FILL) > fill_len:
            fails.append(f"{label}/UNREDACTED:{masked.count(FILL)} fill chars left")
        elif not ted.mask_hits(text):
            fails.append(f"{label}/SILENT[daemon]")
    return "5 second link of the chain still fires and is redacted", total, fails


def daemon_reopen():
    """3a — the same corpus against the daemon mirror the PR-post path calls."""
    ted = _daemon()
    fails, total = [], 0
    for name, text in _cases():
        total += 1
        hits = ted.mask_hits(text)
        if hits:
            fails.append(f"{name}/ALARM:{','.join(sorted(set(hits)))}")
    return "3a reopen [daemon mirror]", total, fails


def underfloor():
    """2 — wq#2868 section B, the 96 leaks this backstop exists to catch."""
    _title, total, fails = under.section_b()
    return "2 under-floor via MASK guard (SILENT = fail)", total, fails


def daemon_underfloor():
    """3b — the same 96 through the daemon mirror."""
    ted = _daemon()
    fails, total = [], 0
    for label, pre, cs, floor, _open, fill in under.RULES:
        if not all(c in cs for c in masking.MASK):
            continue
        blen = floor + 14
        for off in range(0, floor):
            body = fill * off + masking.MASK + fill * (blen - len(masking.MASK) - off)
            total += 1
            if not ted.mask_hits("x " + pre + body + " y"):
                fails.append(f"{label}/off{off}/SILENT")
    return "3b under-floor [daemon mirror] (SILENT = fail)", total, fails


# Committed, and none of them in wq#3018's quiet() list — that list is what wq#2630
# calls the population the fix was checked against. Pinned to the rev below so a later
# edit to any of them cannot change what this probe measured.
PIN = "fe123068"
OUTSIDE = [
    "reports/wq3037_verify_wq3018_orphan_backstop_overfire.md",
    "reports/wq2868_probe_underfloor_class.py",
    "reports/wq3018_probe_orphan_backstop_on_main.py",
    "harness/tests/test_mask_underfloor_invariant.py",
    "harness/tests/test_mask_replay.py",
    "harness/tests/test_mask_at_rest.py",
    "DISPATCH-MODEL-STANDARD.md",
]


def outside():
    """4 — rev-pinned real text from outside wq#3018's own sweep."""
    ted = _daemon()
    fails, total = [], 0
    for path in OUTSIDE:
        done = subprocess.run(["git", "-C", REPO, "show", f"{PIN}:{path}"],
                              capture_output=True, text=True)
        if done.returncode != 0:
            fails.append(f"{path}/UNREADABLE@{PIN}")
            total += 1
            continue
        total += 1
        hits = masking.mask_text(done.stdout)[1] + ted.mask_hits(done.stdout)
        if hits:
            fails.append(f"{path}/ALARM:{','.join(sorted(set(hits)))}")
    return f"4 quiet on committed text outside wq#3018's sweep @{PIN}", total, fails


def main():
    print("masking.py", masking.MASK, "orphan rules", len(masking._ORPHAN_SHAPES))
    bad = 0
    for section in (reopen, underfloor, second_link,
                    daemon_reopen, daemon_underfloor, outside):
        title, total, fails = section()
        print(f"{title}: {total} cases, {len(fails)} failing")
        for f in fails[:12]:
            print("   ", f)
        bad += len(fails)
    print("TOTAL FAILING", bad)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
