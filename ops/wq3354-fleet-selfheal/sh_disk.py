"""CLASS 4 — disk pressure (wq#3354).

When any watched mount crosses `threshold_pct` (90), run the known-safe pruners once
per `cooldown_s`, re-measure, and ticket + page only if the mount is still over.

The pruner list is CODE, not config: selfheal.yaml can switch a pruner off but cannot
point one at a new path. Every target here is regenerable by construction (package
caches, the journal, worktrees wq#2620 already proves merged, clean, unclaimed and 7+
days landed). Data, projects, backups, models and state are never candidates.
lumen-disk-hygiene (weekly, unconditional) stays as is; this is the pressure-triggered
pass it does not do.
"""
import os
import shutil

from selfheal_common import as_lumen as _lumen

CLS = "disk"
HOME = "/home/lumen"
CARGO_CACHE = os.path.join(HOME, ".cargo", "registry", "cache")
PRUNE_WORKTREES = "/home/lumen/foreman/ops/wq2620-prune-stale-worktrees/prune_worktrees.py"


def _clear_dir_contents(path):
    """Delete the children of `path`, never `path` itself; only under CARGO_CACHE."""
    real, root = os.path.realpath(path), os.path.realpath(CARGO_CACHE)
    if real != root and not real.startswith(root + os.sep):
        raise ValueError(f"refusing to clear {path}: outside the cargo cache")
    if not os.path.isdir(real):
        return True
    for name in os.listdir(real):
        child = os.path.join(real, name)
        if os.path.isdir(child) and not os.path.islink(child):
            shutil.rmtree(child, ignore_errors=True)
        else:
            os.unlink(child)
    return True


# name -> (description, argv | callable). Order: cheapest-to-regenerate first.
PRUNERS = {
    "npm_cache": ("npm cache clean --force",
                  _lumen(["npm", "cache", "clean", "--force"])),
    "pip_cache": ("pip cache purge",
                  _lumen(["python3", "-m", "pip", "cache", "purge"])),
    "cargo_registry_cache": (f"clear {CARGO_CACHE}/*",
                             lambda: _clear_dir_contents(CARGO_CACHE)),
    "journal": ("journalctl --vacuum-time=7d",
                ["journalctl", "--vacuum-time=7d"]),
    "stale_worktrees": ("prune_worktrees.py --apply --grace-days 7 (wq#2620)",
                        _lumen(["python3", PRUNE_WORKTREES, "--apply", "--grace-days", "7"])),
}


def used_pct(path):
    st = os.statvfs(path)
    used = (st.f_blocks - st.f_bfree) * st.f_frsize
    avail = st.f_bavail * st.f_frsize
    return 100.0 * used / (used + avail) if used + avail else 0.0


def _mounts(cfg):
    """Configured mounts that exist, one per filesystem."""
    seen, out = set(), []
    for m in cfg.get("mounts", ["/"]):
        try:
            dev = os.stat(m).st_dev
        except OSError:
            continue
        if dev not in seen:
            seen.add(dev)
            out.append(m)
    return out


def sweep(ctx, measure=used_pct):
    d = ctx.cfg.get("disk", {})
    threshold = float(d.get("threshold_pct", 90))
    enabled = d.get("pruners", list(PRUNERS))
    st = ctx.section("disk")
    over = {m: measure(m) for m in _mounts(d)}
    over = {m: p for m, p in over.items() if p >= threshold}
    if not over:
        for key in [k for k in ctx.section("tickets") if k.startswith("disk-pressure:")]:
            ctx.resolve(CLS, key, "back under threshold")
        return
    if ctx.now - st.get("last_prune", 0) < int(d.get("cooldown_s", 3600)):
        return
    st["last_prune"] = int(ctx.now)
    for name in [n for n in PRUNERS if n in enabled]:
        desc, how = PRUNERS[name]
        ok = ctx.act(CLS, f"prune {name}: {desc}", _run_pruner, ctx, how)
        if not ok:
            ctx.note(CLS, f"pruner {name} failed")
    after = {m: measure(m) for m in over}
    st["last_result"] = {m: [round(over[m], 1), round(after[m], 1)] for m in over}
    for m, pct in after.items():
        if pct >= threshold:
            ctx.file_once(CLS, f"disk-pressure:{m}",
                          f"[selfheal] {m} still {pct:.0f}% full after safe prune",
                          f"{m} was {over[m]:.1f}% used; fleet-selfheal ran the safe pruners "
                          f"({', '.join(n for n in PRUNERS if n in enabled)}) and it is "
                          f"still {pct:.1f}% (threshold {threshold:.0f}%). What is left "
                          f"is real data, so a person or agent has to decide what goes: "
                          f"`du -xh --max-depth=2 {m} | sort -h | tail -30`.",
                          page="high")
        else:
            ctx.note(CLS, f"{m}: {over[m]:.1f}% -> {pct:.1f}%")


def _run_pruner(ctx, how):
    if callable(how):
        try:
            return bool(how())
        except (OSError, ValueError):
            return False
    p = ctx.runner(how, timeout=900)
    return bool(p and p.returncode == 0)
