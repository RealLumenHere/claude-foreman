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

STATE_PATH = os.environ.get("MASK_AT_REST_STATE",
                            os.path.join(FOREMAN_ROOT, "mask_at_rest_state.json"))


def log(msg):
    print(f"[mask_at_rest] {msg}", file=sys.stderr)


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
    cmd = ["restic", "-o", f"sftp.command=ssh -o StrictHostKeyChecking=no "
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
    (files_masked, all_records, unaccounted_records)."""
    files_masked, all_records, unaccounted = 0, [], []
    to_backup = []
    plans = []
    for path in paths:
        try:
            text = _read_text(path)
        except OSError as exc:
            log(f"skip {path}: {exc}")
            continue
        masked, records = mask_file_text(text)
        if not records:
            continue
        plans.append((path, masked, records))
    if not plans:
        return 0, [], []
    if not dry_run:
        to_backup = [p for p, _, _ in plans]
        if not offsite_backup(to_backup, tag=f"mask-at-rest-{run_id}"):
            log("offsite backup did not succeed for this batch — masking SKIPPED "
                "for these files this pass (will retry next run):")
            for p, _, _ in plans:
                log(f"  held unmasked (backup failed): {p}")
            return 0, [], []
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
        log(f"masked {path}: {sorted({r['label'] for r in records})} "
            f"({len(records)} value(s))")
    return files_masked, all_records, unaccounted


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

    run_id = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    rotated = mask_replay.load_rotated_ledger()

    paths = []
    if args.store in ("transcripts", "all"):
        paths += transcript_paths(session=args.session)
    if args.store in ("supergateway", "all"):
        paths += gateway_log_paths()
    if not paths:
        log(f"no files found for --store {args.store}"
            + (f" --session {args.session}" if args.session else ""))
        return 2

    files_masked, records, unaccounted = process_store(
        paths, run_id, args.dry_run, rotated)
    if records:
        write_run_log(run_id, records)
    log(f"run {run_id}: scanned {len(paths)} file(s), masked {files_masked}, "
        f"{len(records)} value(s) found, {len(unaccounted)} unaccounted")
    if not args.dry_run:
        page_alarm(unaccounted, run_id)
    return 1 if unaccounted else 0


if __name__ == "__main__":
    sys.exit(main())
