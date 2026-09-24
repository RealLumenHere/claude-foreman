#!/usr/bin/env python3
"""wq#2428 — audit every systemd timer for a scheduled path into the Anthropic
(Max) quota that no budget gate stands in front of.

wq#2426 made the dispatcher's `cloud_ceiling` budget-aware, but that ceiling only
governs the wq dispatch path. A systemd timer is not on that path: it fires on its
own clock, calls whatever its ExecStart calls, and nothing reads the weekly pool
first. On 2026-09-21 the orchestrator's 95% brake stopped five timers by name
because nobody could say which of them actually spent Anthropic tokens.

HOUSE RULE (this ticket): no scheduled service defaults to Max. A timer either
  (a) never reaches Anthropic at all, or
  (b) pins itself at the LiteLLM gateway — `ANTHROPIC_BASE_URL=http://localhost:4000`
      plus a local rung (harness/README.md §"Local rung"),
and anything else is a finding.

Reads only through `systemctl show` / `systemctl list-unit-files`, never /etc
directly, so it runs unprivileged (and inside a worker sandbox). Script bodies named
by ExecStart are opened and scanned for the same markers; a script that cannot be
read is reported `unknown`, never assumed clean — an unknown state is not evidence
of a good one.

Scope: depth 1. It reads the script ExecStart names, not what that script shells out
to, and CLI subcommands that run no inference (`claude auth status`, `--version`,
`mcp`) are not spend and are not reported.

    timer_spend_audit.py                      # report; exit 1 on any `cloud` finding
    timer_spend_audit.py --json
    timer_spend_audit.py --strict             # exit 1 on `unknown` too
    timer_spend_audit.py --roots /home/lumen/bin
                                              # only scan scripts under these roots;
                                              # everything else stays `unknown`
    timer_spend_audit.py --alert              # aq + ntfy on findings (edge-triggered)
"""
import argparse
import json
import os
import re
import subprocess
import sys

AQ = os.environ.get("TSA_AQ", "/home/lumen/bin/aq")
NTFY = os.environ.get("TSA_NTFY", "/home/lumen/bin/ntfy-send")
SOURCE = "timer-spend-guard"
SYSTEMCTL = os.environ.get("TSA_SYSTEMCTL", "systemctl")

# Markers that mean "this reaches Anthropic", in two tables because the two places
# they are read from carry different weight. In ExecStart argv and Environment= every
# token is something systemd will really execute or export, so a bare model id there
# is a live model selection. Inside a script body the same string is as likely to be
# documentation, so only invocation SHAPES count (see markers_in_script).
EXEC_MARKERS = (
    ("claude-model-id", re.compile(r"claude-(?:opus|sonnet|haiku|fable)")),
    ("anthropic-key", re.compile(r"ANTHROPIC_(?:API_KEY|AUTH_TOKEN)")),
    ("anthropic-api", re.compile(r"api\.anthropic\.com")),
)

SCRIPT_MARKERS = (
    ("claude-cli", re.compile(r"(?:^|[\s\"'(\[=])(?:[\w./-]*/)?claude\s+"
                              r"(?:-p\b|--print\b|--model\b|--output-format\b)")),
    ("claude-cli", re.compile(r"[\"']claude[\"']\s*[,\]]")),
    ("anthropic-sdk", re.compile(r"\b(?:import\s+anthropic|from\s+anthropic\b"
                                 r"|anthropic\.Anthropic\()")),
    ("anthropic-api", re.compile(r"api\.anthropic\.com")),
    ("anthropic-key", re.compile(r"ANTHROPIC_(?:API_KEY|AUTH_TOKEN)")),
)

# The house docstring idiom names a command in backticks (`claude --model ...`).
# Three of the first four findings this tool produced were exactly that — prose in
# wq's own comments — so backticked spans and whole-line comments are cut first.
_BACKTICKED = re.compile(r"`[^`]*`")

# Subcommands of the CLI that run no inference and so bill nothing. The fourth
# phantom finding was dispatch_recover.py's `claude auth status` health probe: a real
# invocation from a real timer that cannot cost a token. Flagging it would train the
# reader to ignore this guard.
_NON_BILLING = re.compile(r"claude[\"']?[\s,]+[\"']?"
                          r"(?:auth|mcp|doctor|config|update|install|migrate-installer"
                          r"|setup-token|--version|--help|-v\b|-h\b)")

# Interpreters: their own path never identifies what a unit calls, so the scanner
# walks past them to the first argv element that is an actual file on disk.
# Model selection, read off the same de-prosed lines as the markers. Two shapes are
# enough in practice: an explicit `--model X` / `model=X` / `"model": "X"` parameter,
# and a bare vendor id sitting in the source. Anything else is left to the reader.
MODEL_RXS = (
    # The separator class covers both the shell form (`--model opus-5`, `--model=x`)
    # and the argv-list form (`"--model", "opus-5"`) — a `{` is deliberately absent so
    # an f-string placeholder reports nothing rather than a bogus id.
    re.compile(r"--model[=\s,\"']+([\w.:/-]+)"),
    re.compile(r"[\"']?\bmodel[\"']?\s*[:=]\s*[\"']([\w.:/-]+)[\"']"),
    re.compile(r"\b(claude-(?:opus|sonnet|haiku|fable)[\w.-]*)"),
)

INTERPRETERS = ("python3", "python", "bash", "sh", "env", "node", "perl", "uv", "nice")

MAX_SCRIPT_BYTES = 2 * 1024 * 1024

SHOW_PROPS = ("Id", "UnitFileState", "ActiveState", "ExecStart", "ExecStartPre",
              "ExecStartPost", "Environment", "EnvironmentFiles")


def sh(args, timeout=30):
    """stdout of `args`, or "" on any failure — this tool never fails a run because
    one systemctl call misbehaved; a missing block becomes `unknown` downstream."""
    try:
        r = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
        return r.stdout
    except Exception:
        return ""


# ── parsing ─────────────────────────────────────────────────────────────────────

def parse_show_blocks(text):
    """`systemctl show a b c` output -> [{prop: [values]}], one dict per unit.

    systemd omits a property line entirely when it is empty, so blocks are split on
    blank lines and keyed by the Id line that is always present."""
    blocks, cur = [], {}
    for line in text.splitlines():
        if not line.strip():
            if cur:
                blocks.append(cur)
                cur = {}
            continue
        k, _, v = line.partition("=")
        cur.setdefault(k, []).append(v)
    if cur:
        blocks.append(cur)
    return blocks


_ARGV_RE = re.compile(r"argv\[\]=(.*?)\s;")


def exec_argv(block):
    """Every ExecStart* command line in the block, as a list of argv token lists.

    systemd renders these as `{ path=X ; argv[]=X a b ; ignore_errors=no ; ... }`."""
    out = []
    for prop in ("ExecStart", "ExecStartPre", "ExecStartPost"):
        for raw in block.get(prop, []):
            m = _ARGV_RE.search(raw)
            if m:
                out.append(m.group(1).split())
    return out


def env_file_path(value):
    """systemd renders EnvironmentFiles as `/path (ignore_errors=no)`; keep the path."""
    return value.split(" (ignore_errors=", 1)[0].strip()


def gateway_pin(env_values):
    """'local' | 'cloud' | None — what the unit's Environment= says about the API
    endpoint. A base URL that is not anthropic.com is the gateway (or some other
    local proxy); either way the Max pool is not what gets billed."""
    for entry in env_values:
        for kv in entry.split():
            if kv.startswith("ANTHROPIC_BASE_URL="):
                url = kv.split("=", 1)[1]
                return "cloud" if "anthropic.com" in url else "local"
    return None


def markers_in_exec(tokens):
    """Markers for one ExecStart argv (or a list of Environment= entries).

    `claude` as an argv basename is checked structurally rather than by regex — that
    is the one place the plain name unambiguously means the CLI."""
    line = " ".join(tokens)
    hits = {name for name, rx in EXEC_MARKERS if rx.search(line)}
    if any(os.path.basename(t) == "claude" for t in tokens) \
            and not _NON_BILLING.search(line):
        hits.add("claude-cli")
    return sorted(hits)


def code_lines(text):
    """(lineno, line) for a script body with prose cut: whole-line comments dropped,
    backticked spans blanked. Both the marker scan and the model scan read through
    this, so a docstring can never contribute to either."""
    for lineno, line in enumerate(text.splitlines(), 1):
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        yield lineno, _BACKTICKED.sub(" ", stripped)


def markers_in_script(text):
    """[(lineno, marker)] for a script body, prose excluded."""
    out = []
    for lineno, stripped in code_lines(text):
        for name, rx in SCRIPT_MARKERS:
            if rx.search(stripped):
                if name == "claude-cli" and _NON_BILLING.search(stripped):
                    continue
                out.append((lineno, name))
                break
    return out


def models_in_script(text):
    """Sorted model ids a script body selects.

    A `cloud` verdict says a unit reaches Anthropic; it does not say what it asks for,
    and the repoint differs by answer — a unit already naming a gateway rung needs only
    ANTHROPIC_BASE_URL, one naming opus needs its argv changed too. The model is chosen
    in the script's own argv (the CLI takes `--model`, not an env var), so no drop-in
    can override it and the operator has to see it."""
    found = set()
    for _, stripped in code_lines(text):
        for rx in MODEL_RXS:
            found.update(m for m in rx.findall(stripped) if m)
    return sorted(found)


# ── script scanning ─────────────────────────────────────────────────────────────

def script_target(argv):
    """The file an argv actually runs, skipping interpreters, or None."""
    for tok in argv:
        if tok.startswith("-"):
            continue
        if os.path.basename(tok) in INTERPRETERS:
            continue
        return tok
    return None


def _scanned(markers, models, status):
    return {"markers": markers, "models": models, "status": status}


def scan_script(path, roots=None):
    """{"markers": [(lineno, marker)], "models": [str], "status": str} for `path`.

    status is 'scanned' | 'unreadable' | 'out-of-roots'. `roots` restricts scanning
    to a prefix list — a sandboxed worker passes its own allowed dirs so that paths
    it cannot open are reported honestly instead of being probed."""
    if roots and not any(os.path.abspath(path).startswith(r) for r in roots):
        return _scanned([], [], "out-of-roots")
    try:
        if os.path.getsize(path) > MAX_SCRIPT_BYTES:
            return _scanned([], [], "unreadable")
        with open(path, "r", errors="replace") as f:
            text = f.read()
        return _scanned(markers_in_script(text), models_in_script(text), "scanned")
    except Exception:
        return _scanned([], [], "unreadable")


# ── classification ──────────────────────────────────────────────────────────────

def classify(unit):
    """(verdict, reasons) for one unit dict.

    Pure over:
      id, argv[[str]], environment[str], environment_files[str],
      scripts{path: {"markers": [str], "status": str}}

    cloud   — reaches Anthropic with no gateway pin: bills the Max pool on a timer.
    pinned  — reaches Anthropic-shaped API but Environment= points off anthropic.com.
    clean   — nothing reaching Anthropic, and every target was actually read.
    unknown — a target could not be read (or env comes from an unreadable file), so
              the absence of markers proves nothing.
    """
    reasons = []
    pin = gateway_pin(unit.get("environment", []))
    if pin:
        reasons.append(f"ANTHROPIC_BASE_URL pinned {pin}")

    found = set()
    for argv in unit.get("argv", []):
        hits = markers_in_exec(argv)
        if hits:
            found.update(hits)
            reasons.append(f"ExecStart: {','.join(hits)}")
    env = unit.get("environment", [])
    hits = markers_in_exec(env) if env else []
    if hits:
        found.update(hits)
        reasons.append(f"Environment: {','.join(hits)}")

    unread = []
    for path, info in sorted(unit.get("scripts", {}).items()):
        if info["status"] == "scanned":
            for lineno, name in info["markers"]:
                found.add(name)
                reasons.append(f"{path}:{lineno} {name}")
            if info["markers"] and info.get("models"):
                reasons.append(f"{path} selects model: "
                               + ", ".join(info["models"]))
        else:
            unread.append(f"{path} ({info['status']})")
    for ef in unit.get("environment_files", []):
        unread.append(f"{ef} (EnvironmentFile)")

    if found:
        if pin == "local":
            return "pinned", reasons
        return "cloud", reasons
    if unread:
        return "unknown", reasons + ["not read: " + "; ".join(unread)]
    return "clean", reasons


# ── collection ──────────────────────────────────────────────────────────────────

def timer_units():
    """[(timer, unit_file_state)] for every installed timer, enabled or not.

    list-unit-files rather than list-timers on purpose: a timer the orchestrator
    stopped is exactly the one this audit exists to explain, and a stopped timer is
    absent from list-timers.

    Uninstantiated templates (`foo@.timer`) are skipped: one never fires, and
    `systemctl show` ABORTS on the first template in its argument list, dropping
    every unit after it from the batch."""
    out = []
    for line in sh([SYSTEMCTL, "list-unit-files", "--type=timer",
                    "--no-legend", "--no-pager"]).splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[0].endswith(".timer") and "@." not in parts[0]:
            out.append((parts[0], parts[1]))
    return out


def show(names, props=SHOW_PROPS, chunk=40):
    """{Id: {prop: [values]}} for `names`, batched to keep the systemctl calls few.

    A batch that comes back short is re-queried one unit at a time: `systemctl show`
    gives up on the whole invocation when any single unit in it is unresolvable, so
    a silent short read would otherwise turn well-behaved units into `unknown`."""
    blocks = {}
    args = []
    for p in props:
        args += ["-p", p]

    def query(batch):
        for b in parse_show_blocks(sh([SYSTEMCTL, "show"] + list(batch) + args +
                                      ["--no-pager"])):
            ids = b.get("Id")
            if ids:
                blocks[ids[0]] = b

    for i in range(0, len(names), chunk):
        batch = list(names[i:i + chunk])
        query(batch)
        for missing in [n for n in batch if n not in blocks]:
            query([missing])
    return blocks


def collect(roots=None):
    """[unit dict] for the service each installed timer activates."""
    timers = timer_units()
    tblocks = show([t for t, _ in timers], props=("Id", "Unit", "UnitFileState"))
    activates = {}
    for timer, state in timers:
        b = tblocks.get(timer, {})
        svc = (b.get("Unit") or [timer[:-len(".timer")] + ".service"])[0]
        activates[svc] = (timer, state)

    sblocks = show(list(activates))
    units = []
    for svc, (timer, tstate) in sorted(activates.items()):
        b = sblocks.get(svc)
        if b is None:
            units.append({"id": svc, "timer": timer, "timer_state": tstate,
                          "argv": [], "environment": [], "environment_files": [],
                          "scripts": {svc: {"markers": [], "status": "unreadable"}}})
            continue
        argv = exec_argv(b)
        scripts = {}
        for a in argv:
            tgt = script_target(a)
            if tgt and tgt not in scripts:
                scripts[tgt] = scan_script(tgt, roots)
        units.append({
            "id": svc,
            "timer": timer,
            "timer_state": tstate,
            "active": (b.get("ActiveState") or [""])[0],
            "argv": argv,
            "environment": b.get("Environment", []),
            "environment_files": [env_file_path(e)
                                  for e in b.get("EnvironmentFiles", [])],
            "scripts": scripts,
        })
    return units


# ── reporting ───────────────────────────────────────────────────────────────────

ORDER = ("cloud", "unknown", "pinned", "clean")


def audit(roots=None):
    findings = []
    for unit in collect(roots):
        verdict, reasons = classify(unit)
        findings.append({"unit": unit["id"], "timer": unit["timer"],
                         "timer_state": unit["timer_state"], "verdict": verdict,
                         "reasons": reasons})
    findings.sort(key=lambda f: (ORDER.index(f["verdict"]), f["unit"]))
    return findings


def report(findings, verbose=False):
    counts = {v: 0 for v in ORDER}
    for f in findings:
        counts[f["verdict"]] += 1
    lines = []
    for f in findings:
        if f["verdict"] in ("cloud", "unknown") or verbose:
            lines.append(f"  {f['verdict'].upper():8} {f['unit']} [{f['timer_state']}]")
            for r in f["reasons"]:
                lines.append(f"           {r}")
    head = (f"{len(findings)} timers — " +
            " ".join(f"{v}={counts[v]}" for v in ORDER))
    return head, lines


def alert(findings):
    """Edge-triggered aq row + ntfy for `cloud` findings. Best-effort, never raises."""
    cloud = [f for f in findings if f["verdict"] == "cloud"]
    if not cloud:
        return
    title = f"{len(cloud)} scheduled unit(s) reach Anthropic with no budget gate"
    body = "\n".join(f"{f['unit']}: {'; '.join(f['reasons'])}" for f in cloud)
    try:
        listing = subprocess.run([AQ, "list", "all"], capture_output=True,
                                 text=True, timeout=10).stdout
        if SOURCE not in listing or "open" not in listing.lower():
            subprocess.run([AQ, "add", SOURCE, "systemd-timers", title, body],
                           check=False, timeout=20)
    except Exception:
        pass
    try:
        subprocess.run([NTFY, "-t", "lumen-alerts", "-p", "high", "-T", title,
                        body[:400]], check=False, timeout=20)
    except Exception:
        pass


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--strict", action="store_true",
                    help="exit 1 on `unknown` as well as `cloud`")
    ap.add_argument("--verbose", action="store_true", help="list clean/pinned too")
    ap.add_argument("--alert", action="store_true")
    ap.add_argument("--roots", default="",
                    help="comma-separated path prefixes the script scanner may read")
    a = ap.parse_args(argv)

    roots = [os.path.abspath(r) for r in a.roots.split(",") if r.strip()]
    findings = audit(roots or None)
    if a.alert:
        alert(findings)
    if a.json:
        print(json.dumps(findings, indent=2))
    else:
        head, lines = report(findings, a.verbose)
        print(head)
        print("\n".join(lines))
    bad = {"cloud", "unknown"} if a.strict else {"cloud"}
    return 1 if any(f["verdict"] in bad for f in findings) else 0


if __name__ == "__main__":
    sys.exit(main())
