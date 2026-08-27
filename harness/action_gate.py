#!/usr/bin/env python3
"""action_gate.py — TIERED_INTELLIGENCE_DESIGN.md Layers 1-3 (wq#985).

Layer 1: RED/AMBER/GREEN command interception as a PreToolUse hook, second wall
behind guard.sh (guard.sh = fixed house denials; this gate = destructive-class
classification + per-project rules + AMBER pre-snapshots). RED never executes:
exit 2 with "ACTION BLOCKED", handoff doc written for a reviewing agent (Layer 4,
separate ticket, consumes the flag file). AMBER snapshots the target BEFORE
allowing. GREEN passes.

Layer 2: LoopTracker — imported by fworker.run_child(); detects 3x same-class
failure / 3x same error / 3x same-file re-read without progress and tells the
supervisor to stop the session.

Layer 3: compile_rules() — fworker calls it at ticket start; reads
.fworker-rules.yaml from the target project dirs, merges ADDITIVELY over the
builtin lists (project rules can only add RED/AMBER, never weaken), writes a
compiled JSON into the fworker-owned run dir so mid-session edits to the
worktree copy of the rules file have no effect.

Fail-closed like guard.sh: any internal error in hook mode = exit 2. A failed
AMBER snapshot BLOCKS (executing without the snapshot violates the invariant —
the Miko class is exactly "destructive action with no pre-image on disk").
"""
import json
import os
import re
import shutil
import subprocess
import sys
import time

GATE_RULES_ENV = "FOREMAN_GATE_RULES"
SNAPDIR_ENV = "FOREMAN_GATE_SNAPDIR"
HANDOFF_ENV = "FOREMAN_GATE_HANDOFF_DIR"
EVENTS_DIR = os.environ.get("FOREMAN_EVENTS_DIR", "/home/lumen/foreman/events")
WORKER_ID = os.environ.get("FOREMAN_WORKER", "unknown")
RULES_BASENAME = ".fworker-rules.yaml"
SNAP_FILE_CAP = 64 * 1024 * 1024      # per-file copy cap
SNAP_DB_CAP = 200 * 1024 * 1024       # sqlite db copy cap

RED_MSG = ("ACTION BLOCKED: destructive operation requires review. "
           "Your context has been preserved for a reviewing agent.")

# wq#991 — research-first phase gate. A hardware_class / research-phase-gated
# project blocks ALL device commands (read-only included) until a valid prior-art
# research marker exists. Self-service: the agent clears it by running
# research_phase.py, not by escalation.
PROJECTS_ROOT = os.environ.get("FOREMAN_PROJECTS_ROOT", "/home/lumen/projects")
RESEARCH_MARKER = ".research-complete.json"   # lives in projects/<name>/research/
RESEARCH_SCHEMA = "research-complete/v1"
MIN_FINDINGS_BYTES = 200                       # a bare touch can't satisfy this
RESEARCH_PHASE_TOOL = "/home/lumen/foreman/harness/research_phase.py"

# --------------------------------------------------------------------------
# Layer 1 — builtin classification lists. Regexes run against BOTH the raw
# command and a whitespace-collapsed copy (same evasion posture as guard.sh).
# --------------------------------------------------------------------------

BUILTIN_RED = [
    (r'\bdd\b[^|;&]*\bof=', "dd with of= (block-level destructive)"),
    (r'\bmkfs(\.[a-z0-9]+)?\b', "mkfs (filesystem create destroys contents)"),
    (r'\bwipefs\b', "wipefs"),
    (r'\bblkdiscard\b', "blkdiscard"),
    (r'\bnvme\s+format\b', "nvme format"),
    (r'\bhdparm\b[^|;&]*--security-erase', "hdparm secure erase"),
    (r'\bbadblocks\b[^|;&]*\s-[a-z]*w', "badblocks destructive write test"),
    (r'\b(fdisk|gdisk|sgdisk|sfdisk|parted)\b(?![^|;&]*(\s-l\b|--list\b|\bprint\b))',
     "partition tool without read-only flag"),
    (r'\brm\b[^|;&]*\s-[a-z]*r[a-z]*\b[^|;&]*\s/(boot|etc|usr|var|bin|sbin|lib|opt|srv|data|system)\b',
     "recursive rm on system path"),
    # rm -rf handled by _red_rm_rf() (needs path inspection, root-scoped only)
    (r'factory[\s_-]*reset', "factory reset"),
    (r'\bwipe[\s_]+data\b|--wipe_data\b', "wipe data (Android destructive)"),
    (r'\bfastboot\b\s+(-w\b|erase\b|format\b)', "fastboot erase/format/-w"),
    (r'\brecovery\b[^|;&]*--wipe', "recovery wipe"),
    (r'(>|>>)\s*/dev/(sd|nvme|mmcblk|block/)', "shell redirect onto block device"),
    (r'\bof=/dev/(sd|nvme|mmcblk|block/)', "dd/tool write onto block device"),
    (r'\b(cp|cat|tee|pv)\b[^|;&]*\s/dev/(sd[a-z]|nvme\d|mmcblk\d|block/)',
     "raw write onto block device"),
    (r'\bdrop\s+(table|database|schema)\b', "SQL DROP"),
    (r'\btruncate\s+table\b|\bsqlite3\b[^|;&]*\btruncate\b|\bpsql\b[^|;&]*\btruncate\b',
     "SQL TRUNCATE"),
    (r'\bshred\b', "shred (key/credential destruction)"),
    (r'\brm\b[^|;&]*(\.ssh\b|\.secrets\b|\.credentials|id_ed25519|id_rsa|\.token\b)',
     "credential/key material deletion"),
]

# RED-conditional "flash" class: blocked UNLESS the compiled rules declare a
# verified backup path that exists non-empty on LOCAL disk (design: "flash
# without a verified backup on disk"). A backup on another node does not count —
# the gate cannot verify it, so the action escalates instead.
BUILTIN_FLASH = [
    (r'\bfastboot\b\s+flash\b', "fastboot flash"),
    (r'\bheimdall\b\s+flash\b', "heimdall flash"),
    (r'\bedl\b\s+w', "edl write"),
    (r'\badb\b[^|;&]*\bpush\b[^|;&]*\s/(system|vendor|boot)\b', "adb push to system partition"),
    (r'\bflashrom\b[^|;&]*\s-w\b', "flashrom write"),
]

# AMBER: execute but snapshot first. (kind tags drive snapshot())
BUILTIN_AMBER = [
    (r'\bgit\b[^|;&]*\bpush\b[^|;&]*(--force\b|--force-with-lease\b|\s-f\b)', "git push --force", "git"),
    (r'\bgit\b[^|;&]*\breset\b[^|;&]*--hard\b', "git reset --hard", "git"),
    (r'\bgit\b[^|;&]*\bclean\b[^|;&]*\s-[a-z]*f', "git clean -f", "git"),
    (r'\bsed\b[^|;&]*\s-i\b', "in-place sed", "files"),
    (r'\btee\b(?![^|;&]*\s-a\b)[^|;&]*\s(/[^\s;|&]+)', "tee overwrite", "files"),
    (r'(^|[^>])>\s*(/[^\s;|&]+)', "shell overwrite redirect", "files"),
    (r'\bsystemctl\b\s+(stop|disable|mask)\b', "service stop/disable", "unit"),
    (r'\bdocker\b\s+(rm|rmi|stop|kill)\b', "container removal/stop", "none"),
    (r'\bsqlite3\b[^|;&]*\.(db|sqlite3?)\b[^|;&]*\b(insert|update|delete|alter)\b', "sqlite mutation", "sqlite"),
    (r'\b(apt|apt-get|dnf|yum)\b[^|;&]*\b(install|remove|purge)\b', "system package change", "pkg-dpkg"),
    (r'\bpip3?\b[^|;&]*\b(install|uninstall)\b', "pip package change", "pkg-pip"),
    (r'\bnpm\b[^|;&]*\b(install|uninstall|remove)\b', "npm package change", "none"),
    (r'\b(cp|mv)\b\s+(?:-[a-z]+\s+)*[^\s;|&]+\s+(/[^\s;|&]+)', "cp/mv onto path", "files"),
]

# wq#991 DEVICE — commands that DRIVE the physical device under RE (read-only
# contact included: `adb shell getprop` is first contact and still gated). Host
# OBSERVATION (lsusb, dmesg, ls /dev) is deliberately NOT here — that is how you
# identify the device; gating it is pointless friction. Per-project
# `device_patterns` extends this additively.
BUILTIN_DEVICE = [
    (r'\badb\b', "adb (Android device bridge)"),
    (r'\bfastboot\b', "fastboot"),
    (r'\bheimdall\b', "heimdall (Samsung flash)"),
    (r'\b(edl|qdl|firehose)\b', "Qualcomm EDL/QDL/firehose"),
    (r'\bflashrom\b', "flashrom (SPI/BIOS)"),
    (r'\b(rkdeveloptool|upgrade_tool)\b', "Rockchip flash tool"),
    (r'\b(mtk|mtkclient|sp_?flash(tool)?)\b', "MediaTek flash tool"),
    (r'\buuu\b', "NXP uuu (mfgtools)"),
    (r'\besptool(\.py)?\b', "esptool (ESP flash)"),
    (r'\bopenocd\b', "OpenOCD (JTAG)"),
    (r'\bavrdude\b', "avrdude"),
    (r'\bdfu-util\b', "dfu-util (USB DFU)"),
    (r'\bnvflash\b|\btegrarcm\b|\bfusee\b', "NVIDIA Tegra flash"),
    (r'\b(screen|minicom|picocom|tio|cu)\b[^|;&]*\s(/dev/tty|/dev/serial)', "serial console on tty"),
    (r'(^|[\s;|&])(cat|echo|printf|dd)\b[^|;&]*[<>]\s*/dev/tty', "raw serial tty read/write"),
]

_MUTATION_MARKERS = re.compile(r'(\brm\b|\bmv\b|\bsed\b[^|;&]*-i|\btee\b|>|\btruncate\b|\bcp\b)')
# wq#991 — write-intent tokens for detecting an attempt to FORGE the research
# marker (covers python -c open(...,'w'), redirects, touch, install, etc).
_MARKER_WRITE_INTENT = re.compile(
    r'(>|>>|\btee\b|\btouch\b|\bcp\b|\bmv\b|\bdd\b|\binstall\b|\btruncate\b|'
    r"\bopen\s*\(|\.write\s*\(|\bsed\b[^|;&]*-i|\bprintf\b|\becho\b)")


def _norm(cmd):
    return re.sub(r'\s+', ' ', cmd.replace('\n', ' ').replace('\t', ' '))


def _red_rm_rf(target):
    """rm -rf pointing at / or at protected roots — separate from the regex table
    because it needs path inspection, not just pattern presence."""
    if not re.search(r'\brm\b[^|;&]*\s(-[a-z]*r[a-z]*f[a-z]*|-[a-z]*f[a-z]*r[a-z]*|--recursive)', target):
        return None
    for path in re.findall(r'\s(/[^\s;|&"\']*)', target):
        if path == "/" or re.match(r'^/(boot|etc|usr|var|bin|sbin|lib|opt|srv|data|system|dev)(/|$)', path):
            return f"rm -rf on protected root {path}"
        if re.match(r'^/home/[^/]+/\.(ssh|secrets)(/|$)', path):
            return f"rm -rf on credential dir {path}"
    return None


def _local_backup_ok(rules):
    for p in rules.get("backups", []):
        try:
            if os.path.isfile(p) and os.path.getsize(p) > 0:
                return p
            if os.path.isdir(p) and os.listdir(p):
                return p
        except OSError:
            continue
    return None


# --------------------------------------------------------------------------
# Layer 3 — project rules compiler
# --------------------------------------------------------------------------

def _load_yaml(path):
    import yaml
    with open(path) as f:
        return yaml.safe_load(f) or {}


def compile_rules(project_dirs, out_path):
    """Read .fworker-rules.yaml from each candidate dir (repo root, worktree
    root), merge additively, write compiled JSON to out_path (fworker-owned,
    outside the worktree). Returns out_path, or None if no rules file existed
    (hook then runs builtins-only). Accepts the design doc's schema: `rules`
    may mix prose strings and {backup_required_before: [...]} /
    {phase_gates: [...]} dicts; structured red_patterns/amber_patterns/backups
    top-level keys are also honored."""
    compiled = {"project": None, "hardware_class": None, "prose_rules": [],
                "context": [], "red_patterns": [], "amber_patterns": [],
                "device_patterns": [], "phase_gates": [], "project_root": None,
                "backup_required_before": [], "backups": [], "source_files": []}
    seen = set()
    for d in project_dirs:
        if not d:
            continue
        p = os.path.join(d, RULES_BASENAME)
        rp = os.path.realpath(p)
        if rp in seen or not os.path.isfile(p):
            continue
        seen.add(rp)
        data = _load_yaml(p)
        compiled["source_files"].append(p)
        compiled["project"] = compiled["project"] or data.get("project")
        compiled["hardware_class"] = compiled["hardware_class"] or data.get("hardware_class")
        # wq#991 — canonical project dir (first rules file wins); the research
        # marker lives under projects/<project>/research/, resolved at check time.
        compiled["project_root"] = compiled["project_root"] or os.path.dirname(os.path.realpath(p))
        for item in data.get("rules", []) or []:
            if isinstance(item, str):
                compiled["prose_rules"].append(item)
            elif isinstance(item, dict):
                for k, v in item.items():
                    if k == "backup_required_before" and isinstance(v, list):
                        compiled["backup_required_before"] += [str(t) for t in v]
                    elif k == "phase_gates":
                        for g in (v or []):
                            if str(g) not in compiled["phase_gates"]:
                                compiled["phase_gates"].append(str(g))
                    else:
                        compiled["prose_rules"].append(f"{k}: {v}")
        # top-level phase_gates: (additive with any found under rules:)
        for g in (data.get("phase_gates") or []):
            if str(g) not in compiled["phase_gates"]:
                compiled["phase_gates"].append(str(g))
        compiled["context"] += [str(c) for c in (data.get("context") or [])]
        for key in ("red_patterns", "amber_patterns", "device_patterns"):
            for pat in data.get(key) or []:
                if isinstance(pat, str):
                    entry = {"pattern": pat, "reason": f"project rule ({os.path.basename(d)})"}
                else:
                    entry = {"pattern": pat.get("pattern", ""), "reason": pat.get("reason", "project rule")}
                if entry["pattern"]:
                    re.compile(entry["pattern"])  # fail loudly at compile time, not hook time
                    compiled[key].append(entry)
        compiled["backups"] += [str(b) for b in (data.get("backups") or [])]
    if not compiled["source_files"]:
        return None
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w") as f:
        json.dump(compiled, f, indent=1)
    return out_path


def load_compiled_rules():
    p = os.environ.get(GATE_RULES_ENV)
    if not p or not os.path.isfile(p):
        return {}
    with open(p) as f:
        return json.load(f)


# --------------------------------------------------------------------------
# Layer 1 — classification
# --------------------------------------------------------------------------

def classify_bash(cmd, rules):
    """Returns (verdict, reason, snap). verdict in RED|AMBER|GREEN.
    snap = (kind, cmd) for AMBER. RED checked first; project rules additive."""
    targets = (cmd, _norm(cmd))
    for target in targets:
        for pat, reason in BUILTIN_RED:
            if reason is None:
                continue
            if re.search(pat, target, re.IGNORECASE):
                return "RED", reason, None
        r = _red_rm_rf(target)
        if r:
            return "RED", r, None
        # gate-tamper: any mutation touching a rules file is RED
        if RULES_BASENAME.rsplit(".", 1)[0] in target and _MUTATION_MARKERS.search(target):
            return "RED", "mutation of .fworker-rules.yaml (gate tamper)", None
        # wq#991 — forging the research marker is RED (the only sanctioned writer
        # is research_phase.py, which does the real searches). Reads (cat/ls) pass.
        if RESEARCH_MARKER in target and _MARKER_WRITE_INTENT.search(target):
            return "RED", f"write to {RESEARCH_MARKER} (research-gate tamper)", None
        for entry in rules.get("red_patterns", []):
            if re.search(entry["pattern"], target, re.IGNORECASE):
                return "RED", entry["reason"], None
        # flash class: RED unless a declared LOCAL backup verifies
        for pat, reason in BUILTIN_FLASH:
            if re.search(pat, target, re.IGNORECASE):
                b = _local_backup_ok(rules)
                if b:
                    return "AMBER", f"{reason} (verified local backup: {b})", ("note", cmd)
                return "RED", f"{reason} without a verified local backup on disk", None
        for tok in rules.get("backup_required_before", []):
            if re.search(r'(^|[\s;|&/])' + re.escape(tok), target, re.IGNORECASE):
                b = _local_backup_ok(rules)
                if b:
                    return "AMBER", f"'{tok}' with verified local backup {b}", ("note", cmd)
                return "RED", f"project rule: '{tok}' requires a verified backup on disk (none found locally)", None
    for target in targets:
        for pat, reason, kind in BUILTIN_AMBER:
            if re.search(pat, target, re.IGNORECASE):
                return "AMBER", reason, (kind, cmd)
        for entry in rules.get("amber_patterns", []):
            if re.search(entry["pattern"], target, re.IGNORECASE):
                return "AMBER", entry["reason"], ("git", cmd)
    return "GREEN", "", None


def classify_file_tool(tool_name, file_path, rules):
    if not file_path:
        return "GREEN", "", None
    base = os.path.basename(file_path)
    if base == RULES_BASENAME or base.startswith(".fworker-rules"):
        return "RED", "write/edit of .fworker-rules.yaml (gate tamper)", None
    if base == RESEARCH_MARKER:
        return "RED", f"write/edit of {RESEARCH_MARKER} (research-gate tamper)", None
    if re.match(r'^/(etc|boot|usr|dev)(/|$)', os.path.realpath(file_path)):
        return "RED", f"{tool_name} into system path {file_path}", None
    for entry in rules.get("red_patterns", []):
        if re.search(entry["pattern"], file_path, re.IGNORECASE):
            return "RED", entry["reason"], None
    if tool_name == "Edit" and os.path.isfile(file_path):
        return "AMBER", "file edit (pre-image snapshot)", ("path", file_path)
    if tool_name == "Write" and os.path.isfile(file_path):
        return "AMBER", "file overwrite (pre-image snapshot)", ("path", file_path)
    return "GREEN", "", None


# --------------------------------------------------------------------------
# AMBER snapshots — pre-image BEFORE execution; failure BLOCKS (fail-closed)
# --------------------------------------------------------------------------

def _snapdir():
    d = os.environ.get(SNAPDIR_ENV) or os.path.join("/tmp", f"gate-snapshots-{WORKER_ID}")
    os.makedirs(d, exist_ok=True)
    return d


def _stamp():
    return time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())


def _sanitize(p):
    return re.sub(r'[^A-Za-z0-9._-]', '_', p.strip('/'))[:120]


def _run(cmd, cwd=None, timeout=15):
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout)


def _extract_file_targets(cmd):
    """Best-effort absolute-path targets of an in-place mutation command."""
    out = []
    for m in re.finditer(r'(?:^|\s|>)(/[^\s;|&"\'>]+)', cmd):
        p = m.group(1)
        if os.path.isfile(p):
            out.append(p)
    return out


def snapshot(kind, payload, cwd):
    """Take the pre-image. Returns list of artifact paths. Raises on failure —
    caller blocks the command (invariant: AMBER never executes unsnapshotted)."""
    d = _snapdir()
    ts = _stamp()
    arts = []
    if kind == "path":
        p = payload
        if os.path.getsize(p) <= SNAP_FILE_CAP:
            dst = os.path.join(d, f"{ts}-{_sanitize(p)}")
            shutil.copy2(p, dst)
            arts.append(dst)
        else:
            raise RuntimeError(f"pre-image {p} exceeds {SNAP_FILE_CAP} cap")
    elif kind == "files":
        for p in _extract_file_targets(payload)[:8]:
            if os.path.getsize(p) <= SNAP_FILE_CAP:
                dst = os.path.join(d, f"{ts}-{_sanitize(p)}")
                shutil.copy2(p, dst)
                arts.append(dst)
        if not arts:
            kind = "git"  # nothing extractable — fall through to tree-state record
    if kind == "git":
        dst = os.path.join(d, f"{ts}-git-state.txt")
        with open(dst, "w") as f:
            for args in (["rev-parse", "HEAD"], ["status", "--porcelain"],
                         ["diff"], ["diff", "--staged"], ["stash", "create"]):
                r = _run(["git", *args], cwd=cwd)
                f.write(f"$ git {' '.join(args)}\n{r.stdout}{r.stderr}\n")
        arts.append(dst)
    elif kind == "sqlite":
        m = re.search(r'(/[^\s;|&"\']+\.(?:db|sqlite3?))\b', payload)
        if m and os.path.isfile(m.group(1)):
            p = m.group(1)
            if os.path.getsize(p) > SNAP_DB_CAP:
                raise RuntimeError(f"db {p} exceeds snapshot cap")
            dst = os.path.join(d, f"{ts}-{_sanitize(p)}")
            shutil.copy2(p, dst)
            arts.append(dst)
    elif kind == "pkg-pip":
        r = _run(["pip3", "freeze"], timeout=30)
        dst = os.path.join(d, f"{ts}-pip-freeze.txt")
        open(dst, "w").write(r.stdout)
        arts.append(dst)
    elif kind == "pkg-dpkg":
        r = _run(["dpkg-query", "-W", "-f", "${Package} ${Version}\n"], timeout=30)
        dst = os.path.join(d, f"{ts}-dpkg-list.txt")
        open(dst, "w").write(r.stdout)
        arts.append(dst)
    elif kind == "unit":
        m = re.search(r'systemctl\s+(?:stop|disable|mask)\s+([A-Za-z0-9@._-]+)', payload)
        if m:
            r = _run(["systemctl", "show", m.group(1), "--no-pager"], timeout=15)
            dst = os.path.join(d, f"{ts}-unit-{_sanitize(m.group(1))}.txt")
            open(dst, "w").write(r.stdout)
            arts.append(dst)
    elif kind == "note":
        dst = os.path.join(d, f"{ts}-note.txt")
        open(dst, "w").write(payload)
        arts.append(dst)
    return arts


def _log_event(event, **kv):
    try:
        os.makedirs(EVENTS_DIR, exist_ok=True)
        rec = {"ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
               "worker": WORKER_ID, "event": event}
        rec.update(kv)
        with open(os.path.join(EVENTS_DIR, f"{WORKER_ID}.jsonl"), "a") as f:
            f.write(json.dumps(rec) + "\n")
    except OSError:
        pass  # logging is best-effort; never the reason a command lives or dies


def write_red_handoff(tool_name, detail, reason, rules):
    """Layer 1 on-RED: structured handoff for the (future L4) reviewing agent."""
    d = os.environ.get(HANDOFF_ENV) or EVENTS_DIR
    try:
        os.makedirs(d, exist_ok=True)
        ts = _stamp()
        path = os.path.join(d, f"red-block-{ts}.md")
        with open(path, "w") as f:
            f.write(f"# RED BLOCK — {ts}\n\nworker: {WORKER_ID}\ntool: {tool_name}\n"
                    f"reason: {reason}\n\n## Attempted action\n```\n{detail[:2000]}\n```\n")
            if rules.get("project"):
                f.write(f"\n## Project\n{rules['project']} "
                        f"(hardware_class={rules.get('hardware_class')})\n")
            if rules.get("prose_rules"):
                f.write("\n## Project rules\n" + "\n".join(f"- {r}" for r in rules["prose_rules"]) + "\n")
            if rules.get("context"):
                f.write("\n## Project context\n" + "\n".join(f"- {c}" for c in rules["context"]) + "\n")
        flag = os.path.join(d, "fable-review-requested.json")
        with open(flag, "w") as f:
            json.dump({"ts": ts, "worker": WORKER_ID, "tool": tool_name,
                       "reason": reason, "handoff": path}, f)
        return path
    except OSError:
        return None


# --------------------------------------------------------------------------
# Layer 2 — LoopTracker (used by fworker.run_child)
# --------------------------------------------------------------------------

class LoopTracker:
    """Feeds on the same stream-json frames RunState.observe() sees. Triggers
    when the agent is demonstrably retrying a failed approach without change:
      - the same command class fails N times with NO successful mutating tool
        (Edit/Write/Bash) in between (a TDD loop that edits between failed test
        runs never trips this — the edit resets the streak);
      - the SAME normalized error tail appears N times total (edits that change
        nothing about the error do not count as progress);
      - the same file is Read N times with no successful mutation in between.
    observe() returns a reason string when tripped, else None."""

    def __init__(self, threshold=None):
        self.n = threshold or int(os.environ.get("FOREMAN_LOOP_THRESHOLD", "3"))
        self.pending = {}          # tool_use_id -> (tool, class_sig, file_path)
        self.fail_counts = {}      # class_sig -> count (reset on successful mutation)
        self.error_counts = {}     # error_sig -> count (never reset)
        self.read_counts = {}      # file_path -> count (reset on successful mutation)
        self.recent = []           # last tool calls for the handoff
        self.tripped = None

    @staticmethod
    def _class_sig(tool, tool_input):
        if tool == "Bash":
            toks = [t for t in (tool_input.get("command") or "").split() if not t.startswith("-")]
            toks = [os.path.basename(t) for t in toks[:3]][:2]
            return "bash:" + " ".join(toks)
        return f"{tool}:{os.path.basename(tool_input.get('file_path') or '')}"

    @staticmethod
    def _error_sig(text):
        t = (text or "")[-240:].lower()
        t = re.sub(r'0x[0-9a-f]+', '', t)
        t = re.sub(r'\d{2}:\d{2}:\d{2}(\.\d+)?', '', t)
        t = re.sub(r'\b\d+\.\d+s\b', '', t)
        t = re.sub(r'\s+', ' ', t).strip()
        return t

    @staticmethod
    def _result_text(block):
        c = block.get("content")
        if isinstance(c, str):
            return c
        if isinstance(c, list):
            return " ".join(x.get("text", "") for x in c if isinstance(x, dict))
        return ""

    def _trip(self, reason):
        self.tripped = reason
        return reason

    def observe(self, frame):
        if self.tripped:
            return self.tripped
        ftype = frame.get("type")
        content = (frame.get("message") or {}).get("content") or []
        if ftype == "assistant":
            for b in content:
                if isinstance(b, dict) and b.get("type") == "tool_use":
                    tool = b.get("name") or ""
                    ti = b.get("input") or {}
                    sig = self._class_sig(tool, ti)
                    self.pending[b.get("id")] = (tool, sig, ti.get("file_path"))
                    self.recent.append({"tool": tool, "sig": sig,
                                        "detail": (ti.get("command") or ti.get("file_path") or "")[:160]})
                    self.recent = self.recent[-15:]
        elif ftype == "user":
            for b in content:
                if not (isinstance(b, dict) and b.get("type") == "tool_result"):
                    continue
                tool, sig, fpath = self.pending.pop(b.get("tool_use_id"), ("", "", None))
                if b.get("is_error"):
                    self.fail_counts[sig] = self.fail_counts.get(sig, 0) + 1
                    if self.recent:
                        self.recent[-1]["failed"] = True
                    esig = self._error_sig(self._result_text(b))
                    if esig:
                        self.error_counts[esig] = self.error_counts.get(esig, 0) + 1
                        if self.error_counts[esig] >= self.n:
                            return self._trip(f"same error {self.error_counts[esig]}x: {esig[:120]!r}")
                    if self.fail_counts[sig] >= self.n:
                        return self._trip(f"same command class failing {self.fail_counts[sig]}x with no "
                                          f"intervening successful change: {sig}")
                else:
                    if tool == "Read" and fpath:
                        self.read_counts[fpath] = self.read_counts.get(fpath, 0) + 1
                        if self.read_counts[fpath] >= self.n:
                            return self._trip(f"re-read {os.path.basename(fpath)} "
                                              f"{self.read_counts[fpath]}x without progress")
                    elif tool in ("Edit", "Write", "Bash"):
                        self.fail_counts.clear()
                        self.read_counts.clear()
        return None

    def report(self):
        lines = [f"trigger: {self.tripped}", "", "recent tool calls:"]
        for r in self.recent:
            lines.append(f"- {'FAIL ' if r.get('failed') else ''}{r['tool']}: {r['detail']}")
        return "\n".join(lines)


# --------------------------------------------------------------------------
# wq#991 — research-first phase gate
# --------------------------------------------------------------------------

def is_research_gated(rules):
    """A project is research-gated if it declares a hardware_class OR lists
    'research' among its phase_gates. Either signal opts the project in."""
    if not rules:
        return False
    if rules.get("hardware_class"):
        return True
    return "research" in [str(g).lower() for g in rules.get("phase_gates", [])]


def is_device_command(cmd, rules):
    """True if cmd DRIVES the device under RE (read-only contact included).
    Builtin list + per-project device_patterns (additive)."""
    for target in (cmd, _norm(cmd)):
        for pat, _reason in BUILTIN_DEVICE:
            if re.search(pat, target, re.IGNORECASE):
                return True
        for entry in rules.get("device_patterns", []):
            if re.search(entry["pattern"], target, re.IGNORECASE):
                return True
    return False


def research_dir(rules):
    """Canonical research dir for the gated project — projects/<name>/research
    (the same dir fleet-search writes to), falling back to project_root/research
    when the rules file carries no project name."""
    proj = rules.get("project")
    if proj:
        return os.path.join(PROJECTS_ROOT, str(proj), "research")
    root = rules.get("project_root")
    return os.path.join(root, "research") if root else None


def research_complete(rules):
    """Validate the prior-art marker. Existence is not enough: it must parse,
    carry the right schema, match the project, and point at a findings file that
    actually exists and is non-trivial (a bare touch can't satisfy this)."""
    rdir = research_dir(rules)
    if not rdir:
        return False
    marker = os.path.join(rdir, RESEARCH_MARKER)
    try:
        with open(marker) as f:
            m = json.load(f)
    except (OSError, ValueError):
        return False
    if str(m.get("schema", "")).split("/")[0] != RESEARCH_SCHEMA.split("/")[0]:
        return False
    if rules.get("project") and m.get("project") not in (None, rules.get("project")):
        return False
    findings = m.get("findings_file") or "PRIOR_ART.md"
    fpath = findings if os.path.isabs(findings) else os.path.join(rdir, findings)
    try:
        return os.path.isfile(fpath) and os.path.getsize(fpath) >= MIN_FINDINGS_BYTES
    except OSError:
        return False


def research_gate_reason(tool_name, detail, rules):
    """Return a block reason if this action is a device command on a
    research-gated project whose research is not yet complete; else None.
    Non-device work (reading files, inspecting dumps, git, planning, running the
    research tool itself) is never blocked by this gate."""
    if tool_name != "Bash":
        return None                       # device writes via Write/Edit are already RED (dev/ path)
    if not is_research_gated(rules):
        return None
    if research_complete(rules):
        return None
    if not is_device_command(detail, rules):
        return None
    return (f"research phase required before any device interaction "
            f"(project={rules.get('project')}, hardware_class={rules.get('hardware_class')})")


# --------------------------------------------------------------------------
# hook mode — PreToolUse entry point (fail-closed like guard.sh)
# --------------------------------------------------------------------------

def hook_main():
    try:
        raw = sys.stdin.read()
        env = json.loads(raw)
        tool_name = env.get("tool_name") or ""
        ti = env.get("tool_input") or {}
        cwd = env.get("cwd") or os.getcwd()
        rules = load_compiled_rules()
        if tool_name == "Bash":
            cmd = ti.get("command") or ""
            if not cmd:
                print("action_gate: Bash with no command — fail-closed", file=sys.stderr)
                return 2
            verdict, reason, snap = classify_bash(cmd, rules)
            detail = cmd
        elif tool_name in ("Edit", "Write"):
            verdict, reason, snap = classify_file_tool(tool_name, ti.get("file_path") or "", rules)
            detail = ti.get("file_path") or ""
        else:
            return 0  # Read etc — GREEN by definition here; guard.sh owns its own denials
        if verdict == "RED":
            handoff = write_red_handoff(tool_name, detail, reason, rules)
            _log_event("gate_red_block", reason=reason, snippet=detail[:200], handoff=handoff)
            hc = rules.get("hardware_class")
            if hc and ("project rule" in reason or rules.get("red_patterns")):
                print(f"ACTION BLOCKED: project rule prohibits this on hardware_class={hc}. "
                      f"{reason}. Your context has been preserved for a reviewing agent.",
                      file=sys.stderr)
            else:
                print(f"{RED_MSG} ({reason})", file=sys.stderr)
            return 2
        # wq#991 — research-first phase gate. Checked AFTER RED (destructive
        # always wins) and BEFORE any AMBER/GREEN device command executes. Unlike
        # RED this is self-service: the agent clears it by doing the research.
        rg = research_gate_reason(tool_name, detail, rules)
        if rg:
            rdir = research_dir(rules)
            _log_event("gate_research_block", reason=rg, snippet=detail[:200])
            print(
                f"ACTION BLOCKED: {rg}.\n"
                f"No completed prior-art research found at "
                f"{os.path.join(rdir, RESEARCH_MARKER)}.\n"
                f"This is the wq#991 research gate: hardware-RE projects were "
                f"bricked or stalled because the agent touched the device before "
                f"searching what the community already solved. Run the research "
                f"phase first — it searches Reddit/GitHub/forums via fleet-search, "
                f"writes findings to {rdir}/PRIOR_ART.md, and unlocks device "
                f"commands:\n"
                f"  python3 {RESEARCH_PHASE_TOOL} --project {rules.get('project')}\n"
                f"Then re-run your command.",
                file=sys.stderr)
            return 2
        if verdict == "AMBER":
            try:
                arts = snapshot(snap[0], snap[1], cwd)
            except Exception as e:
                _log_event("gate_amber_snapshot_failed", reason=reason, error=str(e)[:200])
                print(f"ACTION BLOCKED: pre-snapshot for '{reason}' failed ({e}) — "
                      f"AMBER never executes unsnapshotted.", file=sys.stderr)
                return 2
            _log_event("gate_amber_snapshot", reason=reason, artifacts=arts, snippet=detail[:200])
            return 0
        return 0
    except Exception as e:
        print(f"action_gate: internal error — fail-closed ({e})", file=sys.stderr)
        return 2


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "hook":
        sys.exit(hook_main())
    print("usage: action_gate.py hook  (PreToolUse stdin JSON)", file=sys.stderr)
    sys.exit(2)
