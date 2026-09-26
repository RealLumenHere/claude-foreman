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
import fnmatch
import json
import os
import re
import shutil
import subprocess
import sys
import time

try:  # credential shapes — one list, two consumers (read_dir_sweep is the owner)
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import read_dir_sweep as _rds
except Exception:  # never let a sibling import break a fail-closed hook
    _rds = None

GATE_RULES_ENV = "FOREMAN_GATE_RULES"
SNAPDIR_ENV = "FOREMAN_GATE_SNAPDIR"
HANDOFF_ENV = "FOREMAN_GATE_HANDOFF_DIR"
EVENTS_DIR = os.environ.get("FOREMAN_EVENTS_DIR", "/home/lumen/foreman/events")
WORKER_ID = os.environ.get("FOREMAN_WORKER", "unknown")
RULES_BASENAME = ".fworker-rules.yaml"
SNAP_FILE_CAP = 64 * 1024 * 1024      # per-file copy cap
SNAP_DB_CAP = 200 * 1024 * 1024       # sqlite db copy cap
# wq#2116: the snapdir lives under /home/lumen/foreman/_runs, and foreman is every
# worker's --add-dir. A verbatim pre-image of a file that is deliberately OFF every
# read grant (agent/.env, sentinel/config/config.yaml) therefore launders that file
# back into the grant, and keeps doing so long after the live file is fixed. Such a
# pre-image goes here instead: 0600, outside every --add-dir root.
SNAP_VAULT = os.environ.get("FOREMAN_GATE_SNAP_VAULT",
                            os.path.expanduser("~/.secrets/gate-snapshots"))

RED_MSG = ("ACTION BLOCKED: destructive operation requires review. "
           "Your context has been preserved for a reviewing agent.")

# wq#2797 (wq#2788 Phase 1b) -- shadow-mode judge wiring. Kill switch: set
# FOREMAN_JUDGE_SHADOW=0 to disable without redeploying; default ON so the shadow
# actually starts observing real RED decisions. --timeout is kept well under
# bin/judge's own 240s default: this runs synchronously inside a PreToolUse hook,
# and Phase 0 bench put hathor-llama8b at p50 14.7s/p95 17.3s once warm, so 30s
# gives headroom without letting a stuck call hang every RED-blocked tool call.
JUDGE_BIN = os.environ.get("FOREMAN_JUDGE_BIN", "/home/lumen/bin/judge")
JUDGE_SHADOW_TIMEOUT = int(os.environ.get("FOREMAN_JUDGE_SHADOW_TIMEOUT", "30"))


def _judge_shadow_enabled():
    return os.environ.get("FOREMAN_JUDGE_SHADOW", "1") != "0"

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

_MUTATION_MARKERS = re.compile(r'(\brm\b|\bmv\b|\bsed\b[^|;&]*-i|\btee\b|>|\btruncate\b|\bcp\b)')

# --------------------------------------------------------------------------
# wq#1006 — research phase gate (wires the declared phase_gates:[research,...]).
# A project with hardware_class set REQUIRES a non-empty PRIOR_ART.md before
# ANY device-interaction action. Satisfiable ONLY by running fleet-search
# --project <name>, which writes projects/<name>/research/PRIOR_ART.md.
# Token-presence matching (\badb\b etc.), same posture as guard.sh and
# BUILTIN_RED: `grep adb notes.md` blocks too while the gate is unsatisfied —
# accepted false positive, one standalone fleet-search opens the gate for the
# whole attempt. Loose-over-precise means wrappers (timeout/env/xargs adb)
# cannot evade. dd of= stays unconditionally RED via BUILTIN_RED regardless.
# --------------------------------------------------------------------------

RESEARCH_GATE_DEVICE = [
    (r'\badb\b', "adb"),
    (r'\bfastboot\b', "fastboot"),
    (r'\bheimdall\b', "heimdall"),
    (r'(^|[\s;|&])edl(\.py)?\b', "edl"),
    (r'\bflashrom\b', "flashrom"),
    (r'\bmtkclient\b', "mtkclient"),
    (r'\besptool(\.py)?\b', "esptool"),
    (r'\bopenocd\b', "openocd"),
    (r'\bdfu-util\b', "dfu-util"),
    (r'\bstm32flash\b', "stm32flash"),
    (r'\bavrdude\b', "avrdude"),
    (r'\b(picocom|minicom)\b', "serial console"),
    (r'\bscreen\b[^|;&]*/dev/tty(USB|ACM|S)', "serial console (screen)"),
    (r'\bdd\b[^|;&]*\bif=/dev/(sd|mmcblk|nvme|block/)', "dd raw device read"),
]

# The one exemption that prevents deadlock: the sanctioned research command
# itself may mention device tokens in its quoted query ("miko3 adb root").
# Only an ENTIRE command that is a single fleet-search invocation is exempt —
# any ;|&& chaining voids it, so `fleet-search ...; adb ...` still gates.
_FLEET_SEARCH_ONLY = re.compile(r'^\s*(?:/home/lumen/bin/)?fleet-search\b[^;|&]*$')

# Hand-writing PRIOR_ART.md defeats the gate in one GREEN command; it is
# machine-written by fleet-search only (same tamper class as .fworker-rules).
_PRIOR_ART_TAMPER = re.compile(
    r'(\brm\b|\bcp\b|\bmv\b|\btee\b|\btouch\b|\btruncate\b|\bdd\b'
    r'|\bsed\b\s+-i[^|;&]*|>{1,2})\s*[^|;&]*PRIOR_ART', re.IGNORECASE)


def _research_gate_required(rules):
    if not rules.get("hardware_class"):
        return False
    return "research" in [str(g) for g in rules.get("phase_gates") or []]


def _prior_art_ok(rules):
    for p in rules.get("prior_art_paths", []):
        try:
            if os.path.isfile(p) and os.path.getsize(p) > 0:
                return p
        except OSError:
            continue
    return None


def _research_gate_reason(label, rules):
    expect = (rules.get("prior_art_paths") or
              ["projects/<name>/research/PRIOR_ART.md"])[0]
    proj = rules.get("project") or "<name>"
    return (f"research phase gate: '{label}' is device interaction on "
            f"hardware_class={rules.get('hardware_class')} and {expect} is "
            f"missing or empty. Complete the research phase first: run "
            f"`fleet-search --project {proj} web|github|reddit '<topic>'` as a "
            f"standalone command (it writes PRIOR_ART.md), then retry (wq#1006)")


def _norm(cmd):
    return re.sub(r'\s+', ' ', cmd.replace('\n', ' ').replace('\t', ' '))


# wq#2138: the body of a QUOTED heredoc (<<'EOF') is inert text — the shell never
# expands or executes it — so prose inside it ("never rm -rf /") must not read as a
# command. Masking is limited to consumers that only store or forward the body
# (cat/tee/python/git/...): a body fed to a shell, sqlite3, psql, ssh etc. is code,
# and the RED table must keep seeing it. Any unrecognised consumer, any command
# substitution around the heredoc, or a missing terminator leaves the command whole.
_HEREDOC_INERT_CONSUMERS = frozenset({
    "cat", "tee", "cd", "git", "gh", "jq", "grep", "head", "tail", "wc", "sort",
    "wq", "lumen-pr", "python", "python3"})
_PY_VERSIONED = re.compile(r'^python3?\.\d+$')
_HEREDOC_START = re.compile(r'(?<!<)<<(?!<)(-?)\s*(?:\'([^\'\n]+)\'|"([^"\n]+)"|\\(\w+)|(\w+))')


def _scan_heredoc_line(line):
    """Walk one command line outside quotes. Returns (segments, heredocs, subst):
    the simple commands split on unquoted ; | & ( ) { }, each heredoc start as
    (delim, quoted, strip_tabs), and whether a $( or backtick appeared."""
    segments, heredocs, subst = [], [], False
    quote, cur, i = None, [], 0
    while i < len(line):
        ch = line[i]
        if quote:
            if ch == '\\' and quote == '"':
                cur.append(line[i:i + 2]); i += 2; continue
            if ch == quote:
                quote = None
            cur.append(ch); i += 1; continue
        if ch == '\\':
            cur.append(line[i:i + 2]); i += 2; continue
        if ch in '\'"':
            quote = ch; cur.append(ch); i += 1; continue
        m = _HEREDOC_START.match(line, i)
        if m:
            heredocs.append((m.group(2) or m.group(3) or m.group(4) or m.group(5),
                             bool(m.group(2) or m.group(3) or m.group(4)),
                             m.group(1) == "-"))
            cur.append(' '); i = m.end(); continue
        if ch == '`' or line.startswith('$(', i):
            subst = True
        if ch in ';|&(){}':
            segments.append(''.join(cur)); cur = []
        else:
            cur.append(ch)
        i += 1
    segments.append(''.join(cur))
    return segments, heredocs, subst


def _inert_consumer(segment):
    m = re.match(r'\s*(?:\w+=\S*\s+)*(\S+)', segment)
    if not m:
        return True          # empty segment (e.g. after `&&`) consumes nothing
    word = os.path.basename(m.group(1))
    return word in _HEREDOC_INERT_CONSUMERS or bool(_PY_VERSIONED.match(word))


def strip_quoted_heredoc_bodies(cmd):
    """cmd with the bodies of quoted, inert-consumer heredocs removed. The command
    line itself (redirect targets, arguments) is kept, so `cat > /etc/x <<'EOF'`
    still classifies on the `> /etc/x`."""
    if '<<' not in cmd:
        return cmd
    lines = cmd.split('\n')
    out, i = [], 0
    while i < len(lines):
        segments, heredocs, subst = _scan_heredoc_line(lines[i])
        out.append(lines[i]); i += 1
        if not heredocs:
            continue
        inert = not subst and all(_inert_consumer(s) for s in segments)
        for delim, quoted, strip_tabs in heredocs:
            j = i
            while j < len(lines) and (lines[j].lstrip('\t') if strip_tabs else lines[j]) != delim:
                j += 1
            if j >= len(lines):
                break        # unterminated: not a heredoc we can bound, scan it all
            if not (quoted and inert):
                out.extend(lines[i:j + 1])
            else:
                out.append(lines[j])
            i = j + 1
    return '\n'.join(out)


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
                "backup_required_before": [], "backups": [], "source_files": [],
                "phase_gates": [], "prior_art_paths": []}
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
        for item in data.get("rules", []) or []:
            if isinstance(item, str):
                compiled["prose_rules"].append(item)
            elif isinstance(item, dict):
                for k, v in item.items():
                    if k == "backup_required_before" and isinstance(v, list):
                        compiled["backup_required_before"] += [str(t) for t in v]
                    elif k == "phase_gates" and isinstance(v, list):
                        compiled["phase_gates"] += [str(g) for g in v
                                                    if str(g) not in compiled["phase_gates"]]
                    else:
                        compiled["prose_rules"].append(f"{k}: {v}")
        if isinstance(data.get("phase_gates"), list):  # top-level form also honored
            compiled["phase_gates"] += [str(g) for g in data["phase_gates"]
                                        if str(g) not in compiled["phase_gates"]]
        compiled["context"] += [str(c) for c in (data.get("context") or [])]
        for key in ("red_patterns", "amber_patterns"):
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
    # wq#1006: candidate PRIOR_ART.md locations, checked LIVE at hook time (so
    # fleet-search run mid-session opens the gate without a recompile).
    # Canonical projects/<name>/research/ first — that is where fleet-search
    # --project writes — then research/ beside each rules file (worktree copies).
    pa, pa_seen = [], set()
    if compiled["project"]:
        pa.append(os.path.join("/home/lumen/projects", str(compiled["project"]),
                               "research", "PRIOR_ART.md"))
    for src in compiled["source_files"]:
        pa.append(os.path.join(os.path.dirname(src), "research", "PRIOR_ART.md"))
    for p in pa:
        rp = os.path.realpath(p)
        if rp not in pa_seen:
            pa_seen.add(rp)
            compiled["prior_art_paths"].append(p)
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
    # wq#2138: only the builtin destructive table and the rm -rf root check see the
    # heredoc-stripped text; tamper, research-gate, flash and project rules stay
    # loose-over-precise on the raw command.
    stripped = strip_quoted_heredoc_bodies(cmd)
    for target in (stripped, _norm(stripped)):
        for pat, reason in BUILTIN_RED:
            if reason is None:
                continue
            if re.search(pat, target, re.IGNORECASE):
                return "RED", reason, None
        r = _red_rm_rf(target)
        if r:
            return "RED", r, None
    for target in targets:
        # gate-tamper: any mutation touching a rules file is RED
        if RULES_BASENAME.rsplit(".", 1)[0] in target and _MUTATION_MARKERS.search(target):
            return "RED", "mutation of .fworker-rules.yaml (gate tamper)", None
        for entry in rules.get("red_patterns", []):
            if re.search(entry["pattern"], target, re.IGNORECASE):
                return "RED", entry["reason"], None
        # wq#1006 research phase gate: ordered BEFORE the flash/backup path on
        # purpose — a verified backup does not excuse skipping research. Checked
        # live so a fleet-search run this session opens the gate immediately.
        if _research_gate_required(rules):
            if _PRIOR_ART_TAMPER.search(target):
                return ("RED", "direct mutation of PRIOR_ART.md (research gate "
                        "tamper — PRIOR_ART.md is written by fleet-search only)", None)
            if not _prior_art_ok(rules) and not _FLEET_SEARCH_ONLY.search(target):
                for pat, label in RESEARCH_GATE_DEVICE:
                    if re.search(pat, target, re.IGNORECASE):
                        return "RED", _research_gate_reason(label, rules), None
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
    # wq#1006: PRIOR_ART.md is machine-written by fleet-search only — a hand
    # Write/Edit would open the research gate without research.
    if base == "PRIOR_ART.md" and _research_gate_required(rules):
        return ("RED", "write/edit of PRIOR_ART.md (research gate tamper — "
                "run fleet-search instead)", None)
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


def _is_sensitive(path=None, text=None):
    """Would read_dir_sweep flag this artifact as a credential? A failed import counts
    as yes: the vault is a safe place for any pre-image, the snapdir is not."""
    if _rds is None:
        return True
    if path is not None:
        if any(fnmatch.fnmatch(os.path.basename(path), g) for g in _rds.BASENAME_GLOBS):
            return True
        # the sweep's own reader, so both tools skip the same symlinks/binaries/oversize
        text = _rds._read_text(path)
    if not text:
        return False
    return any(rx.search(text) for _, rx in _rds.PATTERNS)


def _stow(d, name, src=None, text=None):
    """Write one pre-image artifact and return its path. A credential-shaped artifact
    goes to SNAP_VAULT at 0600 instead of the snapdir, and `d` gets a path-only pointer
    in place of the bytes — the pre-image still exists for rollback, it just stops being
    readable through the foreman read grant (wq#2116)."""
    if not _is_sensitive(path=src, text=text):
        dst = os.path.join(d, name)
        if src is not None:
            shutil.copy2(src, dst)
        else:
            open(dst, "w").write(text)
        return dst
    os.makedirs(SNAP_VAULT, exist_ok=True, mode=0o700)
    dst = os.path.join(SNAP_VAULT, f"{WORKER_ID}-{name}")
    if src is not None:
        shutil.copy2(src, dst)
    else:
        open(dst, "w").write(text)
    os.chmod(dst, 0o600)
    with open(os.path.join(d, f"{name}.held-out"), "w") as f:
        f.write(f"credential-shaped pre-image of {src or name} kept out of the snapdir\n"
                f"vault (0600): {dst}\n")
    return dst


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
            arts.append(_stow(d, f"{ts}-{_sanitize(p)}", src=p))
        else:
            raise RuntimeError(f"pre-image {p} exceeds {SNAP_FILE_CAP} cap")
    elif kind == "files":
        for p in _extract_file_targets(payload)[:8]:
            if os.path.getsize(p) <= SNAP_FILE_CAP:
                arts.append(_stow(d, f"{ts}-{_sanitize(p)}", src=p))
        if not arts:
            kind = "git"  # nothing extractable — fall through to tree-state record
    if kind == "git":
        # `git diff` of a dirty worktree carries whatever the edit put there, so the
        # state record is stowed by content like every other artifact (wq#2116).
        body = []
        for args in (["rev-parse", "HEAD"], ["status", "--porcelain"],
                     ["diff"], ["diff", "--staged"], ["stash", "create"]):
            r = _run(["git", *args], cwd=cwd)
            body.append(f"$ git {' '.join(args)}\n{r.stdout}{r.stderr}\n")
        arts.append(_stow(d, f"{ts}-git-state.txt", text="".join(body)))
    elif kind == "sqlite":
        m = re.search(r'(/[^\s;|&"\']+\.(?:db|sqlite3?))\b', payload)
        if m and os.path.isfile(m.group(1)):
            p = m.group(1)
            if os.path.getsize(p) > SNAP_DB_CAP:
                raise RuntimeError(f"db {p} exceeds snapshot cap")
            arts.append(_stow(d, f"{ts}-{_sanitize(p)}", src=p))
    elif kind == "pkg-pip":
        r = _run(["pip3", "freeze"], timeout=30)
        arts.append(_stow(d, f"{ts}-pip-freeze.txt", text=r.stdout))
    elif kind == "pkg-dpkg":
        r = _run(["dpkg-query", "-W", "-f", "${Package} ${Version}\n"], timeout=30)
        arts.append(_stow(d, f"{ts}-dpkg-list.txt", text=r.stdout))
    elif kind == "unit":
        m = re.search(r'systemctl\s+(?:stop|disable|mask)\s+([A-Za-z0-9@._-]+)', payload)
        if m:
            # systemctl show prints Environment=, which is a credential surface
            r = _run(["systemctl", "show", m.group(1), "--no-pager"], timeout=15)
            arts.append(_stow(d, f"{ts}-unit-{_sanitize(m.group(1))}.txt", text=r.stdout))
    elif kind == "note":
        arts.append(_stow(d, f"{ts}-note.txt", text=payload))
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


def shadow_judge(tool_name, detail, reason, rules):
    """wq#2797 (wq#2788 Phase 1b) -- on every RED, also ask the central judge and
    log its verdict, WITHOUT changing action_gate's own RED exit code or RED_MSG.
    This is a pure observation: it lets wq#2801's tier-policy sign-off and
    wq#2800's calibration work compare the judge against real production RED
    decisions before anything is allowed to gate on it (wq#2798, not this ticket).

    bin/judge already logs every call -- success or FAIL_CLOSED -- to
    minerva.db judge_verdicts, so there is nothing else to persist here beyond
    a local event for correlation with the RED handoff. Best-effort: any
    failure (judge down, timeout, bad JSON) is caught and logged, never raised
    -- a broken shadow observer must never turn into a broken gate."""
    if not _judge_shadow_enabled():
        return None
    ctx_parts = []
    if rules.get("project"):
        ctx_parts.append(f"project={rules['project']}")
    if rules.get("hardware_class"):
        ctx_parts.append(f"hardware_class={rules['hardware_class']}")
    if rules.get("prose_rules"):
        ctx_parts.append("project_rules: " + " | ".join(rules["prose_rules"][:10]))
    if rules.get("context"):
        ctx_parts.append("project_context: " + " | ".join(rules["context"][:10]))
    context = "; ".join(ctx_parts)
    kind = "bash" if tool_name == "Bash" else "file_write"
    try:
        p = subprocess.run(
            [JUDGE_BIN, "--actor", WORKER_ID, "--kind", kind,
             "--command", detail[:4000], "--context", context[:2000],
             "--gate-verdict", "RED", "--timeout", str(JUDGE_SHADOW_TIMEOUT)],
            capture_output=True, text=True, timeout=JUDGE_SHADOW_TIMEOUT + 15)
        out = json.loads(p.stdout) if p.stdout.strip() else {}
        _log_event("judge_shadow", gate_reason=reason, judge_verdict=out.get("verdict"),
                    judge_tier=out.get("tier"), judge_latency_s=out.get("latency_s"),
                    judge_rationale=out.get("rationale"), exit_code=p.returncode)
        return out
    except Exception as e:  # noqa: BLE001 -- shadow observation must never break the gate
        _log_event("judge_shadow_error", gate_reason=reason, error=str(e)[:200])
        return None


# --------------------------------------------------------------------------
# Layer 2 — LoopTracker (used by fworker.run_child)
# --------------------------------------------------------------------------

# wq#1107: the harness's own permission configuration can refuse a tool call. Claude
# Code's permission layer answers a Bash call the settings pack does not allow with a
# fixed string ("This command requires approval", or the compound-command variant
# "...the following parts require approval: <cmd>"), and answers a path outside the
# session's --add-dir set with "was blocked. For security, Claude Code may only ...".
# A model that retries such a call reads as a loop, but the defect is ours: the same
# command would have succeeded under a settings pack that allowed it. Marking the trip
# lets fworker classify it as a HARNESS defect (PERMISSION_LOOP) instead of charging
# KILLED/INFRA_CRASH — and so the dispatcher circuit breaker — against the model.
PERMISSION_DENIAL_RE = re.compile(
    r"requires? approval|requires? permission|blocked\. for security", re.I)


def is_permission_denial(text):
    """True when `text` is Claude Code's own permission/workspace-boundary refusal
    rather than an error the tool itself produced."""
    return bool(text) and bool(PERMISSION_DENIAL_RE.search(text))


# wq#2138: Claude Code's own shell-safety rule ("...contains brace with quote
# character (expansion obfuscation)") refuses python heredocs whose body carries a
# brace next to a quote. Retrying the heredoc reads as a loop, but the cure is a tool
# switch, not a smarter retry: the first such refusal is a STEER (fworker resumes the
# session once with this text), and only a refusal AFTER the steer counts toward a
# kill — charged to the harness like a permission refusal, not to the ticket.
SHELL_REFUSAL_RE = re.compile(r"brace with quote character|expansion obfuscation", re.I)
SHELL_REFUSAL_STEER = (
    "The Claude CLI refused your last shell command (brace-with-quote / expansion "
    "obfuscation rule). That rule is not retryable. Stop creating or patching files "
    "through shell heredocs (`python3 - <<EOF`, `cat > f <<EOF`): create files with "
    "the Write tool and change them with the Edit tool, then continue the ticket.")


def is_shell_refusal(text):
    """True when `text` is the CLI's brace-with-quote shell refusal."""
    return bool(text) and bool(SHELL_REFUSAL_RE.search(text))


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

    def __init__(self, threshold=None, steered=False):
        self.n = threshold or int(os.environ.get("FOREMAN_LOOP_THRESHOLD", "3"))
        self.steered = steered     # the one shell-refusal steer is already spent
        self.steer = None          # steer text the caller must deliver (wq#2138)
        self.pending = {}          # tool_use_id -> (tool, class_sig, file_path)
        self.fail_counts = {}      # class_sig -> count (reset on successful mutation)
        self.perm_counts = {}      # class_sig -> permission-refusal count (tracks fail_counts)
        self.error_counts = {}     # error_sig -> count (never reset)
        self.read_counts = {}      # file_path -> count (reset on successful mutation)
        self.recent = []           # last tool calls for the handoff
        self.tripped = None
        self.trip_kind = None      # "PERMISSION" when the harness itself refused the call

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

    def _trip(self, reason, kind=None):
        self.tripped = reason
        self.trip_kind = kind
        return reason

    def observe(self, frame):
        if self.tripped:
            return self.tripped
        ftype = frame.get("type")
        _msg = frame.get("message")
        content = (_msg.get("content") if isinstance(_msg, dict) else None) or []
        if ftype == "assistant":
            for b in content:
                if isinstance(b, dict) and b.get("type") == "tool_use":
                    tool = b.get("name") or ""
                    ti = b.get("input") or {}
                    sig = self._class_sig(tool, ti)
                    # paged-read fix (2026-08-29): guard.sh R3 caps every Read at
                    # FOREMAN_READ_LINE_CAP lines, so a large file is legitimately read
                    # in offset pages. Key the re-read counter by (path, offset, limit)
                    # so paging is progress, not a loop; the SAME page 3x still trips.
                    fkey = ti.get("file_path")
                    if tool == "Read" and fkey:
                        fkey = f"{fkey}@{ti.get('offset') or 0}+{ti.get('limit') or 0}"
                    self.pending[b.get("id")] = (tool, sig, fkey)
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
                    raw_err = self._result_text(b)
                    if is_shell_refusal(raw_err) and not self.steered:
                        self.steered = True
                        self.steer = SHELL_REFUSAL_STEER
                        self.fail_counts[sig] -= 1
                        return None
                    harness_refusal = is_permission_denial(raw_err) or is_shell_refusal(raw_err)
                    # wq#1107: keyed off the RAW result text, not the normalized esig —
                    # _error_sig() keeps only the last 240 chars, and a long compound
                    # denial can push the marker phrase out of that tail.
                    if harness_refusal:
                        self.perm_counts[sig] = self.perm_counts.get(sig, 0) + 1
                    esig = self._error_sig(raw_err)
                    if esig:
                        self.error_counts[esig] = self.error_counts.get(esig, 0) + 1
                        if self.error_counts[esig] >= self.n:
                            kind = "PERMISSION" if harness_refusal else None
                            return self._trip(
                                f"same error {self.error_counts[esig]}x: {esig[:120]!r}", kind)
                    if self.fail_counts[sig] >= self.n:
                        kind = ("PERMISSION"
                                if self.perm_counts.get(sig, 0) >= self.n else None)
                        return self._trip(f"same command class failing {self.fail_counts[sig]}x with no "
                                          f"intervening successful change: {sig}", kind)
                else:
                    if tool == "Read" and fpath:
                        self.read_counts[fpath] = self.read_counts.get(fpath, 0) + 1
                        if self.read_counts[fpath] >= self.n:
                            return self._trip(f"re-read {os.path.basename(fpath.split('@')[0])} "
                                              f"{self.read_counts[fpath]}x without progress")
                    elif tool in ("Edit", "Write", "Bash"):
                        self.fail_counts.clear()
                        self.perm_counts.clear()
                        self.read_counts.clear()
        return None

    def report(self):
        lines = [f"trigger: {self.tripped}"]
        if self.trip_kind == "PERMISSION":
            lines += ["", "HARNESS DEFECT (wq#1107), not a model loop: the calls below were "
                      "refused by this session's own permission configuration, not by the "
                      "tool. Fix harness/worker-settings.json (permissions.allow) or the "
                      "--add-dir set rather than reworking the ticket."]
        lines += ["", "recent tool calls:"]
        for r in self.recent:
            lines.append(f"- {'FAIL ' if r.get('failed') else ''}{r['tool']}: {r['detail']}")
        return "\n".join(lines)


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
            shadow_judge(tool_name, detail, reason, rules)  # wq#2797: observe-only, never gates
            hc = rules.get("hardware_class")
            if hc and ("project rule" in reason or rules.get("red_patterns")):
                print(f"ACTION BLOCKED: project rule prohibits this on hardware_class={hc}. "
                      f"{reason}. Your context has been preserved for a reviewing agent.",
                      file=sys.stderr)
            else:
                print(f"{RED_MSG} ({reason})", file=sys.stderr)
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
