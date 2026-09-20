#!/usr/bin/env python3
r"""Teach `wq` and `slock` a read-only subsystem mode: `<name>:ro` (wq#1675).

THE BLOCK. wq's subsystem lock is mode-blind. `subs_free` asks one question — "is scope
`foreman` held by someone who is not me?" — and a ticket that only READS `foreman` answers
it the same way a ticket that REWRITES `foreman` does. So wq#1668 (ticket lifecycle +
closure-quality audit, archetype research-synthesis, subsystems `foreman`) could not be
claimed while wq#1667 (foreman dispatch walkthrough, same archetype, same subsystem) held
it, even though neither ticket ever wrote a byte of foreman. Two analyses of one subsystem
serialize for no reason. Same class as the #569/#571 minerva-db block.

THE MODE. A declared subsystem token is now `<base>`, `<base>:rw` or `<base>:ro`.
  - bare / `:rw`  EXCLUSIVE, exactly what wq has always taken. Every subsystems value in the
                  live DB is bare, so no existing ticket changes behaviour.
  - `:ro`         SHARED. Any number of readers hold one base at once. A reader is refused
                  only by a live WRITER; a writer is refused by a live writer OR by any live
                  reader. That is the "many readers, one writer, writer excluded while
                  readers hold" rule the ticket asks for.

WHERE READERS LIVE. `slocks` is keyed `scope TEXT PRIMARY KEY` and is shared with bin/slock,
queue-status, mctl and lumen-sqlutil — a reader SET cannot be expressed in one row, and
widening that key would change the table every one of those tools reads. So readers go in a
new ADDITIVE table `slocks_ro(scope, holder, ...)` keyed `(scope, holder)`. Writers keep
using `slocks` untouched, which is why bin/slock's own exclusive locking still works with no
change at all; slock is patched here only so that ITS writer path also sees readers (S2) and
`slock who` can name them (S3). Nothing else reads the new table, so no other consumer of
queue.db is affected.

DEFAULT AT ADD. `wq add --archetype research-synthesis|verify-pass` records its subsystems as
`:ro`, because the whole output of those archetypes is a report — they read a subsystem and
write a document. An explicit `:ro`/`:rw` on the command line is never rewritten, and add
prints a NOTE naming what it did. The opt-out matters: this very ticket (wq#1675) is
archetype research-synthesis and DOES write wq-tooling, so the orchestrator needs a one-flag
way to say "this analysis ticket writes" — that flag is `--subsystems wq-tooling:rw`.

WHAT IS NOT CHANGED. Ticket leases, fencing, claim atomicity, the ORCH_PREFIX `orch:<lane>`
scopes (`split_sub` reads `orch:build` as a whole base name, not as base `orch` with mode
`build`, so the orchestrator lock is untouched), and `wq edit --subsystems`, which has never
validated the canonical vocabulary and still does not.

Same keeper shape as ops/wq1498-dead-worker-release and ops/wq1479-close-verdict: both
binaries are rewritten in place by anchored string patches, each anchor is asserted to match
exactly once, the candidates are proven on a scratch queue.db before they replace anything,
and a failed post-apply verification rolls both files back.

    ensure_wq_subsystem_ro.py --check [--json]   are the live wq/slock patched?
    ensure_wq_subsystem_ro.py --apply            patch them (backup, verify, roll back on failure)
"""
import argparse
import json
import os
import py_compile
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time

LIVE_WQ = os.environ.get("WQ_BIN", "/home/lumen/bin/wq")
LIVE_SLOCK = os.environ.get("SLOCK_BIN", "/home/lumen/bin/slock")

# ---------------------------------------------------------------- wq patches

SUB_MODE_HELPERS = r'''
# wq#1675: subsystem MODE. A declared subsystem is '<base>', '<base>:rw' or '<base>:ro'.
# Bare and ':rw' are the EXCLUSIVE lock wq has always taken (one holder, everyone else
# blocked) — every subsystems value in the live DB is bare, so nothing about those tickets
# changes. ':ro' is a SHARED lock: any number of readers hold one base at once, and a writer
# is refused for as long as any live reader holds it. wq#1668 (read-only closure audit) sat
# unclaimable behind wq#1667 (read-only walkthrough) because both declared `foreman` and the
# lock could not tell two readers apart from two writers.
SUB_MODES = ("rw", "ro")
def split_sub(tok):
    """'<base>:ro' -> ('<base>', 'ro'). Bare, or any other suffix, -> (tok, 'rw'). An
    unrecognised suffix is deliberately NOT an error: the orchestrator's own scopes are
    'orch:<lane>', and reading 'orch:build' as base 'orch' would merge every lane's
    orchestrator lock into one."""
    base, _, mode = (tok or "").rpartition(":")
    if base and mode in SUB_MODES:
        return base, mode
    return tok, "rw"
def sub_base(tok): return split_sub(tok)[0]
def is_ro(tok):    return split_sub(tok)[1] == "ro"
def has_explicit_mode(tok): return split_sub(tok)[0] != tok
# wq#1675: archetypes whose entire output is analysis — they read a subsystem and write a
# report, never the subsystem itself. `wq add` records their subsystems as ':ro' so two of
# them never serialize behind each other. Explicit ':rw' opts out and is never rewritten.
RO_DEFAULT_ARCHETYPES = {"research-synthesis", "verify-pass"}
'''

SUB_LOCK_FNS = r'''def ro_holders(c, base, t_now, exclude=None):
    """wq#1675: live readers on a base scope, minus `exclude` (normally the caller, whose own
    read hold must never block its own writer claim on the same base)."""
    return [h for (h,) in c.execute(
        "SELECT holder FROM slocks_ro WHERE scope=? AND expires_at >= ?", (base, t_now))
        if h != exclude]
def subs_free(c, subs, holder, t_now):
    """Which declared subsystems are held by a DIFFERENT holder (blocking). Expired = free.
    wq#1675: a ':ro' token is blocked by a live WRITER only; a writer token is blocked by a
    live writer OR by any live reader. The returned scope is the DECLARED token, not the
    base, so the refusal names the mode the ticket actually asked for."""
    blocked = []
    for s in subs:
        base = sub_base(s)
        r = c.execute("SELECT holder,expires_at FROM slocks WHERE scope=?", (base,)).fetchone()
        if r and r[1] >= t_now and r[0] != holder:
            blocked.append((s, r[0]))
            continue
        if not is_ro(s):
            readers = ro_holders(c, base, t_now, exclude=holder)
            if readers:
                more = f" +{len(readers) - 1} more" if len(readers) > 1 else ""
                blocked.append((s, f"{readers[0]}{more} (read-only)"))
    return blocked
def acquire_subs(c, subs, holder, ttl_min, note):
    exp = lease_from(ttl_min)
    for s in subs:
        base, mode = split_sub(s)
        if mode == "ro":
            # wq#1675: keyed (scope,holder), so a second reader ADDS a row instead of
            # stealing the first one's. Re-acquiring is how heartbeat extends a read hold.
            c.execute("""INSERT INTO slocks_ro(scope,holder,acquired_at,expires_at,note)
                         VALUES(?,?,?,?,?)
                         ON CONFLICT(scope,holder) DO UPDATE SET
                           expires_at=excluded.expires_at, note=excluded.note""",
                      (base, holder, now(), exp, note))
            continue
        c.execute("""INSERT INTO slocks(scope,holder,acquired_at,expires_at,note)
                     VALUES(?,?,?,?,?)
                     ON CONFLICT(scope) DO UPDATE SET holder=excluded.holder,
                       expires_at=excluded.expires_at, note=excluded.note""",
                  (base, holder, now(), exp, note))
def release_subs(c, subs, holder):
    """wq#1675: drops BOTH modes for every declared base. A ticket whose subsystems were
    edited from ':ro' to ':rw' mid-claim would otherwise leave the hold it originally took
    behind to expire on its own, blocking the base for a full TTL."""
    for s in subs:
        base = sub_base(s)
        c.execute("DELETE FROM slocks WHERE scope=? AND holder=?", (base, holder))
        c.execute("DELETE FROM slocks_ro WHERE scope=? AND holder=?", (base, holder))
'''

CMD_ADD_HEAD = r'''def cmd_add(a):
    subs = parse_subs(a.subsystems)
    # wq#1675: validate on the BASE — ':ro'/':rw' is a mode, not new vocabulary.
    bad = [s for s in subs if sub_base(s) not in CANON_SUBSYSTEMS]
    if bad and not a.force:
        print(f"REFUSED: non-canonical subsystem(s): {', '.join(bad)}")
        print(f"Canonical set: {', '.join(sorted(CANON_SUBSYSTEMS))}\nUse --force to override (discouraged).")
        sys.exit(1)
    # wq#1675: an analysis archetype reads its subsystem and writes a report, so record the
    # read intent. Without this the NEXT analysis ticket on the same subsystem queues behind
    # this one for a full lease (wq#1668 behind wq#1667). Explicit ':ro'/':rw' always wins.
    ro_defaulted = []
    if (a.archetype or "") in RO_DEFAULT_ARCHETYPES:
        widened = []
        for s in subs:
            if has_explicit_mode(s):
                widened.append(s)
            else:
                widened.append(s + ":ro"); ro_defaulted.append(s)
        subs = widened
    if ro_defaulted:
        print(f"NOTE (wq#1675): archetype {a.archetype} is read-only, so "
              f"{', '.join(ro_defaulted)} recorded as shared (:ro) — other analysis tickets "
              f"may hold the same subsystem at the same time, and any writer is refused while "
              f"they do. Pass '<name>:rw' if this ticket will actually write the subsystem.")
'''

WQ_PATCHES = [
    ("doc-header",
     "queue-db, messenger, research-index, fleet-ssh, cloudflared, fleet-state, wq-tooling.\n",
     "queue-db, messenger, research-index, fleet-ssh, cloudflared, fleet-state, wq-tooling.\n"
     "wq#1675 SUBSYSTEM MODE (2026-09-11): a declared subsystem may carry ':ro' (shared) or\n"
     "':rw' (exclusive, the default and the only prior behaviour). Many ':ro' holders coexist on\n"
     "one base; a ':rw' claim is refused while any live reader holds it. research-synthesis and\n"
     "verify-pass tickets get ':ro' at add unless the mode is given explicitly.\n",
     1),
    ("sub-mode-helpers",
     'def parse_subs(s):\n'
     '    return [x.strip().lower() for x in (s or "").replace(",", " ").split() if x.strip()]\n',
     'def parse_subs(s):\n'
     '    return [x.strip().lower() for x in (s or "").replace(",", " ").split() if x.strip()]\n'
     + SUB_MODE_HELPERS.lstrip("\n"),
     1),
    ("slocks-ro-table",
     '    c.execute("""CREATE TABLE IF NOT EXISTS slocks(\n'
     "        scope TEXT PRIMARY KEY, holder TEXT, acquired_at TEXT,\n"
     "        expires_at TEXT, note TEXT DEFAULT '')\"\"\")\n",
     '    c.execute("""CREATE TABLE IF NOT EXISTS slocks(\n'
     "        scope TEXT PRIMARY KEY, holder TEXT, acquired_at TEXT,\n"
     "        expires_at TEXT, note TEXT DEFAULT '')\"\"\")\n"
     "    # wq#1675: SHARED (read-only) subsystem holds. A separate table because `slocks` is\n"
     "    # keyed scope-unique by design and shared with bin/slock — a reader SET cannot live in\n"
     "    # one row, and widening that key would change a table four other tools read. ADDITIVE:\n"
     "    # only wq and slock's writer check read this, so queue-status/mctl/lumen-sqlutil are\n"
     "    # untouched.\n"
     '    c.execute("""CREATE TABLE IF NOT EXISTS slocks_ro(\n'
     "        scope TEXT NOT NULL, holder TEXT NOT NULL, acquired_at TEXT,\n"
     "        expires_at TEXT, note TEXT DEFAULT '', PRIMARY KEY(scope,holder))\"\"\")\n",
     1),
    ("sub-lock-fns",
     'def subs_free(c, subs, holder, t_now):\n'
     '    """Which declared subsystems are held by a DIFFERENT holder (blocking). Expired = free."""\n'
     "    blocked = []\n"
     "    for s in subs:\n"
     '        r = c.execute("SELECT holder,expires_at FROM slocks WHERE scope=?", (s,)).fetchone()\n'
     "        if r and r[1] >= t_now and r[0] != holder:\n"
     "            blocked.append((s, r[0]))\n"
     "    return blocked\n"
     "def acquire_subs(c, subs, holder, ttl_min, note):\n"
     "    exp = lease_from(ttl_min)\n"
     "    for s in subs:\n"
     '        c.execute("""INSERT INTO slocks(scope,holder,acquired_at,expires_at,note)\n'
     "                     VALUES(?,?,?,?,?)\n"
     "                     ON CONFLICT(scope) DO UPDATE SET holder=excluded.holder,\n"
     '                       expires_at=excluded.expires_at, note=excluded.note""",\n'
     "                  (s, holder, now(), exp, note))\n"
     "def release_subs(c, subs, holder):\n"
     "    for s in subs:\n"
     '        c.execute("DELETE FROM slocks WHERE scope=? AND holder=?", (s, holder))\n',
     SUB_LOCK_FNS,
     1),
    ("session-holdings-readers",
     "    locks = [r[0] for r in c.execute(\n"
     '        "SELECT scope FROM slocks WHERE holder=? AND expires_at >= ?", (sid, t_now))]\n'
     "    return held, locks\n",
     "    locks = [r[0] for r in c.execute(\n"
     '        "SELECT scope FROM slocks WHERE holder=? AND expires_at >= ?", (sid, t_now))]\n'
     "    # wq#1675: a reader holds just as much as a writer. Omitting it here would let the\n"
     "    # never-purge-a-holder rule and every refusal that names a blocking holder miss a live\n"
     "    # read-only session.\n"
     '    locks += [r[0] + ":ro" for r in c.execute(\n'
     '        "SELECT scope FROM slocks_ro WHERE holder=? AND expires_at >= ?", (sid, t_now))]\n'
     "    return held, locks\n",
     1),
    ("retire-drops-readers",
     '    locks = [r[0] for r in c.execute("SELECT scope FROM slocks WHERE holder=?", (sid,))]\n'
     "    if locks:\n"
     '        c.execute("DELETE FROM slocks WHERE holder=?", (sid,))\n'
     "    return tickets, locks\n",
     '    locks = [r[0] for r in c.execute("SELECT scope FROM slocks WHERE holder=?", (sid,))]\n'
     "    if locks:\n"
     '        c.execute("DELETE FROM slocks WHERE holder=?", (sid,))\n'
     "    # wq#1675: retirement drops read holds too, or a retired session leaves every base it\n"
     "    # was reading blocked against writers until the TTL runs out.\n"
     '    ro = [r[0] + ":ro" for r in c.execute("SELECT scope FROM slocks_ro WHERE holder=?", (sid,))]\n'
     "    if ro:\n"
     '        c.execute("DELETE FROM slocks_ro WHERE holder=?", (sid,))\n'
     "    return tickets, locks + ro\n",
     1),
    ("purge-keeps-readers",
     '                 AND session_id NOT IN (SELECT COALESCE(holder,\'\') FROM slocks WHERE expires_at >= ?)""",\n'
     "              (cutoff, t_now, t_now))\n",
     "                 AND session_id NOT IN (SELECT COALESCE(holder,'') FROM slocks WHERE expires_at >= ?)\n"
     "                 AND session_id NOT IN (SELECT COALESCE(holder,'') FROM slocks_ro "
     'WHERE expires_at >= ?)""",\n'
     "              (cutoff, t_now, t_now, t_now))  # wq#1675: a live reader is a holder\n",
     1),
    ("live-sessions-readers",
     '        locks = c.execute("SELECT scope FROM slocks WHERE holder=? AND expires_at >= ?", (sid, t_now)).fetchall()\n',
     '        locks = c.execute("SELECT scope FROM slocks WHERE holder=? AND expires_at >= ?", (sid, t_now)).fetchall()\n'
     '        locks += c.execute("SELECT scope || \':ro\' FROM slocks_ro WHERE holder=? AND expires_at >= ?",\n'
     "                           (sid, t_now)).fetchall()  # wq#1675\n",
     1),
    ("add-ro-default",
     "def cmd_add(a):\n"
     "    subs = parse_subs(a.subsystems)\n"
     "    bad = [s for s in subs if s not in CANON_SUBSYSTEMS]\n"
     "    if bad and not a.force:\n"
     "        print(f\"REFUSED: non-canonical subsystem(s): {', '.join(bad)}\")\n"
     "        print(f\"Canonical set: {', '.join(sorted(CANON_SUBSYSTEMS))}\\nUse --force to override (discouraged).\")\n"
     "        sys.exit(1)\n",
     CMD_ADD_HEAD,
     1),
]

# ---------------------------------------------------------------- slock patches

SLOCK_WHO = r'''def cmd_who(a):
    c = conn(); c.isolation_level = None
    c.execute("BEGIN IMMEDIATE")
    t_now = now()
    c.execute("DELETE FROM slocks WHERE expires_at < ?", (t_now,))
    c.execute("DELETE FROM slocks_ro WHERE expires_at < ?", (t_now,))  # wq#1675
    q, args = "SELECT scope,holder,acquired_at,expires_at,note,fence FROM slocks", ()
    rq = "SELECT scope,holder,acquired_at,expires_at,note FROM slocks_ro"
    if a.scope:
        q += " WHERE scope=?"; rq += " WHERE scope=?"; args = (a.scope.strip().lower(),)
    rows = c.execute(q + " ORDER BY scope", args).fetchall()
    ro_rows = c.execute(rq + " ORDER BY scope,holder", args).fetchall()
    c.execute("COMMIT"); c.close()
    if not rows and not ro_rows: print("(no active locks)"); return
    c2 = conn()
    for r in rows:
        t = ticket_for_holder(c2, r[1], r[4] or '')
        ctx = f"  wq#{t[0]} [{t[1]}/p{t[2]}/{t[3]}]" if t else "  (no ticket)"
        print(f"{r[0]:<20} {r[1]:<28} acquired {r[2]}Z expires {r[3]}Z FENCE={r[5] or 0}{ctx}"
              + (f"  # {r[4]}" if r[4] else ""))
        if t: print(f"{'':<20} {t[4][:76]}")
    # wq#1675: read holds. Several may share one scope; each still blocks a writer.
    for r in ro_rows:
        t = ticket_for_holder(c2, r[1], r[4] or '')
        ctx = f"  wq#{t[0]} [{t[1]}/p{t[2]}/{t[3]}]" if t else "  (no ticket)"
        print(f"{r[0] + ':ro':<20} {r[1]:<28} acquired {r[2]}Z expires {r[3]}Z READ-ONLY{ctx}"
              + (f"  # {r[4]}" if r[4] else ""))
        if t: print(f"{'':<20} {t[4][:76]}")
    c2.close()
'''

SLOCK_PATCHES = [
    ("slocks-ro-table",
     '    c.execute("""CREATE TABLE IF NOT EXISTS slocks(\n'
     "        scope TEXT PRIMARY KEY, holder TEXT, acquired_at TEXT,\n"
     "        expires_at TEXT, note TEXT DEFAULT '')\"\"\")\n",
     '    c.execute("""CREATE TABLE IF NOT EXISTS slocks(\n'
     "        scope TEXT PRIMARY KEY, holder TEXT, acquired_at TEXT,\n"
     "        expires_at TEXT, note TEXT DEFAULT '')\"\"\")\n"
     "    # wq#1675: wq records SHARED subsystem holds here. slock never takes one — it is an\n"
     "    # exclusive-lock tool — but its acquire must SEE them, so the table is created here too\n"
     "    # for the case where slock reaches a fresh queue.db first.\n"
     '    c.execute("""CREATE TABLE IF NOT EXISTS slocks_ro(\n'
     "        scope TEXT NOT NULL, holder TEXT NOT NULL, acquired_at TEXT,\n"
     "        expires_at TEXT, note TEXT DEFAULT '', PRIMARY KEY(scope,holder))\"\"\")\n",
     1),
    ("acquire-refused-by-readers",
     '        print(f"DENIED: \'{scope}\' held by {r[0]} until {r[1]}Z" + (f" — {r[2]}" if r[2] else ""))\n'
     '        print("Do NOT touch this subsystem. Coordinate or wait for expiry.")\n'
     "        sys.exit(1)\n",
     '        print(f"DENIED: \'{scope}\' held by {r[0]} until {r[1]}Z" + (f" — {r[2]}" if r[2] else ""))\n'
     '        print("Do NOT touch this subsystem. Coordinate or wait for expiry.")\n'
     "        sys.exit(1)\n"
     "    # wq#1675: slock takes EXCLUSIVE holds only, and a writer is excluded while any wq\n"
     "    # read hold is live. Without this a `<name>:ro` ticket and a slock writer would run on\n"
     "    # one subsystem at once — the exact overlap the shared mode exists to forbid.\n"
     "    readers = [(h, e) for h, e in c.execute(\n"
     '        "SELECT holder,expires_at FROM slocks_ro WHERE scope=? AND expires_at >= ?",\n'
     "        (scope, t_now)) if h != holder]\n"
     "    if readers:\n"
     '        c.execute("COMMIT"); c.close()\n'
     "        print(f\"DENIED: '{scope}' has {len(readers)} live read-only holder(s) (wq#1675): \"\n"
     '              + ", ".join(f"{h} until {e}Z" for h, e in readers[:4]))\n'
     '        print("They are reading this subsystem. Wait for expiry, or coordinate — an\\n"\n'
     '              "exclusive hold taken now would write under them.")\n'
     "        sys.exit(1)\n",
     1),
    ("who-shows-readers",
     "def cmd_who(a):\n"
     "    c = conn(); c.isolation_level = None\n"
     '    c.execute("BEGIN IMMEDIATE")\n'
     '    c.execute("DELETE FROM slocks WHERE expires_at < ?", (now(),))\n'
     '    q, args = "SELECT scope,holder,acquired_at,expires_at,note,fence FROM slocks", ()\n'
     '    if a.scope: q += " WHERE scope=?"; args = (a.scope.strip().lower(),)\n'
     '    rows = c.execute(q + " ORDER BY scope", args).fetchall()\n'
     '    c.execute("COMMIT"); c.close()\n'
     '    if not rows: print("(no active locks)"); return\n'
     "    c2 = conn()\n"
     "    for r in rows:\n"
     "        t = ticket_for_holder(c2, r[1], r[4] or '')\n"
     '        ctx = f"  wq#{t[0]} [{t[1]}/p{t[2]}/{t[3]}]" if t else "  (no ticket)"\n'
     '        print(f"{r[0]:<20} {r[1]:<28} acquired {r[2]}Z expires {r[3]}Z FENCE={r[5] or 0}{ctx}"\n'
     '              + (f"  # {r[4]}" if r[4] else ""))\n'
     "        if t: print(f\"{'':<20} {t[4][:76]}\")\n"
     "    c2.close()\n",
     SLOCK_WHO,
     1),
]

TARGETS = [("wq", LIVE_WQ, WQ_PATCHES), ("slock", LIVE_SLOCK, SLOCK_PATCHES)]


def read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def status(src, patches):
    """Per-patch: applied / pending / MISSING-ANCHOR (the target moved and the old text is gone)."""
    out = {}
    for name, old, new, n in patches:
        if src.count(new) >= n:
            out[name] = "applied"
        elif src.count(old) == n:
            out[name] = "pending"
        else:
            out[name] = f"MISSING-ANCHOR (old={src.count(old)} new={src.count(new)} want={n})"
    return out


def apply_to(src, patches, label):
    for name, old, new, n in patches:
        if src.count(new) >= n:
            continue
        if src.count(old) != n:
            raise RuntimeError(f"anchor for {label}:{name!r} matched {src.count(old)} times, "
                               f"want {n} — {label} has moved; re-derive the patch before applying")
        src = src.replace(old, new)
    return src


def verify(cand_wq, cand_slock):
    """Prove both candidates on a scratch queue.db before either goes near a live binary:
    two readers coexist, a writer is refused under them, a reader is refused under a writer,
    a bare subsystem is still exclusive, the add-time default lands, ':rw' opts out, and
    slock's exclusive acquire is refused while a wq reader holds the base."""
    py_compile.compile(cand_wq, doraise=True)
    py_compile.compile(cand_slock, doraise=True)
    d = tempfile.mkdtemp(prefix="wq1675-verify")
    env = dict(os.environ)
    env["WQ_DB"] = os.path.join(d, "q.db")
    env["WQ_FOREMAN_ROUTER"] = "/nonexistent/wq2144/router.py"  # wq#2139 lane guard fails open

    def run(binary, *args, agent=None):
        e = dict(env)
        if agent is not None:
            e["WQ_AGENT"] = agent
        else:
            e.pop("WQ_AGENT", None)
        return subprocess.run([sys.executable, binary, *args], capture_output=True, text=True, env=e)

    def wq(*args, agent=None):
        return run(cand_wq, *args, agent=agent)

    def slock(*args, agent=None):
        return run(cand_slock, *args, agent=agent)

    def add(title, *extra):
        p = wq("add", "--lane", "auto", "--title", title, *extra, agent="wq1675-verify")
        # The ':ro' NOTE prints BEFORE the confirmation, so find the line, never stdout[0].
        line = next((l for l in p.stdout.splitlines() if l.startswith("added task ")), None)
        if p.returncode != 0 or not line:
            raise RuntimeError(f"scratch add failed: {p.stdout}{p.stderr}")
        return line.split()[2], p.stdout

    def claim(tid, holder):
        return wq("claim", holder, "--id", str(tid), agent=holder)

    def subs_of(tid):
        c = sqlite3.connect(env["WQ_DB"])
        v = c.execute("SELECT subsystems FROM tasks WHERE id=?", (tid,)).fetchone()[0]
        c.close()
        return v

    # 1. add-time default: a research-synthesis ticket records ':ro' and says so.
    t1, out = add("wq1675 verify: analysis A", "--subsystems", "wq-tooling",
                  "--archetype", "research-synthesis")
    if subs_of(t1) != "wq-tooling:ro":
        raise RuntimeError(f"research-synthesis add did not default to :ro, got {subs_of(t1)!r}")
    if "wq#1675" not in out:
        raise RuntimeError(f"add did not announce the :ro default: {out!r}")

    # 2. explicit ':rw' opts out and is never rewritten.
    t_rw, _ = add("wq1675 verify: analysis that writes", "--subsystems", "wq-tooling:rw",
                  "--archetype", "research-synthesis")
    if subs_of(t_rw) != "wq-tooling:rw":
        raise RuntimeError(f"explicit :rw was rewritten, got {subs_of(t_rw)!r}")

    # 3. a non-analysis archetype is untouched — bare stays bare, and stays exclusive.
    t_b1, _ = add("wq1675 verify: build A", "--subsystems", "wq-tooling",
                  "--archetype", "standard-engineering")
    if subs_of(t_b1) != "wq-tooling":
        raise RuntimeError(f"standard-engineering subsystems were rewritten: {subs_of(t_b1)!r}")

    # 4. THE TICKET: two readers hold one base at once.
    t2, _ = add("wq1675 verify: analysis B", "--subsystems", "wq-tooling",
                "--archetype", "research-synthesis")
    p = claim(t1, "reader-one")
    if f"CLAIMED {t1}" not in p.stdout:
        raise RuntimeError(f"first reader could not claim: {p.stdout}{p.stderr}")
    p = claim(t2, "reader-two")
    if f"CLAIMED {t2}" not in p.stdout:
        raise RuntimeError(f"second reader blocked behind the first — wq#1675 is not fixed: "
                           f"{p.stdout}{p.stderr}")
    c = sqlite3.connect(env["WQ_DB"])
    holders = sorted(h for (h,) in c.execute("SELECT holder FROM slocks_ro WHERE scope='wq-tooling'"))
    c.close()
    if len(holders) != 2:
        raise RuntimeError(f"expected 2 rows in slocks_ro, got {holders}")

    # 5. a writer is refused while readers hold, and the refusal names a reader.
    p = claim(t_b1, "writer-one")
    if p.returncode == 0 and f"CLAIMED {t_b1}" in p.stdout:
        raise RuntimeError(f"writer claimed under two live readers: {p.stdout}")
    if "read-only" not in (p.stdout + p.stderr):
        raise RuntimeError(f"writer refusal does not name the read holders: {p.stdout}{p.stderr}")

    # 6. slock's exclusive acquire is refused under the same readers.
    p = slock("acquire", "wq-tooling", "--ttl", "10", agent="slock-writer")
    if p.returncode == 0:
        raise RuntimeError(f"slock acquired an exclusive hold under live readers: {p.stdout}")
    if "wq#1675" not in p.stdout:
        raise RuntimeError(f"slock refusal is missing its wq#1675 explanation: {p.stdout!r}")
    p = slock("who", "wq-tooling")
    if "READ-ONLY" not in p.stdout:
        raise RuntimeError(f"slock who does not list read holders: {p.stdout!r}")

    # 7. releasing both readers frees the base for the writer.
    for tid, holder in ((t1, "reader-one"), (t2, "reader-two")):
        p = wq("release", str(tid), agent=holder)
        if p.returncode != 0:
            raise RuntimeError(f"release {tid} failed: {p.stdout}{p.stderr}")
    c = sqlite3.connect(env["WQ_DB"])
    left = c.execute("SELECT COUNT(*) FROM slocks_ro").fetchone()[0]
    c.close()
    if left:
        raise RuntimeError(f"release left {left} read hold(s) behind")
    p = claim(t_b1, "writer-one")
    if f"CLAIMED {t_b1}" not in p.stdout:
        raise RuntimeError(f"writer still blocked after every reader released: {p.stdout}{p.stderr}")

    # 8. a reader is refused while a writer holds the base — the classic direction still holds.
    p = claim(t2, "reader-three")
    if p.returncode == 0 and f"CLAIMED {t2}" in p.stdout:
        raise RuntimeError(f"reader claimed under a live writer: {p.stdout}")

    # 9. the bare/exclusive path is unchanged: two bare tickets still serialize.
    t_b2, _ = add("wq1675 verify: build B", "--subsystems", "wq-tooling",
                  "--archetype", "standard-engineering")
    p = claim(t_b2, "writer-two")
    if p.returncode == 0 and f"CLAIMED {t_b2}" in p.stdout:
        raise RuntimeError(f"two exclusive holders on one subsystem: {p.stdout}")

    # 10. 'orch:<lane>' is a base name, not a mode — the orchestrator lock is untouched.
    p = wq("orch", "acquire", "--lanes", "build", agent="orch-verify")
    if p.returncode == 0:
        c = sqlite3.connect(env["WQ_DB"])
        scopes = [s for (s,) in c.execute("SELECT scope FROM slocks WHERE holder='orch-verify'")]
        c.close()
        if scopes and not any(s.startswith("orch:") for s in scopes):
            raise RuntimeError(f"orch scope was mangled by the mode split: {scopes}")

    shutil.rmtree(d, ignore_errors=True)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--check", action="store_true", help="report whether the live binaries are patched")
    ap.add_argument("--apply", action="store_true", help="patch the live binaries (idempotent)")
    ap.add_argument("--selftest", action="store_true",
                    help="run the 10 verification scenarios against the binaries as they stand, "
                         "on a scratch queue.db — the same checks --apply gates on, re-runnable "
                         "after the fact as the regression test for this change")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--wq", default=LIVE_WQ)
    ap.add_argument("--slock", default=LIVE_SLOCK)
    a = ap.parse_args()
    if not (a.check or a.apply or a.selftest):
        ap.error("one of --check / --apply / --selftest")

    live = {"wq": a.wq, "slock": a.slock}
    if a.selftest:
        verify(a.wq, a.slock)
        print(f"SELFTEST PASS — {a.wq} + {a.slock}: two readers coexist on one subsystem, a "
              f"writer is refused under them (wq + slock), a reader is refused under a writer, "
              f"bare subsystems still serialize, and add defaults analysis archetypes to :ro")
        if not (a.check or a.apply):
            return 0
    srcs = {label: read(live[label]) for label, _, _ in TARGETS}
    st = {label: status(srcs[label], patches) for label, _, patches in TARGETS}
    ok = all(v == "applied" for per in st.values() for v in per.values())

    if a.check or not a.apply:
        report = {"targets": live, "patched": ok, "patches": st}
        if a.json:
            print(json.dumps(report, indent=2))
        else:
            for label, _, _ in TARGETS:
                done = all(v == "applied" for v in st[label].values())
                print(f"{live[label]}: {'PATCHED' if done else 'NOT PATCHED'}")
                for k, v in st[label].items():
                    print(f"  {k:32} {v}")
        if not a.apply:
            return 0 if ok else 1

    if ok:
        print("already applied — nothing to do")
        return 0

    stamp = time.strftime("%Y%m%d_%H%M%S")
    cand_dir = tempfile.mkdtemp(prefix="wq1675-cand")
    backups, cands = {}, {}
    for label, _, patches in TARGETS:
        backups[label] = f"{live[label]}.bak.wq1675.{stamp}"
        shutil.copy2(live[label], backups[label])
        cands[label] = os.path.join(cand_dir, label)
        with open(cands[label], "w", encoding="utf-8") as f:
            f.write(apply_to(srcs[label], patches, label))
        shutil.copymode(live[label], cands[label])

    try:
        verify(cands["wq"], cands["slock"])
    except Exception as e:
        print(f"REFUSING to apply — the candidates failed verification: {e}", file=sys.stderr)
        return 2
    for label, _, _ in TARGETS:
        shutil.copyfile(cands[label], live[label])
    try:
        verify(live["wq"], live["slock"])
    except Exception as e:
        for label, _, _ in TARGETS:
            shutil.copyfile(backups[label], live[label])
        print(f"applied then ROLLED BACK from {backups}: {e}", file=sys.stderr)
        return 2
    print(f"applied to {', '.join(live.values())} (backups {', '.join(backups.values())})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
