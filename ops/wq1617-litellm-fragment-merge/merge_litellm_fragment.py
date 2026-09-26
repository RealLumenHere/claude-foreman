#!/usr/bin/env python3
"""wq#1617 — guard against the staged-full-copy lost-update hazard on the live
LiteLLM proxy config (`/home/lumen/agent/litellm_config.yaml`).

Root cause: `.staged.*` files are full copies of the config taken at some
point in the past. If a session applies one wholesale, it silently reverts
every change made to the live config after that copy was taken — the
triggering case was `.staged.minimax-m3` (copied 2026-07-31, before wq#1591
registered glm-5.3-flash pricing at 05:31 UTC 2026-09-10): applying it would
have dropped every `input_cost_per_token`/`output_cost_per_token`/
`cache_read_input_token_cost` line the live config had gained since, sending
the fleet's largest workload back to logging $0.00 spend with no error.

Two subcommands, matching the ticket's two acceptable fixes:

    check <live.yaml> <candidate.yaml>
        Refuses (exit 1) if applying `candidate` over `live` wholesale would
        drop any pricing key an existing model_list entry currently has.
        Run this before ANY `cp candidate live` on the gateway node.

    merge <live.yaml> <fragment.yaml> [-o out.yaml]
        Applies `fragment`'s model_info keys onto the matching model_name
        entries in `live` (adding a new entry if the model_name doesn't
        exist yet), leaving every other live entry untouched byte-for-byte
        in structure. Never silently drops a pricing key that live already
        has — refuses instead (same guard as `check`, run automatically
        before writing).

Usage on the gateway node (this BUILD worker's sandbox has no read/write
access to /home/lumen/agent — same convention as wq#897/wq#1299/wq#1406):

    python3 merge_litellm_fragment.py check /home/lumen/agent/litellm_config.yaml \\
        /home/lumen/agent/litellm_config.yaml.staged.minimax-m3

    python3 merge_litellm_fragment.py merge /home/lumen/agent/litellm_config.yaml \\
        fragments/minimax-m3.yaml -o /tmp/litellm_config.yaml.merged
"""
import argparse
import sys

PRICING_KEYS = (
    "input_cost_per_token",
    "output_cost_per_token",
    "cache_read_input_token_cost",
)


def _model_list_by_name(config: dict) -> dict[str, dict]:
    """Last entry wins for a duplicated model_name, matching LiteLLM's own
    load order (later `model_list` entries override earlier ones with the
    same alias)."""
    result: dict[str, dict] = {}
    for entry in config.get("model_list") or []:
        name = entry.get("model_name")
        if name is not None:
            result[name] = entry
    return result


def _pricing(entry: dict) -> dict:
    info = entry.get("model_info") or {}
    return {k: info[k] for k in PRICING_KEYS if info.get(k) is not None}


def find_dropped_pricing(live_config: dict, candidate_config: dict) -> list[dict]:
    """Models present in `live_config` with at least one pricing key set,
    where `candidate_config` either lacks the model entirely or lacks one of
    those keys. Returns one dict per affected model:
    {"model_name": ..., "dropped_keys": [...]}."""
    live_by_name = _model_list_by_name(live_config)
    candidate_by_name = _model_list_by_name(candidate_config)
    dropped = []
    for name, live_entry in live_by_name.items():
        live_pricing = _pricing(live_entry)
        if not live_pricing:
            continue
        candidate_entry = candidate_by_name.get(name)
        candidate_pricing = _pricing(candidate_entry) if candidate_entry else {}
        missing = [k for k in live_pricing if k not in candidate_pricing]
        if missing:
            dropped.append({"model_name": name, "dropped_keys": missing})
    return dropped


def find_pricing_erasures(live_config: dict, fragment_config: dict) -> list[dict]:
    """Merge (dict.update of model_info) only ever SETS the keys a fragment
    explicitly lists — it can't drop a live pricing key by omission the way
    a full-file replace can. The only way an overlay merge destroys pricing
    is if the fragment explicitly sets a key live already has to a
    null/blank value. Returns one dict per such model:
    {"model_name": ..., "erased_keys": [...]}."""
    live_by_name = _model_list_by_name(live_config)
    erasures = []
    for frag_entry in fragment_config.get("model_list") or []:
        name = frag_entry.get("model_name")
        live_entry = live_by_name.get(name)
        if live_entry is None:
            continue
        live_pricing = _pricing(live_entry)
        frag_info = frag_entry.get("model_info") or {}
        erased = [k for k in PRICING_KEYS if k in frag_info and frag_info[k] is None and k in live_pricing]
        if erased:
            erasures.append({"model_name": name, "erased_keys": erased})
    return erasures


def merge_fragment(live_config: dict, fragment_config: dict) -> dict:
    """Overlay each fragment model_list entry's model_info keys onto the
    matching live entry (by model_name), preserving every other live key
    (litellm_params, unrelated model_info keys, other model_list entries).
    A model_name absent from live is appended as a brand-new entry. Raises
    ValueError instead of silently erasing a pricing key live already has
    — merge is additive-only for pricing, never destructive."""
    erasures = find_pricing_erasures(live_config, fragment_config)
    if erasures:
        raise ValueError(
            "refusing to merge: fragment would erase existing pricing keys: "
            + "; ".join(f"{e['model_name']}: {e['erased_keys']}" for e in erasures)
        )

    merged = dict(live_config)
    model_list = [dict(e) for e in (live_config.get("model_list") or [])]
    index_by_name = {e.get("model_name"): i for i, e in enumerate(model_list)}

    for frag_entry in fragment_config.get("model_list") or []:
        name = frag_entry.get("model_name")
        frag_info = frag_entry.get("model_info") or {}
        if name in index_by_name:
            i = index_by_name[name]
            live_entry = dict(model_list[i])
            live_info = dict(live_entry.get("model_info") or {})
            live_info.update(frag_info)
            live_entry["model_info"] = live_info
            model_list[i] = live_entry
        else:
            model_list.append(dict(frag_entry))

    merged["model_list"] = model_list
    return merged


def _load_yaml(path: str) -> dict:
    import yaml  # local import: keep module importable/testable without PyYAML on PATH mismatch
    with open(path) as f:
        return yaml.safe_load(f)


def _dump_yaml(config: dict) -> str:
    import yaml
    return yaml.safe_dump(config, sort_keys=False)


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    p_check = sub.add_parser("check", help="refuse if candidate would drop live pricing")
    p_check.add_argument("live")
    p_check.add_argument("candidate")

    p_merge = sub.add_parser("merge", help="overlay a fragment's model_info onto live")
    p_merge.add_argument("live")
    p_merge.add_argument("fragment")
    p_merge.add_argument("-o", "--output", help="write merged YAML here instead of stdout")

    args = parser.parse_args(argv)

    if args.command == "check":
        live_config = _load_yaml(args.live)
        candidate_config = _load_yaml(args.candidate)
        dropped = find_dropped_pricing(live_config, candidate_config)
        if dropped:
            print(f"REFUSED: applying {args.candidate} over {args.live} would drop pricing:")
            for d in dropped:
                print(f"  {d['model_name']}: {d['dropped_keys']}")
            return 1
        print(f"OK: {args.candidate} does not drop any pricing key {args.live} currently has.")
        return 0

    if args.command == "merge":
        live_config = _load_yaml(args.live)
        fragment_config = _load_yaml(args.fragment)
        try:
            merged = merge_fragment(live_config, fragment_config)
        except ValueError as exc:
            print(f"REFUSED: {exc}")
            return 1
        text = _dump_yaml(merged)
        if args.output:
            with open(args.output, "w") as f:
                f.write(text)
            print(f"wrote merged config to {args.output}")
        else:
            print(text)
        return 0

    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
