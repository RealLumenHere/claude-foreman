#!/usr/bin/env python3
"""Score a Squidbrake observe-mode event log against the hardware ruleset.

wq#4750 (wq#4744 T6). During the one-week observe-mode pilot Squidbrake HOLDS nothing;
it only logs what it *would* have held. This tool replays that log through the same
rules.yaml the pilot ran under and reports the held / would-have-held / false-positive
rates the ticket asks for, plus a per-rule breakdown to inform the review-mode decision.

The rules.yaml `match_mcp` / `match_cmd` / `action` fields are the source of truth: this
tool does not hard-code any tool names, so the ruleset and the report never drift.

Event log format: JSON Lines, one object per line, e.g.
    {"ts": "...", "session_cwd": "/home/lumen/re",
     "mcp_tool": "mcp__openocd__flash_firmware",
     "operator": {"verdict": "denied", "note": "wrong image"}}
    {"ts": "...", "session_cwd": "/home/lumen/re",
     "command": "flashrom -p ... -w fw.bin",
     "operator": {"verdict": "benign", "note": "read-only, misfired"}}

operator.verdict (optional, filled in by the human during review):
    legit  = correct hold, action was a real energize the operator approved
    denied = correct hold, operator blocked the action (a real catch)
    benign = the hold was a FALSE POSITIVE; action should not have been held

Usage:
    observe_report.py --rules rules.yaml --log events.jsonl [--out REPORT.md]
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from dataclasses import dataclass, field

import yaml

# Hardware MCP servers whose UNLISTED tools default to HOLD (energizing surfaces we have
# not enumerated — fail safe). sigrok is passive measurement, so its unlisted tools
# default to ALLOW. A server not named here is out of hardware scope entirely.
HOLD_DEFAULT_SERVERS = ("openocd", "limpet-flashrom", "serial-maksoor")
ALLOW_DEFAULT_SERVERS = ("sigrok",)


@dataclass
class Rules:
    mode: str
    when_unreachable: str
    cwd_prefixes: list[str]
    require_hardware_mcp: bool
    exclude_headless_fworker: bool
    mcp_action: dict[str, tuple[str, str]]          # tool -> (action, rule_id)
    cmd_rules: list[tuple[re.Pattern, str, str]]    # (compiled, action, rule_id)


def load_rules(path: str) -> Rules:
    with open(path, encoding="utf-8") as fh:
        doc = yaml.safe_load(fh)
    scope = doc.get("scope", {}) or {}
    mcp_action: dict[str, tuple[str, str]] = {}
    cmd_rules: list[tuple[re.Pattern, str, str]] = []
    for rule in doc.get("rules", []) or []:
        action = rule["action"]
        rid = rule["id"]
        for tool in rule.get("match_mcp", []) or []:
            mcp_action[tool] = (action, rid)
        for pat in rule.get("match_cmd", []) or []:
            cmd_rules.append((re.compile(pat), action, rid))
    return Rules(
        mode=doc.get("mode", "observe"),
        when_unreachable=doc.get("when_unreachable", "block_risky"),
        cwd_prefixes=list(scope.get("session_cwd_prefixes", []) or []),
        require_hardware_mcp=bool(scope.get("require_hardware_mcp", False)),
        exclude_headless_fworker=bool(scope.get("exclude_headless_fworker", True)),
        mcp_action=mcp_action,
        cmd_rules=cmd_rules,
    )


def _mcp_server(tool: str) -> str | None:
    # mcp__<server>__<fn>  (server names may contain hyphens, not "__")
    if not tool.startswith("mcp__"):
        return None
    rest = tool[len("mcp__"):]
    return rest.split("__", 1)[0] if "__" in rest else rest


@dataclass
class Decision:
    in_scope: bool
    action: str          # "hold" | "allow" | "out_of_scope"
    rule_id: str


def classify_event(ev: dict, rules: Rules) -> Decision:
    """Decide scope + hold/allow for one event, using only the ruleset."""
    mcp_tool = ev.get("mcp_tool")
    command = ev.get("command")
    cwd = ev.get("session_cwd", "")

    server = _mcp_server(mcp_tool) if mcp_tool else None
    cwd_ok = any(cwd == p or cwd.startswith(p.rstrip("/") + "/") for p in rules.cwd_prefixes)
    has_hw_mcp = server in (HOLD_DEFAULT_SERVERS + ALLOW_DEFAULT_SERVERS)

    in_scope = cwd_ok or (rules.require_hardware_mcp and has_hw_mcp)
    if rules.exclude_headless_fworker and ev.get("headless_fworker"):
        in_scope = False
    if not in_scope:
        return Decision(False, "out_of_scope", "")

    if mcp_tool:
        if mcp_tool in rules.mcp_action:
            action, rid = rules.mcp_action[mcp_tool]
            return Decision(True, action, rid)
        if server in HOLD_DEFAULT_SERVERS:
            return Decision(True, "hold", "unknown-hw-mcp-default-hold")
        if server in ALLOW_DEFAULT_SERVERS:
            return Decision(True, "allow", "passive-server-default-allow")
        return Decision(False, "out_of_scope", "")

    if command:
        for pattern, action, rid in rules.cmd_rules:
            if pattern.search(command):
                return Decision(True, action, rid)
        # An in-scope shell command that matches no hold rule is not a hardware write.
        return Decision(True, "allow", "cmd-no-match-allow")

    return Decision(False, "out_of_scope", "")


@dataclass
class Report:
    mode: str
    total_events: int = 0
    in_scope: int = 0
    out_of_scope: int = 0
    triggers: int = 0                 # hold matches = would-have-held (observe) / held (review)
    allowed: int = 0
    verdicts: Counter = field(default_factory=Counter)   # legit/denied/benign/<unannotated>
    per_rule: Counter = field(default_factory=Counter)   # hold rule_id -> count

    @property
    def false_positives(self) -> int:
        return self.verdicts.get("benign", 0)

    @property
    def unannotated(self) -> int:
        return self.verdicts.get("<unannotated>", 0)

    def _rate(self, n: int, d: int) -> float:
        return round(n / d, 4) if d else 0.0

    def summary(self) -> dict:
        label = "held" if self.mode == "review" else "would_have_held"
        return {
            "mode": self.mode,
            "total_events": self.total_events,
            "in_scope": self.in_scope,
            "out_of_scope": self.out_of_scope,
            label: self.triggers,
            "allowed": self.allowed,
            "would_have_held_rate": self._rate(self.triggers, self.in_scope),
            "false_positives": self.false_positives,
            "false_positive_rate": self._rate(self.false_positives, self.triggers),
            "true_catch_rate": self._rate(
                self.verdicts.get("legit", 0) + self.verdicts.get("denied", 0), self.triggers),
            "annotation_coverage": self._rate(self.triggers - self.unannotated, self.triggers),
            "verdicts": dict(self.verdicts),
            "per_rule": dict(self.per_rule),
        }


def build_report(events: list[dict], rules: Rules) -> Report:
    rep = Report(mode=rules.mode)
    for ev in events:
        rep.total_events += 1
        d = classify_event(ev, rules)
        if not d.in_scope:
            rep.out_of_scope += 1
            continue
        rep.in_scope += 1
        if d.action == "hold":
            rep.triggers += 1
            rep.per_rule[d.rule_id] += 1
            verdict = (ev.get("operator") or {}).get("verdict") or "<unannotated>"
            rep.verdicts[verdict] += 1
        else:
            rep.allowed += 1
    return rep


def read_log(path: str) -> list[dict]:
    events = []
    with open(path, encoding="utf-8") as fh:
        for n, line in enumerate(fh, 1):
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError as exc:
                raise SystemExit(f"{path}:{n}: invalid JSON: {exc}")
    return events


def render_markdown(rep: Report) -> str:
    s = rep.summary()
    label = "held" if rep.mode == "review" else "would_have_held"
    lines = [
        "# Squidbrake observe-mode pilot report",
        "",
        f"- mode: `{s['mode']}`",
        f"- total events: {s['total_events']} (in-scope {s['in_scope']}, "
        f"out-of-scope {s['out_of_scope']})",
        f"- {label}: {s[label]}  ({s['would_have_held_rate']:.1%} of in-scope)",
        f"- allowed: {s['allowed']}",
        f"- false positives: {s['false_positives']}  "
        f"({s['false_positive_rate']:.1%} of {label})",
        f"- true catch rate: {s['true_catch_rate']:.1%}  "
        f"(operator verdict legit or denied)",
        f"- annotation coverage: {s['annotation_coverage']:.1%}",
        "",
        "## Holds by rule",
        "",
        "| rule | count |",
        "|---|---|",
    ]
    for rid, cnt in sorted(rep.per_rule.items(), key=lambda kv: -kv[1]):
        lines.append(f"| {rid} | {cnt} |")
    lines += ["", "## Operator verdicts", "", "| verdict | count |", "|---|---|"]
    for v, cnt in sorted(rep.verdicts.items(), key=lambda kv: -kv[1]):
        lines.append(f"| {v} | {cnt} |")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--rules", required=True, help="path to rules.yaml")
    ap.add_argument("--log", required=True, help="observe-mode event log (JSON Lines)")
    ap.add_argument("--out", help="write a markdown REPORT to this path")
    args = ap.parse_args(argv)

    rules = load_rules(args.rules)
    events = read_log(args.log)
    rep = build_report(events, rules)

    json.dump(rep.summary(), sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")
    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write(render_markdown(rep))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
