"""wq#2546: mask credential-shaped values that reached disk UNMASKED in two stores
harness/masking.py's own write path never touches: Claude Code's own session
transcripts (~/.claude/projects/**/*.jsonl, one per session, written by the CLI
itself, never by fworker) and the supergateway MCP gateway log
(/home/lumen/logs/supergateway/*.log*, every Commander tool call's raw
request/response body, wq#1698's journald-avoidance drop-in). wq#2514's consumer
scan found the live RealLumenHere PAT (fp 27ec5cf7418a) in both.

masking.py protects every artefact FOREMAN derives from a run (mask_replay.py's own
docstring: "masking runs before any artefact write"). It has no reach here: a CC
transcript is written by the `claude` binary itself, not by anything in this repo,
and the supergateway body log is written by a vendored npm package
(dist/gateways/stdioToStatelessStreamableHttp.js) neither of which calls into
harness/masking.py. So the fix is a separate at-rest PASS over both stores, using the
same PATTERNS table (imported, never re-forked — the sed-vs-python drift documented
at the top of masking.py is exactly the failure mode a second copy would repeat).

Design (wq#2546 BUILD a/b):
  - Walk each store, mask every line with masking.mask_text() (NDJSON-safe: the
    replacement alphabet has no JSON metacharacters, so a masked JSONL line still
    parses).
  - A file with zero hits is left untouched (mtime, and therefore the nightly
    incremental scan below, stays honest).
  - A file WITH a hit is snapshotted off-box first (restic, the existing offsite
    repo backup-offsite.sh already ships — sftp:thoth, wq#330/backup-offsite), then
    masked in place: temp file + os.replace (atomic on the same filesystem), mode
    and ownership preserved. Transcripts are never deleted (audit value, per the
    ticket) — the pre-mask bytes live in the offsite restic repo, off Lumen, which is
    the "off-box .bak" the ticket asks for; nothing pre-mask is left in-box, because
    a second local copy would just be a second thing to burn.
  - Every hit is logged the way mask_alarm logs one (masking.scan() records: label,
    kept prefix, value length+sha12, masked context — never the value), appended to
    a JSONL run log under _runs/mask_at_rest/, and paged same as any instinct-4 alarm
    UNLESS every value is already accounted for by the rotated ledger
    (mask_replay.load_rotated_ledger) — a value that already 401s is not a new alarm
    just because this is the first time this pass has seen its resting place.

Runs two ways: nightly via lumen-mask-at-rest.timer (full sweep, mtime-gated so an
untouched file costs one stat) and on-demand, scoped to one session id + the
current gateway log tail, from fworker.mask_alarm right when a live run's stream
already told us a value burned (wq#2546 BUILD d) — no reason to wait for the
timer when we already know which transcript just got the value written to it.

    python3 mask_at_rest.py --store all                 # nightly sweep, all stores
    python3 mask_at_rest.py --store transcripts --session <id>   # one session's file
    python3 mask_at_rest.py --store supergateway         # gateway log only
    python3 mask_at_rest.py --store all --dry-run        # report only, mask nothing

Exit 0: no unaccounted hit (or none found). Exit 1: at least one file had a hit not
already in the rotated ledger (paged; still masked either way — masking never waits
on the page). Exit 2: a store path does not exist / nothing to scan.
"""
import argparse
import glob
import gzip
import json
import os
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import masking  # noqa: E402
import mask_replay  # noqa: E402  (rotated ledger — a value that already 401s is settled)

FOREMAN_ROOT = os.environ.get("FOREMAN_ROOT", "/home/lumen/foreman")
RUN_LOG_DIR = os.environ.get("MASK_AT_REST_RUN_DIR",
                             os.path.join(FOREMAN_ROOT, "_runs", "mask_at_rest"))

PROJECTS_ROOT = os.environ.get("CLAUDE_PROJECTS_ROOT",
                               os.path.expanduser("~/.claude/projects"))
GATEWAY_LOG_DIR = os.environ.get("MASK_AT_REST_GATEWAY_DIR",
                                 "/home/lumen/logs/supergateway")

# Same repo backup-offsite.sh already writes to (wq#330) — reused, never duplicated,
# so there is exactly one offsite credential surface for restic to protect, not two.
OFFSITE_REPO = os.environ.get(
    "MASK_AT_REST_OFFSITE_REPO",
    "sftp:thoth@192.168.8.218:/mnt/storage/lumen-backup-offsite")
OFFSITE_PASSWORD_FILE = os.environ.get(
    "MASK_AT_REST_OFFSITE_PW", "/home/lumen/services/backup/.restic-password")
OFFSITE_SSH_KEY = os.environ.get(
    "MASK_AT_REST_OFFSITE_KEY", "/home/lumen/.ssh/thoth_ed25519")
RESTIC_TIMEOUT_S = 120
# wq#3164: lumen-mask-at-rest.service sets no Environment=PATH=, so it runs under
# systemd's own default $PATH (systemd.exec(5): /usr/local/sbin:/usr/local/bin:
# /usr/sbin:/usr/bin:/sbin:/bin), which does not include ~/.local/bin — restic's
# actual install location on this box. A bare "restic" argv[0] made subprocess do a
# PATH lookup that failed every unit run ("No such file or directory: restic"),
# skipping the offsite snapshot and leaving every hit file "held unmasked" with no
# alert (283 transcripts on the 2026-09-29 03:17Z run). Resolve by absolute path
# instead, so the unit's PATH is irrelevant.
RESTIC_BIN = os.environ.get("RESTIC_BIN", "/home/lumen/.local/bin/restic")

# Same fail-open aq wiring as read_dir_sweep.aq_alert — a missing/broken aq binary
# must never break the sweep itself.
AQ_BIN = os.environ.get("FOREMAN_AQ_BIN", "/home/lumen/bin/aq")
AQ_SOURCE = "mask-at-rest"

STATE_PATH = os.environ.get("MASK_AT_REST_STATE",
                            os.path.join(FOREMAN_ROOT, "mask_at_rest_state.json"))
# 2026-09-26: single-flight. fworker.trigger_mask_at_rest fires on every live alarm and
# the nightly timer runs the same sweep; two of them at once only double the restic and
# scan load (96 stacked `--store all` runs pinned wq#2720's worker cgroup at its quota
# and halted the dispatcher). Held for the whole run; a second invocation that finds it
# busy logs and exits 0 — the running sweep covers the same store, so nothing is lost.
LOCK_PATH = os.environ.get("MASK_AT_REST_LOCK",
                           os.path.join(FOREMAN_ROOT, "runtime", "mask_at_rest.lock"))


def log(msg):
    print(f"[mask_at_rest] {msg}", file=sys.stderr)


def acquire_lock(path=None):
    """Non-blocking exclusive flock on LOCK_PATH. Returns the open file (keep it
    referenced for the run) or None when another run holds it. A lock dir that cannot
    be created or a filesystem without flock fails OPEN: the sweep still runs, since a
    missed mask is worse than a doubled one."""
    import fcntl
    path = path or LOCK_PATH
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        fh = open(path, "a+")
    except OSError as e:
        log(f"WARN: lock {path} unavailable ({e!r}); running unlocked")
        return object()
    try:
        fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        fh.close()
        return None
    try:
        fh.seek(0); fh.truncate(); fh.write(f"{os.getpid()} {time.time():.0f}\n"); fh.flush()
    except OSError:
        pass
    return fh


def _load_state():
    try:
        with open(STATE_PATH) as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def _save_state(state):
    os.makedirs(os.path.dirname(STATE_PATH), exist_ok=True)
    tmp = STATE_PATH + ".tmp"
    with open(tmp, "w") as fh:
        json.dump(state, fh, indent=2)
        fh.write("\n")
    os.replace(tmp, STATE_PATH)


def _new_or_changed(paths, scanned_mtimes):
    """`paths` whose current mtime is newer than the last mtime recorded clean/masked
    for it in STATE_PATH (or that were never recorded at all) — wq#3164's incremental
    bound. A path that errors on stat is treated as changed (scan it; the read below
    will hit and log the same error)."""
    out = []
    for p in paths:
        try:
            mtime = os.path.getmtime(p)
        except OSError:
            out.append(p)
            continue
        prev = scanned_mtimes.get(p)
        if prev is None or mtime > prev:
            out.append(p)
    return out


def _update_state(state, store, transcripts, gateways, scanned):
    """Fold this pass's `scanned` {path: mtime} map into STATE_PATH, split back into
    the transcripts/supergateway buckets the next run's `_new_or_changed` reads. Only
    ever grows/refreshes entries for paths this pass actually settled (process_store
    never puts a held-unmasked or errored path in `scanned`).

    A bucket is only pruned when its store was actually in scope this run (`store`)
    — a `--store transcripts` pass must not wipe the supergateway bucket just because
    it never listed that store's files this time. When a store IS in scope, any of
    its bucket entries no longer in the fresh listing (deleted/rotated-away file) are
    dropped rather than kept as dead weight."""
    gw_set = set(gateways)
    t_bucket = state.setdefault("transcripts", {})
    g_bucket = state.setdefault("supergateway", {})
    for p, mtime in scanned.items():
        (g_bucket if p in gw_set else t_bucket)[p] = mtime
    if store in ("transcripts", "all"):
        still_live = set(transcripts)
        for stale in [p for p in t_bucket if p not in still_live]:
            del t_bucket[stale]
    if store in ("supergateway", "all"):
        still_live = gw_set
        for stale in [p for p in g_bucket if p not in still_live]:
            del g_bucket[stale]
    _save_state(state)


def transcript_paths(session=None):
    """CC session transcript files under PROJECTS_ROOT, newest-mtime first.

    `session` narrows to one session id (fworker.mask_alarm's targeted call) —
    globbed rather than looked up by directory, since a session can be resumed
    into a different project slug than it started in.
    """
    if session:
        return sorted(glob.glob(os.path.join(PROJECTS_ROOT, "*", f"{session}.jsonl")))
    if not os.path.isdir(PROJECTS_ROOT):
        return []
    paths = glob.glob(os.path.join(PROJECTS_ROOT, "*", "*.jsonl"))
    return sorted(paths, key=lambda p: -os.path.getmtime(p))


def gateway_log_paths():
    """The live gateway body log plus any rotated copies (.log.N / .log.N.gz).

    logrotate.conf (wq#1698) caps this at 50M x 4, so the total corpus here is
    bounded even though the live transcripts store is not.
    """
    if not os.path.isdir(GATEWAY_LOG_DIR):
        return []
    return sorted(
        p for p in glob.glob(os.path.join(GATEWAY_LOG_DIR, "*.log*"))
        if os.path.isfile(p) and not p.endswith(".masking_tmp"))


def _read_text(path):
    """Text of `path`, transparently decompressing a rotated `.gz` copy."""
    opener = gzip.open if path.endswith(".gz") else open
    with opener(path, "rt", errors="replace") as fh:
        return fh.read()


def _write_text(path, text, mode):
    """Atomic same-filesystem replace, mode preserved, gzip re-applied for `.gz`."""
    tmp = path + ".masking_tmp"
    opener = gzip.open if path.endswith(".gz") else open
    with opener(tmp, "wt") as fh:
        fh.write(text)
    os.chmod(tmp, mode)
    os.replace(tmp, path)


def mask_file_text(text):
    """(masked_text, [scan hit record, ...]) — line-by-line, so a masked JSONL line
    still parses and a hit's `context` window never crosses a record boundary."""
    out_lines, records = [], []
    for line in text.splitlines(keepends=True):
        masked, hits = masking.scan(line)
        out_lines.append(masked)
        records.extend(hits)
    return "".join(out_lines), records


def offsite_backup(paths, tag):
    """restic backup of `paths` to OFFSITE_REPO, tagged `tag`. Best-effort: a failed
    snapshot BLOCKS the mask (return False) — an at-rest fix that could silently
    drop the only pre-mask copy on a repo hiccup is worse than leaving the file
    unmasked one more pass, since masking.py's rules never regress a real hit to
    invisible."""
    if not paths:
        return True
    env = dict(os.environ)
    env["RESTIC_PASSWORD_FILE"] = OFFSITE_PASSWORD_FILE
    env["RESTIC_REPOSITORY"] = OFFSITE_REPO
    cmd = [RESTIC_BIN, "-o", f"sftp.command=ssh -o StrictHostKeyChecking=no "
                            f"-i {OFFSITE_SSH_KEY} thoth@192.168.8.218 -s sftp",
           "backup", "--tag", tag, "--host", "lumen-mask-at-rest", "--"] + paths
    try:
        proc = subprocess.run(cmd, env=env, capture_output=True, text=True,
                              timeout=RESTIC_TIMEOUT_S)
    except (OSError, subprocess.SubprocessError) as exc:
        log(f"offsite backup FAILED to invoke: {exc}")
        return False
    if proc.returncode != 0:
        log(f"offsite backup FAILED (exit {proc.returncode}): "
            f"{proc.stderr.strip()[-500:]}")
        return False
    return True


def process_store(paths, run_id, dry_run, rotated):
    """One store's files: mask each, backing up off-box first. Returns
    (files_masked, all_records, unaccounted_records, held_unmasked_paths, scanned).

    `scanned` is a {path: mtime} map of every file settled this pass (clean, or
    masked with a successful backup) — never a file that errored or is still
    held unmasked. main() folds this into STATE_PATH so the next incremental pass
    (wq#3164) can skip a stat-unchanged file instead of re-reading it."""
    files_masked, all_records, unaccounted, scanned = 0, [], [], {}
    plans = []
    for path in paths:
        try:
            mtime = os.path.getmtime(path)
            text = _read_text(path)
        except OSError as exc:
            log(f"skip {path}: {exc}")
            continue
        masked, records = mask_file_text(text)
        if not records:
            scanned[path] = mtime
            continue
        plans.append((path, masked, records))
    if not plans:
        return 0, [], [], [], scanned
    if not dry_run:
        to_backup = [p for p, _, _ in plans]
        if not offsite_backup(to_backup, tag=f"mask-at-rest-{run_id}"):
            log("offsite backup did not succeed for this batch — masking SKIPPED "
                "for these files this pass (will retry next run):")
            held = [p for p, _, _ in plans]
            for p in held:
                log(f"  held unmasked (backup failed): {p}")
            return 0, [], [], held, scanned
    for path, masked, records in plans:
        all_records.extend({**r, "path": path} for r in records)
        loose = [r for r in records
                if r["value_sha256_12"] not in rotated
                and r["value_sha256_12"] not in masking.SELF_OUTPUT_SHA12]
        unaccounted.extend({**r, "path": path} for r in loose)
        if dry_run:
            log(f"[dry-run] would mask {path}: "
                f"{sorted({r['label'] for r in records})}")
            continue
        mode = os.stat(path).st_mode & 0o777
        _write_text(path, masked, mode)
        files_masked += 1
        scanned[path] = os.path.getmtime(path)
        log(f"masked {path}: {sorted({r['label'] for r in records})} "
            f"({len(records)} value(s))")
    return files_masked, all_records, unaccounted, [], scanned


def page_alarm(unaccounted, run_id):
    """Best-effort ntfy page, same shape as fworker.mask_alarm — never raises."""
    if not unaccounted:
        return
    labels = sorted({r["label"] for r in unaccounted})
    msg = (f"mask-at-rest {run_id}: {len(unaccounted)} unaccounted credential-shaped "
          f"value(s) found at rest ({','.join(labels)}) — masked in place, rotate "
          f"if not already dead")
    try:
        subprocess.run(["/home/lumen/bin/ntfy-send", "-t", "lumen-fleet",
                       "-T", "mask-at-rest: credential found unmasked at rest", msg],
                      timeout=15, capture_output=True)
    except (OSError, subprocess.SubprocessError):
        pass
    log(msg)


def aq_alert_held_unmasked(held_unmasked, run_id):
    """wq#3164: a failed offsite backup used to make process_store return an empty
    (0, [], []) — indistinguishable from "nothing to do" — so main() exited 0 and
    283 unmasked transcripts sat silent until the next audit found them. File an aq
    row instead of trusting exit 0 to carry this; fail-open on a missing/broken aq
    binary, same as read_dir_sweep.aq_alert."""
    if not held_unmasked or not os.path.exists(AQ_BIN):
        return
    title = (f"mask-at-rest {run_id}: offsite backup failed, "
             f"{len(held_unmasked)} file(s) held unmasked")
    body = "restic invoke or backup failed; see mask_at_rest log for these paths:\n" \
        + "\n".join(held_unmasked[:50])
    try:
        subprocess.run([AQ_BIN, "add", AQ_SOURCE, run_id, title, body],
                       check=False, timeout=15)
    except (OSError, subprocess.SubprocessError):
        pass
    log(title)


def write_run_log(run_id, records):
    os.makedirs(RUN_LOG_DIR, exist_ok=True)
    path = os.path.join(RUN_LOG_DIR, f"{run_id}.jsonl")
    with open(path, "w") as fh:
        for rec in records:
            fh.write(json.dumps(rec) + "\n")
    return path


def main(argv=None):
    argv = sys.argv[1:] if argv is None else list(argv)
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--store", choices=("transcripts", "supergateway", "all"),
                    default="all")
    ap.add_argument("--session", default=None,
                    help="scope 'transcripts' to one session id (fworker.mask_alarm "
                         "targeted call), instead of the whole projects tree")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    lock = acquire_lock()
    if lock is None:
        log(f"another mask_at_rest run holds {LOCK_PATH}; skipping (it covers the store)")
        return 0

    run_id = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    rotated = mask_replay.load_rotated_ledger()
    state = _load_state()

    transcripts = (transcript_paths(session=args.session)
                  if args.store in ("transcripts", "all") else [])
    gateways = (gateway_log_paths()
               if args.store in ("supergateway", "all") else [])
    all_paths = transcripts + gateways
    if not all_paths:
        log(f"no files found for --store {args.store}"
            + (f" --session {args.session}" if args.session else ""))
        return 2

    # wq#3164: a targeted single-session call always scans what it was asked for; the
    # nightly full sweep skips any file whose mtime hasn't moved since it was last
    # recorded clean/masked in STATE_PATH — the tree-wide re-read of every transcript
    # every night (1h08m CPU, 5.3G peak on the 2026-09-29 run) cost time proportional
    # to the whole corpus instead of to what changed since the last pass.
    if args.session:
        paths = all_paths
    else:
        paths = (_new_or_changed(transcripts, state.get("transcripts", {})) +
                 _new_or_changed(gateways, state.get("supergateway", {})))

    files_masked, records, unaccounted, held_unmasked, scanned = process_store(
        paths, run_id, args.dry_run, rotated)
    if records:
        write_run_log(run_id, records)
    log(f"run {run_id}: scanned {len(paths)}/{len(all_paths)} file(s), "
        f"masked {files_masked}, {len(records)} value(s) found, "
        f"{len(unaccounted)} unaccounted, {len(held_unmasked)} held unmasked")
    if not args.dry_run:
        page_alarm(unaccounted, run_id)
        aq_alert_held_unmasked(held_unmasked, run_id)
        if not args.session:
            _update_state(state, args.store, transcripts, gateways, scanned)
    return 1 if (unaccounted or held_unmasked) else 0


if __name__ == "__main__":
    sys.exit(main())
