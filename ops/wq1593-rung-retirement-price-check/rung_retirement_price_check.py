#!/usr/bin/env python3
"""wq#1593 — refuse a rung-retirement ticket that skips the price delta.

FLEET_COST_PLAN.md D7: mistral-small was removed from litellm_config.yaml
(last spend 2026-08-29 21:00) while carrying $8.79 in the 14-day window at
$0.106/M measured input, the cheapest rung on the board. LPP moved onto
deepseek-v4-flash at $0.208/M input — roughly doubling the input rate on the
fleet's biggest workload — with no cost review anywhere in the ticket that
did it. Section 6, item 8 names the class fix: a rung retirement that touches
a workload above a spend threshold should require the price delta in the
ticket.

This is a pre-flight check, not a runtime callback (contrast
ops/wq1590-deepseek-connect-guard, which watches live traffic): the live
config lives at /home/lumen/agent/litellm_config.yaml on the gateway node,
outside this repo and outside this sandbox's write access (see
ops/wq1591-glm53flash-pricing/README.md for that convention), so there is no
diff here to gate in CI. Instead this runs by hand (or from a future `wq add`
wrapper) before a rung-retirement ticket is filed or dispatched: feed it the
old/new model_list snippets plus a recent per-rung spend export, and it
refuses the ticket body unless it discloses the old-vs-new $/M input price
for every retired rung whose spend cleared the threshold.

Usage:
    python3 rung_retirement_price_check.py \\
        --old-config old_model_list.yaml --new-config new_model_list.yaml \\
        --spend-json spend_by_rung.json --ticket-body ticket.txt \\
        [--threshold-usd 5.0]

spend_by_rung.json: {"mistral-small": 8.79, "deepseek-v4-flash": 45.40, ...}
  — dollars spent per rung (model_name) over the measurement window, same
  shape as FLEET_COST_PLAN.md section 1's per-rung table.

Exits 0 and prints OK when every high-spend retirement is disclosed, exits 1
and prints one REFUSED line per violation otherwise.
"""
import argparse
import json
import re
import sys

DEFAULT_THRESHOLD_USD = 5.0

# A retired rung's disclosure must name it and give both sides of the delta as
# a $/M-tokens figure, e.g. "mistral-small: $0.106/M -> deepseek-v4-flash:
# $0.208/M". Deliberately loose on prose around the numbers (dispatch prose
# style varies) and strict on which rung each number is about.
PRICE_PER_M_RE = re.compile(r"\$\s*([0-9]+(?:\.[0-9]+)?)\s*/\s*M", re.IGNORECASE)

# A price figure is read as disclosing the rung whose name sits nearest to it
# in the same clause, so quoted pricing for models that aren't on the board
# can't stand in for a retired rung's own price (wq#1602 gap 2). Clauses break
# on sentence/statement punctuation and blank lines — never on a lone newline,
# which in a hard-wrapped ticket body falls mid-clause. The digit lookarounds
# keep "$0.106" and "glm-5.3-flash" from splitting mid-number.
CLAUSE_SPLIT_RE = re.compile(r"(?<!\d)[.;](?!\d)|\n\s*\n")
MAX_NAME_TO_PRICE_CHARS = 120


def model_names(model_list: list) -> set:
    """model_name values out of a litellm model_list (or [] for an empty/None
    config, so a fully-retired file still parses)."""
    return {m.get("model_name") for m in (model_list or []) if m.get("model_name")}


def removed_rungs(old_model_list: list, new_model_list: list) -> set:
    """Rungs present in the old config and gone from the new one."""
    return model_names(old_model_list) - model_names(new_model_list)


def high_spend_retirements(old_model_list: list, new_model_list: list,
                            spend_by_rung: dict, threshold_usd: float) -> list:
    """Removed rungs whose measured spend in the window clears the threshold,
    sorted for stable output. These are the ones a retirement ticket must
    price out — a rung that never spent anything needs no review."""
    removed = removed_rungs(old_model_list, new_model_list)
    return sorted(r for r in removed if spend_by_rung.get(r, 0.0) >= threshold_usd)


def price_points_disclosed(ticket_body: str) -> int:
    """How many $x/M price figures the ticket body states, anywhere. A raw
    count — it says nothing about which rung each figure is about, so it is
    reported in refusal text but never gates on its own."""
    return len(PRICE_PER_M_RE.findall(ticket_body or ""))


def _rung_name_re(rung: str):
    """Whole-token match for a rung name, so 'mistral-small' doesn't match
    inside 'mistral-small-v2' (or the other way round)."""
    return re.compile(r"(?<![\w.-])" + re.escape(rung) + r"(?![\w-])", re.IGNORECASE)


def _clause_spans(text: str) -> list:
    """(start, end) of each non-empty clause in the body."""
    spans, start = [], 0
    for m in CLAUSE_SPLIT_RE.finditer(text):
        spans.append((start, m.start()))
        start = m.end()
    spans.append((start, len(text)))
    return [(s, e) for s, e in spans if e > s]


def price_disclosures_by_rung(ticket_body: str, known_rungs) -> dict:
    """Map every known rung name to the number of $x/M figures that disclose
    *that* rung. Each figure is attributed to the nearest rung name in its own
    clause (within MAX_NAME_TO_PRICE_CHARS) and to no other, so a ticket that
    prices one retirement plus the shared replacement cannot also count as
    pricing the retirement standing beside it."""
    body = ticket_body or ""
    counts = {r: 0 for r in known_rungs}
    for c_start, c_end in _clause_spans(body):
        clause = body[c_start:c_end]
        names = [(m.start(), m.end(), rung)
                 for rung in sorted(known_rungs)
                 for m in _rung_name_re(rung).finditer(clause)]
        if not names:
            continue
        for price in PRICE_PER_M_RE.finditer(clause):
            nearest, best = None, None
            for n_start, n_end, rung in names:
                gap = (n_start - price.end()) if n_start >= price.end() else (price.start() - n_end)
                gap = max(gap, 0)
                if gap <= MAX_NAME_TO_PRICE_CHARS and (best is None or gap < best):
                    nearest, best = rung, gap
            if nearest:
                counts[nearest] += 1
    return counts


def check_retirement(old_model_list: list, new_model_list: list,
                      spend_by_rung: dict, ticket_body: str,
                      threshold_usd: float = DEFAULT_THRESHOLD_USD) -> list:
    """Core decision, pure and side-effect free. Returns a list of violation
    strings (empty means the ticket is clear to file/dispatch). Every flagged
    retirement needs its own priced figure, and the board it moves onto needs
    one too — a body-wide count let one disclosure cover several retirements
    (wq#1602)."""
    flagged = high_spend_retirements(old_model_list, new_model_list, spend_by_rung, threshold_usd)
    if not flagged:
        return []
    surviving = sorted(model_names(new_model_list))
    disclosed = price_disclosures_by_rung(ticket_body, model_names(old_model_list) | set(surviving))
    total = price_points_disclosed(ticket_body)

    violations = []
    for rung in flagged:
        if disclosed.get(rung, 0) >= 1:
            continue
        violations.append(
            f"REFUSED: retiring {rung} (spend ${spend_by_rung.get(rung, 0.0):.2f}) clears the "
            f"${threshold_usd:.2f} review threshold but none of the {total} '$x/M' price "
            f"figure(s) in the ticket body is stated alongside '{rung}' — state that rung's own "
            f"measured $/M input price where it is named, so the delta is reviewable "
            f"(FLEET_COST_PLAN.md D7)."
        )
    if surviving and not any(disclosed.get(rung, 0) for rung in surviving):
        violations.append(
            f"REFUSED: retiring {', '.join(flagged)} clears the ${threshold_usd:.2f} review "
            f"threshold but no rung left on the board after this change ({', '.join(surviving)}) "
            f"carries a '$x/M' price figure — state the replacement rung's $/M input price so "
            f"the delta has a right-hand side (FLEET_COST_PLAN.md D7)."
        )
    return violations


def _load_yaml_model_list(path: str) -> list:
    import yaml
    with open(path) as f:
        data = yaml.safe_load(f) or {}
    return data.get("model_list") or []


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                  formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--old-config", required=True, help="litellm model_list yaml before the retirement")
    ap.add_argument("--new-config", required=True, help="litellm model_list yaml after the retirement")
    ap.add_argument("--spend-json", required=True, help="path to {rung: dollars} spend export")
    ap.add_argument("--ticket-body", required=True, help="path to the ticket text ('-' for stdin)")
    ap.add_argument("--threshold-usd", type=float, default=DEFAULT_THRESHOLD_USD)
    args = ap.parse_args(argv)

    old_model_list = _load_yaml_model_list(args.old_config)
    new_model_list = _load_yaml_model_list(args.new_config)
    with open(args.spend_json) as f:
        spend_by_rung = json.load(f)
    ticket_body = sys.stdin.read() if args.ticket_body == "-" else open(args.ticket_body).read()

    violations = check_retirement(old_model_list, new_model_list, spend_by_rung,
                                   ticket_body, args.threshold_usd)
    if violations:
        for v in violations:
            print(v)
        return 1
    print("OK: no undisclosed high-spend rung retirement")
    return 0


if __name__ == "__main__":
    sys.exit(main())
