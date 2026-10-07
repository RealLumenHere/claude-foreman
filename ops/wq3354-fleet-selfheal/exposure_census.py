#!/usr/bin/env python3
"""exposure-census — content-driven secret-exposure sweep (wq#3514, class 8).

leakwatch (class 3) is path-list driven: a new secret-bearing dir is blind until
someone adds it to selfheal.yaml (wq#3512). This sweep inverts the model: harvest the
live secret VALUES from the known stores, hash them, then walk the whole tree and flag
any file whose content holds an exact live value AND whose permissions actually expose
it. A new leak location is found by its content, no yaml edit needed.

Decisions and their reasons: design/wq3514_content_driven_exposure.md. In short:
  harvest    masking.read() per store line is the calibration — the house credential
             label vocabulary decides what counts, not an entropy knob (census2
             admitted VERIFY_JUDGE_MODEL / R2_ACCOUNT_ID and hit 5334 files); plus
             basic-auth URL passwords and whole-file raw tokens, over a ≥12-char
             ≥3-class floor. A digest seen in more than prevalence_cap files is
             demoted to one calibration note (triage before alarming).
  schedule   own daily oneshot timer (lumen-exposure-census.timer) — inotify cannot
             watch every dir, and a minutes-long walk cannot ride the 1-min sweep.
  values     only sha256 digests leave harvest(); reports carry sha12 + source NAME,
             state persists per-dir name+sha12 signatures. Never a value.
  traversal  world exposure requires o+x on every ancestor (a 0750 home is not an
             alarm); group exposure requires g+x-or-o+x ancestors AND a group that
             reaches an account besides the owner (wq#3456 bar) — then alert-only.
  action     report-only: one ticket per offending dir per episode (rotation is the
             fix; the census chmods nothing), resolve on a later clean complete sweep.

    exposure_census.py [--config selfheal.yaml] [--state PATH] [--dry-run]
"""
import argparse
import glob
import hashlib
import os
import re
import stat
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import selfheal_common as common  # noqa: E402
from leakwatch import group_readers  # noqa: E402

CLS = "exposure"
URLCRED = re.compile(r"://[^:/@\s]+:([^@/\s]+)@")
TOKEN = re.compile(r"[^\s\"'`,;<>()\[\]{}|\\]{12,}")
BIN_SNIFF = 8192

DEFAULTS = {
    "root": "/home/lumen",
    "sources": ["/home/lumen/agent/.env", "/home/lumen/foreman/.env",
                "/home/lumen/langfuse/.env", "/home/lumen/langfuse/langfuse-web.env",
                "/home/lumen/langfuse/langfuse-worker.env", "/home/lumen/.git-credentials"],
    "source_globs": ["/home/lumen/.secrets/*"],
    "prune": ["node_modules", ".git", ".cache", ".npm-global", ".cargo", ".rustup",
              ".claude", "__pycache__", ".venv", "venv", "snap", "re"],
    "max_file_kb": 2048,
    "prevalence_cap": 20,
    "max_dir_tickets": 5,
    "time_cap_s": 1500,
    "harness_dir": "/home/lumen/foreman/harness",
}


def _cfg(ctx):
    out = dict(DEFAULTS)
    out.update(ctx.cfg.get("exposure", {}) or {})
    return out


def _masking(cfg):
    sys.path.insert(0, cfg["harness_dir"])
    import masking
    return masking


def plausible(v):
    """Floor under every extractor: long and multi-class, so an exact match elsewhere
    is evidence, not coincidence. The real calibration is the label gate above this."""
    if not 12 <= len(v) <= 512 or v.lower() in ("true", "false", "null", "none"):
        return False
    classes = sum(bool(re.search(p, v)) for p in (r"[a-z]", r"[A-Z]", r"[0-9]",
                                                  r"[^A-Za-z0-9]"))
    return classes >= 3 or (classes >= 2 and re.search(r"[0-9]", v) and len(v) >= 20)


def _digest(v):
    return hashlib.sha256(v.encode("utf-8", "replace")).hexdigest()


def harvest(cfg, read):
    """{sha256_hex: source_name} from the live stores. `read` is masking.read — the
    label gate: only values the house masker would redact count as secrets, which is
    what keeps model names and account ids (census2's flood) out. Raw values are
    hashed and dropped here; nothing else ever holds one."""
    paths = [p for p in cfg["sources"]]
    for g in cfg["source_globs"]:
        paths += [p for p in sorted(glob.glob(g)) if os.path.isfile(p)]
    live, lens = {}, set()
    for p in paths:
        try:
            txt = open(p, errors="replace").read()
        except OSError:
            continue
        src = os.path.relpath(p, cfg["root"])
        for ln in txt.splitlines():
            key = ln.split("=", 1)[0].strip() if "=" in ln else ""
            name = f"{src}:{key}" if key else src
            vals = [v.strip().strip("\"'").rstrip(",;") for _l, _p, v, _a, _b in read(ln)]
            vals += [m.group(1) for m in URLCRED.finditer(ln)]
            for v in vals:
                if plausible(v):
                    live.setdefault(_digest(v), name)
                    lens.add(len(v))
        whole = txt.strip()        # .secrets/* raw single-token files carry no label
        if whole and "\n" not in whole and " " not in whole and plausible(whole):
            live.setdefault(_digest(whole), src)
            lens.add(len(whole))
    return live, lens


def digests_in(text, live, lens):
    """Live digests whose value appears verbatim in `text`. Tokens are hashed and
    looked up — the live values themselves are never present to compare against."""
    found = set()
    for t in TOKEN.findall(text):
        t = t.rstrip(".,;:")
        cands = {t}
        for sep in ("=", ":"):
            if sep in t:
                cands.add(t.split(sep, 1)[1].strip("\"'"))
        cands.update(m.group(1) for m in URLCRED.finditer(t))
        for v in cands:
            if len(v) in lens:
                d = _digest(v)
                if d in live:
                    found.add(d)
    return found


def _reachable(d, mask, cache, stop):
    """True when every dir from `d` up to and including `stop` grants an execute bit
    in `mask` (0o001 world, 0o011 group-or-world). Above `stop` is assumed traversable
    — the census judges the tree it scans, not /home's own mode twice over."""
    key = (d, mask)
    if key in cache:
        return cache[key]
    ok, cur = True, d
    while True:
        try:
            if not os.stat(cur).st_mode & mask:
                ok = False
                break
        except OSError:
            ok = False
            break
        if os.path.samefile(cur, stop) or cur == os.path.dirname(cur):
            break
        cur = os.path.dirname(cur)
    cache[key] = ok
    return ok


def scan_tree(cfg, live, lens, deadline):
    """[(path, mode, world_exposed, group_exposed, {digest})]. Candidate = regular,
    group- or world-readable, under the size cap, not binary. Exposure additionally
    needs the matching ancestor-traversal bit (and, for group, outside readers)."""
    hits, cache = [], {}
    root = cfg["root"]
    prune = set(cfg["prune"])
    cap = cfg["max_file_kb"] * 1024
    partial = False
    for cur, dirs, files in os.walk(root, followlinks=False):
        dirs[:] = sorted(d for d in dirs
                         if d not in prune and not os.path.ismount(os.path.join(cur, d)))
        if time.time() > deadline:
            partial = True
            break
        for f in files:
            p = os.path.join(cur, f)
            try:
                st = os.lstat(p)
                mode = st.st_mode
                if not stat.S_ISREG(mode) or st.st_size > cap or not mode & 0o044:
                    continue
                with open(p, "rb") as fh:
                    head = fh.read(BIN_SNIFF)
                    if b"\0" in head:
                        continue
                    text = (head + fh.read()).decode("utf-8", "replace")
            except OSError:
                continue
            found = digests_in(text, live, lens)
            if not found:
                continue
            world = bool(mode & 0o004) and _reachable(cur, 0o001, cache, root)
            group = (not world and bool(mode & 0o040)
                     and _reachable(cur, 0o011, cache, root) and bool(group_readers(st)))
            hits.append((p, mode & 0o777, world, group, found))
    return hits, partial


def demote_prevalent(hits, live, cap):
    """Digests present in more than `cap` files are mis-harvested config, not leaked
    credentials — strip them everywhere and report each once as a calibration note."""
    count = {}
    for _p, _m, _w, _g, found in hits:
        for d in found:
            count[d] = count.get(d, 0) + 1
    demoted = {d: n for d, n in count.items() if n > cap}
    kept = []
    for p, m, w, g, found in hits:
        found -= set(demoted)
        if found:
            kept.append((p, m, w, g, found))
    notes = [f"sha12={d[:12]} ({live[d]}) matched {n} files > prevalence_cap {cap}; "
             f"demoted as likely non-secret — recalibrate or rotate by hand"
             for d, n in sorted(demoted.items(), key=lambda kv: -kv[1])]
    return kept, notes


def _sig(rows):
    return ";".join(f"{os.path.basename(p)}:" + ",".join(sorted(d[:12] for d in found))
                    for p, _m, _w, _g, found in sorted(rows))


def _listing(rows, live):
    lines = []
    for p, m, _w, _g, found in sorted(rows):
        srcs = sorted({live[d] for d in found})
        shas = sorted(d[:12] for d in found)
        lines.append(f"  sha12={','.join(shas)}  mode={oct(m)}  {os.path.basename(p)}  "
                     f"[{', '.join(srcs)}]")
    return "\n".join(lines)


def report(ctx, cfg, hits, partial, notes, live):
    for n in notes:
        ctx.note(CLS, n)
        if not ctx.dry_run:
            ctx.out.alert(f"exposure-calibration:{n[:60]}", "exposure-census demotion", n)
    by_dir = {}
    for row in hits:
        by_dir.setdefault(os.path.dirname(row[0]), []).append(row)
    seen = ctx.section("exposure")
    filed = held = 0
    for d, rows in sorted(by_dir.items()):
        world = [r for r in rows if r[2]]
        grp = [r for r in rows if r[3]]
        if world:
            sig = _sig(world)
            if seen.get(d) != sig:
                if filed >= cfg["max_dir_tickets"]:
                    held += 1
                else:
                    filed += 1
                    if d in seen:
                        ctx.resolve(CLS, f"exposure:{d}")  # new file/value set = new episode
                    seen[d] = sig
                    ctx.file_once(
                        CLS, f"exposure:{d}",
                        f"[selfheal] exposed: {len(world)} file(s) in {d} hold a live "
                        f"credential value",
                        f"exposure-census (wq#3514) found files whose CONTENT contains "
                        f"an exact live secret value and which are world-readable with "
                        f"a world-traversable path:\n{_listing(world, live)}\n"
                        f"Treat each named source credential as burned: rotate it, then "
                        f"secure or delete the files. If this dir legitimately holds "
                        f"secrets, add it to the leakwatch rules in selfheal.yaml so "
                        f"the real-time layer covers it. The census edited nothing.",
                        page="urgent")
        if grp:
            gseen = ctx.section("exposure_group")
            gsig = _sig(grp)
            if gseen.get(d) != gsig:
                gseen[d] = gsig
                ctx.note(CLS, f"{d}: {len(grp)} group-readable file(s) hold a live "
                              f"value; alert only (wq#3456 bar)")
                if not ctx.dry_run:
                    ctx.out.alert(f"exposure-group:{d}",
                                  f"{d}: group-readable file(s) hold a live credential",
                                  f"Readable through a group that reaches accounts "
                                  f"besides the owner:\n{_listing(grp, live)}\n"
                                  f"chmod 600 them if no service reads them by group.")
    if not partial:    # a partial walk must never close a real episode
        for d in [d for d in list(seen)
                  if not any(r[2] for r in by_dir.get(d, []))]:
            ctx.resolve(CLS, f"exposure:{d}",
                        f"{d} no longer holds an exposed live credential value")
            seen.pop(d, None)
    if held and not ctx.dry_run:
        ctx.out.alert("exposure-overflow", "exposure-census hit max_dir_tickets",
                      f"{held} further exposed dir(s) held back this sweep; they file "
                      f"on a later sweep as earlier episodes resolve.")


def sweep(ctx):
    cfg = _cfg(ctx)
    live, lens = harvest(cfg, _masking(cfg).read)
    ctx.note(CLS, f"harvested {len(live)} live value digest(s)")
    if not live:
        return
    hits, partial = scan_tree(cfg, live, lens, time.time() + cfg["time_cap_s"])
    hits, notes = demote_prevalent(hits, live, cfg["prevalence_cap"])
    ctx.note(CLS, f"{len(hits)} file(s) hold a live value"
                  + (" [PARTIAL: time cap]" if partial else ""))
    report(ctx, cfg, hits, partial, notes, live)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--config", default=common.CONFIG_PATH)
    ap.add_argument("--state", default=os.environ.get(
        "EXPOSURE_STATE", "/home/lumen/agents/fleet-selfheal/exposure-state.json"))
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)
    cfg = common.load_config(a.config)
    if not cfg.get("exposure", {}).get("enabled", True):
        return 0
    state = common.load_state(a.state)
    ctx = common.Ctx(cfg, state, dry_run=a.dry_run)
    sweep(ctx)
    for line in ctx.actions:
        print(line, flush=True)
    if not a.dry_run:
        common.save_state(state, a.state)
    return 0


if __name__ == "__main__":
    sys.exit(main())
