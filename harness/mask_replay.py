"""wq#2307: settle a `[rotation] burned credential` ticket from the frozen artefacts
of the run that fired it — name every credential-shaped string that reached the model
by hash, and say which committed file already contains it.

The tripwire (wq#2275, fworker.mask_alarm) files a pri-1 "rotate today" ticket naming
a pattern LABEL. Until mask-hits-N.json landed (wq#2288, 2026-09-20 22:40) that label
was all that reached disk, so ~20 of these tickets are open against runs whose value
cannot be named from the run dir at all. Each one costs an xhigh worker an hour, and
the answering worker's own reads re-arm the same alarm — wq#2278 -> #2290 -> #2297 /
#2299 -> #2303 (ISSUE-LOG rows 568-571).

The evidence is already on disk. A run leaves its snapshots (`_runs/wq<id>/snapshots`,
the exact bytes a gated write or read froze), its event stream, its read cache (the
file regions the Read tool pulled, `events/read_cache/<session>.jsonl`) and the CLI's
own session transcript. Replaying that corpus under the rule set that was live WHEN
THE TICKET WAS FILED reproduces the hit;
hashing each matched value against the tracked tree then classifies it. A value that
appears verbatim in a committed file is a fixture or a line of source — it cannot be a
live credential, because the tree is public to every worker. A value nothing in the
tree explains is the only shape that is actually burned, and the only one worth an
instinct-4 rotation. That is wq#2290's burn_filter screen applied after the fact, to
runs that predate it.

The transcript is the only source that can answer the question the ticket actually
asks — did a value reach the MODEL — for a value a shell command printed (wq#2311).
Masking runs before any artefact write, so a token in a command's stdout is on disk
only as `<prefix>MASKED`, in a snapshot of nothing and a read cache that never saw a
Read call: the other three sources can be complete and still report a clean run. The
CLI keeps what the model was actually sent in `~/.claude/projects/<slug>/<session>.
jsonl`, unmasked, and a session id from episode.jsonl names it. Reading it here costs
nothing at the model's context — only hashes leave this module — which is precisely
the property that makes a "no rotation" verdict worth signing.

This is the after-the-fact twin of harness/burn_filter.py (wq#2290), which screens the
same way live, before the alarm pages. Two deliberate differences, both of which is why
this does not just call it: burn_filter answers yes/no for a running attempt and puts
the value in a `git grep` argv to do it (its own docstring flags the instinct-4 shape),
while a rotation ticket needs the value NAMED — which file and line already carry it —
over a corpus that is read once, so the tree is loaded in-process and nothing leaves
this module. It also accounts against the kb doctrine log, which burn_filter cannot see
from the single `repo_root` fworker hands it.

No value is ever printed or returned: a record carries the kept prefix, the value's
length, 12 hex of its sha256, and the matching line with the value spliced out.

    python3 harness/mask_replay.py 2288            # legacy rules (what fired)
    python3 harness/mask_replay.py 2288 --rules current

Exit 0: every value is accounted for in the tree — nothing to rotate. Exit 1: at least
one is not. Exit 2: the run left no corpus to replay.
"""
import argparse
import glob
import hashlib
import json
import os
import re
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import masking  # noqa: E402

FOREMAN_ROOT = os.environ.get("FOREMAN_ROOT", "/home/lumen/foreman")
RUNS_ROOT = os.environ.get("FOREMAN_RUNS_ROOT", os.path.join(FOREMAN_ROOT, "_runs"))
EVENTS_DIR = os.environ.get("FOREMAN_EVENTS_DIR", os.path.join(FOREMAN_ROOT, "events"))
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Committed, but in the kb repo rather than this one, and read by nearly every run:
# its rows quote the very shapes (`ghs_solver`, `sk-ant-oat01-…`) that filed half the
# rotation backlog, so a replay that cannot see it reports them unaccounted.
DOCTRINE_TRACKED = [os.environ.get("FOREMAN_DOCTRINE_LOG",
                                   "/home/lumen/kb/ISSUE-LOG.md")]

# Where the CLI keeps session transcripts, one directory per project (the worktree
# path with `/` replaced by `-`). Looked up by session id rather than by slug: a
# resumed attempt keeps its id across worktrees, and the id is what episode.jsonl has.
PROJECTS_ROOT = os.environ.get("CLAUDE_PROJECTS_ROOT",
                               os.path.expanduser("~/.claude/projects"))

# harness/masking.py at aa52797 — the rules that were live from 22:09 to 22:35 on
# 2026-09-20, which is when most of the open rotation backlog was filed. Frozen
# EVIDENCE, not policy: it is deliberately a private copy, so that tightening the
# real table (wq#2292's boundary, wq#2288's tail lookahead, wq#2298's mask-literal
# filter) never silently rewrites the history this tool is here to reproduce.
LEGACY_PATTERNS = [
    ("github", re.compile(r"(ghp_|gho_|ghu_|ghs_|ghr_|github_pat_)[A-Za-z0-9_]{6,}")),
    ("cloudflare", re.compile(r"(cfut_?)[A-Za-z0-9_.-]{6,}")),
    ("sk-key", re.compile(r"(sk-[a-zA-Z-]*)[A-Za-z0-9_-]{16,}")),
    ("slack", re.compile(r"(xox[bpasr]-)[A-Za-z0-9-]{8,}")),
    ("aws", re.compile(r"(AKIA)[0-9A-Z]{16}")),
    ("kv-secret", re.compile(
        r'((TOKEN|KEY|SECRET|PASSWORD|PASSWD|PAT)["\s]*[:=]["\s]*)[A-Za-z0-9._-]{12,}',
        re.IGNORECASE)),
]

CURRENT_PATTERNS = [(label, rx) for label, rx, _ in masking.PATTERNS]


def _read(path, offset=None, limit=None):
    """Text of `path`, or the 1-indexed `offset`..`offset+limit` line window of it."""
    try:
        with open(path, errors="replace") as fh:
            lines = fh.readlines()
    except OSError:
        return None
    if offset is None:
        return "".join(lines)
    start = max(0, int(offset) - 1)
    return "".join(lines[start:start + int(limit)])


def session_ids(ticket_id):
    """Session ids the run reported, for the read caches keyed by them."""
    out = []
    for line in (_read(os.path.join(RUNS_ROOT, f"wq{ticket_id}", "episode.jsonl"))
                 or "").splitlines():
        try:
            sid = json.loads(line).get("session_id")
        except ValueError:
            continue
        if sid and sid not in out:
            out.append(sid)
    return out


def corpus(ticket_id):
    """{source -> text} for everything wq<id>'s run left behind that the model saw.

    Four sources, in the order they explain a hit: the gate snapshots (exact frozen
    bytes of a read or written file), the run/event records, the read cache — which
    holds no content, only `{path, offset, limit}` per Read call, so the window is
    re-cut from the file now — and last the CLI transcript. A file edited since then
    re-cuts imperfectly; the snapshots are the authority and are listed first.

    The transcript goes last on purpose. It is the most complete source (it is the
    model's context verbatim) but the least legible — a hit attributed to it says only
    "some tool printed this", where a snapshot names the file. Ordering it after the
    other three means it adds the values nothing else recorded, and re-attributes
    nothing.

    wq#2318 is the case that needs the transcript. A snapshot of an edited file is the
    PRE-image, so a value the worker WROTE is in none of the snapshots frozen before
    its alarm — it lands in the next freeze, after it. Triaging that alarm by cutting
    the corpus off at the alarm second scores zero and still does not mean the context
    was clean: the Edit's new_string put the value there, and only the transcript
    records it. Bracket such an alarm between the pre- and post-image of the file being
    edited, and keep the transcript in the corpus.
    """
    items, run = {}, os.path.join(RUNS_ROOT, f"wq{ticket_id}")
    snaps = os.path.join(run, "snapshots")
    for name in sorted(os.listdir(snaps) if os.path.isdir(snaps) else []):
        text = _read(os.path.join(snaps, name))
        if text:
            items[f"snapshot:{name}"] = text
    for name in sorted(os.listdir(run) if os.path.isdir(run) else []):
        if name.endswith((".json", ".jsonl", ".md")):
            text = _read(os.path.join(run, name))
            if text:
                items[f"run:{name}"] = text
    for name in sorted(os.listdir(EVENTS_DIR) if os.path.isdir(EVENTS_DIR) else []):
        if name.startswith(f"fworker-wq{ticket_id}-"):
            text = _read(os.path.join(EVENTS_DIR, name))
            if text:
                items[f"events:{name}"] = text
    sessions = session_ids(ticket_id)
    for sid in sessions:
        cache = os.path.join(EVENTS_DIR, "read_cache", f"{sid}.jsonl")
        for line in (_read(cache) or "").splitlines():
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            text = _read(rec.get("path", ""), rec.get("offset"), rec.get("limit"))
            if text:
                items[f"read:{rec['path']}@{rec.get('offset')}"] = text
    for sid in sessions:
        for path in sorted(glob.glob(os.path.join(PROJECTS_ROOT, "*", f"{sid}.jsonl"))):
            text = _read(path)
            if text:
                items[f"transcript:{sid}"] = text
    return items


def tracked_text(repo=REPO_ROOT, extra=DOCTRINE_TRACKED):
    """{path -> text} over the committed tree, plus `extra` absolute paths.

    Committed is the point: everything here is readable by every worker and by every
    reviewer of the PR, so a credential-shaped string that lives in it is a fixture or
    a source line, not a live secret.
    """
    proc = subprocess.run(["git", "-C", repo, "ls-files"],
                          capture_output=True, text=True)
    blobs = {}
    for rel in proc.stdout.split():
        text = _read(os.path.join(repo, rel))
        if text:
            blobs[rel] = text
    for path in extra:
        text = _read(path)
        if text:
            blobs[path] = text
    return blobs


def account(value, tracked):
    """`path:line` of the first committed file holding this exact string, else None."""
    for path, text in tracked.items():
        at = text.find(value)
        if at >= 0:
            return f"{path}:{text.count(chr(10), 0, at) + 1}"
    return None


def _hits(patterns, text, skip_masked_output):
    """(label, prefix, value) per match. `skip_masked_output` drops a match that ends
    in the mask literal — masking._matches' rule (wq#2298), true of the current table
    and false of the legacy one, which is exactly why re-reading a masked artefact
    used to re-arm the alarm."""
    for label, rx in patterns:
        for m in rx.finditer(text):
            if skip_masked_output and m.group(0).endswith(masking.MASK):
                continue
            yield label, m.group(1), m.group(0)[len(m.group(1)):]


def replay(ticket_id, patterns=None, tracked=None, items=None):
    """One record per distinct (label, value) the run put in front of the model.

    Records are hash-only and ordered by corpus source, so the first record for a
    label is the earliest artefact that could have fired that label's alarm.
    """
    patterns = LEGACY_PATTERNS if patterns is None else patterns
    tracked = tracked_text() if tracked is None else tracked
    items = corpus(ticket_id) if items is None else items
    legacy = patterns is LEGACY_PATTERNS
    records, seen = [], set()
    for source, text in items.items():
        for label, prefix, value in _hits(patterns, text, skip_masked_output=not legacy):
            if (label, value) in seen:
                continue
            seen.add((label, value))
            line_at = text.rfind("\n", 0, text.find(prefix + value)) + 1
            end = text.find("\n", line_at)
            line = text[line_at:end if end >= 0 else len(text)]
            today = [v for _, _, v in _hits(CURRENT_PATTERNS, line,
                                            skip_masked_output=True)]
            records.append({
                "label": label,
                "prefix": prefix.strip()[-masking.PREFIX_CHARS:],
                "value_len": len(value),
                "value_sha256_12": hashlib.sha256(
                    value.encode("utf-8", "replace")).hexdigest()[:12],
                "first_seen_in": source,
                "accounted_in": account(value, tracked),
                "still_matches_today": value in today,
                # Splice this value out, then mask whatever else the line holds. On an
                # artefact the second pass is a no-op (it is already masked, and
                # masking is a fixed point), but a transcript line is raw: a record
                # quoted into a ticket must not carry the credential NEXT to the one
                # it is about.
                "line": masking.mask_text(
                    line.replace(value, f"<{label} {len(value)} chars>"))[0][:160],
            })
    return records


def unaccounted(records):
    """Records that are neither committed text nor a closed pattern defect.

    Two things explain a legacy hit benignly. The value is in the tracked tree, or
    today's table no longer reads that string as a credential at all — the second
    covers the dominant class, a called identifier (`base, token = _resolve()`) in a
    worktree edit that never landed in the shape the snapshot froze, so there is no
    tree text to match it against. Only what survives both screens is worth an
    instinct-4 rotation.
    """
    return [r for r in records
            if not r["accounted_in"] and r["still_matches_today"]]


def report(ticket_id, records, out=sys.stdout):
    loose = unaccounted(records)
    print(f"wq#{ticket_id}: {len(records)} credential-shaped values in the run's "
          f"model context, {len(loose)} unaccounted", file=out)
    for rec in records:
        if rec["accounted_in"]:
            mark = "tracked"
        elif not rec["still_matches_today"]:
            mark = "rule-fixed"
        else:
            mark = "BURNED?"
        print(f"  [{mark}] {rec['label']} len={rec['value_len']} "
              f"sha256={rec['value_sha256_12']} prefix={rec['prefix']!r}", file=out)
        print(f"      seen in : {rec['first_seen_in']}", file=out)
        print(f"      accounted: {rec['accounted_in']}", file=out)
        print(f"      line     : {rec['line']}", file=out)
    if loose:
        print("\nROTATE: the values above marked BURNED? appear nowhere in the tracked "
              "tree. Hash a candidate against value_sha256_12 to name one.", file=out)
    else:
        print("\nNo rotation: every value is committed text, or a string today's "
              "masking.PATTERNS no longer reads as a credential.", file=out)
    return 1 if loose else 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("ticket", type=int, help="ticket id of the run that fired")
    ap.add_argument("--rules", choices=("legacy", "current"), default="legacy",
                    help="legacy (default): the pre-2026-09-20T22:35 table that filed "
                         "the backlog; current: today's masking.PATTERNS")
    ap.add_argument("--repo", default=REPO_ROOT)
    ap.add_argument("--extra-tracked", action="append", default=DOCTRINE_TRACKED)
    ap.add_argument("--json", action="store_true", help="records as JSON, no report")
    args = ap.parse_args(argv)

    items = corpus(args.ticket)
    if not items:
        print(f"wq#{args.ticket}: no run artefacts under {RUNS_ROOT} — nothing to "
              f"replay", file=sys.stderr)
        return 2
    records = replay(
        args.ticket,
        patterns=LEGACY_PATTERNS if args.rules == "legacy" else CURRENT_PATTERNS,
        tracked=tracked_text(args.repo, args.extra_tracked),
        items=items)
    if args.json:
        json.dump(records, sys.stdout, indent=2)
        print()
        return 1 if unaccounted(records) else 0
    return report(args.ticket, records)


if __name__ == "__main__":
    sys.exit(main())
