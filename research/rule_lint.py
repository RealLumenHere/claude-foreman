#!/usr/bin/env python3
"""Rule-corpus lint (wq#4295) — offline contradiction/drift checks for a schema-2
instincts.yaml-shaped rule corpus (id/tier/supersedes/text per rule).

Built to answer wq#4295's question for real rather than from memory: does the fleet's
hand-maintained rule file (/home/lumen/instincts.yaml, 477 lines / 35 rules as read
2026-10-18) need RuleBridge (github.com/Shadorux/RuleBridge, npm @shadorux/rulebridge
0.1.0) or Rule Coherence Graph (github.com/alast9/rule-coherence-graph, pypi
rule-coherence-graph 0.7.0) — or does a few-dozen-line linter cover the same ground.

Three checks, all pure stdlib + PyYAML, no network, no LLM, no graph DB:

  dup-id        two rules share an `id` — the corpus's own primary key is broken.
  dangling-ref  a `supersedes` points at an id absent from the corpus — either a typo
                or a rule that was deleted instead of mechanically retired.
  polarity      two rules in the same tier share a salient keyword (>=4 chars, not a
                stopword) AND carry opposite-polarity modal markers (never/do not/
                retired vs always/must/mandatory) — the same signal RCG's own
                benchmark (benchmarks/RESULTS.md, fetched 2026-10-18) found gets
                syntactic-only precision 1.000 / recall 0.500; this is that pass,
                without Neo4j, embeddings, or a judge call.

polarity is intentionally the cheap, low-recall pass: RCG's own numbers show the
expensive half (semantic + LLM judge) is what it takes to lift recall past 0.5, and
that cost (a reasoning call per candidate pair, every run) is not worth paying for a
single-author, single-file, 35-rule corpus — see research/WQ4295_RULEBRIDGE_RCG_EVAL.md.

Run:  python3 research/rule_lint.py path/to/instincts.yaml
      python3 research/rule_lint.py --selfcheck
Exit 0 clean, 1 findings, 2 setup error (bad YAML / missing file).
"""

from __future__ import annotations

import argparse
import sys

import yaml

NEGATIVE_MARKERS = ("never", "do not", "don't", "none", "not a", "retired")
POSITIVE_MARKERS = ("always", "must", "mandatory", "every", "required")
STOPWORDS = {
    "this", "that", "with", "from", "have", "been", "when", "before", "after",
    "never", "always", "must", "than", "does", "will", "each", "into", "over",
}


def load_rules(path: str) -> list[dict]:
    with open(path) as fh:
        doc = yaml.safe_load(fh)
    if not isinstance(doc, dict) or "rules" not in doc:
        raise ValueError(f"not a schema-2 rule corpus: {path}")
    return doc["rules"]


def keywords(text: str) -> set[str]:
    words = {w.strip(".,()'\"-").lower() for w in text.split()}
    return {w for w in words if len(w) >= 4 and w not in STOPWORDS}


def polarity(text: str) -> int:
    low = text.lower()
    neg = any(m in low for m in NEGATIVE_MARKERS)
    pos = any(m in low for m in POSITIVE_MARKERS)
    if neg and not pos:
        return -1
    if pos and not neg:
        return 1
    return 0


def check(rules: list[dict]) -> list[str]:
    findings = []
    seen_ids: dict[str, int] = {}
    for i, r in enumerate(rules):
        rid = r.get("id")
        if rid in seen_ids:
            findings.append(f"dup-id: '{rid}' at rules[{seen_ids[rid]}] and rules[{i}]")
        else:
            seen_ids[rid] = i

    all_ids = {r.get("id") for r in rules}
    for i, r in enumerate(rules):
        sup = r.get("supersedes")
        if sup and sup not in all_ids:
            findings.append(f"dangling-ref: rules[{i}] '{r.get('id')}' supersedes missing id '{sup}'")

    by_tier: dict[object, list[tuple[int, dict, set[str], int]]] = {}
    for i, r in enumerate(rules):
        pol = polarity(r.get("text", ""))
        if pol == 0:
            continue
        kw = keywords(r.get("text", ""))
        by_tier.setdefault(r.get("tier"), []).append((i, r, kw, pol))

    for tier, entries in by_tier.items():
        for a in range(len(entries)):
            for b in range(a + 1, len(entries)):
                i1, r1, kw1, p1 = entries[a]
                i2, r2, kw2, p2 = entries[b]
                if p1 == p2:
                    continue
                shared = kw1 & kw2
                if r1.get("supersedes") == r2.get("id") or r2.get("supersedes") == r1.get("id"):
                    continue
                if shared:
                    findings.append(
                        f"polarity: tier {tier} '{r1.get('id')}' (i{i1}) vs "
                        f"'{r2.get('id')}' (i{i2}) opposite polarity, shared keywords {sorted(shared)}"
                    )
    return findings


CLEAN_FIXTURE = """
schema: 2
rules:
  - id: a
    tier: 0
    text: "Always back up the volume before a migration."
  - id: b
    tier: 0
    text: "Never delete a snapshot within 24h of creation."
"""

DIRTY_FIXTURE = """
schema: 2
rules:
  - id: a
    tier: 0
    text: "Always force-push to main after a hotfix."
  - id: a
    tier: 0
    text: "Never force-push to main, ever."
  - id: c
    tier: 1
    supersedes: ghost-rule-not-present
    text: "Some other rule."
"""


def selfcheck() -> int:
    import tempfile

    for name, fixture, want_clean in (("clean", CLEAN_FIXTURE, True), ("dirty", DIRTY_FIXTURE, False)):
        with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False) as fh:
            fh.write(fixture)
            path = fh.name
        findings = check(load_rules(path))
        ok = (len(findings) == 0) == want_clean
        print(f"[selfcheck] {name}: {'PASS' if ok else 'FAIL'} ({len(findings)} findings)")
        for f in findings:
            print(f"    {f}")
        if not ok:
            return 1
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("path", nargs="?", help="path to a schema-2 rule corpus YAML file")
    ap.add_argument("--selfcheck", action="store_true", help="run built-in clean/dirty fixtures")
    args = ap.parse_args()

    if args.selfcheck:
        return selfcheck()
    if not args.path:
        ap.error("path required unless --selfcheck")

    try:
        rules = load_rules(args.path)
    except (OSError, yaml.YAMLError, ValueError) as exc:
        print(f"setup error: {exc}", file=sys.stderr)
        return 2

    findings = check(rules)
    for f in findings:
        print(f)
    if findings:
        print(f"{len(findings)} finding(s)", file=sys.stderr)
        return 1
    print("clean")
    return 0


if __name__ == "__main__":
    sys.exit(main())
