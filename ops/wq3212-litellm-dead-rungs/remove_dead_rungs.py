#!/usr/bin/env python3
"""remove_dead_rungs (wq#3212) — drop the 5 permanently-dead rungs from
litellm_config.yaml, riding the wq#1402 litellm-upgrade recreate (Mon
2026-10-05 window) so there is exactly one container recreate.

Removal set and per-rung decision (full reasoning in README.md):
  claude-opus-5       (a) provider anthropic, key defused by design — no API
                          entitlement, 401s forever.
  claude-max-sonnet   (b) claude-shim :8091 was never installed (ISSUE-LOG
  claude-max-opus         wq#2628, wq#3168); council seat already retired.
  lumen-lfm2.5-2.6b   (c) host.docker.internal:8000 is a docker-proxy to an
  kimik3-0.4b-local       unrelated 127.0.0.1-bound container; Lumen runs no
                          models by directive.

Text-surgery removal (never a yaml round-trip: that would strip the config's
curated comments, e.g. the May-28 no-silent-reroute note). Safety gates:
  - refuses if a removed name is referenced anywhere OUTSIDE model_list
    (fallback chains, router settings) — that must be cleaned first;
  - post-edit the parsed result must equal the pre-edit parse minus exactly
    the removed model_list entries, else nothing is written;
  - timestamped .bak next to the config before writing.

On --apply it also resolves the litellm-completion aq rows (2579/2583/2585
class) for every host the removal emptied, keyed by host like the probe's
own `aq add` (wq#3169). The updated probe would do the same on its next tick
("left the probe set"); doing it here just closes them inside the window.

Usage:
  remove_dead_rungs.py --check [--config PATH]   # dry-run, exits 1 on any gate
  remove_dead_rungs.py --apply [--config PATH]
"""
import argparse
import copy
import datetime
import pathlib
import re
import shutil
import sys

import yaml

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))          # repo checkout
sys.path.append("/home/lumen/bin")     # deployed copy
import litellm_rungs  # noqa: E402

REMOVE = ("claude-opus-5", "claude-max-sonnet", "claude-max-opus",
          "lumen-lfm2.5-2.6b", "kimik3-0.4b-local")
DEFAULT_CONFIG = "/home/lumen/agent/litellm_config.yaml"
AQ_SOURCE = "litellm-completion"
ENTRY_RE = re.compile(r"^(\s*)-\s+model_name:\s*['\"]?([^'\"\s]+)")


def _find_refs_outside_model_list(cfg: dict, names: set) -> list:
    """Every place a removed name is used other than its own model_list entry —
    a fallback chain or router setting naming a gone model breaks the gateway."""
    hits = []

    def walk(node, path):
        if isinstance(node, dict):
            for k, v in node.items():
                if isinstance(k, str) and k in names:   # {claude-max-opus: [...]}
                    hits.append(f"{path}.{k} (key)")
                walk(v, f"{path}.{k}")
        elif isinstance(node, list):
            for i, v in enumerate(node):
                walk(v, f"{path}[{i}]")
        elif isinstance(node, str) and node in names:
            hits.append(f"{path} = {node}")

    for key, val in (cfg or {}).items():
        if key != "model_list":
            walk(val, key)
    return hits


def remove_entries(text: str, names: set) -> tuple:
    """Drop each named model_list entry (plus its directly-preceding same-indent
    comment lines) from the raw text. Returns (new_text, removed_names)."""
    lines = text.splitlines(keepends=True)
    drop = [False] * len(lines)
    removed = set()
    i = 0
    while i < len(lines):
        m = ENTRY_RE.match(lines[i])
        if not m or m.group(2) not in names:
            i += 1
            continue
        indent, name = len(m.group(1)), m.group(2)
        start = i
        while start > 0 and re.match(r"^\s*#", lines[start - 1]) \
                and len(lines[start - 1]) - len(lines[start - 1].lstrip()) >= indent:
            start -= 1
        end = i + 1
        while end < len(lines):
            ln = lines[end]
            if not ln.strip():
                end += 1
                continue
            cur = len(ln) - len(ln.lstrip())
            if cur < indent or (cur == indent and not re.match(r"^\s*#", ln)):
                break
            end += 1
        for j in range(start, end):
            drop[j] = True
        removed.add(name)
        i = end
    return "".join(ln for j, ln in enumerate(lines) if not drop[j]), removed


def validate(before: dict, after: dict, removed: set) -> list:
    """after must be before minus exactly the removed model_list entries."""
    errors = []
    expect = copy.deepcopy(before)
    expect["model_list"] = [e for e in before.get("model_list", [])
                            if e.get("model_name") not in removed]
    if after != expect:
        errors.append("post-edit parse != pre-edit parse minus removed entries")
    still = {e.get("model_name") for e in after.get("model_list", [])} & removed
    if still:
        errors.append(f"still present after edit: {sorted(still)}")
    return errors


def resolve_aq_rows(host_keys: list) -> None:
    """Resolve open litellm-completion rows for emptied hosts (schema per
    ops/wq2903-model-ref-drift/lumen_model_watchdog.py::aq_clear, wq#548)."""
    try:
        sys.path.insert(0, "/home/lumen/agents/alert-queue")
        import aq as _aq
        c = _aq.db()
        ts = _aq.now()
        for key in host_keys:
            rows = c.execute("SELECT id FROM alerts WHERE source=? AND key=? "
                             "AND status='open'", (AQ_SOURCE, key)).fetchall()
            for (rid,) in rows:
                c.execute("UPDATE alerts SET status='resolved', note=?, resolved_ts=? "
                          "WHERE id=? AND status='open'",
                          (f"wq#3212: rung removed from litellm_config.yaml on {ts}",
                           ts, rid))
            print(f"aq: resolved {len(rows)} open row(s) for key={key}")
        c.commit()
        c.close()
    except Exception as e:  # noqa: BLE001 - aq is best-effort; the edit already landed
        print(f"aq resolve skipped ({e}) — resolve keys manually: {host_keys}")


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
    names = set(REMOVE)

    present = {e.get("model_name") for e in before.get("model_list", [])} & names
    if not present:
        print("nothing to do: none of the removal set is in model_list")
        return 0

    refs = _find_refs_outside_model_list(before, present)
    if refs:
        print("REFUSING: removed names are referenced outside model_list — clean these first:")
        for h in refs:
            print("  " + h)
        return 1

    new_text, removed = remove_entries(text, names)
    after = yaml.safe_load(new_text)
    errors = validate(before, after, removed)
    if errors:
        for e in errors:
            print("VALIDATION FAIL: " + e)
        return 1

    hosts_before = litellm_rungs.host_groups(args.config)
    emptied = [h for h, models in hosts_before.items()
               if all(m in removed for m in models)]
    print(f"removing {sorted(removed)}; hosts emptied: {emptied or 'none'}")

    if args.check:
        print("check PASS (no write)")
        return 0

    bak = path.with_name(path.name + ".bak."
                         + datetime.datetime.now().strftime("%Y%m%d_%H%M%S") + "_wq3212")
    shutil.copy2(path, bak)
    path.write_text(new_text)
    print(f"wrote {path} (backup: {bak})")
    resolve_aq_rows(emptied)
    print("NOTE: the litellm container reads the config at startup — the wq#1402 "
          "recreate (litellm-upgrade --apply) picks this up; no extra restart needed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
