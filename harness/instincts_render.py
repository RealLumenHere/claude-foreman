"""Tier-aware renderer for /home/lumen/instincts.yaml (schema 2, wq#959).

Replaces byte-window injection (`head -c 4096`, wq#958's defect: 35/57 rules
silently dropped, cut landing mid-rule). Rules render as whole units, tier 0
always first — R1_instincts_efficacy.md §6.

Audiences:
  worker  -> tier 0 only (hard constraints; goes into every fworker prompt)
  core    -> tier 0, tier 1, then the tier-2 trigger index (orchestrator /
             audit / chat sessions; claude_context.sh --core)
  boot    -> tier 0 + tier 1 whole, tier 2 as bare trigger titles with no rule
             text (wq#4540: `--audience core`'s tier-2 index still carries each
             rule's full text, the single biggest line item in the ~22K-tok
             `claude_context.sh --core` boot payload; a session that needs a
             tier-2 rule reads its playbook, so the title is enough to route
             there)

Fail-safe contract: tier0_block() and render() return "" on ANY failure
(missing file, schema-1 flat list, parse error) — injection must never break a
session spawn. Use `--check` to surface validation errors loudly.
"""

import argparse
import os
import sys

INSTINCTS_PATH = os.environ.get("FOREMAN_INSTINCTS_PATH", "/home/lumen/instincts.yaml")

# wq#959 acceptance was tier 0 under 600 tok; wq#3660: the live file grew to 8
# rules (~688 tok, lumen-headless-ssh alone ~218) and the cap silently dropped the
# LAST rule (fabricated-evidence) from every fworker prompt. Raised to 750 (693 used) so all
# 8 fit. instincts.yaml is proposal-only standing law, so the cap moved, not the
# rule text. chars/4 is the house estimate (research/TOKEN_BASELINE.md); rules
# beyond the cap are dropped whole, loudly.
TIER0_TOKEN_BUDGET = 750

TIER0_HEADER = "INSTINCTS — TIER 0 HARD CONSTRAINTS (non-negotiable, apply before any other guidance):"
TIER1_HEADER = "INSTINCTS — OPERATING DOCTRINE:"
TIER2_HEADER = "INSTINCTS — DOMAIN TRIGGERS (read the named playbook BEFORE acting in that domain):"


def _est_tokens(text):
    return len(text) // 4


def load_rules(path=None):
    """Return the rule list, or None if the file is missing/old-format/invalid.

    Schema 2 is a mapping with `schema: 2` and `rules:` holding dicts. The
    schema-1 flat string list returns None so callers fall back to silence
    rather than injecting metadata soup.
    """
    import yaml
    path = path or INSTINCTS_PATH
    try:
        with open(path) as f:
            doc = yaml.safe_load(f)
    except (OSError, yaml.YAMLError):
        return None
    if not isinstance(doc, dict) or doc.get("schema") != 2:
        return None
    rules = doc.get("rules")
    if not isinstance(rules, list) or not all(
        isinstance(r, dict) and r.get("text") and isinstance(r.get("tier"), int)
        for r in rules
    ):
        return None
    return rules


def _tier(rules, n):
    return [r for r in rules if r["tier"] == n]


def tier0_block(path=None, budget=TIER0_TOKEN_BUDGET, warn=None):
    """Header + numbered tier-0 rules, "" on any failure. Whole-rule budget cut."""
    rules = load_rules(path)
    if not rules:
        return ""
    lines = [TIER0_HEADER]
    used = _est_tokens(TIER0_HEADER)
    for i, r in enumerate(_tier(rules, 0), 1):
        line = "%d. %s" % (i, " ".join(r["text"].split()))
        cost = _est_tokens(line)
        if used + cost > budget:
            if warn:
                warn("instincts tier0 over %d-tok budget; dropped from rule %r on" % (budget, r.get("id")))
            break
        lines.append(line)
        used += cost
    return "\n".join(lines) if len(lines) > 1 else ""


def render(audience, path=None):
    """Full render for an audience ('worker', 'core', or 'boot'). "" on any failure."""
    if audience == "worker":
        return tier0_block(path)
    rules = load_rules(path)
    if not rules:
        return ""
    out = [tier0_block(path)]
    t1 = _tier(rules, 1)
    if t1:
        out.append(TIER1_HEADER)
        out.extend("- %s" % " ".join(r["text"].split()) for r in t1)
    t2 = _tier(rules, 2)
    if t2:
        out.append(TIER2_HEADER)
        if audience == "boot":
            # Titles only: a trigger is enough to route to the playbook; the rule text
            # itself is what --audience core pays the tokens for (wq#4540).
            out.extend(
                "- [%s]" % r.get("trigger", r.get("domain", "?"))
                for r in t2
            )
        else:
            out.extend(
                "- [%s] %s" % (r.get("trigger", r.get("domain", "?")), " ".join(r["text"].split()))
                for r in t2
            )
    return "\n".join(x for x in out if x)


def check(path=None):
    """Validate the corpus; returns a list of error strings (empty = clean)."""
    errs = []
    rules = load_rules(path)
    if rules is None:
        return ["not a schema-2 instincts file: %s" % (path or INSTINCTS_PATH)]
    ids = [r.get("id") for r in rules]
    for rid in ids:
        if not rid:
            errs.append("rule missing id")
    dupes = {i for i in ids if ids.count(i) > 1}
    if dupes:
        errs.append("duplicate ids: %s" % sorted(dupes))
    for r in rules:
        for field in ("added", "source"):
            if not r.get(field):
                errs.append("%s: missing %s" % (r.get("id"), field))
        if r["tier"] not in (0, 1, 2):
            errs.append("%s: bad tier %r" % (r.get("id"), r["tier"]))
        if r["tier"] == 2 and not (r.get("playbook") and r.get("trigger")):
            errs.append("%s: tier-2 rule needs playbook + trigger" % r.get("id"))
        sup = r.get("supersedes")
        if sup and sup in ids:
            errs.append("%s: supersedes %r but it is still in the corpus" % (r.get("id"), sup))
    t0 = "\n".join(" ".join(r["text"].split()) for r in _tier(rules, 0))
    if _est_tokens(t0) > TIER0_TOKEN_BUDGET:
        errs.append("tier 0 over budget: ~%d tok > %d" % (_est_tokens(t0), TIER0_TOKEN_BUDGET))
    for r in _tier(rules, 2):
        pb = r.get("playbook")
        if pb and not os.path.exists(pb):
            errs.append("%s: playbook missing on disk: %s" % (r.get("id"), pb))
    return errs


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--file", default=None, help="instincts.yaml path override")
    ap.add_argument("--audience", choices=("worker", "core", "boot"), help="render for audience")
    ap.add_argument("--tier", type=int, help="dump one tier's rule texts raw")
    ap.add_argument("--check", action="store_true", help="validate corpus, exit 1 on errors")
    args = ap.parse_args(argv)
    if args.check:
        errs = check(args.file)
        for e in errs:
            print("ERROR: %s" % e, file=sys.stderr)
        rules = load_rules(args.file) or []
        for n in (0, 1, 2):
            block = "\n".join(" ".join(r["text"].split()) for r in rules if r["tier"] == n)
            print("tier %d: %d rules, ~%d tok" % (n, len([r for r in rules if r["tier"] == n]), _est_tokens(block)))
        return 1 if errs else 0
    if args.tier is not None:
        rules = load_rules(args.file) or []
        for r in rules:
            if r["tier"] == args.tier:
                print(" ".join(r["text"].split()))
        return 0
    if args.audience:
        out = render(args.audience, args.file)
        if out:
            print(out)
        return 0
    ap.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
