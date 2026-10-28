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
import hashlib
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
# wq#4751: fleet-side hardware rules for repos this harness cannot commit a
# rules file into (Limpet, /home/lumen/re). Each YAML lists the repo roots it
# covers under `repos:`; read from the harness checkout fworker imports, so a
# worktree edit takes effect only once it lands.
HW_RULES_DIR = os.environ.get("FOREMAN_HW_RULES_DIR") or os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "hw_rules")
SNAP_FILE_CAP = 64 * 1024 * 1024      # per-file copy cap
# wq#4599: a credential pre-image is registered with an expiry so it cannot outlive the rollback it exists for
# (secret-backup-sweep shreds it once every value in it is provably dead). Off when a non-default vault is set,
# so tests and one-off runs never write into the real registry, unless FOREMAN_BACKUP_REGISTER names a tool.
BACKUP_REGISTER = os.environ.get("FOREMAN_BACKUP_REGISTER") or (
    "" if os.environ.get("FOREMAN_GATE_SNAP_VAULT") else "/home/lumen/bin/secret-backup-sweep")
SNAP_DB_CAP = 200 * 1024 * 1024       # sqlite db copy cap
# wq#2116: the snapdir lives under /home/lumen/foreman/_runs, and foreman is every
# worker's --add-dir. A verbatim pre-image of a file that is deliberately OFF every
# read grant (agent/.env, sentinel/config/config.yaml) therefore launders that file
# back into the grant, and keeps doing so long after the live file is fixed. Such a
# pre-image goes here instead: 0600, outside every --add-dir root.
SNAP_VAULT = os.environ.get("FOREMAN_GATE_SNAP_VAULT",
                            os.path.expanduser("~/.secrets/gate-snapshots"))
# wq#4654: secret-backup-sweep (the vault's owner for value-level retirement) classifies
# a vault file NO_SECRETS whenever extract() finds no secret-shaped value in it, and
# cmd_apply only ever shreds verdict=="ELIGIBLE" rows — a NO_SECRETS pre-image is never
# in that set, by design, so it sits forever regardless of --ttl. That's exactly the
# basename-only divert (test_credential_basename_alone_diverts: a `prod.env` with no
# token inside still lands in the vault) and the fail-safe unreadable/binary/oversize
# divert (_is_sensitive returning True with no text to scan). prune_vault() below is the
# blunt age-based backstop for that class: the rollback window, not a dead-value proof.
SNAP_PRUNE_DAYS = int(os.environ.get("FOREMAN_GATE_SNAP_PRUNE_DAYS", "30"))

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

# wq#2798 (wq#2788 Phase 1d) -- live enforcement cutover. Kill switch default OFF:
# wq#2801 (Frank's own sign-off on the tier policy drafted in
# research/RISK_TIERED_JUDGE.md#7) has not landed as of this change, and shadow-mode
# (wq#2797) has logged only a handful of real RED calls so far -- nowhere near the
# review window the policy sign-off assumes. "A judge that has never seen a single
# real production decision does not get to start enforcing on day one" (wq#2788
# research note) applies just as much to a freshly-confirmed policy as to a
# freshly-shipped model. The code path below is real, tested, and fail-closed
# today; flipping FOREMAN_JUDGE_ENFORCE=1 once wq#2801 closes is the only action
# left to go live -- not a code change.
JUDGE_ENFORCE_ENV = "FOREMAN_JUDGE_ENFORCE"
JUDGE_ENFORCE_TIMEOUT = int(os.environ.get("FOREMAN_JUDGE_ENFORCE_TIMEOUT",
                                            str(JUDGE_SHADOW_TIMEOUT)))


JUDGE_TIERS = ("Low", "Low-Medium", "Medium", "High", "EXTREMELY HIGH")


def _judge_enforce_enabled():
    return os.environ.get(JUDGE_ENFORCE_ENV, "0") == "1"

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
    (r'(>|>>)\s*/dev/(sd|nvme|mmcblk|block/|mtd|mem)', "shell redirect onto block/MTD/mem device"),
    (r'\bof=/dev/(sd|nvme|mmcblk|block/|mtd|mem)', "dd/tool write onto block/MTD/mem device"),
    (r'\b(cp|cat|tee|pv)\b[^|;&]*\s/dev/(sd[a-z]|nvme\d|mmcblk\d|block/|mtd\d|mem\b)',
     "raw write onto block/MTD/mem device"),
    (r'\bdrop\s+(table|database|schema)\b', "SQL DROP"),
    (r'\btruncate\s+table\b|\bsqlite3\b[^|;&]*\btruncate\b|\bpsql\b[^|;&]*\btruncate\b',
     "SQL TRUNCATE"),
    (r'\bshred\b', "shred (key/credential destruction)"),
    (r'\brm\b[^|;&]*(\.ssh\b|\.secrets\b|\.credentials|id_ed25519|id_rsa|\.token\b)',
     "credential/key material deletion"),
    # wq#4746 (wq#4744 T1 gap-analysis §2(a)): eFuse burns are one-time and
    # irreversible — unlike BUILTIN_FLASH below, no verified backup makes a
    # blown fuse undoable, so this stays unconditional RED regardless of rules.
    (r'\bespefuse(\.py)?\b[^|;&]*\bburn_\w+\b', "espefuse burn (one-time, irreversible eFuse write)"),
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
    # wq#4746 (wq#4744 T1 gap-analysis §2(a)): MCU/SoC flasher write/erase verbs
    # missing from this class. Read verbs (read_flash, -r, -U *:r:, --upload, rl,
    # spinor/spinand read, mtk.py r, nanddump, mtd_debug read, ...) are
    # deliberately left unmatched so they stay GREEN.
    (r'\bflashrom\b[^|;&]*\s-E\b', "flashrom erase"),
    (r'\besptool(\.py)?\b[^|;&]*\b(write_flash|erase_flash|erase_region)\b',
     "esptool write/erase flash"),
    (r'\bopenocd\b[^|;&]*\bprogram\b', "openocd program"),
    (r'\bopenocd\b[^|;&]*\bflash\s+write_image\b', "openocd flash write_image"),
    (r'\bopenocd\b[^|;&]*\bflash\s+erase_(sector|address)\b', "openocd flash erase"),
    (r'\bst-flash\b[^|;&]*\b(write|erase)\b', "st-flash write/erase"),
    (r'\bstm32flash\b[^|;&]*\s-w\b', "stm32flash write"),
    (r'\bstm32flash\b[^|;&]*\s-[eo]\b', "stm32flash erase"),
    (r'\bavrdude\b[^|;&]*-U\s*\S*:w:', "avrdude write"),
    (r'\bavrdude\b[^|;&]*\s-e\b', "avrdude chip erase"),
    (r'\bdfu-util\b[^|;&]*(-D\b|--download\b)', "dfu-util download (write)"),
    (r'\b(rkdeveloptool|upgrade_tool)\b[^|;&]*\bwl\b', "rkdeveloptool/upgrade_tool write LBA"),
    (r'\b(rkdeveloptool|upgrade_tool)\b[^|;&]*\bef\b', "rkdeveloptool/upgrade_tool erase flash"),
    (r'\b(sunxi-fel|xfel)\b[^|;&]*\bspiflash-write\b', "sunxi-fel/xfel SPI flash write"),
    (r'\b(sunxi-fel|xfel)\b[^|;&]*\bspi(nor|nand)\s+(write|erase)\b',
     "sunxi-fel/xfel SPI NOR/NAND write/erase"),
    (r'\bmtk(\.py)?\b[^|;&]*\s(w|wf|wl)\s', "mtkclient write"),
    (r'\bmtk(\.py)?\b[^|;&]*\s(e|es)\s', "mtkclient erase"),
    (r'\bflashcp\b', "flashcp (MTD write)"),
    (r'\bnandwrite\b', "nandwrite (MTD write)"),
    (r'\bflash_erase\b', "flash_erase (MTD erase)"),
    (r'\bmtd_debug\b[^|;&]*\bwrite\b', "mtd_debug write"),
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


def _sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


_SHA256_HEX = re.compile(r'^[0-9a-f]{64}$')


def _sha256_matches(path, declared):
    """None when path is a regular file whose sha256 equals declared, else why not.
    Hashed live on every call, never cached: a cache would sit in a run dir the
    worker can write, and one forged entry would vouch for a garbage image."""
    declared = str(declared or "").strip().lower()
    if not _SHA256_HEX.match(declared):
        return f"declared sha256 for {path} is not 64 hex chars"
    try:
        if not os.path.isfile(path):
            return f"{path} is not a file on local disk"
        actual = _sha256_file(path)
    except OSError as e:
        return f"{path} unreadable ({e.strerror})"
    if actual != declared:
        return f"{path} sha256 {actual[:12]}... does not match declared {declared[:12]}..."
    return None


def _local_backup_ok(rules):
    """A declared backup that verifies. {path, sha256} entries must hash to the
    declared value (wq#4751); bare-string entries keep the legacy non-empty test
    and are only honored for projects without hardware_class (those go through
    _hw_write_evidence instead)."""
    for entry in rules.get("backups", []):
        if isinstance(entry, dict):
            p = str(entry.get("path") or "")
            if p and _sha256_matches(p, entry.get("sha256")) is None:
                return f"{p} (sha256 verified)"
            continue
        p = entry
        try:
            if os.path.isfile(p) and os.path.getsize(p) > 0:
                return p
            if os.path.isdir(p) and os.listdir(p):
                return p
        except OSError:
            continue
    return None


# --------------------------------------------------------------------------
# wq#4751 (gap-analysis §2(b), T3) — per-target hardware evidence manifest.
# On a hardware_class project a write-class action (BUILTIN_FLASH or a
# backup_required_before token) needs a declared target whose manifest names
# the datasheet, the flash layout / memory map and a recovery image, and the
# recovery image must hash to its declared sha256 at hook time. A bare
# non-empty backup file no longer opens a write there. Declared recovery
# images and known-good dumps are write-protected (_protected_mutation).
# --------------------------------------------------------------------------

TARGET_REQUIRED = ("id", "datasheet", "flash_layout", "recovery_image", "recovery_sha256")


def _select_target(cmd, rules):
    """(target, None) or (None, why). A target with `match` regexes claims the
    commands they hit; a lone target without `match` claims every command."""
    targets = rules.get("targets") or []
    hits = [t for t in targets
            if any(re.search(p, cmd, re.IGNORECASE) for p in t.get("match") or [])]
    if len(hits) == 1:
        return hits[0], None
    if len(hits) > 1:
        ids = ", ".join(str(t.get("id")) for t in hits)
        return None, f"command matches more than one declared target ({ids})"
    if len(targets) == 1 and not targets[0].get("match"):
        return targets[0], None
    if not targets:
        return None, "no target manifest is declared for this project"
    return None, "command names none of the declared targets (no `match` hit)"


def _target_evidence(target):
    """(summary, None) when the manifest is complete and verifies, else (None, why)."""
    tid = target.get("id") or "<no id>"
    missing = [k for k in TARGET_REQUIRED if not str(target.get(k) or "").strip()]
    if missing:
        return None, f"target manifest '{tid}' is missing {', '.join(missing)}"
    for key in ("datasheet", "flash_layout"):
        ref = str(target[key])
        if ref.startswith("/"):
            try:
                if not (os.path.isfile(ref) and os.path.getsize(ref) > 0):
                    return None, f"target '{tid}' {key} {ref} is missing or empty"
            except OSError:
                return None, f"target '{tid}' {key} {ref} is unreadable"
    bad = _sha256_matches(str(target["recovery_image"]), target["recovery_sha256"])
    if bad:
        return None, f"target '{tid}' recovery image: {bad}"
    return f"target '{tid}', recovery image {target['recovery_image']} sha256 verified", None


def _hw_write_evidence(cmd, rules):
    target, why = _select_target(cmd, rules)
    if target is None:
        return None, why
    return _target_evidence(target)


def _hw_write_reason(label, why):
    return (f"{label} on hardware_class project without verified target evidence: "
            f"{why}. Declare the target (id, datasheet, flash_layout, "
            f"recovery_image, recovery_sha256) in the project's hardware rules "
            f"(harness/hw_rules/ or .fworker-rules.yaml `targets:`) and keep the "
            f"recovery image on local disk (wq#4751)")


# Write-protection for declared recovery images and known-good dumps. Loose
# token matching like the rest of the gate: a path argument counts if it
# resolves to a protected path, sits under a protected dir, or has a protected
# file's basename. Reads (sha256sum, xxd, cp FROM it, flashing FROM it) pass.
_PROTECT_DESTROY = frozenset({"rm", "unlink", "shred", "truncate", "touch", "chmod",
                              "chown", "ln", "mv", "tee", "sponge"})
_PROTECT_COPY = frozenset({"cp", "install", "rsync"})
_PROTECT_WRAPPERS = frozenset({"sudo", "env", "nice", "ionice", "nohup", "command", "exec"})
# Flasher verbs that READ the device INTO a local file: aimed at a protected
# path they overwrite the known-good dump with whatever the device holds now.
_READ_INTO_FILE = [
    r'\besptool(\.py)?\b.*\bread_flash\b',
    r'\bflashrom\b.*\s(-r|--read)\b',
    r'\bst-flash\b.*\bread\b',
    r'\bstm32flash\b.*\s-r\b',
    r'\bavrdude\b.*-U\s*\S*:r:',
    r'\bdfu-util\b.*(\s-U\b|--upload\b)',
    r'\b(rkdeveloptool|upgrade_tool)\b.*\brl\b',
    r'\b(mtk(\.py)?|edl(\.py)?)\s.*\s(r|rl|rf|rs)\s',
    r'\b(sunxi-fel|xfel)\b.*\b(read|spiflash-read)\b',
    r'\bnanddump\b',
    r'\bmtd_debug\b.*\bread\b',
    r'\badb\b.*\bpull\b',
    r'\bopenocd\b.*\bdump_image\b',
]


def _shell_segments(cmd):
    """[(words, redirect_targets)] per simple command; quote-aware via shlex,
    whitespace split if the quoting does not parse."""
    import shlex
    try:
        lex = shlex.shlex(cmd, posix=True, punctuation_chars=True)
        lex.whitespace_split = True
        toks = list(lex)
    except ValueError:
        toks = cmd.split()
    segs, words, redirs, i = [], [], [], 0
    while i < len(toks):
        t = toks[i]
        if t and set(t) <= set(";|&()"):
            segs.append((words, redirs)); words, redirs = [], []
        elif t and set(t) <= set("<>&|") and ">" in t:
            if i + 1 < len(toks):
                redirs.append(toks[i + 1]); i += 1
        else:
            words.append(t)
        i += 1
    segs.append((words, redirs))
    return segs


def _path_candidates(word):
    """A word plus the paths embedded in it (of=X, --out=X, flash:r:X:i)."""
    out = [word]
    for sep in ("=", ":"):
        out += [p for p in word.split(sep) if p and p != word]
    return out


def _protected_hit(word, protected, cwd):
    for cand in _path_candidates(word):
        rp = os.path.realpath(os.path.join(cwd, os.path.expanduser(cand)))
        base = os.path.basename(cand.rstrip("/"))
        for p in protected:
            if rp == p or rp.startswith(p.rstrip("/") + "/"):
                return p
            if base and not os.path.isdir(p) and base == os.path.basename(p):
                return p
    return None


def _protected_mutation(cmd, rules, cwd=None):
    """The protected path a Bash command would overwrite or remove, else None."""
    protected = [os.path.realpath(p) for p in rules.get("protected_paths") or []]
    if not protected:
        return None
    cwd = cwd or os.getcwd()
    for words, redirs in _shell_segments(cmd):
        for r in redirs:
            hit = _protected_hit(r, protected, cwd)
            if hit:
                return hit
        while words and (words[0] in _PROTECT_WRAPPERS or re.match(r'^\w+=', words[0])):
            words = words[1:]
        if not words:
            continue
        verb = os.path.basename(words[0])
        args = [w for w in words[1:] if not w.startswith("-") or "=" in w]
        if verb == "sed" and not any(w.startswith("-i") or w == "--in-place"
                                     for w in words[1:]):
            continue
        if verb in _PROTECT_DESTROY or verb == "sed":
            scan = args
        elif verb in _PROTECT_COPY:
            scan = args[-1:]
        elif any(re.search(p, " ".join(words), re.IGNORECASE) for p in _READ_INTO_FILE):
            scan = words[1:]
        else:
            continue
        for w in scan:
            hit = _protected_hit(w, protected, cwd)
            if hit:
                return hit
    return None


def _protected_file(file_path, rules):
    rp = os.path.realpath(file_path)
    for p in rules.get("protected_paths") or []:
        p = os.path.realpath(p)
        if rp == p or rp.startswith(p.rstrip("/") + "/"):
            return p
    return None


# --------------------------------------------------------------------------
# Layer 3 — project rules compiler
# --------------------------------------------------------------------------

def _load_yaml(path):
    import yaml
    with open(path) as f:
        return yaml.safe_load(f) or {}


def _hw_registry_sources(project_dirs, registry=None):
    """[(registry yaml, matched project dir)] for every HW_RULES_DIR file whose
    `repos:` holds a root that one of project_dirs is, or sits under."""
    registry = registry or HW_RULES_DIR
    try:
        names = sorted(n for n in os.listdir(registry) if n.endswith((".yaml", ".yml")))
    except OSError:
        return []
    out = []
    for n in names:
        p = os.path.join(registry, n)
        roots = [os.path.realpath(str(r)) for r in (_load_yaml(p).get("repos") or [])]
        for d in project_dirs:
            if not d:
                continue
            rd = os.path.realpath(d)
            if any(rd == r or rd.startswith(r.rstrip("/") + "/") for r in roots):
                out.append((p, d))
                break
    return out


def compile_rules(project_dirs, out_path):
    """Read .fworker-rules.yaml from each candidate dir (repo root, worktree
    root), merge additively, write compiled JSON to out_path (fworker-owned,
    outside the worktree). Returns out_path, or None if no rules file existed
    (hook then runs builtins-only). Accepts the design doc's schema: `rules`
    may mix prose strings and {backup_required_before: [...]} /
    {phase_gates: [...]} dicts; structured red_patterns/amber_patterns/backups
    top-level keys are also honored. wq#4751: HW_RULES_DIR files whose
    `repos:` covers a project dir merge in the same way, and `targets:` (the
    per-target evidence manifest) plus `protected_paths:` are compiled too."""
    compiled = {"project": None, "hardware_class": None, "prose_rules": [],
                "context": [], "red_patterns": [], "amber_patterns": [],
                "backup_required_before": [], "backups": [], "source_files": [],
                "phase_gates": [], "prior_art_paths": [], "targets": [],
                "protected_paths": []}
    seen = set()
    sources = []          # (rules file, dir whose research/ holds PRIOR_ART.md)
    for d in project_dirs:
        if d:
            sources.append((os.path.join(d, RULES_BASENAME), d))
    sources += _hw_registry_sources(project_dirs)
    for p, d in sources:
        rp = os.path.realpath(p)
        if rp in seen or not os.path.isfile(p):
            continue
        seen.add(rp)
        data = _load_yaml(p)
        compiled["source_files"].append(p)
        compiled.setdefault("_research_dirs", []).append(d)
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
        compiled["backups"] += [b if isinstance(b, dict) else str(b)
                                for b in (data.get("backups") or [])]
        for t in data.get("targets") or []:
            if not isinstance(t, dict):
                raise ValueError(f"{p}: targets entries must be mappings, got {t!r}")
            for pat in t.get("match") or []:
                re.compile(pat)
            compiled["targets"].append(t)
            compiled["protected_paths"] += [str(x) for x in
                                            [t.get("recovery_image")] + list(t.get("known_good_dumps") or [])
                                            if x]
        compiled["protected_paths"] += [str(x) for x in (data.get("protected_paths") or [])]
    if not compiled["source_files"]:
        return None
    research_dirs = compiled.pop("_research_dirs")
    # wq#1006: candidate PRIOR_ART.md locations, checked LIVE at hook time (so
    # fleet-search run mid-session opens the gate without a recompile).
    # Canonical projects/<name>/research/ first — that is where fleet-search
    # --project writes — then research/ beside each rules file (worktree copies).
    pa, pa_seen = [], set()
    if compiled["project"]:
        pa.append(os.path.join("/home/lumen/projects", str(compiled["project"]),
                               "research", "PRIOR_ART.md"))
    for d in research_dirs:
        pa.append(os.path.join(d, "research", "PRIOR_ART.md"))
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

def classify_bash(cmd, rules, cwd=None):
    """Returns (verdict, reason, snap, hw_floor). verdict in RED|AMBER|GREEN.
    snap = (kind, cmd) for AMBER. RED checked first; project rules additive.

    hw_floor (wq#4748 T4) is True only for a RED that must never be
    judge-allowed under FOREMAN_JUDGE_ENFORCE=1, whatever tier the judge
    would assign: BUILTIN_FLASH, the research gate (including PRIOR_ART.md
    tamper), the future hardware write/erase/fuse class (T1, wq#4746 --
    tag any new table entry there the same way once it lands), or a
    hardware_class project rule (red_patterns/backup_required_before on a
    project whose compiled rules set hardware_class), a write-class action
    lacking verified target evidence, or a mutation of a declared recovery
    image / known-good dump (wq#4751). False for every
    other RED, including the general destructive BUILTIN_RED table and
    plain gate-tamper -- those stay judge-eligible as before."""
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
                return "RED", reason, None, False
        r = _red_rm_rf(target)
        if r:
            return "RED", r, None, False
    hw_class = bool(rules.get("hardware_class"))
    for target in targets:
        # gate-tamper: any mutation touching a rules file is RED
        if RULES_BASENAME.rsplit(".", 1)[0] in target and _MUTATION_MARKERS.search(target):
            return "RED", "mutation of .fworker-rules.yaml (gate tamper)", None, False
        prot = _protected_mutation(target, rules, cwd)
        if prot:
            return ("RED", f"mutation of declared recovery image / known-good dump "
                    f"{prot} (write-protected, wq#4751)", None, True)
        for entry in rules.get("red_patterns", []):
            if re.search(entry["pattern"], target, re.IGNORECASE):
                return "RED", entry["reason"], None, hw_class
        # wq#1006 research phase gate: ordered BEFORE the flash/backup path on
        # purpose — a verified backup does not excuse skipping research. Checked
        # live so a fleet-search run this session opens the gate immediately.
        if _research_gate_required(rules):
            if _PRIOR_ART_TAMPER.search(target):
                return ("RED", "direct mutation of PRIOR_ART.md (research gate "
                        "tamper — PRIOR_ART.md is written by fleet-search only)", None, True)
            if not _prior_art_ok(rules) and not _FLEET_SEARCH_ONLY.search(target):
                for pat, label in RESEARCH_GATE_DEVICE:
                    if re.search(pat, target, re.IGNORECASE):
                        return "RED", _research_gate_reason(label, rules), None, True
        # flash class: RED unless a declared LOCAL backup verifies
        for pat, reason in BUILTIN_FLASH:
            if re.search(pat, target, re.IGNORECASE):
                if hw_class:
                    ok, why = _hw_write_evidence(target, rules)
                    if ok:
                        return "AMBER", f"{reason} ({ok})", ("note", cmd), False
                    return "RED", _hw_write_reason(reason, why), None, True
                b = _local_backup_ok(rules)
                if b:
                    return "AMBER", f"{reason} (verified local backup: {b})", ("note", cmd), False
                return "RED", f"{reason} without a verified local backup on disk", None, True
        for tok in rules.get("backup_required_before", []):
            if re.search(r'(^|[\s;|&/])' + re.escape(tok), target, re.IGNORECASE):
                if hw_class:
                    ok, why = _hw_write_evidence(target, rules)
                    if ok:
                        return "AMBER", f"'{tok}' ({ok})", ("note", cmd), False
                    return "RED", _hw_write_reason(f"project rule '{tok}'", why), None, True
                b = _local_backup_ok(rules)
                if b:
                    return "AMBER", f"'{tok}' with verified local backup {b}", ("note", cmd), False
                return ("RED", f"project rule: '{tok}' requires a verified backup on "
                        f"disk (none found locally)", None, hw_class)
    for target in targets:
        for pat, reason, kind in BUILTIN_AMBER:
            if re.search(pat, target, re.IGNORECASE):
                return "AMBER", reason, (kind, cmd), False
        for entry in rules.get("amber_patterns", []):
            if re.search(entry["pattern"], target, re.IGNORECASE):
                return "AMBER", entry["reason"], ("git", cmd), False
    return "GREEN", "", None, False


def classify_file_tool(tool_name, file_path, rules):
    """Returns (verdict, reason, snap, hw_floor) -- see classify_bash for the
    hw_floor contract (wq#4748 T4)."""
    if not file_path:
        return "GREEN", "", None, False
    base = os.path.basename(file_path)
    if base == RULES_BASENAME or base.startswith(".fworker-rules"):
        return "RED", "write/edit of .fworker-rules.yaml (gate tamper)", None, False
    # wq#1006: PRIOR_ART.md is machine-written by fleet-search only — a hand
    # Write/Edit would open the research gate without research.
    if base == "PRIOR_ART.md" and _research_gate_required(rules):
        return ("RED", "write/edit of PRIOR_ART.md (research gate tamper — "
                "run fleet-search instead)", None, True)
    prot = _protected_file(file_path, rules)
    if prot:
        return ("RED", f"{tool_name} of declared recovery image / known-good dump "
                f"{prot} (write-protected, wq#4751)", None, True)
    if re.match(r'^/(etc|boot|usr|dev)(/|$)', os.path.realpath(file_path)):
        return "RED", f"{tool_name} into system path {file_path}", None, False
    for entry in rules.get("red_patterns", []):
        if re.search(entry["pattern"], file_path, re.IGNORECASE):
            return "RED", entry["reason"], None, bool(rules.get("hardware_class"))
    if tool_name == "Edit" and os.path.isfile(file_path):
        return "AMBER", "file edit (pre-image snapshot)", ("path", file_path), False
    if tool_name == "Write" and os.path.isfile(file_path):
        return "AMBER", "file overwrite (pre-image snapshot)", ("path", file_path), False
    return "GREEN", "", None, False


# --------------------------------------------------------------------------
# AMBER snapshots — pre-image BEFORE execution; failure BLOCKS (fail-closed)
# --------------------------------------------------------------------------

def _snapdir():
    d = os.environ.get(SNAPDIR_ENV) or os.path.join("/tmp", f"gate-snapshots-{WORKER_ID}")
    # wq#2911: pre-images are copies of whatever the worker was about to touch; owner-only
    # dir, so the 0644 copies inside stay unreachable from the 0711 run dir.
    os.makedirs(d, mode=0o700, exist_ok=True)
    os.chmod(d, 0o700)
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
        # a symlink's own name says nothing about what it points at, so glob both
        names = {os.path.basename(path), os.path.basename(os.path.realpath(path))}
        if any(fnmatch.fnmatch(c, g)
               for n in names
               for c in _rds.basename_candidates(n)
               for g in _rds.BASENAME_GLOBS):
            return True
        text = _rds._read_text(path)
        if text is None:
            # symlink, binary, oversize or unreadable: the sweep skips these, so nothing
            # ever clears them. Unscanned bytes go to the vault, not the read-granted
            # snapdir (wq#2123 — returning False here copied secrets through both).
            return True
    if not text:
        return False
    return any(rx.search(text) for _, rx in _rds.PATTERNS)


def _register_backup(path):
    """Best-effort: a registry hiccup must never block the gate. The pre-image already exists on disk."""
    if not BACKUP_REGISTER or not os.access(BACKUP_REGISTER, os.X_OK):
        return
    try:
        subprocess.run([BACKUP_REGISTER, "register", path, "--ttl", "7d", "--owner", WORKER_ID,
                        "--why", "gate credential pre-image"], capture_output=True, timeout=5)
    except Exception:
        pass


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
    _register_backup(dst)
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


def _carries_secret_pattern(path):
    """Re-derive _is_sensitive's content check in isolation, to tell a pre-image that
    still carries a trackable credential value (secret-backup-sweep owns its retirement)
    apart from one that only looked sensitive at stow time and has nothing for that
    sweep to ever prove dead. Unreadable/binary/symlink stays conservative (True, same
    fail-safe as _is_sensitive) — prune_vault ages those out by mtime like anything else
    it can't clear by content."""
    if _rds is None:
        return True
    text = _rds._read_text(path)
    if text is None:
        return True
    return any(rx.search(text) for _, rx in _rds.PATTERNS)


def prune_vault(max_age_days=None, vault=None, dry_run=False):
    """Delete vault pre-images older than the rollback window that carry no secret
    pattern for secret-backup-sweep to track — the NO_SECRETS class it never shreds
    (see SNAP_PRUNE_DAYS comment). Returns the list of paths removed.

    dry_run=True selects the same set and unlinks nothing: wq#4706 needs a read-only
    probe against the REAL vault for the ops bundle's --verify and for wq#2762's
    `probe:` term, and an installer that has to delete files to prove itself is not a
    pre-flight check."""
    vault = vault or SNAP_VAULT
    max_age_days = SNAP_PRUNE_DAYS if max_age_days is None else max_age_days
    cutoff = time.time() - max_age_days * 86400
    removed = []
    if not os.path.isdir(vault):
        return removed
    for name in os.listdir(vault):
        p = os.path.join(vault, name)
        if not os.path.isfile(p) or os.path.islink(p):
            continue
        try:
            old_enough = os.stat(p).st_mtime < cutoff
        except OSError:
            continue
        if not old_enough or _carries_secret_pattern(p):
            continue
        if dry_run:
            removed.append(p)
            continue
        try:
            os.unlink(p)
            removed.append(p)
        except OSError:
            pass
    return removed


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


def _call_judge_bin(tool_name, detail, reason, rules, timeout):
    """Raw subprocess call to bin/judge -- shared by shadow_judge (wq#2797,
    observe-only) and enforce_judge_verdict (wq#2798, live). Returns
    (returncode_or_None, parsed_json_or_None, error_str_or_None); a non-None
    error means the call itself failed (timeout, unreachable model, bad JSON)
    and the caller must treat that the same as an explicit FAIL_CLOSED verdict
    -- never retry, never guess."""
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
             "--gate-verdict", "RED", "--timeout", str(timeout)],
            capture_output=True, text=True, timeout=timeout + 15)
        out = json.loads(p.stdout) if p.stdout.strip() else {}
        return p.returncode, out, None
    except Exception as e:  # noqa: BLE001 -- a broken judge call must never raise into the gate
        return None, None, str(e)


def shadow_judge(tool_name, detail, reason, rules):
    """wq#2797 (wq#2788 Phase 1b) -- on every RED, also ask the central judge and
    log its verdict, WITHOUT changing action_gate's own RED exit code or RED_MSG.
    This is a pure observation: it lets wq#2801's tier-policy sign-off and
    wq#2800's calibration work compare the judge against real production RED
    decisions before anything is allowed to gate on it. Only called when
    enforcement (wq#2798, enforce_judge_verdict below) is OFF -- once enforcement
    is on, enforce_judge_verdict makes the one live call itself instead.

    bin/judge already logs every call -- success or FAIL_CLOSED -- to
    minerva.db judge_verdicts, so there is nothing else to persist here beyond
    a local event for correlation with the RED handoff. Best-effort: any
    failure (judge down, timeout, bad JSON) is caught and logged, never raised
    -- a broken shadow observer must never turn into a broken gate."""
    if not _judge_shadow_enabled():
        return None
    rc, out, err = _call_judge_bin(tool_name, detail, reason, rules, JUDGE_SHADOW_TIMEOUT)
    if err is not None:
        _log_event("judge_shadow_error", gate_reason=reason, error=err[:200])
        return None
    _log_event("judge_shadow", gate_reason=reason, judge_verdict=out.get("verdict"),
                judge_tier=out.get("tier"), judge_latency_s=out.get("latency_s"),
                judge_rationale=out.get("rationale"), exit_code=rc)
    return out


def stage_judge_conditions(conditions, tool_name, detail, cwd):
    """wq#2798 -- stage the safeguards an allow-with-conditions verdict named,
    BEFORE the caller lets the action through (same invariant AMBER already
    enforces: never execute unsnapshotted). A condition whose text names a
    snapshot/rollback/backup safeguard reuses the exact pre-image mechanism
    AMBER uses for this tool_name. A condition this gate cannot mechanically
    stage on disk (e.g. "Frank notified", "declared maintenance window") is
    paged to aq instead of silently dropped. Raises on a snapshot failure --
    the caller must fail closed, matching the AMBER snapshot invariant. An
    empty conditions list is itself a judge-contract gap, not a safe default,
    so it still gets a baseline pre-image."""
    if tool_name == "Bash":
        kind, payload = "files", detail
    else:
        kind, payload = ("path", detail) if os.path.isfile(detail) else ("git", detail)
    staged = []
    for c in (conditions or ["(none named by judge — baseline snapshot)"]):
        cl = c.lower()
        if "snapshot" in cl or "rollback" in cl or "backup" in cl or not conditions:
            staged.append({"condition": c, "artifacts": snapshot(kind, payload, cwd)})
        else:
            _page_unstaged_condition(c, tool_name, detail)
            staged.append({"condition": c, "artifacts": ["aq:judge"]})
    return staged


def _page_unstaged_condition(condition, tool_name, detail):
    """Best-effort page for a named condition action_gate has no mechanical way
    to stage on disk. Must never raise -- a paging failure is not a reason to
    fail closed on an action the judge already approved."""
    try:
        subprocess.run(
            ["aq", "add", "judge", f"judge-condition-{WORKER_ID}",
             f"judge allow-with-conditions named a safeguard action_gate cannot "
             f"stage on disk: {condition}",
             f"tool={tool_name} detail={detail[:200]}"],
            timeout=10, capture_output=True)
    except Exception:  # noqa: BLE001 -- paging is best-effort, never the gate's problem
        pass


def enforce_judge_verdict(tool_name, detail, reason, rules, cwd):
    """wq#2798 (wq#2788 Phase 1d) -- Layer 4 live. Only reached when
    FOREMAN_JUDGE_ENFORCE=1 (default off, see JUDGE_ENFORCE_ENV above). Turns a
    RED verdict into deny (today's hard block, unchanged) or allow/
    allow-with-conditions per the judge, staging the named conditions before
    letting the action through. Fail-closed, no exceptions: a FAIL_CLOSED
    verdict, a broken judge call, an unrecognised verdict string, or a failed
    condition-staging attempt all come back as "deny" here so the caller falls
    through to the ordinary unconditional RED block."""
    rc, out, err = _call_judge_bin(tool_name, detail, reason, rules, JUDGE_ENFORCE_TIMEOUT)
    if err is not None or not out or out.get("verdict") not in (
            "allow", "deny", "allow-with-conditions"):
        _log_event("judge_enforce_fail_closed", gate_reason=reason,
                    error=err or f"no/invalid verdict: {out!r}", exit_code=rc)
        return {"action": "deny", "why": "judge FAIL_CLOSED", "judge": out}
    verdict = out["verdict"]
    tier = out.get("tier")
    _log_event("judge_enforce", gate_reason=reason, judge_verdict=verdict,
                judge_tier=tier, judge_rationale=out.get("rationale"),
                exit_code=rc)
    if verdict == "deny":
        return {"action": "deny", "why": "judge denied", "judge": out}
    # wq#2869 tier ceiling: the tier bounds what the verdict may do, whatever the
    # verdict string says. A self-contradicting "allow" on a severe tier is the
    # exact false-allow shape the bench measures, so the tier wins.
    #   unknown/missing tier -> deny (fail closed)
    #   EXTREMELY HIGH       -> deny (Frank only)
    #   High                 -> deny (needs a declared maintenance window + Frank
    #                           notified; action_gate cannot stage either, so it
    #                           stays a hard block until a window gate is wired)
    #   Medium + allow       -> allow-with-conditions (a snapshot is mandatory)
    if tier not in JUDGE_TIERS:
        return {"action": "deny", "why": f"judge tier missing/unknown: {tier!r}", "judge": out}
    if tier in ("EXTREMELY HIGH", "High"):
        return {"action": "deny", "why": f"tier ceiling: {tier} never auto-allows", "judge": out}
    if verdict == "allow" and tier == "Medium":
        verdict = "allow-with-conditions"
        out = dict(out, conditions=list(out.get("conditions") or []) +
                   ["snapshot before executing (forced: Medium tier)"])
    if verdict == "allow":
        return {"action": "allow", "judge": out}
    try:
        staged = stage_judge_conditions(out.get("conditions") or [], tool_name, detail, cwd)
    except Exception as e:  # noqa: BLE001 -- a staging failure must fail closed, never raise
        _log_event("judge_enforce_condition_stage_failed", gate_reason=reason,
                    error=str(e)[:200])
        return {"action": "deny", "why": f"condition staging failed: {e}", "judge": out}
    return {"action": "allow-with-conditions", "judge": out, "staged": staged}


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


# wq#4634: transport-ceiling kill signatures. A long shell command can be killed by
# its transport, not by failing: the Bash tool's own timeout ("Error: Command timed
# out after 2m 0.0s", anthropics/claude-code#1717; SIGURG kills even run_in_background,
# #34138), or — on the Commander path — supergateway's group reap when the Worker
# aborts the tools/call at 70 s (reports/wq3264_mcpw_commander_drops_rca.md). Retrying
# cannot help (the same ceiling kills the retry), so fworker re-runs the command under
# /home/lumen/bin/watchdog (setsid → survives both kill modes) and resumes the session
# with the verdict. The MCP patterns are gated on the tool name: "context canceled" or
# "request timed out" inside ordinary Bash OUTPUT is the command's own business, not a
# transport kill.
CEILING_KILL_SHELL_RE = re.compile(
    r"command timed out after|killed by signal|sig(kill|term) received", re.I)
CEILING_KILL_MCP_RE = re.compile(
    r"context canceled|mcp error\s*-32001|request timed out", re.I)


def is_ceiling_kill(text, tool=""):
    """True when an is_error tool_result's `text` is a transport-ceiling kill of a
    running command rather than the command's own failure."""
    if not text:
        return False
    if CEILING_KILL_SHELL_RE.search(text):
        return True
    return tool.startswith("mcp__") and bool(CEILING_KILL_MCP_RE.search(text))


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

    def __init__(self, threshold=None, steered=False, ceiling_spent=False):
        self.n = threshold or int(os.environ.get("FOREMAN_LOOP_THRESHOLD", "3"))
        self.steered = steered     # the one shell-refusal steer is already spent
        self.steer = None          # steer text the caller must deliver (wq#2138)
        # wq#4634: the one watchdog reissue per attempt chain is already spent
        self.ceiling_spent = ceiling_spent
        self.ceiling = None        # {"command", "error"} of the ceiling-killed call
        self.pending = {}          # tool_use_id -> (tool, class_sig, file_path, command)
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
                    cmd = ti.get("command") if isinstance(ti.get("command"), str) else None
                    self.pending[b.get("id")] = (tool, sig, fkey, cmd)
                    self.recent.append({"tool": tool, "sig": sig,
                                        "detail": (ti.get("command") or ti.get("file_path") or "")[:160]})
                    self.recent = self.recent[-15:]
        elif ftype == "user":
            for b in content:
                if not (isinstance(b, dict) and b.get("type") == "tool_result"):
                    continue
                tool, sig, fpath, cmd = self.pending.pop(b.get("tool_use_id"),
                                                          ("", "", None, None))
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
                    # wq#4634: a transport ceiling killed a running command — not a
                    # model failure, so it never feeds the loop counters. fworker
                    # stops the session, re-runs the command under watchdog, and
                    # resumes with the verdict. Once per attempt chain (ceiling_spent).
                    if cmd and not self.ceiling_spent and is_ceiling_kill(raw_err, tool):
                        self.ceiling_spent = True
                        self.ceiling = {"command": cmd, "error": raw_err[:500]}
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
            verdict, reason, snap, hw_floor = classify_bash(cmd, rules, cwd)
            detail = cmd
        elif tool_name in ("Edit", "Write"):
            verdict, reason, snap, hw_floor = classify_file_tool(
                tool_name, ti.get("file_path") or "", rules)
            detail = ti.get("file_path") or ""
        else:
            return 0  # Read etc — GREEN by definition here; guard.sh owns its own denials
        if verdict == "RED":
            handoff = write_red_handoff(tool_name, detail, reason, rules)
            _log_event("gate_red_block", reason=reason, snippet=detail[:200], handoff=handoff)
            if hw_floor:
                # wq#4748 T4: a hardware-class RED is never judge-allowed, whatever
                # tier the judge would assign -- deny before the judge is even
                # consulted (no enforce call, no shadow call).
                _log_event("gate_hardware_floor_deny", reason=reason)
            elif _judge_enforce_enabled():
                outcome = enforce_judge_verdict(tool_name, detail, reason, rules, cwd)
                if outcome["action"] in ("allow", "allow-with-conditions"):
                    _log_event(f"gate_judge_{outcome['action'].replace('-', '_')}",
                                reason=reason, judge=outcome.get("judge"),
                                staged=outcome.get("staged"))
                    return 0
                _log_event("gate_judge_deny", reason=reason, why=outcome.get("why"),
                            judge=outcome.get("judge"))
            else:
                shadow_judge(tool_name, detail, reason, rules)  # wq#2797: observe-only when enforcement is off
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
    if len(sys.argv) > 1 and sys.argv[1] == "prune-vault":
        _dry = "--dry-run" in sys.argv[2:]
        _n = 0
        for _p in prune_vault(dry_run=_dry):
            _n += 1
            print(f"{'would prune' if _dry else 'pruned'} {_p}")
        print(f"{'would prune' if _dry else 'pruned'} {_n} file(s) from {SNAP_VAULT} "
              f"(older than {SNAP_PRUNE_DAYS}d, no secret pattern)")
        sys.exit(0)
    print("usage: action_gate.py hook  (PreToolUse stdin JSON)\n"
          "       action_gate.py prune-vault [--dry-run]  (delete non-credential vault "
          "pre-images past the rollback window; --dry-run only lists them)",
          file=sys.stderr)
    sys.exit(2)
