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
# style varies) and strict on requiring two priced-per-M figures.
PRICE_PER_M_RE = re.compile(r"\$\s*([0-9]+(?:\.[0-9]+)?)\s*/\s*M", re.IGNORECASE)


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
    """How many $x/M price figures the ticket body states. The class fix
    wants at least two per flagged retirement: the retired rung's price and
    its replacement's, so a reviewer can see the delta without re-deriving it
    from spend logs after the fact."""
    return len(PRICE_PER_M_RE.findall(ticket_body or ""))


def has_price_delta_disclosure(ticket_body: str) -> bool:
    return price_points_disclosed(ticket_body) >= 2


def check_retirement(old_model_list: list, new_model_list: list,
                      spend_by_rung: dict, ticket_body: str,
                      threshold_usd: float = DEFAULT_THRESHOLD_USD) -> list:
    """Core decision, pure and side-effect free. Returns a list of violation
    strings (empty means the ticket is clear to file/dispatch)."""
    flagged = high_spend_retirements(old_model_list, new_model_list, spend_by_rung, threshold_usd)
    if not flagged:
        return []
    if has_price_delta_disclosure(ticket_body):
        return []
    rungs = ", ".join(flagged)
    spends = ", ".join(f"{r}=${spend_by_rung.get(r, 0.0):.2f}" for r in flagged)
    return [
        f"REFUSED: retiring {rungs} (spend {spends}) clears the "
        f"${threshold_usd:.2f} review threshold but the ticket body carries "
        f"fewer than 2 '$x/M' price figures — state the retired rung's "
        f"measured $/M input price and the replacement rung's $/M input "
        f"price so the delta is reviewable (FLEET_COST_PLAN.md D7)."
    ]


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
