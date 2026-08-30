#!/usr/bin/env python3
"""systemd_desired_state — desired-state for the fleet's own systemd units (wq#1041, R5 G3).

Captures every lumen-* unit + drop-in under /etc/systemd/system into a dedicated git
repo (the desired state), detects drift live-vs-git (READ-ONLY), and can apply the
git state back to disk (idempotent, DRY-RUN BY DEFAULT — --apply is opt-in and never
runs from a timer). Extends the wq-drift-guard / repo_sentinel drift-alert class to
unit files. Vendor units (anything not lumen-*) are excluded.

Masked units are symlinks to /dev/null; capture records them as symlinks (target),
never copies /dev/null content, so a mask is itself part of the desired state.

Commands:
  capture              rebuild the snapshot from live + git commit (baseline / update)
  check                READ-ONLY drift report (live vs git); edge-triggered alert
  status               short summary, no alert
  apply [--apply]      reconcile git->disk; DRY-RUN unless --apply (then sudo + reload)
Env: SDS_REPO (repo dir), SDS_ALERT=0 to silence, SDS_AQ/SDS_NTFY overrides for tests.
"""
import os, sys, json, glob, hashlib, shutil, subprocess, datetime

REPO = os.environ.get("SDS_REPO", "/home/lumen/foreman/ops/systemd-desired-state")
FILES = os.path.join(REPO, "files")
MANIFEST = os.path.join(REPO, "manifest.json")
SYS = "/etc/systemd/system"
AQ = os.environ.get("SDS_AQ", "/home/lumen/bin/aq")
NTFY = os.environ.get("SDS_NTFY", "/home/lumen/bin/ntfy-send")
SOURCE = "systemd-drift"
ALERT = os.environ.get("SDS_ALERT", "1") != "0"

def now(): return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

def live_paths():
    """Every lumen-* unit + its drop-in .conf files under /etc/systemd/system,
    as relpaths (basename or lumen-x.d/30-y.conf)."""
    out = []
    for ext in ("service", "timer", "socket", "path", "target", "mount"):
        for p in glob.glob(f"{SYS}/lumen-*.{ext}"):
            out.append(os.path.basename(p))
    for d in glob.glob(f"{SYS}/lumen-*.d"):
        base = os.path.basename(d)
        for c in glob.glob(f"{d}/*.conf"):
            out.append(f"{base}/{os.path.basename(c)}")
    return sorted(set(out))

def describe(relpath):
    """Live descriptor: symlink target or file sha256."""
    ap = os.path.join(SYS, relpath)
    if os.path.islink(ap):
        return {"type": "symlink", "target": os.readlink(ap)}
    if os.path.isfile(ap):
        with open(ap, "rb") as f:
            return {"type": "file", "sha256": hashlib.sha256(f.read()).hexdigest()}
    return {"type": "absent"}

def build_live_manifest():
    return {rp: describe(rp) for rp in live_paths()}

def load_manifest():
    if not os.path.exists(MANIFEST):
        return {}
    with open(MANIFEST) as f:
        return json.load(f)

# ── capture ───────────────────────────────────────────────────────────────────
def capture():
    os.makedirs(FILES, exist_ok=True)
    if not os.path.isdir(os.path.join(REPO, ".git")):
        subprocess.run(["git", "init", "-q"], cwd=REPO, check=True)
        subprocess.run(["git", "config", "user.email", "lumen@fleet"], cwd=REPO, check=False)
        subprocess.run(["git", "config", "user.name", "lumen-desired-state"], cwd=REPO, check=False)
    if os.path.isdir(FILES):
        shutil.rmtree(FILES)          # rebuild so removals are reflected; keep .git
    os.makedirs(FILES, exist_ok=True)
    man = {}
    for rp in live_paths():
        d = describe(rp)
        man[rp] = d
        if d["type"] == "file":
            dst = os.path.join(FILES, rp)
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.copyfile(os.path.join(SYS, rp), dst)
        # symlinks (masked) recorded in manifest only; no content copied
    with open(MANIFEST, "w") as f:
        json.dump(man, f, indent=1, sort_keys=True)
    subprocess.run(["git", "add", "-A"], cwd=REPO, check=False)
    r = subprocess.run(["git", "commit", "-q", "-m", f"capture {now()} ({len(man)} objects)"],
                       cwd=REPO, capture_output=True, text=True)
    print(f"captured {len(man)} objects into {REPO} " +
          ("(new commit)" if r.returncode == 0 else "(no change since last capture)"))
    return man

# ── drift check (READ-ONLY) ─────────────────────────────────────────────────────
def diff():
    want = load_manifest(); live = build_live_manifest()
    modified, new, removed = [], [], []
    for rp, d in live.items():
        if rp not in want:
            new.append(rp)
        elif want[rp] != d:
            modified.append(rp)
    for rp in want:
        if rp not in live:
            removed.append(rp)
    return {"modified": sorted(modified), "new": sorted(new), "removed": sorted(removed)}

def _aq_open():
    """Edge-trigger: True if an OPEN systemd-drift aq row already exists."""
    try:
        out = subprocess.run([AQ, "list", "all"], capture_output=True, text=True, timeout=10).stdout
        return SOURCE in out and "open" in out.lower()
    except Exception:
        return False

def check():
    if not os.path.exists(MANIFEST):
        print("no baseline yet — run `systemd_desired_state.py capture` first"); return 3
    d = diff()
    n = len(d["modified"]) + len(d["new"]) + len(d["removed"])
    if n == 0:
        print("CLEAN — live /etc/systemd/system lumen-* matches git desired-state")
        return 0
    lines = []
    for k in ("modified", "new", "removed"):
        for rp in d[k]:
            lines.append(f"  {k.upper():8} {rp}")
    body = f"systemd desired-state DRIFT: {n} object(s)\n" + "\n".join(lines)
    print(body)
    if ALERT:
        title = f"systemd DRIFT: {n} unit object(s)"
        if not _aq_open():   # edge-trigger (repo_sentinel / wq#548)
            subprocess.run([AQ, "add", SOURCE, "systemd-units", title, body], check=False)
        subprocess.run([NTFY, "-t", "lumen-alerts", "-p", "high", "-T", title, body[:400]], check=False)
    return 1

# ── apply (DRY-RUN DEFAULT; --apply opt-in, never from a timer) ──────────────────
def apply(do_it):
    if not os.path.exists(MANIFEST):
        print("no baseline — capture first"); return 3
    d = diff(); want = load_manifest(); actions = []
    for rp in d["modified"] + d["new"]:    # git -> disk (restore desired content)
        t = want.get(rp, {}).get("type")
        if t == "file": actions.append(("write", rp))
        elif t == "symlink": actions.append(("relink", rp, want[rp]["target"]))
    for rp in d["removed"]:                 # on disk but not desired
        actions.append(("remove", rp))
    if not actions:
        print("nothing to reconcile — already matches desired-state"); return 0
    for a in actions:
        print(("APPLY " if do_it else "DRY-RUN ") + " ".join(map(str, a)))
    if not do_it:
        print("\n(dry-run) re-run with --apply to reconcile git->disk (sudo + daemon-reload).")
        return 0
    for a in actions:
        if a[0] == "write":
            rp = a[1]
            subprocess.run(["sudo", "install", "-D", "-m", "644",
                            os.path.join(FILES, rp), os.path.join(SYS, rp)], check=False)
        elif a[0] == "relink":
            subprocess.run(["sudo", "ln", "-sfn", a[2], os.path.join(SYS, a[1])], check=False)
        elif a[0] == "remove":
            subprocess.run(["sudo", "rm", "-f", os.path.join(SYS, a[1])], check=False)
    subprocess.run(["sudo", "systemctl", "daemon-reload"], check=False)
    print(f"applied {len(actions)} action(s) + daemon-reload")
    return 0

def status():
    if not os.path.exists(MANIFEST):
        print("no baseline"); return 3
    d = diff(); n = sum(len(v) for v in d.values())
    print(f"desired-state objects: {len(load_manifest())} | drift: {n} "
          f"(modified={len(d['modified'])} new={len(d['new'])} removed={len(d['removed'])})")
    return 0 if n == 0 else 1

def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"
    if cmd == "capture": capture()
    elif cmd == "check": sys.exit(check())
    elif cmd == "status": sys.exit(status())
    elif cmd == "apply": sys.exit(apply("--apply" in sys.argv))
    else: print(__doc__)

if __name__ == "__main__":
    main()
