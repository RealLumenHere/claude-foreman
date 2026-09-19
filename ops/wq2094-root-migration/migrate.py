#!/usr/bin/env python3
"""migrate.py — wq#2094: move bulk data off Lumen's root LV onto the RAID array.

Root is a 466G LV that filled to 100% once (backup-repo blowup) and sits at 75%
while /scratch (md1) has 1.7T free. Each move here is the same audited
sequence, one directory at a time:

  preflight -> rsync warm-up (services still up) -> stop services -> final
  rsync -> verify (dry-run rsync must report zero diffs) -> src becomes
  src.pre-wq2094 (the backup: nothing is deleted) -> bind-mount dest onto src
  + fstab line -> start services -> confirm.

Any failure after the services stop unwinds (unmount, restore src, restart).
Root space is only freed by `finalize`, after the operator has confirmed the
service is healthy. `rollback` undoes a move that has not been finalized.

A bind mount (not a symlink or a data-root edit) keeps every existing path,
relative link and config valid, and needs no daemon.json change: docker keeps
using /var/lib/docker. Each stopped service also gets a systemd drop-in
`RequiresMountsFor=<src>` so it cannot start against the empty pre-mount
directory if the array fails to mount at boot.

Usage (needs root except `plan`):
  migrate.py plan
  migrate.py move NAME [--execute]      # default is a dry run printing the steps
  migrate.py finalize NAME [--execute]
  migrate.py rollback NAME [--execute]
"""
import argparse
import os
import shutil
import subprocess
import sys
import time

DEST_ROOT = "/scratch/lumen-root-migration"
SUFFIX = ".pre-wq2094"
MARK = ".wq2094-in-progress"  # suffix of DEST_ROOT/<name>.wq2094-in-progress
FSTAB = "/etc/fstab"
UNIT_DIR = "/etc/systemd/system"
DROPIN = "wq2094-requires-mount.conf"
HOME = "/home/lumen"
# fstypes on which the bind target is safe (docker's overlay2 needs d_type).
GOOD_FS = {"ext4", "xfs", "btrfs"}

# name -> (src, services stopped around the final sync). Ordered by payoff
# and risk: cold data first, live worktrees last. Sizes as measured 2026-09-19.
MOVES = {
    "docker": ("/var/lib/docker", ["docker.socket", "docker.service"]),
    "gocheckin": (f"{HOME}/gocheckin", []),  # 23G
    "bets": (f"{HOME}/bets", []),  # 16G
    "re": (f"{HOME}/re", []),  # 8.4G
    "sandbox": (f"{HOME}/sandbox", []),  # 6.8G
    "simpill-scratch-preserved": (f"{HOME}/simpill-scratch-preserved", []),  # 4.2G
    "esphome": (f"{HOME}/esphome", []),  # 3.9G
    "projects": (f"{HOME}/projects", []),  # 58G, holds live worktrees
}


class Abort(Exception):
    pass


def sh(cmd, check=True, capture=True):
    r = subprocess.run(cmd, capture_output=capture, text=True)
    if check and r.returncode != 0:
        raise Abort(f"{' '.join(cmd)} -> rc={r.returncode}: {(r.stderr or '').strip()[:300]}")
    return r


def dir_bytes(path):
    r = sh(["du", "-sxB1", path], check=False)
    try:
        return int(r.stdout.split()[0])
    except (IndexError, ValueError):
        return 0


def free_bytes(path):
    st = os.statvfs(path)
    return st.f_bavail * st.f_frsize


def fs_type(path):
    r = sh(["findmnt", "-no", "FSTYPE", "-T", path], check=False)
    return r.stdout.strip()


def gib(n):
    return f"{n / 2**30:.1f}G"


def dest_of(name):
    return os.path.join(DEST_ROOT, name)


def marker_of(name):
    """Resume marker beside (not inside) dest: the final `rsync --delete` would erase it."""
    return dest_of(name) + MARK


def fstab_line(name, src):
    return f"{dest_of(name)} {src} none bind,nofail,x-systemd.requires-mounts-for={DEST_ROOT} 0 0"


def pids_with_cwd_under(src):
    """PIDs whose cwd is inside src (a live shell/worker there would keep the
    stale pre-mount inode after the bind mount)."""
    hits, prefix = [], src.rstrip("/") + "/"
    for pid in filter(str.isdigit, os.listdir("/proc")):
        try:
            cwd = os.readlink(f"/proc/{pid}/cwd")
        except OSError:
            continue
        if cwd == src or cwd.startswith(prefix):
            hits.append(int(pid))
    return hits


def rsync(src, dest, dry=False, tolerate_vanished=False):
    cmd = ["rsync", "-aHAX", "--numeric-ids", "--delete", "-x"]
    if dry:
        cmd += ["-n", "-i"]
    cmd += [src.rstrip("/") + "/", dest.rstrip("/") + "/"]
    r = sh(cmd, check=False)
    if r.returncode == 24 and tolerate_vanished:
        return r
    if r.returncode != 0:
        raise Abort(f"rsync rc={r.returncode}: {r.stderr.strip()[:300]}")
    return r


def preflight(name, src, services):
    if os.geteuid() != 0:
        raise Abort("must run as root (lumen-run start --root -- ...)")
    if not os.path.isdir(src) or os.path.islink(src):
        raise Abort(f"{src} is not a real directory")
    if os.path.ismount(src):
        raise Abort(f"{src} is already a mountpoint (already moved?)")
    if os.path.exists(src + SUFFIX):
        raise Abort(f"{src + SUFFIX} exists: finalize or rollback the previous move first")
    if not os.path.ismount("/scratch"):
        raise Abort("/scratch is not mounted")
    ftype = fs_type("/scratch")
    if ftype not in GOOD_FS:
        raise Abort(f"/scratch fstype {ftype!r} not in {sorted(GOOD_FS)}")
    dest = dest_of(name)
    if os.path.exists(dest) and os.listdir(dest) and not os.path.exists(marker_of(name)):
        raise Abort(f"{dest} is non-empty and not a resumed wq2094 copy")
    size = dir_bytes(src)
    need = int(size * 1.1)
    os.makedirs(DEST_ROOT, exist_ok=True)
    if free_bytes(DEST_ROOT) < need:
        raise Abort(f"need {gib(need)} on /scratch, have {gib(free_bytes(DEST_ROOT))}")
    if not services:
        busy = pids_with_cwd_under(src)
        if busy:
            raise Abort(f"processes with cwd under {src}: {busy[:10]} — quiesce them first")
    if name == "docker":
        root = sh(["docker", "info", "--format", "{{.DockerRootDir}}"]).stdout.strip()
        if root != src:
            raise Abort(f"docker root is {root!r}, not {src!r}")
    return size


def running_containers():
    r = sh(["docker", "ps", "--format", "{{.Names}}"], check=False)
    return sorted(r.stdout.split())


def write_dropins(src, services):
    for unit in services:
        d = os.path.join(UNIT_DIR, unit + ".d")
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, DROPIN), "w") as f:
            f.write(f"[Unit]\nRequiresMountsFor={src}\n")
    if services:
        sh(["systemctl", "daemon-reload"])


def remove_dropins(services):
    for unit in services:
        p = os.path.join(UNIT_DIR, unit + ".d", DROPIN)
        if os.path.exists(p):
            os.remove(p)
    if services:
        sh(["systemctl", "daemon-reload"])


def edit_fstab(line, add):
    with open(FSTAB) as f:
        lines = f.read().splitlines()
    kept = [l for l in lines if l.strip() != line]
    if add:
        kept.append(line)
    if kept == lines:
        return
    bak = FSTAB + SUFFIX
    if not os.path.exists(bak):
        shutil.copy2(FSTAB, bak)
    tmp = FSTAB + ".wq2094.tmp"
    with open(tmp, "w") as f:
        f.write("\n".join(kept) + "\n")
    shutil.copystat(FSTAB, tmp)
    os.replace(tmp, FSTAB)


def start_services(services, want_containers):
    for unit in services:
        sh(["systemctl", "start", unit])
    for unit in services:
        if sh(["systemctl", "is-active", unit], check=False).stdout.strip() != "active":
            raise Abort(f"{unit} not active after restart")
    if want_containers:
        deadline = time.time() + 180
        while time.time() < deadline:
            missing = set(want_containers) - set(running_containers())
            if not missing:
                return
            time.sleep(5)
        raise Abort(f"containers not back after 180s: {sorted(missing)}")


def cmd_plan(_args):
    total = 0
    print(f"{'name':28}{'size':>9}  {'state':18}src")
    for name, (src, _svc) in MOVES.items():
        if not os.path.isdir(src):
            print(f"{name:28}{'-':>9}  {'MISSING':18}{src}")
            continue
        state = "moved" if os.path.ismount(src) else ("pending-finalize" if os.path.exists(src + SUFFIX) else "pending")
        size = 0 if state == "moved" else dir_bytes(src)
        total += size
        print(f"{name:28}{gib(size):>9}  {state:18}{src}")
    st = os.statvfs("/")
    used = (st.f_blocks - st.f_bfree) * st.f_frsize
    cap = st.f_blocks * st.f_frsize
    print(f"\nroot {gib(used)}/{gib(cap)} = {100 * used / cap:.0f}%; "
          f"after all pending moves + finalize: ~{100 * max(used - total, 0) / cap:.0f}% "
          f"(docker size is only visible as root; unreadable dirs undercount)")
    print(f"/scratch free {gib(free_bytes('/scratch'))}" if os.path.isdir("/scratch") else "/scratch missing")


def cmd_move(args):
    src, services = MOVES[args.name]
    dest = dest_of(args.name)
    steps = ["preflight", f"rsync warm-up {src} -> {dest}", f"stop {services or 'nothing'}",
             "final rsync + zero-diff verify", f"{src} -> {src + SUFFIX} (backup, kept)",
             f"fstab + bind mount {dest} on {src}", f"start {services or 'nothing'} + confirm"]
    if not args.execute:
        print(f"DRY RUN {args.name}:\n  " + "\n  ".join(steps))
        return
    size = preflight(args.name, src, services)
    print(f"preflight ok: {gib(size)} to move")
    want = running_containers() if args.name == "docker" else []
    os.makedirs(dest, exist_ok=True)
    open(marker_of(args.name), "w").close()
    rsync(src, dest, tolerate_vanished=True)
    print("warm-up rsync done")
    stopped, swapped, mounted = False, False, False
    try:
        for unit in services:
            sh(["systemctl", "stop", unit])
        stopped = True
        rsync(src, dest)
        diff = rsync(src, dest, dry=True).stdout.strip()
        if diff:
            raise Abort("verify failed, src and dest differ:\n" + diff[:600])
        print("verify ok: zero-diff")
        os.rename(src, src + SUFFIX)
        swapped = True
        os.mkdir(src)
        shutil.copystat(src + SUFFIX, src)
        st = os.stat(src + SUFFIX)
        os.chown(src, st.st_uid, st.st_gid)
        write_dropins(src, services)
        edit_fstab(fstab_line(args.name, src), add=True)
        sh(["mount", "--bind", dest, src])
        mounted = True
        if not os.path.ismount(src):
            raise Abort(f"{src} not a mountpoint after bind mount")
        os.remove(marker_of(args.name))
        start_services(services, want)
        stopped = False
    except Exception as e:
        print(f"ABORT: {e} — unwinding", file=sys.stderr)
        if mounted:
            sh(["umount", src], check=False)
        if swapped:
            shutil.rmtree(src, ignore_errors=True)
            os.rename(src + SUFFIX, src)
        edit_fstab(fstab_line(args.name, src), add=False)
        remove_dropins(services)
        if stopped:
            for unit in reversed(services):
                sh(["systemctl", "start", unit], check=False)
        raise
    print(f"moved {args.name}. Confirm the service is healthy, then: "
          f"migrate.py finalize {args.name} --execute  (frees {gib(size)} on root)")


def cmd_finalize(args):
    src, _ = MOVES[args.name]
    old = src + SUFFIX
    if not args.execute:
        print(f"DRY RUN: would rm -rf {old} (only if {src} is a mountpoint)")
        return
    if not os.path.ismount(src) or not os.path.isdir(old):
        raise Abort(f"nothing to finalize: {src} mounted={os.path.ismount(src)}, {old} exists={os.path.isdir(old)}")
    sh(["rm", "-rf", "--one-file-system", old])
    print(f"finalized {args.name}: {old} removed")


def cmd_rollback(args):
    src, services = MOVES[args.name]
    old, dest = src + SUFFIX, dest_of(args.name)
    if not args.execute:
        print(f"DRY RUN: stop {services}, sync {dest} back into {old}, umount {src}, restore {old}, "
              f"drop fstab line + drop-ins, start {services}")
        return
    if not os.path.ismount(src) or not os.path.isdir(old):
        raise Abort(f"cannot roll back: {src} mounted={os.path.ismount(src)}, {old} exists={os.path.isdir(old)}")
    want = running_containers() if args.name == "docker" else []
    for unit in services:
        sh(["systemctl", "stop", unit])
    rsync(dest, old)
    sh(["umount", src])
    shutil.rmtree(src)
    os.rename(old, src)
    edit_fstab(fstab_line(args.name, src), add=False)
    remove_dropins(services)
    start_services(services, want)
    print(f"rolled back {args.name}; {dest} left in place, remove it by hand")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("plan")
    for c in ("move", "finalize", "rollback"):
        p = sub.add_parser(c)
        p.add_argument("name", choices=list(MOVES))
        p.add_argument("--execute", action="store_true")
    args = ap.parse_args(argv)
    try:
        {"plan": cmd_plan, "move": cmd_move, "finalize": cmd_finalize, "rollback": cmd_rollback}[args.cmd](args)
    except Abort as e:
        print(f"ABORT: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
