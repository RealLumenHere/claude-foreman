#!/usr/bin/env python3
"""leakwatch — real-time credential-leak detection on known sensitive files (wq#3354
class 3).

lumen-mask-at-rest sweeps every CC transcript nightly (1h38m, OOM-prone, wq#3207). The
files that actually hold or attract credentials are a handful, and waiting up to a day
to notice a key pasted into one of them is the gap. This daemon puts inotify watches on
their DIRECTORIES (so a new `.env.bak.*` or a rename-into-place is seen too) and checks
a matching file the moment a write closes:

  rule action `chmod` (secret stores: .env*, claude_credentials.md*, config backups) —
      the file is SUPPOSED to hold secrets; the leak is its mode. World access is
      stripped (o-rwx) with an aq row; group access is only reported, once, because a
      service may legitimately read it through its group.
  rule action `scan` (claude_memory.md, litellm configs) — the file must hold no
      literal credential. Run harness/masking.scan line by line; a hit files one
      rotation ticket + aq row + page naming the label and sha12 (never the value).
      The file is NOT edited: instinct 7 forbids auto-editing claude_memory.md, a
      masked litellm config would break the rung it names, and a credential that hit
      disk is burned either way (rotation is the fix, not redaction).

Every matching file is checked once at start-up, so a leak that landed while the
daemon was down is still caught. Dedup: one ticket per (path, set of sha12).

    leakwatch.py [--config selfheal.yaml] [--once] [--dry-run]
"""
import argparse
import ctypes
import ctypes.util
import fnmatch
import os
import select
import stat
import struct
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import selfheal_common as common  # noqa: E402

CLS = "leakwatch"
IN_ATTRIB, IN_CLOSE_WRITE, IN_MOVED_TO, IN_CREATE = 0x4, 0x8, 0x80, 0x100
MASK = IN_ATTRIB | IN_CLOSE_WRITE | IN_MOVED_TO | IN_CREATE
EVENT_HDR = struct.Struct("iIII")
DEBOUNCE_S = 2.0


class Inotify:
    """Directory watches over libc inotify; `read()` yields (dir, name) per event."""

    def __init__(self):
        self.libc = ctypes.CDLL(ctypes.util.find_library("c"), use_errno=True)
        self.fd = self.libc.inotify_init1(os.O_NONBLOCK | os.O_CLOEXEC)
        if self.fd < 0:
            raise OSError(ctypes.get_errno(), "inotify_init1 failed")
        self.wds = {}

    def watch(self, path):
        wd = self.libc.inotify_add_watch(self.fd, os.fsencode(path), MASK)
        if wd < 0:
            raise OSError(ctypes.get_errno(), f"inotify_add_watch {path} failed")
        self.wds[wd] = path

    def read(self, timeout):
        if not select.select([self.fd], [], [], timeout)[0]:
            return []
        try:
            buf = os.read(self.fd, 64 * 1024)
        except BlockingIOError:
            return []
        out, i = [], 0
        while i + EVENT_HDR.size <= len(buf):
            wd, _mask, _cookie, length = EVENT_HDR.unpack_from(buf, i)
            name = buf[i + EVENT_HDR.size:i + EVENT_HDR.size + length].rstrip(b"\0")
            i += EVENT_HDR.size + length
            if wd in self.wds and name:
                out.append((self.wds[wd], os.fsdecode(name)))
        return out


def rule_for(path, rules):
    d, name = os.path.split(path)
    for r in rules:
        if os.path.realpath(r["dir"]) == os.path.realpath(d) and \
                any(fnmatch.fnmatch(name, g) for g in r["globs"]):
            return r
    return None


def scan_hits(path, scanner):
    """[(label, sha12)] for literal credentials in `path`."""
    hits = []
    with open(path, errors="replace") as f:
        for line in f:
            for rec in scanner(line)[1]:
                if not rec.get("suppressed"):
                    hits.append((f"{rec.get('label')} {rec.get('prefix') or ''}".strip(),
                                 str(rec.get("value_sha256_12") or "?")))
    return sorted(set(hits))


def check(ctx, path, rule, scanner):
    try:
        st = os.stat(path)
    except FileNotFoundError:
        return
    if not stat.S_ISREG(st.st_mode):
        return
    if rule["action"] == "chmod":
        mode = st.st_mode & 0o777
        if mode & 0o007:
            ctx.act(CLS, f"chmod o-rwx {path} (was {oct(mode)})",
                    os.chmod, path, mode & ~0o007)
            if not ctx.dry_run:
                ctx.out.alert(f"leak-mode:{path}", f"removed world access to {path}",
                              f"secret store was {oct(mode)}, readable by any local user")
        elif mode & 0o070 and path not in ctx.section("leakwatch_group"):
            ctx.section("leakwatch_group")[path] = oct(mode)
            ctx.note(CLS, f"{path} is group-accessible ({oct(mode)}); alert only")
            if not ctx.dry_run:
                ctx.out.alert(f"leak-mode:{path}", f"{path} is group-accessible",
                              f"secret store is {oct(mode)}. Left as is in case a service "
                              f"reads it through its group; chmod 600 it if nothing does.")
        return
    hits = scan_hits(path, scanner)
    seen = ctx.section("leakwatch")
    sig = ",".join(sha for _, sha in hits)
    if not hits:
        if seen.pop(path, None):
            ctx.resolve(CLS, f"leak:{path}", f"{path} no longer holds a literal credential")
        return
    if seen.get(path) == sig:
        return
    seen[path] = sig
    ctx.resolve(CLS, f"leak:{path}")    # a new set of values is a new episode
    listing = "\n".join(f"  {label}  sha12={sha}" for label, sha in hits)
    ctx.file_once(CLS, f"leak:{path}",
                  f"[selfheal] rotate: {len(hits)} literal credential(s) in "
                  f"{os.path.basename(path)}",
                  f"leakwatch found literal credentials in {path}:\n{listing}\n"
                  f"Treat each as burned: rotate it at the provider, replace the literal "
                  f"with a pointer to its 0600 store; leakwatch closes this episode on "
                  f"the first clean re-scan after the file is saved. The file was not "
                  f"edited (instinct 7 / config safety).",
                  page="urgent")


def load_rules(cfg):
    rules = []
    for r in cfg.get("leakwatch", {}).get("rules", []):
        if os.path.isdir(r["dir"]):
            rules.append({"dir": r["dir"], "globs": list(r["globs"]),
                          "action": r.get("action", "scan")})
    return rules


def initial_sweep(ctx, rules, scanner):
    for r in rules:
        for name in sorted(os.listdir(r["dir"])):
            if any(fnmatch.fnmatch(name, g) for g in r["globs"]):
                check(ctx, os.path.join(r["dir"], name), r, scanner)


def _scanner(cfg):
    sys.path.insert(0, cfg.get("leakwatch", {}).get("harness_dir",
                                                     "/home/lumen/foreman/harness"))
    import masking
    return masking.scan


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--config", default=common.CONFIG_PATH)
    ap.add_argument("--state", default=os.environ.get(
        "LEAKWATCH_STATE", "/home/lumen/agents/fleet-selfheal/leakwatch-state.json"))
    ap.add_argument("--once", action="store_true", help="start-up sweep only, then exit")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)
    cfg = common.load_config(a.config)
    rules = load_rules(cfg)
    scanner = _scanner(cfg)
    state = common.load_state(a.state)

    def flush(ctx):
        for line in ctx.actions:
            print(line, flush=True)
        ctx.actions.clear()
        if not a.dry_run:
            common.save_state(state, a.state)

    ctx = common.Ctx(cfg, state, dry_run=a.dry_run)
    initial_sweep(ctx, rules, scanner)
    flush(ctx)
    if a.once:
        return 0
    ino = Inotify()
    for r in rules:
        ino.watch(r["dir"])
    print(f"leakwatch: watching {len(rules)} dirs", flush=True)
    pending = {}
    while True:
        for d, name in ino.read(timeout=DEBOUNCE_S):
            path = os.path.join(d, name)
            if rule_for(path, rules):
                pending[path] = time.time()
        due = [p for p, t in pending.items() if time.time() - t >= DEBOUNCE_S]
        if due:
            ctx = common.Ctx(cfg, state, dry_run=a.dry_run)
            for path in due:
                pending.pop(path)
                check(ctx, path, rule_for(path, rules), scanner)
            flush(ctx)


if __name__ == "__main__":
    sys.exit(main())
