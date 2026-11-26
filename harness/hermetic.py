#!/usr/bin/env python3
"""hermetic.py — the hermetic fworker output contract (wq#4830).

One rule: a worker's file output goes to its own lumen-pr worktree (the PR path) or
to its per-ticket output dir /scratch/agent-work/<ticket-id>/. Nothing else is
writable. The wq scratch dir (/tmp/<agent>/, wq#1697) and a Frank-approved H1 grant
target stay writable because the harness itself hands them out.

Three layers, each with its own switch (fworker.build_env exports them to the child;
values come from defaults < FOREMAN_ROOT/hermetic.json < fworker's own environment):

  FOREMAN_HERMETIC        enforce (default) | shadow | off
      Write / Edit / NotebookEdit targets are resolved (symlinks, `..`) and must sit
      inside the writable set. guard.sh calls `hermetic.py hook` for this.
  FOREMAN_HERMETIC_BASH   shadow (default) | enforce | off
      Explicit Bash write destinations (redirects, cp/mv/tee/touch/sed -i/...) are
      held to the same set. Always enforced, whatever this is set to, as long as
      FOREMAN_HERMETIC is not off: a destination inside the foreman source tree or
      the secrets dir. Bash can always write through an interpreter, so this layer
      catches honest mistakes, not a hostile worker. The mount layer is the wall.
  FOREMAN_HERMETIC_MOUNT  off (default) | enforce
      The child runs under bubblewrap: the whole filesystem read-only, the writable
      set and the listed state dirs bound read-write, the secrets dir replaced by an
      empty tmpfs. Off by default: sudo does not work inside it, so lumen-run fails.

Fail-closed like guard.sh: an unknown mode value means enforce, and any internal
error in `hook` exits non-zero, which guard.sh turns into a block.
"""
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import time

FOREMAN_ROOT = os.environ.get("FOREMAN_ROOT", "/home/lumen/foreman")
DEFAULT_OUTPUT_ROOT = "/scratch/agent-work"
SECRETS_DIR = os.environ.get("FOREMAN_SECRETS_DIR", "/home/lumen/.secrets")
CONFIG_PATH = os.environ.get("FOREMAN_HERMETIC_CONFIG",
                             os.path.join(FOREMAN_ROOT, "hermetic.json"))
HOME = "/home/lumen"
LUMEN_PR_WORKTREE_ROOT = "/home/lumen/scratch/worktrees"
ISSUE_LOG = "/home/lumen/kb/ISSUE-LOG.md"

MODE_VARS = {
    "FOREMAN_HERMETIC": ("enforce", ("enforce", "shadow", "off")),
    "FOREMAN_HERMETIC_BASH": ("shadow", ("enforce", "shadow", "off")),
    "FOREMAN_HERMETIC_MOUNT": ("off", ("enforce", "off")),
}
# A writable root this broad is a config error, never a grant.
TOO_BROAD = {"/", "/home", HOME, "/scratch", "/tmp", "/var", "/etc", "/usr"}
# Runtime state under the foreman tree that is not source: not part of the Bash hard set.
FOREMAN_RUNTIME_DIRS = ("_runs", "events")
DEV_OK = ("/dev/null", "/dev/stdout", "/dev/stderr", "/dev/tty", "/dev/zero")
DEV_OK_PREFIXES = ("/dev/fd/", "/proc/self/fd/")
_TICKET_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")


class HermeticError(Exception):
    pass


def output_root():
    """Read at call time so a test run can point it away from /scratch."""
    return os.environ.get("FOREMAN_OUTPUT_ROOT") or DEFAULT_OUTPUT_ROOT


# ---- modes -----------------------------------------------------------------------

def _config():
    try:
        with open(CONFIG_PATH) as f:
            cfg = json.load(f)
        return cfg if isinstance(cfg, dict) else {}
    except FileNotFoundError:
        return {}
    except Exception as e:
        raise HermeticError(f"unreadable hermetic config {CONFIG_PATH}: {e}")


def mode(var, env=None):
    """The mode of one layer as the hook sees it: env only. Unset means the default;
    a value outside the known set means enforce (a typo must not open the wall)."""
    default, known = MODE_VARS[var]
    raw = (os.environ if env is None else env).get(var)
    if raw is None or raw.strip() == "":
        return default
    v = raw.strip().lower()
    return v if v in known else "enforce"


def settings(env=None):
    """{var: mode} for fworker to export to the child: defaults, overridden by the
    `modes` map in hermetic.json, overridden by fworker's own environment."""
    env = os.environ if env is None else env
    cfg_modes = _config().get("modes") or {}
    out = {}
    for var, (default, known) in MODE_VARS.items():
        v = env.get(var)
        if v is None or v.strip() == "":
            v = cfg_modes.get(var, default)
        v = str(v).strip().lower()
        out[var] = v if v in known else "enforce"
    return out


# ---- the writable set ------------------------------------------------------------

def output_dir(ticket_id):
    tid = str(ticket_id)
    if not _TICKET_RE.match(tid):
        raise HermeticError(f"ticket id {tid!r} cannot name an output dir")
    return os.path.join(output_root(), tid)


def ensure_output_dir(ticket_id):
    """Create /scratch/agent-work/<ticket-id>/ and return it. Refuses a path that
    resolves anywhere else (a planted symlink would move the whole contract)."""
    d = output_dir(ticket_id)
    os.makedirs(d, mode=0o775, exist_ok=True)
    want = os.path.join(os.path.realpath(output_root()), str(ticket_id))
    if os.path.realpath(d) != want or not os.path.isdir(d):
        raise HermeticError(f"output dir {d} resolves to {os.path.realpath(d)}, expected {want}")
    return d


def _under(path, root):
    return path == root or path.startswith(root.rstrip(os.sep) + os.sep)


def write_roots(env=None):
    """Resolved writable roots from the child env. A root that is unset, relative or
    too broad is dropped, which can only shrink the set."""
    env = os.environ if env is None else env
    raw = [env.get("FOREMAN_WORKTREE_ROOT"), env.get("FOREMAN_OUTPUT_DIR"),
           env.get("FOREMAN_SCRATCH_DIR")]
    raw += (env.get("FOREMAN_HERMETIC_EXTRA_WRITE") or "").split(":")
    roots = []
    for r in raw:
        if not r or not os.path.isabs(r):
            continue
        rp = os.path.realpath(r)
        if (rp in TOO_BROAD or os.path.normpath(r) in TOO_BROAD
                or rp == os.path.realpath(output_root())):
            continue
        if rp not in roots:
            roots.append(rp)
    return roots


def ticket_id(env=None):
    env = os.environ if env is None else env
    d = env.get("FOREMAN_OUTPUT_DIR")
    return os.path.basename(os.path.normpath(d)) if d else (env.get("FOREMAN_TICKET_ID") or "")


def own_pr_worktree(rp, env=None):
    """True if `rp` is inside a lumen-pr worktree whose name carries this ticket's id
    (`lumen-pr start <repo> fix/wq<id>-<slug> --worktree` gives <repo>-fix-wq<id>-<slug>).
    That is how a worker changes a second repo, and it is a PR path like its own
    worktree. Anyone else's worktree under the same root stays out of the set."""
    env = os.environ if env is None else env
    tid = ticket_id(env)
    root = os.path.realpath(env.get("LUMEN_PR_WORKTREE_ROOT") or LUMEN_PR_WORKTREE_ROOT)
    if not tid or rp == root or not _under(rp, root):
        return False
    name = os.path.relpath(rp, root).split(os.sep)[0]
    return re.search(rf"(^|[^0-9A-Za-z])wq{re.escape(tid)}([^0-9]|$)", name) is not None


def contract_active(env=None):
    """True for any child fworker spawned (it always sets the worktree root). A guard
    run by hand with neither variable set has no contract to hold it to."""
    env = os.environ if env is None else env
    return bool(env.get("FOREMAN_WORKTREE_ROOT") or env.get("FOREMAN_OUTPUT_DIR"))


def resolve(path, cwd=None):
    if not os.path.isabs(path):
        path = os.path.join(cwd or os.getcwd(), path)
    return os.path.realpath(path)


def primary_trees(env=None):
    env = os.environ if env is None else env
    raw = env.get("FOREMAN_PRIMARY_TREES")
    if raw:
        return [p for p in raw.split(":") if p]
    try:
        import primary_tree_guard
        return list(primary_tree_guard.STATIC_PRIMARY_TREES)
    except Exception:
        return [FOREMAN_ROOT, "/home/lumen/bin"]


def _where(env):
    wt = env.get("FOREMAN_WORKTREE_ROOT") or "your worktree"
    out = env.get("FOREMAN_OUTPUT_DIR") or f"{output_root()}/<ticket-id>"
    tid = ticket_id(env) or "<id>"
    return (f"PR changes go in your worktree ({wt}) and land with lumen-pr; every other "
            f"file you produce goes under {out}. A second repo is changed in a lumen-pr "
            f"worktree whose branch name carries wq{tid}")


def check_write(tool, path, env=None, cwd=None):
    """(ok, reason, resolved) for one Write/Edit/NotebookEdit target."""
    env = os.environ if env is None else env
    if not path:
        return False, f"{tool} with no target path", ""
    rp = resolve(path, cwd)
    plan = env.get("FOREMAN_FIRE_PLAN_PATH")
    if plan and path == plan:                      # wq#717: exact string, as in guard.sh
        return True, "", rp
    if tool == "Edit" and path == ISSUE_LOG:       # wq#1831: exact string, Edit only
        return True, "", rp
    for root in write_roots(env):
        if _under(rp, root):
            return True, "", rp
    if own_pr_worktree(rp, env):
        return True, "", rp
    return False, (f"hermetic output contract (wq#4830): {tool} target {rp} is outside "
                   f"this ticket's writable set. {_where(env)}"), rp


def primary_hit(rp, env=None):
    """The primary tree a RESOLVED path falls in, else None. Closes the wq#1719 wall's
    raw-string gap: `<worktree>/../../foreman/x` and a symlink into a primary both
    passed it."""
    for tree in primary_trees(env):
        if _under(rp, os.path.realpath(tree)) or _under(rp, tree):
            return tree
    return None


# ---- Bash: explicit write destinations --------------------------------------------

_OPS = ("&>>", "<<<", ">>", "&>", ">&", ">|", "<<", "<&", "<>", "&&", "||", "|&", ";;",
        ";", "&", "|", "(", ")", "<", ">")
_OPCHARS = "();<>|&"
_CONTROL = {";", "&&", "||", "|", "|&", "&", "(", ")", ";;"}
_REDIR_OUT = {">", ">>", ">|", "&>", "&>>", ">&", "<>"}
_REDIR_IN = {"<", "<<", "<<<", "<&"}
_HEREDOC_RE = re.compile(r"(?<!<)<<(?!<)-?\s*(['\"]?)([A-Za-z_][A-Za-z0-9_]*)\1")
_ASSIGN_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
_WRAPPERS = {"command", "nohup", "time", "exec", "env", "sudo", "nice", "ionice", "stdbuf"}
_DYNAMIC_RE = re.compile(r"[$`*?\[\]{}]")
_VALUE_OPTS = {
    "cp": {"-t", "-S", "--target-directory", "--suffix"},
    "mv": {"-t", "-S", "--target-directory", "--suffix"},
    "install": {"-t", "-m", "-o", "-g", "-S", "--target-directory", "--mode"},
    "ln": {"-t", "-S", "--target-directory", "--suffix"},
    "rsync": {"-e", "--rsh", "--exclude", "--include", "--files-from", "--exclude-from"},
    "touch": {"-d", "-t", "-r", "--date", "--reference"},
    "truncate": {"-s", "-r", "--size", "--reference"},
    "mkdir": {"-m", "--mode"},
    "sed": {"-e", "-f", "-l", "--expression", "--file", "--line-length"},
}
_ALL_ARGS = {"tee", "touch", "mkdir", "rmdir", "truncate", "rm", "unlink", "shred"}
_LAST_ARG = {"cp", "mv", "install", "rsync", "ln"}
_SKIP_FIRST = {"chmod", "chown", "chgrp"}
# These act on the directory entry itself: a symlink in the last component is the
# thing replaced or removed, never followed.
_NOFOLLOW = {"ln", "rm", "unlink", "mv", "rmdir"}


def strip_heredocs(cmd):
    """Drop heredoc bodies: they are data for the command, not commands."""
    lines, out, i = cmd.split("\n"), [], 0
    while i < len(lines):
        line = lines[i]
        out.append(line)
        i += 1
        for m in _HEREDOC_RE.finditer(line):
            while i < len(lines) and lines[i].strip() != m.group(2):
                i += 1
            i += 1
    return "\n".join(out)


def tokenize(cmd):
    """[(text, is_operator)] with quotes removed. Quoted operator characters stay
    plain text, which shlex cannot tell apart. ValueError on an unterminated quote."""
    toks, cur, have, i, n = [], [], False, 0, len(cmd)

    def flush():
        nonlocal cur, have
        if have:
            toks.append(("".join(cur), False))
        cur, have = [], False

    while i < n:
        c = cmd[i]
        if c == "\\":
            if i + 1 < n and cmd[i + 1] != "\n":
                cur.append(cmd[i + 1])
                have = True
            i += 2
        elif c == "'":
            j = cmd.find("'", i + 1)
            if j < 0:
                raise ValueError("unterminated single quote")
            cur.append(cmd[i + 1:j])
            have, i = True, j + 1
        elif c == '"':
            j, buf = i + 1, []
            while j < n and cmd[j] != '"':
                if cmd[j] == "\\" and j + 1 < n and cmd[j + 1] in '"\\$`\n':
                    if cmd[j + 1] != "\n":
                        buf.append(cmd[j + 1])
                    j += 2
                else:
                    buf.append(cmd[j])
                    j += 1
            if j >= n:
                raise ValueError("unterminated double quote")
            cur.append("".join(buf))
            have, i = True, j + 1
        elif c == "\n":
            flush()
            toks.append((";", True))
            i += 1
        elif c in " \t\r":
            flush()
            i += 1
        elif c in _OPCHARS:
            flush()
            op = next(o for o in _OPS if cmd.startswith(o, i))
            toks.append((op, True))
            i += len(op)
        elif c == "#" and not have:
            j = cmd.find("\n", i)
            i = n if j < 0 else j
        else:
            cur.append(c)
            have = True
            i += 1
    flush()
    return toks


def _expand(tok, env, cwd):
    t = tok
    if t == "~" or t.startswith("~/"):
        t = HOME + t[1:]
    known = {"HOME": HOME, "PWD": cwd or ""}
    for k in ("TMPDIR", "FOREMAN_OUTPUT_DIR", "FOREMAN_SCRATCH_DIR", "FOREMAN_WORKTREE_ROOT"):
        known[k] = env.get(k) or ""
    return re.sub(r"\$\{?([A-Za-z_][A-Za-z0-9_]*)\}?",
                  lambda m: known[m.group(1)] if known.get(m.group(1)) else m.group(0), t)


def _split_opts(verb, words):
    """(positional args, {option: value}) for one command's words after the verb."""
    takes, args, opts, i, end = _VALUE_OPTS.get(verb, set()), [], {}, 0, False
    while i < len(words):
        w = words[i]
        if end or not w.startswith("-") or w == "-":
            args.append(w)
        elif w == "--":
            end = True
        elif "=" in w and w.startswith("--"):
            k, v = w.split("=", 1)
            opts[k] = v
        elif w in takes and i + 1 < len(words):
            opts[w] = words[i + 1]
            i += 1
        else:
            opts[w] = True
        i += 1
    return args, opts


def _command_dests(words):
    """Paths one simple command writes to, by its own argument convention."""
    while words and (_ASSIGN_RE.match(words[0]) or os.path.basename(words[0]) in _WRAPPERS
                     or (words[0].startswith("-") and len(words) > 1)):
        words = words[1:]
    if not words:
        return []
    verb = os.path.basename(words[0])
    args, opts = _split_opts(verb, words[1:])
    if verb in _LAST_ARG:
        target = opts.get("-t") or opts.get("--target-directory")
        if isinstance(target, str):
            return [target]
        if verb == "install" and opts.get("-d"):
            return args
        if len(args) >= 2:
            return [] if (verb == "rsync" and re.match(r"^[^/]+:", args[-1])) else [args[-1]]
        return ["."] if (verb == "ln" and args) else []
    if verb in _ALL_ARGS:
        return args
    if verb in _SKIP_FIRST:
        return args if "--reference" in opts else args[1:]
    if verb == "sed":
        if not any(o == "--in-place" or (o.startswith("-i") and not o.startswith("--"))
                   or (o.startswith("-") and not o.startswith("--") and "i" in o[1:]
                       and o not in ("-e", "-f", "-l")) for o in opts):
            return []
        scripted = any(o in opts for o in ("-e", "-f", "--expression", "--file"))
        return args if scripted else args[1:]
    if verb == "dd":
        return [w[3:] for w in words[1:] if w.startswith("of=")]
    return []


def _secret_reach(word, env, cwd, secrets):
    """True if `word`, read as a path, lands in the secrets dir: by resolution, or by
    a glob component that would expand to it (`/home/lumen/.sec*`)."""
    import fnmatch
    t = _expand(word, env, cwd)
    if not os.path.isabs(t):
        if cwd is None:
            return False
        t = os.path.join(cwd, t)
    parent, name = os.path.dirname(secrets), os.path.basename(secrets)
    parts, cur = [p for p in t.split("/") if p], "/"
    for p in parts:
        if _DYNAMIC_RE.search(p):
            return (os.path.realpath(cur) == parent and p.startswith(".")
                    and fnmatch.fnmatchcase(name, re.sub(r"[{}$`]", "*", p)))
        cur = os.path.join(cur, p)
        if os.path.realpath(cur) == secrets:
            return True
    return False


def _foreman_source(rp):
    root = os.path.realpath(FOREMAN_ROOT)
    if not _under(rp, root):
        return False
    return not any(_under(rp, os.path.join(root, d)) for d in FOREMAN_RUNTIME_DIRS)


def check_bash(cmd, env=None, cwd=None):
    """[(kind, target, reason)] with kind 'hard' (always blocks) or 'soft' (blocks
    under FOREMAN_HERMETIC_BASH=enforce, logged under shadow). [] if the command
    cannot be parsed: the regex walls in guard.sh still stand in front of it."""
    env = os.environ if env is None else env
    try:
        toks = tokenize(strip_heredocs(cmd))
    except ValueError:
        return []
    roots, secrets = write_roots(env), os.path.realpath(SECRETS_DIR)
    findings, seg, redirs = [], [], []

    def judge(dest, append=False, nofollow=False):
        t = _expand(dest, env, cwd)
        dyn = _DYNAMIC_RE.search(t)
        if dyn:                       # judge the literal directory prefix only
            t = t[:dyn.start()]
            t = t if t.endswith("/") else os.path.dirname(t)
            if not t:
                return
        if not os.path.isabs(t):
            if cwd is None:
                return
            t = os.path.join(cwd, t)
        if t in DEV_OK or t.startswith(DEV_OK_PREFIXES):
            return
        if nofollow and not dyn and os.path.basename(t.rstrip("/")):
            t = t.rstrip("/")
            rp = os.path.join(os.path.realpath(os.path.dirname(t)), os.path.basename(t))
        else:
            rp = os.path.realpath(t)
        if any(_under(rp, r) for r in roots) or own_pr_worktree(rp, env):
            return
        if _under(rp, secrets):
            findings.append(("hard", rp, f"write into the secrets dir ({rp}) denied (wq#4830)"))
        elif _foreman_source(rp):
            findings.append(("hard", rp,
                             f"write into the foreman tree ({rp}) denied (wq#4830): it is "
                             f"read-only for workers and changes only through lumen-pr. "
                             f"{_where(env)}"))
        elif append and rp == ISSUE_LOG:
            return                    # wq#1831: the issue log is appended to by doctrine
        elif not dyn:
            findings.append(("soft", rp, f"hermetic output contract (wq#4830): Bash writes "
                                         f"{rp}, outside this ticket's writable set. "
                                         f"{_where(env)}"))

    def close_segment():
        nonlocal seg, redirs, cwd
        words = seg
        for w in words:
            for cand in ([w] + ([w.split("=", 1)[1]] if "=" in w else [])):
                if cand and " " not in cand and _secret_reach(cand, env, cwd, secrets):
                    findings.append(("hard", cand, "path reaches the secrets dir "
                                     f"({SECRETS_DIR}) — denied (wq#616, wq#4830)"))
        for dest, app in redirs:
            judge(dest, app)
        verb = next((os.path.basename(w) for w in words if not _ASSIGN_RE.match(w)
                     and os.path.basename(w) not in _WRAPPERS and not w.startswith("-")), "")
        for dest in _command_dests(list(words)):
            judge(dest, nofollow=verb in _NOFOLLOW)
        if words and os.path.basename(words[0]) == "cd":
            target = _expand(words[1], env, cwd) if len(words) > 1 else HOME
            if _DYNAMIC_RE.search(target) or target == "-":
                cwd = None
            elif os.path.isabs(target):
                cwd = os.path.realpath(target)
            else:
                cwd = os.path.realpath(os.path.join(cwd, target)) if cwd else None
        seg, redirs = [], []

    i = 0
    while i < len(toks):
        text, is_op = toks[i]
        if not is_op:
            seg.append(text)
        elif text in _CONTROL:
            close_segment()
        elif text in _REDIR_OUT or text in _REDIR_IN:
            if seg and seg[-1].isdigit():
                seg.pop()             # the fd number of `2>file`
            nxt = toks[i + 1] if i + 1 < len(toks) else None
            if nxt and not nxt[1]:
                i += 1
                fd_dup = text == ">&" and (nxt[0].isdigit() or nxt[0] == "-")
                if text in _REDIR_OUT and not fd_dup:
                    redirs.append((nxt[0], text in (">>", "&>>")))
        i += 1
    close_segment()
    return findings


# ---- the PreToolUse hook (called from guard.sh) ------------------------------------

def _log_event(env, event, reason, snippet):
    """Same row shape as guard.sh's block(): one JSON line in the worker's event log."""
    try:
        d = env.get("FOREMAN_EVENTS_DIR") or os.path.join(FOREMAN_ROOT, "events")
        os.makedirs(d, exist_ok=True)
        row = {"ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
               "worker": env.get("FOREMAN_WORKER", "unknown"), "event": event,
               "reason": reason, "snippet": (snippet or "")[:200]}
        with open(os.path.join(d, f"{row['worker']}.jsonl"), "a") as f:
            f.write(json.dumps(row) + "\n")
    except OSError:
        pass                          # a shadow row must never decide the verdict


def hook(envelope, env=None):
    """(exit_code, reason). 0 allows, 2 blocks."""
    env = os.environ if env is None else env
    m = mode("FOREMAN_HERMETIC", env)
    if m == "off" or not contract_active(env):
        return 0, ""
    tool = envelope.get("tool_name") or ""
    ti = envelope.get("tool_input") or {}
    cwd = envelope.get("cwd") or env.get("FOREMAN_WORKTREE_ROOT") or None
    if tool in ("Write", "Edit", "NotebookEdit", "MultiEdit"):
        path = ti.get("file_path") or ti.get("notebook_path") or ""
        ok, reason, rp = check_write(tool, path, env, cwd)
        if ok:
            return 0, ""
        tree = primary_hit(rp, env) if rp else None
        if tree:                      # the wq#1719 wall, on the resolved path
            return 2, (f"{tool} target resolves to {rp}, inside PRIMARY tree {tree} "
                       f"(wq#1719, wq#4830): primary trees are read-only for workers. "
                       f"{_where(env)}")
        if m == "shadow":
            _log_event(env, "hermetic_shadow", reason, path)
            return 0, ""
        return 2, reason
    if tool == "Bash":
        bash_mode = mode("FOREMAN_HERMETIC_BASH", env)
        cmd = ti.get("command") or ""
        soft = None
        for kind, target, reason in check_bash(cmd, env, cwd):
            if kind == "hard":
                return 2, reason
            soft = soft or reason
        if soft and bash_mode == "enforce":
            return 2, soft
        if soft and bash_mode == "shadow":
            _log_event(env, "hermetic_shadow_bash", soft, cmd)
    return 0, ""


# ---- the mount layer (called from fworker.run_child) -------------------------------

# What a worker's sanctioned tools write besides its own output. `~` is /home/lumen.
# Missing paths are skipped. hermetic.json `mount.rw_extra` adds to this list.
DEFAULT_RW_STATE = (
    "~/.claude",                      # CLI session transcripts, todos, shell snapshots
    "~/.claude.json",                 # CLI project state
    "~/.cache", "~/.npm", "~/.local/state",
    "/home/lumen/agents/work-queue",  # wq claim / heartbeat / done (sqlite + journal)
)


def _git_common_dir(worktree):
    try:
        p = subprocess.run(["git", "-C", worktree, "rev-parse", "--path-format=absolute",
                            "--git-common-dir"], capture_output=True, text=True, timeout=10)
        d = p.stdout.strip()
        return d if p.returncode == 0 and os.path.isabs(d) and os.path.isdir(d) else None
    except Exception:
        return None


def mount_prefix(worktree, env=None, run_dir=None):
    """bwrap argv that runs a command with the hermetic filesystem view. Everything is
    read-only except: the writable set, the worktree's git common dir (the lumen-pr
    path: commits and refs, never the primary's working files), the events dir, this
    ticket's run dir and the state list. The secrets dir is an empty tmpfs. /tmp is
    private, with the wq scratch dir bound back in."""
    env = os.environ if env is None else env
    bwrap = shutil.which("bwrap")
    if not bwrap:
        raise HermeticError("FOREMAN_HERMETIC_MOUNT=enforce but bwrap is not installed")
    mcfg = _config().get("mount") or {}
    worktree = os.path.realpath(worktree)
    rw = list(write_roots(env))
    if worktree not in rw:
        rw.append(worktree)
    pr_root = os.path.realpath(env.get("LUMEN_PR_WORKTREE_ROOT") or LUMEN_PR_WORKTREE_ROOT)
    if os.path.isdir(pr_root):
        rw += [os.path.join(pr_root, n) for n in sorted(os.listdir(pr_root))
               if own_pr_worktree(os.path.join(pr_root, n), env)]
    common = _git_common_dir(worktree)
    state = list(DEFAULT_RW_STATE) + list(mcfg.get("rw_extra") or [])
    state += [env.get("FOREMAN_EVENTS_DIR") or os.path.join(FOREMAN_ROOT, "events")]
    state += [p for p in (common, run_dir) if p]
    for p in state:
        p = os.path.realpath(HOME + p[1:] if p.startswith("~") else p)
        if os.path.exists(p) and p not in rw and p not in TOO_BROAD:
            rw.append(p)
    secrets = os.path.realpath(SECRETS_DIR)
    hide = [secrets] + [os.path.realpath(p) for p in (mcfg.get("hide") or [])]
    bad = [p for p in rw if any(_under(p, h) or _under(h, p) for h in hide)]
    if bad:
        raise HermeticError(f"a writable path overlaps a hidden one: {bad}")
    argv = [bwrap, "--die-with-parent", "--ro-bind", "/", "/", "--dev", "/dev",
            "--proc", "/proc", "--tmpfs", "/tmp"]
    for p in sorted(rw, key=lambda x: x.count(os.sep)):
        argv += ["--bind", p, p]
    for h in hide:
        if os.path.isdir(h):
            argv += ["--tmpfs", h]
        elif os.path.exists(h):
            argv += ["--ro-bind", "/dev/null", h]
    return argv + ["--chdir", worktree, "--"]


def wrap(cmd, worktree, env=None, run_dir=None):
    """The child argv fworker should spawn: unchanged unless the mount layer is on."""
    if mode("FOREMAN_HERMETIC_MOUNT", env) != "enforce":
        return list(cmd)
    return mount_prefix(worktree, env, run_dir) + list(cmd)


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if argv[:1] == ["hook"]:
        try:
            code, reason = hook(json.loads(sys.stdin.read()))
        except Exception as e:        # fail-closed: guard.sh blocks on any non-zero
            print(f"hermetic.py hook error: {e!r}", file=sys.stderr)
            return 3
        if code:
            print(reason, file=sys.stderr)
        return code
    if argv[:1] == ["status"]:
        print(json.dumps({"modes": settings(), "output_root": output_root(),
                          "config": CONFIG_PATH, "roots": write_roots(),
                          "bwrap": shutil.which("bwrap")}, indent=1))
        return 0
    if argv[:1] == ["mount-argv"] and len(argv) == 2:
        print(" ".join(shlex.quote(a) for a in mount_prefix(argv[1])))
        return 0
    print("usage: hermetic.py hook | status | mount-argv <worktree>", file=sys.stderr)
    return 64


if __name__ == "__main__":
    sys.exit(main())
