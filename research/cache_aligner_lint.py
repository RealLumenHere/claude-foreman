#!/usr/bin/env python3
"""CacheAligner lint (wq#887) — read-only prefix-stability check on worker prompts + settings.

Wraps headroom-ai's CacheAligner (no ML, no proxy, sub-ms) as a lint. Reports content that
would break a provider KV-cache prefix: UUIDs, ISO-8601 timestamps, JWT-shaped tokens, hex
hashes. Never writes to a target; CacheAligner itself never rewrites (transforms_applied is
always empty upstream) so there is nothing to opt out of.

Three checks per target:

  volatile   headroom's detect_volatile_content, per line, on the file as it sits on disk.
  tail       for prompt templates: every per-run placeholder must sit in the trailing block,
             so the static head is byte-identical across spawns (the wq#630 invariant).
  spawn      renders a template twice with different tickets and compares the shared byte
             prefix, then runs CacheAligner over each rendering for its stable_prefix_hash.

Install:  python3 -m venv /tmp/hr && /tmp/hr/bin/pip install headroom-ai   # no [ml] extra
Run:      /tmp/hr/bin/python research/cache_aligner_lint.py [--json] [--selfcheck] [PATH ...]
Exit 0 clean, 1 findings, 2 setup error. Findings and method: research/CACHE_ALIGNER_LINT.md
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import warnings

# Token counts here are tiktoken approximations. An exact count needs an Anthropic client,
# and the foreman guardrails forbid API spend; the numbers are used for relative sizing.
warnings.filterwarnings("ignore", message=".*No client provided.*")

# CacheAligner logs its own warning on every apply(). We surface the same text once per
# target from the returned TransformResult, so the duplicate log line is noise.
logging.getLogger("headroom.transforms.cache_aligner").setLevel(logging.ERROR)

try:
    from headroom import CacheAligner, CacheAlignerConfig, Tokenizer
    from headroom.providers.anthropic import AnthropicTokenCounter
    from headroom.transforms.cache_aligner import detect_volatile_content
except ImportError:
    sys.stderr.write(
        "headroom-ai not importable. pip install headroom-ai (no [ml] extra).\n")
    raise SystemExit(2)

FOREMAN = os.environ.get("FOREMAN_ROOT", "/home/lumen/foreman")

# Targets. worker-settings-interim.json (named in wq#887) no longer exists: the harness
# reads harness/worker-settings.json, per T2A_WORKER_HARNESS_DESIGN.md §4 and
# fworker.py:65. Both survivors are linted. worker.md is the template fworker actually
# renders (fworker.py:859); FOREMAN_DISPATCH.prompt is the operator-facing one.
TARGETS = [
    (os.path.join(FOREMAN, "FOREMAN_DISPATCH.prompt"), "prompt"),
    (os.path.join(FOREMAN, "harness/prompts/worker.md"), "prompt"),
    (os.path.join(FOREMAN, "harness/prompts/fire-diagnosis.md"), "prompt"),
    (os.path.join(FOREMAN, "harness/prompts/fire-apply.md"), "prompt"),
    (os.path.join(FOREMAN, "worker-settings-general.json"), "settings"),
    (os.path.join(FOREMAN, "harness/worker-settings.json"), "settings"),
    # Auto-loaded into every worker's prefix by Claude Code, so it is prefix content
    # whether or not the harness thinks of it as a prompt.
    ("/home/lumen/CLAUDE.md", "prompt"),
]

MODEL = "claude-opus-4-20250514"

# Static bytes stranded behind the first per-run value are cache the provider can never
# reuse. A few tokens of label text ("WORKTREE: ") between consecutive values is
# unavoidable; a stranded *block* of instructions is a real miss worth moving. 50 tokens is
# roughly a paragraph — below it there is nothing to act on.
STRANDED_STATIC_TOKEN_BUDGET = 50

# Per-run substitution points. fworker.py replaces the {{...}} forms; <TICKET> is the
# manual-dispatch equivalent in FOREMAN_DISPATCH.prompt.
PLACEHOLDERS = ["{{TICKET_ID}}", "{{TITLE}}", "{{DETAIL}}", "{{WORKTREE}}",
                "{{PRIOR_ATTEMPT_NOTES}}", "{{PLAN_PATH}}", "{{APPROVAL_PATH}}",
                "{{DRIFT_PATH}}", "{{PLAN_TEXT}}", "<TICKET>"]

# Two synthetic dispatches. Differ in every substituted field so the shared byte prefix
# measures the static head and nothing else.
def _spawn(tid, attempt, title, detail, notes, plan_text):
    """One synthetic dispatch. Path shapes match fire_gate.plan_path/approval_path/
    drift_path and fworker's worktree layout, so a rendering diverges where a real one
    would."""
    return {
        "{{TICKET_ID}}": tid,
        "{{TITLE}}": title,
        "{{DETAIL}}": detail,
        "{{WORKTREE}}": f"/home/lumen/worktrees/wq{tid}-a{attempt}",
        "{{PRIOR_ATTEMPT_NOTES}}": notes,
        "{{PLAN_PATH}}": f"{FOREMAN}/plans/wq{tid}.md",
        "{{APPROVAL_PATH}}": f"{FOREMAN}/approvals/wq{tid}.approved",
        "{{DRIFT_PATH}}": f"{FOREMAN}/drift/wq{tid}.drift",
        "{{PLAN_TEXT}}": plan_text,
        "<TICKET>": tid,
    }


SPAWNS = [
    _spawn("887", 1, "[foreman-tok] CacheAligner diagnostic",
           "Run headroom CacheAligner as a read-only lint.", "",
           "## Current State\nNo lint exists.\n## Change\nAdd one."),
    _spawn("633", 2, "[foreman-ctx] context budget probe",
           "Measure the fixed-prefix cost of a worker spawn.",
           "attempt 1 ran out of turns",
           "## Current State\nBaseline is stale.\n## Change\nRe-measure."),
]


def tokenizer():
    return Tokenizer(AnthropicTokenCounter(MODEL), MODEL)


def scan_volatile(text):
    """detect_volatile_content per line, so every finding carries a line number."""
    out = []
    for lineno, line in enumerate(text.splitlines(), 1):
        for f in detect_volatile_content(line):
            out.append({"line": lineno, "label": f.label, "sample": f.sample})
    return out


def check_tail(text, tok):
    """Every placeholder must sit in the trailing block, static head byte-identical."""
    hits = [(text.index(p), p) for p in PLACEHOLDERS if p in text]
    if not hits:
        return {"placeholders": [], "aligned": True,
                "note": "no per-run placeholders; whole file is static"}
    first_off, first_name = min(hits)
    trailing = text[first_off:]
    # Static text left behind the first per-run value: everything in the trailing block
    # that is not itself a placeholder. Those bytes can never land in a shared prefix.
    stranded = trailing
    for _, p in hits:
        stranded = stranded.replace(p, "")
    stranded_tokens = tok.count_text(stranded)
    return {
        "placeholders": [{"name": p, "offset": off} for off, p in sorted(hits)],
        "first_placeholder": first_name,
        "static_head_bytes": first_off,
        "trailing_block_bytes": len(trailing),
        "static_head_pct": round(100.0 * first_off / len(text), 1),
        "stranded_static_bytes": len(stranded),
        "stranded_static_tokens": stranded_tokens,
        "aligned": stranded_tokens <= STRANDED_STATIC_TOKEN_BUDGET,
    }


def check_spawn(text, tok):
    """Render twice with different tickets; measure what stays byte-identical."""
    renders = []
    for spawn in SPAWNS:
        out = text
        for k, v in spawn.items():
            out = out.replace(k, v)
        renders.append(out)
    a, b = renders
    shared = 0
    for ca, cb in zip(a, b):
        if ca != cb:
            break
        shared += 1
    aligner = CacheAligner(CacheAlignerConfig(enabled=True, use_dynamic_detector=False))
    hashes, warns = [], []
    for r in renders:
        # A dispatch prompt is the first user message in a real `claude -p` run, but
        # CacheAligner only inspects system-role content; system is the closest analogue
        # of the frozen prefix it is asked to reason about.
        res = aligner.apply([{"role": "system", "content": r}], tok)
        hashes.append(res.cache_metrics.stable_prefix_hash)
        warns.extend(res.warnings)
    return {
        "shared_prefix_bytes": shared,
        "shared_prefix_tokens_est": tok.count_text(a[:shared]),
        "render_bytes": [len(a), len(b)],
        "shared_prefix_pct": round(100.0 * shared / max(len(a), len(b)), 1),
        "stable_prefix_hashes": hashes,
        "hash_identical_across_spawns": hashes[0] == hashes[1],
        "aligner_warnings": sorted(set(warns)),
        "alignment_score": aligner.get_alignment_score(
            [{"role": "system", "content": a}]),
    }


def scan_settings_values(path):
    """Walk the JSON so a volatile value is reported by key path, not by line."""
    with open(path) as fh:
        data = json.load(fh)
    out = []

    def walk(node, path_parts):
        if isinstance(node, dict):
            for k, v in node.items():
                walk(v, path_parts + [k])
        elif isinstance(node, list):
            for i, v in enumerate(node):
                walk(v, path_parts + [str(i)])
        elif isinstance(node, str):
            for f in detect_volatile_content(node):
                out.append({"key": ".".join(path_parts),
                            "label": f.label, "sample": f.sample})

    walk(data, [])
    return out


def lint(path, kind, tok):
    if not os.path.exists(path):
        return {"path": path, "kind": kind, "status": "MISSING"}
    with open(path) as fh:
        text = fh.read()
    report = {
        "path": path,
        "kind": kind,
        "bytes": len(text),
        "tokens_est": tok.count_text(text),
        "volatile": scan_volatile(text),
    }
    if kind == "prompt":
        report["tail"] = check_tail(text, tok)
        report["spawn"] = check_spawn(text, tok)
    else:
        report["volatile_values"] = scan_settings_values(path)
    findings = len(report["volatile"]) + len(report.get("volatile_values", []))
    if not report.get("tail", {}).get("aligned", True):
        findings += 1
    report["findings"] = findings
    report["status"] = "CLEAN" if findings == 0 else "FINDINGS"
    return report


SELFCHECK_FIXTURE = (
    "You are a worker.\n"
    "Session 550e8400-e29b-41d4-a716-446655440000 started 2026-08-24T01:13:44Z.\n"
    "TICKET: {{TICKET_ID}}\n"
    + "static rules that belong at the top. " * 40 + "\n"
    "digest 5d41402abc4b2a76b9719d911017c592\n"
)


def selfcheck(tok):
    """Prove the detector is not vacuously clean before trusting a CLEAN verdict."""
    labels = {f["label"] for f in scan_volatile(SELFCHECK_FIXTURE)}
    tail = check_tail(SELFCHECK_FIXTURE, tok)
    spawn = check_spawn(SELFCHECK_FIXTURE, tok)
    checks = [
        ("uuid detected", "uuid" in labels),
        ("iso8601 detected", "iso8601" in labels),
        ("hex_hash detected", "hex_hash" in labels),
        ("stranded static flagged", not tail["aligned"]),
        ("alignment score penalised", spawn["alignment_score"] < 100),
        ("aligner warned", bool(spawn["aligner_warnings"])),
    ]
    for name, ok in checks:
        print(f"{'ok  ' if ok else 'FAIL'} {name}")
    return 0 if all(ok for _, ok in checks) else 1


def render_text(reports):
    lines = []
    for r in reports:
        if r["status"] == "MISSING":
            lines.append(f"{r['status']:9} {r['path']}  (not on disk)")
            continue
        lines.append(f"{r['status']:9} {r['path']}  "
                     f"{r['bytes']}B / ~{r['tokens_est']} tok")
        for f in r["volatile"]:
            lines.append(f"            line {f['line']}: {f['label']} {f['sample']!r}")
        for f in r.get("volatile_values", []):
            lines.append(f"            {f['key']}: {f['label']} {f['sample']!r}")
        t = r.get("tail")
        if t and t["placeholders"]:
            lines.append(f"            tail: static head {t['static_head_bytes']}B "
                         f"({t['static_head_pct']}%), first per-run value "
                         f"{t['first_placeholder']}, "
                         f"~{t['stranded_static_tokens']} tok stranded behind it "
                         f"(budget {STRANDED_STATIC_TOKEN_BUDGET})")
        elif t:
            lines.append(f"            tail: {t['note']}")
        s = r.get("spawn")
        if s:
            lines.append(f"            spawn: {s['shared_prefix_bytes']}B "
                         f"(~{s['shared_prefix_tokens_est']} tok, "
                         f"{s['shared_prefix_pct']}%) identical across two dispatches; "
                         f"alignment score {s['alignment_score']}")
            for w in s["aligner_warnings"]:
                lines.append(f"            aligner: {w}")
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("paths", nargs="*", help="override the default target list")
    ap.add_argument("--json", action="store_true", help="emit the raw report")
    ap.add_argument("--selfcheck", action="store_true",
                    help="run the detector against a known-dirty fixture and exit")
    args = ap.parse_args()

    if args.selfcheck:
        return selfcheck(tokenizer())

    targets = TARGETS
    if args.paths:
        targets = [(p, "settings" if p.endswith(".json") else "prompt")
                   for p in args.paths]

    tok = tokenizer()
    reports = [lint(p, k, tok) for p, k in targets]
    print(json.dumps(reports, indent=2) if args.json else render_text(reports))
    return 1 if any(r.get("findings") for r in reports) else 0


if __name__ == "__main__":
    raise SystemExit(main())
