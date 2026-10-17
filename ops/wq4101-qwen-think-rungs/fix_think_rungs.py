#!/usr/bin/env python3
"""fix_think_rungs (wq#4101) — move qwen3* thinking rungs off LiteLLM's native
`ollama/` provider onto ollama's OpenAI-compat endpoint with thinking disabled.

Why (fleet-health-c8220e, 2026-10-16, verified live against isis 2026-10-17):
  - Through `ollama/<qwen3*>` the model thinks by default, the thinking eats
    the whole max_tokens budget, LiteLLM drops the `thinking` field AND maps
    the real done_reason=length to finish_reason=stop — callers get an empty
    string that looks like a successful completion.
  - ollama's own /v1 endpoint has neither defect: it reports the true
    finish_reason ("length") and carries thinking in a `reasoning` field.
    With `reasoning: {effort: none}` in the body, thinking is off entirely
    (probe: 15 completion tokens, correct code, finish_reason=stop).
  - `extra_body: {reasoning: {effort: none}}` on an `openai/` rung is the
    registry-standard shape (research/inference_nodes.yaml:48, wq#2251 —
    nephthys-qwen14b runs it in production today). Upstream LiteLLM `think`
    mapping for the native provider is broken (BerriAI/litellm #15059,
    #11680), so converting the provider beats patching the native route.

Per-entry transform (model_list entries whose litellm_params.model matches
ollama/qwen3* or ollama_chat/qwen3*):
  1. model:    ollama/<blob> -> openai/<blob>
  2. api_base: append /v1 when missing
  3. api_key:  insert `none` when absent (openai provider requires one)
  4. extra_body: {reasoning: {effort: none}}  (inserted; an entry that already
     carries an extra_body line is REPORTED for hand-merge, not edited)
Other native-ollama entries (non-qwen3) are reported as finish_reason-masking
suspects but not touched — converting them is a per-alias decision.

Text surgery like remove_dead_rungs.py (wq#3212), never a yaml round-trip
(that would strip the config's curated comments). Safety gates:
  - post-edit parse must equal pre-edit parse plus exactly the intended
    mutations, else nothing is written;
  - timestamped .bak next to the config before writing.

Usage:
  fix_think_rungs.py --check [--config PATH]   # dry-run, exit 1 on a failed gate
  fix_think_rungs.py --apply [--config PATH]
"""
import argparse
import copy
import datetime
import pathlib
import re
import shutil
import sys

import yaml

DEFAULT_CONFIG = "/home/lumen/agent/litellm_config.yaml"
NATIVE_RE = re.compile(r"^(ollama|ollama_chat)/(.+)$")
THINKING_BLOB_RE = re.compile(r"^qwen3", re.IGNORECASE)
EXTRA_BODY = {"reasoning": {"effort": "none"}}
EXTRA_BODY_LINE = "extra_body: {reasoning: {effort: none}}"


def classify(cfg: dict) -> dict:
    """Split model_list into {fix: [names], hand_merge: [names], suspects: [names]}.
    fix        — ollama/qwen3* entries this script converts.
    hand_merge — would be fixed but already carry extra_body (operator merges).
    suspects   — other native-ollama entries (finish_reason masking class)."""
    out = {"fix": [], "hand_merge": [], "suspects": []}
    for e in cfg.get("model_list") or []:
        params = e.get("litellm_params") or {}
        m = NATIVE_RE.match(str(params.get("model", "")))
        if not m:
            continue
        name = e.get("model_name")
        if not THINKING_BLOB_RE.match(m.group(2)):
            out["suspects"].append(name)
        elif "extra_body" in params:
            out["hand_merge"].append(name)
        else:
            out["fix"].append(name)
    return out


def _mutate_parsed(entry: dict) -> None:
    """Apply the transform to one parsed entry, in place (the expectation the
    text surgery is validated against)."""
    params = entry["litellm_params"]
    blob = NATIVE_RE.match(params["model"]).group(2)
    params["model"] = f"openai/{blob}"
    base = str(params.get("api_base", ""))
    if base and not base.rstrip("/").endswith("/v1"):
        params["api_base"] = base.rstrip("/") + "/v1"
    if "api_key" not in params:
        params["api_key"] = "none"
    params["extra_body"] = copy.deepcopy(EXTRA_BODY)


def expected_after(before: dict, fix_names: set) -> dict:
    expect = copy.deepcopy(before)
    for e in expect.get("model_list") or []:
        if e.get("model_name") in fix_names and NATIVE_RE.match(
                str((e.get("litellm_params") or {}).get("model", ""))):
            _mutate_parsed(e)
    return expect


def _entry_ranges(lines: list) -> list:
    """(start, end, model_name) per `- model_name:` item, end exclusive."""
    entry_re = re.compile(r"^(\s*)-\s+model_name:\s*['\"]?([^'\"\s]+)")
    found = []
    for i, ln in enumerate(lines):
        m = entry_re.match(ln)
        if m:
            found.append((i, len(m.group(1)), m.group(2)))
    ranges = []
    for n, (start, indent, name) in enumerate(found):
        end = found[n + 1][0] if n + 1 < len(found) else len(lines)
        while end > start + 1 and (not lines[end - 1].strip()
                                   or _indent_of(lines[end - 1]) < indent):
            end -= 1
        ranges.append((start, end, name))
    return ranges


def _indent_of(ln: str) -> int:
    return len(ln) - len(ln.lstrip())


def rewrite_entry(lines: list) -> list:
    """Transform one entry's lines (text surgery, comments preserved)."""
    out = list(lines)
    model_re = re.compile(r"^(\s*)model:\s*['\"]?(ollama(?:_chat)?)/([^'\"\s]+)['\"]?\s*(#.*)?$")
    base_re = re.compile(r"^(\s*)api_base:\s*['\"]?([^'\"\s]+?)/?['\"]?\s*(#.*)?$")
    key_re = re.compile(r"^\s*api_key:")
    model_i = model_indent = None
    for i, ln in enumerate(out):
        m = model_re.match(ln)
        if m:
            comment = f"  {m.group(4)}" if m.group(4) else ""
            out[i] = f"{m.group(1)}model: openai/{m.group(3)}{comment}"
            model_i, model_indent = i, m.group(1)
            continue
        b = base_re.match(ln)
        if b and not b.group(2).endswith("/v1"):
            comment = f"  {b.group(3)}" if b.group(3) else ""
            out[i] = f"{b.group(1)}api_base: {b.group(2)}/v1{comment}"
    if model_i is None:
        raise RuntimeError("no native-ollama model: line found in entry")
    inserts = [f"{model_indent}{EXTRA_BODY_LINE}"]
    if not any(key_re.match(ln) for ln in out):
        inserts.insert(0, f"{model_indent}api_key: none")
    return out[:model_i + 1] + inserts + out[model_i + 1:]


def fix_text(text: str, fix_names: set) -> str:
    lines = text.splitlines()
    for start, end, name in reversed(_entry_ranges(lines)):
        if name in fix_names and any(
                re.search(r"model:\s*['\"]?ollama(_chat)?/", ln) for ln in lines[start:end]):
            lines[start:end] = rewrite_entry(lines[start:end])
    return "\n".join(lines) + ("\n" if text.endswith("\n") else "")


def main(argv=None) -> int:
    p = argparse.ArgumentParser()
    mode = p.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true")
    mode.add_argument("--apply", action="store_true")
    p.add_argument("--config", default=DEFAULT_CONFIG)
    args = p.parse_args(argv)

    path = pathlib.Path(args.config)
    text = path.read_text()
    before = yaml.safe_load(text)
    plan = classify(before)

    for name in plan["suspects"]:
        print(f"SUSPECT (native ollama, finish_reason masked, not touched): {name}")
    for name in plan["hand_merge"]:
        print(f"HAND-MERGE (has extra_body already — add reasoning effort none "
              f"and move to openai/ yourself): {name}")
    if not plan["fix"]:
        print("nothing to do: no ollama/qwen3* entries without extra_body")
        return 0
    print(f"converting to openai/ + reasoning-off: {sorted(plan['fix'])}")

    new_text = fix_text(text, set(plan["fix"]))
    after = yaml.safe_load(new_text)
    expect = expected_after(before, set(plan["fix"]))
    if after != expect:
        print("VALIDATION FAIL: post-edit parse != pre-edit parse + intended mutations; "
              "nothing written")
        return 1

    if args.check:
        print("check PASS (no write)")
        return 0

    bak = path.with_name(path.name + ".bak."
                         + datetime.datetime.now().strftime("%Y%m%d_%H%M%S") + "_wq4101")
    shutil.copy2(path, bak)
    path.write_text(new_text)
    print(f"wrote {path} (backup: {bak})")
    print("NOTE: the litellm container reads the config at startup — restart it via a "
          "detached systemd-run (instinct 5), then run probe_think_rung.py per alias.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
