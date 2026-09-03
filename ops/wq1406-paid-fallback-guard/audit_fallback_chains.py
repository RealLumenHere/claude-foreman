#!/usr/bin/env python3
"""wq#1406 — audit every fallback chain in the live LiteLLM proxy config for a
silent PAID-first fallback (the DeepSeek/LPP runaway class: Sep 1-2 2026,
Cerebras 402'd, LPP fell through to deepseek-v4-flash, $23/day, no cap/alert).

Rule: within a `router_settings.fallbacks` chain, every FREE/local deployment
must appear before the first PAID one. A chain that goes straight to a paid
deployment (no free/local rung at all) is also flagged, since "no local
fallback exists" is exactly the condition item (1) exists to surface — the
fix in that case is either add a local rung or accept the paid one is load-
bearing and make sure item (2)/(3) (alert + tripwire) cover it.

Usage (on the gateway node, where the live config actually lives — this
BUILD worker's sandbox has no read access to it, same convention as
wq#897/wq#911/wq#948/wq#1299; see README.md):

    python3 audit_fallback_chains.py /home/lumen/agent/litellm_config.yaml

Exits 1 if any chain violates the rule, 0 otherwise. Prints a human report;
--json emits the same findings as machine-readable JSON.
"""
import json
import sys

# Cloud providers that bill per token. Matches the ticket's named list
# (deepseek/openrouter/mistral/anthropic/openai) plus cerebras: the incident
# itself was Cerebras returning 402 (payment required) — a "free" tier that
# can itself become a paid drain once quota is exhausted, so it is audited
# as paid rather than trusted as a free rung.
PAID_PROVIDER_PREFIXES = (
    "deepseek/", "openrouter/", "mistral/", "anthropic/", "openai/", "cerebras/",
)


def classify_model(litellm_model: str) -> str:
    """'paid' | 'free' — pure classification off the litellm_params.model
    string ("<provider>/<model>"), which is what actually selects the
    billing path; deployment aliases can be named anything."""
    m = (litellm_model or "").strip().lower()
    for prefix in PAID_PROVIDER_PREFIXES:
        if m.startswith(prefix):
            return "paid"
    return "free"


def audit_chain(model_group: str, chain: list[str], group_class: dict[str, str]) -> dict | None:
    """Returns a violation dict for `model_group`'s fallback `chain`
    (in order, NOT including `model_group` itself — a paid primary is a
    separate, intentional-billing question, not a silent fallback), else
    None. `group_class` maps model_group name -> 'paid'/'free'/'unknown'
    (unknown groups, e.g. a typo'd target, are not silently treated as
    free — see ISSUE-LOG #9, dangling fallback targets are their own bug
    class and out of scope here). Two violation shapes:

    - "no-free-anywhere": neither `model_group` nor any rung in `chain` is
      free/local — the whole path can only ever bill (the DeepSeek/LPP
      runaway shape: a paid primary with only paid fallbacks behind it).
    - "paid-before-free": `chain` itself contains a free rung, but a paid
      rung appears before it — the free option should have been tried
      first (wq#907's accepted pattern, a free/local primary falling
      through to an all-paid `chain` as last resort, is NOT this case:
      that's fine, see the "no-free-anywhere" check above, which looks at
      the whole path including the primary).
    """
    classes = [group_class.get(name, "unknown") for name in chain]
    primary_free = group_class.get(model_group) == "free"

    if not primary_free and "free" not in classes:
        paid_rung = chain[0] if chain else model_group
        return {
            "model_group": model_group,
            "chain": chain,
            "paid_rung": paid_rung,
            "reason": (
                f"{model_group}'s entire path (primary + fallbacks) has no free/local "
                f"rung at all: {[model_group] + chain} — every request that reaches "
                f"a fallback here bills, with no local alternative to try first"
            ),
        }

    first_paid_idx = next((i for i, c in enumerate(classes) if c == "paid"), None)
    if first_paid_idx is not None and "free" in classes[first_paid_idx + 1:]:
        paid_rung = chain[first_paid_idx]
        return {
            "model_group": model_group,
            "chain": chain,
            "paid_rung": paid_rung,
            "reason": (
                f"'{paid_rung}' (paid) is ordered before a free/local rung later in "
                f"{model_group}'s fallback chain: {chain} — reorder free/local first"
            ),
        }
    return None


def group_classification(config: dict) -> dict[str, str]:
    """model_group name -> 'paid'/'free', from model_list deployments.
    A group is 'paid' if ANY deployment under that alias is paid (a mixed
    group can silently route to the paid deployment under load-balancing)."""
    result: dict[str, str] = {}
    for entry in config.get("model_list") or []:
        name = entry.get("model_name")
        params = entry.get("litellm_params") or {}
        cls = classify_model(params.get("model", ""))
        if name is None:
            continue
        if result.get(name) != "paid":
            result[name] = cls
    return result


def run_audit(config: dict) -> list[dict]:
    group_class = group_classification(config)
    fallbacks = (config.get("router_settings") or {}).get("fallbacks") or []
    violations = []
    for entry in fallbacks:
        # LiteLLM shape: [{"<model_group>": ["<fallback1>", "<fallback2>", ...]}]
        for model_group, chain in entry.items():
            v = audit_chain(model_group, list(chain), group_class)
            if v:
                violations.append(v)
    return violations


def main(argv: list[str]) -> int:
    args = [a for a in argv if not a.startswith("--")]
    as_json = "--json" in argv
    if not args:
        print(__doc__)
        return 2
    import yaml  # local import: keep module importable/testable without PyYAML on PATH mismatch
    with open(args[0]) as f:
        config = yaml.safe_load(f)

    violations = run_audit(config)
    if as_json:
        print(json.dumps(violations, indent=2))
    else:
        if not violations:
            print("OK: no fallback chain reaches a paid model before a free/local one.")
        for v in violations:
            print(f"VIOLATION: {v['reason']}")
        print(f"\n{len(violations)} violation(s).")
    return 1 if violations else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
