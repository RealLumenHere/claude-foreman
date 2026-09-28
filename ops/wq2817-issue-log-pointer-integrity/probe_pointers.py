#!/usr/bin/env python3
"""wq#2817 probe: are kb/ISSUE-LOG.md's cross-row pointers resolvable?

Doctrine tells every worker to read ISSUE-LOG.md before changing anything, so a
row that says "see row N" is a navigation instruction. wq#2763 found row 824
sending readers to row 834 (an unrelated judgment) instead of 835. This probe
generalises that single finding into three checks over EVERY row, so it can fail
on rows the wq#2817 fix never touched:

  DUP       row number defined more than once -> any "row N" citing it is ambiguous
  DANGLING  a citation whose target row number does not exist
  ONE-WAY   a "see row N" correction pointer where row N never mentions the
            citing row back. Correction rows in this log cite their subject
            ("row 824 above closes rotation row ..." -- row 835), so a missing
            back-reference is the signature of a mis-typed target.

wq#2877 (judgment on wq#2861's contradiction) widened what counts as a citation.
The first cut matched only the all-lowercase, single-target, bare "see row N" that
wq#2740 happened to mistype, so it read 3 of the corpus's 34 cross-row citations
(9%) and called that "every row": sentence-initial "See row N" (row 80 today),
"per row N" (29 occurrences), "per row #687", and the list form "see rows
38/147/148" were all invisible, so the class of defect this probe exists to catch
could recur in any of them silently.

wq#2894 (judgment on wq#2892's contradiction) dropped the verb requirement
entirely. wq#2877 replaced one hardcoded verb with three and claimed to cover
"whatever verb introduces it", but the corpus's commonest cross-row reference
carries no verb at all -- the parenthetical "(row 80)" / "(rows 131, 142)" form,
and the bare narrative "same class as row 82". A DANGLING pointer does not care
how the prose introduces it, so CITE_RE now matches "row N" wherever it appears.
Measured on kb rev 70ef27e before choosing between that and a narrower
"(row N)"-only branch: verb-required read 38 pointers / 0 dangling, an explicit
paren branch 156 / 1, verb-less 482 / 4 -- and all 4 are real (row 220 -> 206b,
row 854 -> 923, rows 891 and 892 -> 889 after wq#2891 renamed it 889b), with the
paren-only variant seeing just one of them because the corpus also writes
"(fleet pw rotation, row 206b)", where the paren is nowhere near the word "row".
The only false positive verb-less matching produced was "grows 330s per repo" --
"rows?" inside "grows" -- hence the \b anchor; with it, 0 false positives across
850 rows.

ONE-WAY deliberately stays on "see row N" alone: it is a heuristic about
*correction* pointers, and "per row N" means "as row N requires", not "correcting
row N". Widening ONE-WAY too takes the live corpus from 2 findings to 31, all of
them noise -- and to ~480 under a verb-less rule.

Widening the regex adds findings that were always in the corpus but unread, so
wq#2857's known-findings-baseline.json was re-pinned in the same commit: an
already-present finding the probe only just learned to see is not a new collision,
and alarming on it would spend the alarm's whole signal on backlog prose.

Input is rev-pinned by default (`--rev <sha>` reads through `git show`), so a
run is reproducible after the live file moves on.

Usage:
  probe_pointers.py --rev 10e77b2            # baseline: bug live at kb HEAD
  probe_pointers.py --path /home/lumen/kb/ISSUE-LOG.md
Exit 0 when no check fails, 1 otherwise.
"""
import argparse
import re
import subprocess
import sys

KB_DIR = "/home/lumen/kb"
# wq#2891: trailing lowercase letter is a disambiguation suffix on a duplicated
# row number (887 -> 887b, per reports/2857_policy_issue_log_row_disambiguation.md),
# not a new numbering scheme -- keep it optional so a bare row is still matched.
ROW_RE = re.compile(r"^\|\s*(\d+[a-z]?)\s*\|")
_LIST = r"((?:\s*(?:[/,&]|and)\s*#?\d+[a-z]?)*)"  # "see rows 38/147/148", "rows 131, 142"
# wq#2894: no verb. \b keeps "rows?" from matching inside "grows 330s per repo".
CITE_RE = re.compile(r"\brows?\s+#?(\d+[a-z]?)" + _LIST, re.I)
SEE_RE = re.compile(r"\bsee\s+rows?\s+#?(\d+[a-z]?)" + _LIST, re.I)
_NUM_RE = re.compile(r"\d+[a-z]?")
# wq#2894: a row number inside a code span is quoted text, not a pointer -- rows
# about pointer defects quote the bad pointer (row 893: "`(row 9999 ...)` -> 0
# pointers checked"), and every such row would otherwise dangle by construction.
_CODE_SPAN_RE = re.compile(r"`[^`]*`")


def prose(text):
    """`text` with code spans blanked, so a quoted "row N" is not read as a citation."""
    return _CODE_SPAN_RE.sub(" ", text)


def _sort_key(num):
    """Numeric-then-suffix ordering: '887' < '887b' < '888'."""
    m = re.match(r"(\d+)([a-z]?)", num)
    return (int(m.group(1)), m.group(2))


def targets(pattern, text):
    """Every row number `pattern` cites in `text`, list forms expanded."""
    out = set()
    for head, rest in pattern.findall(prose(text)):
        out.add(head)
        out.update(_NUM_RE.findall(rest))
    return out


def load(rev, path):
    if rev:
        out = subprocess.run(
            ["git", "-C", KB_DIR, "show", f"{rev}:ISSUE-LOG.md"],
            check=True, capture_output=True, text=True,
        )
        return out.stdout.splitlines()
    with open(path, encoding="utf-8") as fh:
        return fh.read().splitlines()


def rows(lines):
    """row number (string, may carry a disambiguation suffix like '887b') ->
    list of (1-based line number, full row text)."""
    found = {}
    for n, text in enumerate(lines, 1):
        m = ROW_RE.match(text)
        if m:
            found.setdefault(m.group(1), []).append((n, text))
    return found


def _findings(found):
    """Yield (key, text) for every failure check() would report.

    key is check()'s finding stripped of line numbers, e.g. 'DUP:836:2' or
    'ONEWAY:164:38' -- the stable identity wq#2857's baseline alarm diffs
    on, since a plain append never shifts an existing row's line number in
    this append-only log but the formatted text embeds 'L<n>' regardless.
    """
    for num in sorted(found, key=_sort_key):
        if len(found[num]) > 1:
            at = ", ".join(f"L{ln}" for ln, _ in found[num])
            yield (
                f"DUP:{num}:{len(found[num])}",
                f"DUP      row {num} defined {len(found[num])}x ({at})",
            )
    for num in sorted(found, key=_sort_key):
        for line_no, text in found[num]:
            for target in sorted(targets(CITE_RE, text) - set(found), key=_sort_key):
                yield (
                    f"DANGLING:{num}:{target}",
                    f"DANGLING row {num} (L{line_no}) -> 'row {target}': no such row",
                )
            # A back-reference is written the same way a citation is, so accept the
            # same shapes: "row 824", "Rows 824/825", "row #824" all count.
            back = re.compile(rf"\brows?\s+#?{num}\b", re.I)
            for target in sorted(targets(SEE_RE, text) & set(found), key=_sort_key):
                if not any(back.search(prose(t)) for _, t in found[target]):
                    yield (
                        f"ONEWAY:{num}:{target}",
                        f"ONE-WAY  row {num} (L{line_no}) -> 'see row {target}': "
                        f"row {target} never mentions row {num}",
                    )


def check(found):
    return [text for _, text in _findings(found)]


def finding_keys(found):
    """wq#2857: finding identities only, for a baseline diff (see _findings)."""
    return {key for key, _ in _findings(found)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--rev", help="read ISSUE-LOG.md from this kb rev instead of the worktree")
    ap.add_argument("--path", default=f"{KB_DIR}/ISSUE-LOG.md")
    args = ap.parse_args()

    found = rows(load(args.rev, args.path))
    failures = check(found)
    source = f"rev {args.rev}" if args.rev else args.path
    print(f"source: {source}  rows: {len(found)}  pointers checked: "
          f"{sum(len(targets(CITE_RE, t)) for v in found.values() for _, t in v)}")
    for f in failures:
        print(f)
    print(f"FAIL ({len(failures)})" if failures else "PASS")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
