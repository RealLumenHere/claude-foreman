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
same way live, before the alarm pages. The deliberate difference, and why this does not
just call it: burn_filter answers yes/no for a running attempt and puts the value in a
`git grep` argv to do it (its own docstring flags the instinct-4 shape), while a
rotation ticket needs the value NAMED — which file and line already carry it — over a
corpus that is read once, so the tree is loaded in-process and nothing leaves this
module. Accounting against the kb doctrine log was a second difference until wq#2319:
burn_filter now screens against every directory the child was granted, which is where
the tickets this tool settles were coming from.

No value is ever printed or returned: a record carries the kept prefix, the value's
length, 12 hex of its sha256, and the matching line with the value spliced out.

    python3 harness/mask_replay.py 2288             # auto: dated by the run's own start
    python3 harness/mask_replay.py 2288 --rules legacy   # force the table that filed it

Exit 0: every value is accounted for in the tree — nothing to rotate. Exit 1: at least
one is not. Exit 2: the run left no corpus to replay.
"""
import argparse
import calendar
import glob
import hashlib
import json
import os
import re
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import masking  # noqa: E402

FOREMAN_ROOT = os.environ.get("FOREMAN_ROOT", "/home/lumen/foreman")
RUNS_ROOT = os.environ.get("FOREMAN_RUNS_ROOT", os.path.join(FOREMAN_ROOT, "_runs"))
EVENTS_DIR = os.environ.get("FOREMAN_EVENTS_DIR", os.path.join(FOREMAN_ROOT, "events"))
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# wq#2543: wq#2514 AC1 (this tool exits 0) is unreachable for a REAL burn, because
# rotating the value does not remove it from the run's frozen corpus — replay would
# keep reporting it forever. The ledger is the settlement: one entry per rotated
# value, keyed by the same hash `unaccounted()` already carries, holding nothing an
# attacker could use. It lives under foreman state (not this repo) because it is
# runtime fact recorded by whoever confirmed the rotation, not code.
ROTATED_LEDGER_PATH = os.environ.get(
    "FOREMAN_ROTATED_LEDGER", os.path.join(FOREMAN_ROOT, "rotated_ledger.json"))

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

# wq#2715 (ISSUE-LOG row 793, wq#2696): the instant LEGACY_PATTERNS retired. A run
# that started at or after this is scored under CURRENT_PATTERNS by --rules auto,
# never the frozen table above, which does not know today's shapes exist.
LEGACY_CUTOFF_ISO = "2026-09-20T22:35:00Z"
LEGACY_CUTOFF_S = calendar.timegm(
    time.strptime(LEGACY_CUTOFF_ISO, "%Y-%m-%dT%H:%M:%SZ"))

GIT_TIMEOUT_S = 20
HISTORY_DEPTH = 50


def _window(text, offset=None, limit=None):
    """The 1-indexed `offset`..`offset+limit` line window of `text`, or all of it."""
    if text is None or offset is None:
        return text
    start = max(0, int(offset) - 1)
    return "".join(text.splitlines(keepends=True)[start:start + int(limit)])


def _read(path, offset=None, limit=None):
    """Text of `path`, or the 1-indexed `offset`..`offset+limit` line window of it."""
    try:
        with open(path, errors="replace") as fh:
            text = fh.read()
    except OSError:
        return None
    return _window(text, offset, limit)


def _git(repo, *args):
    """stdout of a git command, or "" — history lookups are best-effort evidence."""
    try:
        proc = subprocess.run(["git", "-C", repo, *args],
                              capture_output=True, text=True, timeout=GIT_TIMEOUT_S)
    except (OSError, subprocess.SubprocessError):
        return ""
    return proc.stdout


def blob_at_size(path, size):
    """`path`'s text from the revision where it was exactly `size` bytes, else None.

    The read cache records the file's size at read time, and several of the files a
    rotation run reads are the harness's own masking code, which that very run is
    editing. Re-cutting the window from today's file then reports lines the model
    never saw: replaying wq#2291 off the live tree attributed two sk-key values to
    masking.py:52 and test_masking.py:49, comment lines written 18 minutes AFTER the
    alarm, while the read itself was of a 2137B file that no longer exists on disk.
    Size is enough to identify the revision — it is a fingerprint of the exact bytes,
    checked against history rather than assumed.
    """
    top = _git(os.path.dirname(path) or ".", "rev-parse", "--show-toplevel").strip()
    if not top:
        return None
    rel = os.path.relpath(path, top)
    for sha in _git(top, "log", f"-{HISTORY_DEPTH}", "--format=%h", "--",
                    rel).split():
        at = _git(top, "cat-file", "-s", f"{sha}:{rel}").strip()
        if at.isdigit() and int(at) == size:
            return _git(top, "show", f"{sha}:{rel}")
    return None


def _stat_fp(path):
    """The `stat -c '%s:%Y:%.9Y'` fingerprint guard.sh writes to the read cache, or
    None if `path` doesn't exist. Recomputed on a snapshot copy this confirms the
    copy is byte-for-byte the file a read-cache record names, not just same-sized:
    `shutil.copy2` (action_gate._stow) preserves mtime to the nanosecond, so a
    snapshot's fp only equals the recorded one when it is the exact pre-image.
    """
    try:
        st = os.stat(path)
    except OSError:
        return None
    whole, frac = divmod(st.st_mtime_ns, 1_000_000_000)
    return f"{st.st_size}:{whole}:{whole}.{frac:09d}"


def _sanitize_name(path):
    """action_gate._sanitize's transform, duplicated rather than imported: a
    snapshot filename is `<timestamp>-<sanitized path>`, and matching it here must
    not pull in action_gate's own runtime (vault paths, WORKER_ID, gate state)."""
    return re.sub(r"[^A-Za-z0-9._-]", "_", path.strip("/"))[:120]


def snapshot_for(ticket_id, path, fp):
    """(text, snapshot name) of `ticket_id`'s own frozen pre-image of `path` at
    `fp`, else (None, None).

    This is the run's OWN gate snapshot (`_runs/wq<id>/snapshots/`, the AMBER
    pre-image action_gate.snapshot() takes before a mutating command runs), not a
    same-sized guess reconstructed from git history: a git revision that happens to
    match the recorded size might not be the one this run actually read, where a
    byte-exact fp match against the run's own pre-image can only be that run's
    bytes. Preferred over `blob_at_size` for exactly that reason. Newest first, in
    case the run froze the same path more than once.
    """
    if not fp:
        return None, None
    snaps = os.path.join(RUNS_ROOT, f"wq{ticket_id}", "snapshots")
    if not os.path.isdir(snaps):
        return None, None
    suffix = f"-{_sanitize_name(path)}"
    for name in sorted((n for n in os.listdir(snaps) if n.endswith(suffix)),
                       reverse=True):
        full = os.path.join(snaps, name)
        if _stat_fp(full) == fp:
            return _read(full), name
    return None, None


def session_ids(ticket_id):
    """Session ids the run reported, for the read caches keyed by them.

    Two sources. The event stream's `start` line names the session the moment the
    attempt begins, so a run that was killed before `attempt_end` wrote episode.jsonl
    still resolves (wq#2358); episode.jsonl adds any id a resumed attempt picked up.
    """
    out = []
    streams = [os.path.join(EVENTS_DIR, name)
               for name in sorted(os.listdir(EVENTS_DIR) if os.path.isdir(EVENTS_DIR)
                                  else [])
               if name.startswith(f"fworker-wq{ticket_id}-") and name.endswith(".jsonl")]
    streams.append(os.path.join(RUNS_ROOT, f"wq{ticket_id}", "episode.jsonl"))
    for path in streams:
        for line in (_read(path) or "").splitlines():
            try:
                sid = json.loads(line).get("session_id")
            except (ValueError, AttributeError):
                continue
            if sid and sid not in out:
                out.append(sid)
    return out


def corpus(ticket_id):
    """{source -> text} for everything wq<id>'s run left behind that the model saw.

    Four sources, in the order they explain a hit: the gate snapshots (exact frozen
    bytes of a read or written file), the run/event records, the read cache — which
    holds no content, only `{path, offset, limit, fp}` per Read call, so the window is
    re-cut — and last the CLI transcript. `fp` carries the file's size as read, so a
    file edited since is re-cut from the history revision that matched that size; when
    no revision does, the window is keyed `read-stale:` and the record it produces is
    evidence of nothing. The snapshots are still the authority and are listed first.

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
            text, kind, provenance = _recut(rec, ticket_id)
            if text:
                items[f"{kind}:{rec['path']}@{rec.get('offset')}#{provenance}"] = text
    for sid in sessions:
        for path in sorted(glob.glob(os.path.join(PROJECTS_ROOT, "*", f"{sid}.jsonl"))):
            text = _read(path)
            if text:
                items[f"transcript:{sid}"] = text
    return items


def _parse_ts(value):
    """Epoch seconds for a `ts` field: an ISO-8601 `...Z` string (the event stream's
    own format) or an already-numeric epoch, else None."""
    if isinstance(value, (int, float)):
        return float(value)
    if not isinstance(value, str):
        return None
    try:
        return calendar.timegm(time.strptime(value, "%Y-%m-%dT%H:%M:%SZ"))
    except ValueError:
        try:
            return float(value)
        except ValueError:
            return None


def earliest_event_ts(items):
    """Epoch seconds of the earliest `ts` in any `events:`-sourced corpus text, or
    None if the run left nothing timestamped to date it by.

    The per-attempt event stream's first record (`event: start`) times the moment
    the worker picked the ticket up — already loaded into `items` by `corpus()`, so
    this re-parses text already in hand rather than re-reading the run's files.
    Deciding `--rules auto` (wq#2715) from this, rather than from wall-clock `now`,
    keeps a replay run days later still dated by when the TICKET fired, not by when
    someone got around to checking it.
    """
    earliest = None
    for source, text in items.items():
        if not source.startswith("events:"):
            continue
        for line in text.splitlines():
            try:
                ts = _parse_ts(json.loads(line).get("ts"))
            except (ValueError, AttributeError):
                continue
            if ts is not None and (earliest is None or ts < earliest):
                earliest = ts
    return earliest


def _recut(rec, ticket_id=None):
    """(window, source kind, provenance) for one read-cache record.

    `read` means these are the bytes the model saw — the file is unchanged, the
    run's own frozen pre-image matched byte-for-byte, a git revision at the
    recorded size was found, or the record predates `fp` and cannot be checked.
    `read-stale` means it is today's text standing in for bytes that are gone.
    `provenance` names which of those four resolved it, so the corpus key (and
    from there the report's `first_seen_in`) can say so honestly either way.
    """
    path, offset, limit = rec.get("path", ""), rec.get("offset"), rec.get("limit")
    fp = rec.get("fp")
    was = (fp or ":").split(":")[0]
    try:
        live = os.path.getsize(path)
    except OSError:
        return None, "read", "live"
    if not was.isdigit() or int(was) == live:
        return _read(path, offset, limit), "read", "live"
    if ticket_id is not None:
        text, name = snapshot_for(ticket_id, path, fp)
        if text is not None:
            return _window(text, offset, limit), "read", f"snapshot:{name}"
    historical = blob_at_size(path, int(was))
    if historical is not None:
        return _window(historical, offset, limit), "read", "git-history"
    return _read(path, offset, limit), "read-stale", "unresolved"


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


# wq#2326: a credential-shaped fixture gets SPLIT to keep the tripwire off its own
# source — `"sk-ant-" + "oat01-padded"` at harness/tests/test_token_injection.py:24,
# the split 345bf49 made so that reading that file would stop filing rotation
# tickets. The runtime value still reaches the transcript whole, so a `find` over
# the source misses it and the dummy reports as BURNED?: a rotate-today page for a
# committed fixture, which is the same chain those splits were added to end.
# Only a value whose body STRADDLES the seam is affected, which is why this hid for
# so long — lines 21/22/25 of that file each keep a >=16-char body inside one
# fragment and account fine, and line 24 alone is short enough to need `ant-` from
# the left half. Seam whitespace is [ \t] and never a newline, so closing a seam
# cannot move a line number; a concatenation split across LINES stays unaccounted,
# which is the safe direction to fail.
# wq#2358: the two quotes are the SAME character. With `["']` on both sides, a left
# literal that itself ends in a double quote (test_daemon.py:338's kv fixture) makes the
# leftmost match the `"'` at the end of its body, which closes nothing and leaves the
# real seam standing — so the whole string was never found and the fixture paged as
# BURNED. A mixed-quote join (`"a" 'b'`) is no longer closed; that fails loud, not quiet.
_SEAM = re.compile(r"([\"'])[ \t]*\+?[ \t]*\1")
TRUNCATED_FLOOR = 12  # burn_filter.MIN_SECRET_LEN: the shortest body that can excuse a hit
_JOINED = {}


def _seam_joined(text):
    """`text` with same-line string-concatenation seams closed. Memoised: a value
    that is unaccounted costs a pass over every tracked file, once per value."""
    joined = _JOINED.get(text)
    if joined is None:
        joined = _JOINED[text] = _SEAM.sub("", text)
    return joined


def _find(needle, tracked):
    """`path:line` of the first tracked file holding `needle`, else None."""
    for path, text in tracked.items():
        at = text.find(needle)
        if at >= 0:
            return f"{path}:{text.count(chr(10), 0, at) + 1}"
    for path, text in tracked.items():
        if not _SEAM.search(text):
            continue
        joined = _seam_joined(text)
        at = joined.find(needle)
        if at >= 0:
            return f"{path}:{joined.count(chr(10), 0, at) + 1}"
    return None


# wq#2740: the direct path searched the BODY alone and never checked that the rule's
# `prefix` sat in front of the hit, so the cloudflare row `sha256:a745825001bc` — whose
# value is the 9-char ordinary word `-prefixed` — accounted to 24 unrelated tracked files
# (`vendor-`, `dash-`, `env-var-` + that word) while the string `_hits` actually cut,
# prefix and body glued, was in NONE of them. wq#2733 closed that row "committed text, no
# rotation" on one of those coincidences, citing a file with no `cfut` in it. The same
# coincidence would have cleared a genuinely leaked prefix+body pair.
# `burn_filter._is_accounted` already had the rule this lacked: the whole match first, the
# body alone only once it reaches MIN_SECRET_LEN, "so a short body cannot excuse a match by
# coincidence" (burn_filter.py:216, :237-239). Same floor, so a body long enough to be a
# credible secret still accounts on its own (the split-at-the-prefix-seam mitigation that
# leaves only the body on disk keeps working, ISSUE-LOG rows 814/822), and only a body too
# short to mean anything alone now has to show up with its prefix attached. Fails toward
# unaccounted, i.e. loud.
def account(value, tracked, prefix=""):
    """`path:line` of the first committed file holding this exact string, else None.

    A body under TRUNCATED_FLOOR is only accounted where `prefix` immediately precedes
    it, never on its own. Falls back to the seam-closed view of each file, so a fixture
    assembled from concatenated literals accounts for the whole value its runtime
    produces, then to the value minus a trailing `...` (masking.untruncated, wq#2648),
    then to that cut with the rule's `prefix` attached (masking.untruncated_whole,
    wq#2663).
    """
    needle = prefix + value if prefix and len(value) < TRUNCATED_FLOOR else value
    hit = _find(needle, tracked)
    if hit:
        return hit
    cut = masking.untruncated(value, TRUNCATED_FLOOR)
    if cut:
        return account(cut, tracked)
    whole = masking.untruncated_whole(prefix, value, TRUNCATED_FLOOR)
    return account(whole, tracked) if whole else None


# wq#2557: a run that edits ANOTHER repo (wq#2555 fixed /home/lumen/minerva through a
# linked worktree, per ISSUE-LOG #687) reads that repo's committed prose, and
# `tracked_text` only knew this one. A comment in minerva's index_research.py —
# `~4 chars/token = <an English phrase>` — paged as BURNED? for want of a tree to find it
# in. Only repos the run actually read from are loaded, not the whole managed universe
# (~190 MB across repo_sweep.managed_repos()).
FOREIGN_BLOB_CAP = 1 << 20


def _common_dir(path):
    """Absolute git common dir holding `path` (shared by every linked worktree), or ""."""
    d = path if os.path.isdir(path) else os.path.dirname(path)
    return _git(d or ".", "rev-parse", "--path-format=absolute",
                "--git-common-dir").strip()


def foreign_repos(items, repo=REPO_ROOT):
    """Git common dirs of every repo other than `repo` that the run Read a file from."""
    own = _common_dir(repo)
    found = set()
    for source in items:
        kind, _, rest = source.partition(":")
        if kind in ("read", "read-stale"):
            common = _common_dir(rest.rsplit("@", 1)[0])
            if common and common != own:
                found.add(common)
    return sorted(found)


def committed_text(common_dir):
    """{abs path -> text} of the blobs at the primary checkout's HEAD.

    Committed blobs, not the working tree, and the PRIMARY checkout's HEAD, not the
    linked worktree the run edited: a value the worker wrote into its own branch is
    exactly what must stay unaccounted until it lands.
    """
    top = os.path.dirname(common_dir) if common_dir.endswith("/.git") else common_dir
    listing = _git(common_dir, "ls-tree", "-r", "-z", "--long", "HEAD")
    wanted = []
    for entry in listing.split("\0"):
        meta, _, rel = entry.partition("\t")
        parts = meta.split()
        if len(parts) == 4 and parts[1] == "blob" and parts[3].isdigit() \
                and int(parts[3]) <= FOREIGN_BLOB_CAP:
            wanted.append((parts[2], rel))
    if not wanted:
        return {}
    try:
        proc = subprocess.run(
            ["git", "-C", common_dir, "cat-file", "--batch"],
            input="".join(f"{sha}\n" for sha, _ in wanted).encode(),
            capture_output=True, timeout=GIT_TIMEOUT_S)
    except (OSError, subprocess.SubprocessError):
        return {}
    out, at, blobs = proc.stdout, 0, {}
    for _, rel in wanted:
        nl = out.find(b"\n", at)
        header = out[at:nl].split()
        if len(header) != 3 or not header[2].isdigit():
            break
        size = int(header[2])
        body = out[nl + 1:nl + 1 + size]
        at = nl + 1 + size + 1
        if b"\0" not in body:
            blobs[os.path.join(top, rel)] = body.decode("utf-8", "replace")
    return blobs


def load_rotated_ledger(path=None):
    """{value_sha256_12 -> record} for every settled rotation, or {} if none yet.

    Written by the operator/orch via `record-rotated` (below) once a rotation is
    CONFIRMED — the old value now returns 401 — never by a replay run itself. Only
    the hash, a timestamp and the confirming ticket are ever stored; the file holds
    nothing that could re-arm the alarm it settles.
    """
    path = ROTATED_LEDGER_PATH if path is None else path
    try:
        with open(path) as fh:
            records = json.load(fh)
    except (OSError, ValueError):
        return {}
    return {r["value_sha256_12"]: r for r in records if r.get("value_sha256_12")}


def record_rotated(sha256_12, ticket, note=None, path=None, rotated_at=None):
    """Append one ledger entry, de-duplicated on `sha256_12`. Returns the record.

    Takes a hash, never a value — the caller (the operator/orch that just confirmed
    the rotation) reads `sha256_12` off a prior mask_replay BURNED? record, not off
    the credential itself.
    """
    path = ROTATED_LEDGER_PATH if path is None else path
    try:
        with open(path) as fh:
            records = json.load(fh)
    except (OSError, ValueError):
        records = []
    record = {
        "value_sha256_12": sha256_12,
        "rotated_at": rotated_at or time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "ticket": str(ticket),
    }
    if note:
        record["note"] = note
    records = [r for r in records if r.get("value_sha256_12") != sha256_12]
    records.append(record)
    dirname = os.path.dirname(path)
    if dirname:
        os.makedirs(dirname, exist_ok=True)
    with open(path, "w") as fh:
        json.dump(records, fh, indent=2)
        fh.write("\n")
    return record


def _hits(patterns, text, skip_masked_output):
    """(label, prefix, value) per match. `skip_masked_output` drops a match that ends
    in the mask literal — masking._matches' rule (wq#2298), true of the current table
    and false of the legacy one, which is exactly why re-reading a masked artefact
    used to re-arm the alarm.

    Today's table with the skip on is exactly what the tripwire runs, so it reads
    through masking.raw_matches() — multi-pass, own-output screened — instead of one
    raw finditer per rule, which missed every value a glued prefix frees on a later
    pass (wq#2875: wq#2848's aws and slack hits never reached its own replay)."""
    if patterns is CURRENT_PATTERNS and skip_masked_output:
        yield from masking.raw_matches(text)[0]
        return
    for label, rx in patterns:
        for m in rx.finditer(text):
            if skip_masked_output and m.group(0).endswith(masking.MASK):
                continue
            yield label, m.group(1), m.group(0)[len(m.group(1)):]


def _context(line, value, label):
    """The masked text AROUND the hit — the leak path — not the head of its line.

    wq#2317: the window used to be the line's first 160 characters, which is the same
    thing on a source file and useless on a transcript, where one physical line is a
    whole JSONL record. Every transcript-sourced record in the report opened
    `{"parentUuid":"…","message":{"model":…` and never reached the value, so the one
    field that says WHERE a credential printed from said nothing — and naming the
    printing command is the difference between "8 unaccounted values" and "8 synthetic
    literals this worker typed into its own Edit".

    Masked first, cut second, per masking.scan()'s rule (wq#1441): cutting a raw window
    first can hand back a credential the substitution never saw. Splicing the record's
    own value out before the mask is what guarantees the marker to center on.
    """
    marker = f"<{label} {len(value)} chars>"
    masked = masking.mask_text(line.replace(value, marker))[0]
    at = masked.find(marker)
    if at < 0:  # unreachable via the splice above; a head window still beats nothing
        return masked[:masking.CONTEXT_CHARS]
    start, end = max(0, at - masking.CONTEXT_CHARS), \
        at + len(marker) + masking.CONTEXT_CHARS
    return ("…" if start else "") + masked[start:end] + ("…" if end < len(masked) else "")


def replay(ticket_id, patterns=None, tracked=None, items=None, verdict=None,
           skip_masked=None, rotated=None):
    """One record per distinct (label, value) the run put in front of the model.

    Records are hash-only and ordered by corpus source, so the first record for a
    label is the earliest artefact that could have fired that label's alarm.

    `verdict(label, prefix, value)` is called in-process with the value and returns a
    dict merged into the record; it is the seam mask_triage classifies through
    without the value ever leaving this loop. `skip_masked` says whether a match that
    ends in the mask literal is dropped (see _hits); the default is what the bundled
    LEGACY_PATTERNS need, and a caller replaying a historic table passes its own.
    `rotated` is the {value_sha256_12 -> ledger record} map (default: the on-disk
    ledger) that settles a value already rotated, regardless of what the tree or
    today's rules say about it.
    """
    patterns = LEGACY_PATTERNS if patterns is None else patterns
    tracked = tracked_text() if tracked is None else tracked
    items = corpus(ticket_id) if items is None else items
    rotated = load_rotated_ledger() if rotated is None else rotated
    if skip_masked is None:
        skip_masked = patterns is not LEGACY_PATTERNS
    records, seen = [], set()
    for source, text in items.items():
        for label, prefix, value in _hits(patterns, text, skip_masked_output=skip_masked):
            if (label, value) in seen:
                continue
            seen.add((label, value))
            line_at = text.rfind("\n", 0, text.find(prefix + value)) + 1
            end = text.find("\n", line_at)
            line = text[line_at:end if end >= 0 else len(text)]
            today_hits = [(p, v) for _, p, v in _hits(CURRENT_PATTERNS, line,
                                                       skip_masked_output=True)]
            today = [v for _, v in today_hits]
            # wq#2423: a widened prefix that recuts this same span into a DIFFERENT
            # value string must not read as a retired rule. The line is only closed
            # when today's table stops firing on it altogether, or when whatever it
            # cuts today is itself already accounted for (tracked text, not this
            # exact-value comparison, which a recut always fails).
            today_closed = not today_hits or any(
                account(v, tracked, p) for p, v in today_hits)
            sha12 = hashlib.sha256(value.encode("utf-8", "replace")).hexdigest()[:12]
            records.append({
                "label": label,
                "prefix": prefix.strip()[-masking.PREFIX_CHARS:],
                "value_len": len(value),
                "value_sha256_12": sha12,
                "first_seen_in": source,
                "faithful": not source.startswith("read-stale:"),
                "accounted_in": account(value, tracked, prefix),
                "still_matches_today": value in today,
                "today_closed": today_closed,
                "rotated": rotated.get(sha12),
                # Splice this value out, then mask whatever else the line holds. On an
                # artefact the second pass is a no-op (it is already masked, and
                # masking is a fixed point), but a transcript line is raw: a record
                # quoted into a ticket must not carry the credential NEXT to the one
                # it is about.
                "line": _context(line, value, label),
            })
            if verdict:
                records[-1].update(verdict(label, prefix, value))
    return records


def unaccounted(records):
    """Records that are neither committed text, a closed pattern defect, nor rotated.

    Three things explain a legacy hit benignly. The value is in the tracked tree,
    today's table no longer reads that string as a credential at all — the second
    covers the dominant class, a called identifier (`base, token = _resolve()`) in a
    worktree edit that never landed in the shape the snapshot froze, so there is no
    tree text to match it against — or the ledger says it was rotated: a value that
    now 401s is settled no matter what the tree says about the string that used to
    work. Only what survives all three screens is worth an instinct-4 rotation.

    A record from an unfaithful re-cut is not one of them: it was scored off text
    that replaced what the run actually read, so it can no more demand a rotation
    than it can excuse one. It stays in the report, marked, because the gap it names
    is real — that window's true bytes are simply unrecoverable from this run.
    """
    return [r for r in records
            if not r.get("rotated")
            and r.get("faithful", True)
            and not r["accounted_in"] and not r.get("today_closed", not r["still_matches_today"])]


def suppressed_records(ticket_id, items=None):
    """What the rule DECLINED to alarm on during wq<id>'s run (wq#2677).

    Two sources, in the order that can answer honestly. First the run's own
    mask-hits-N.json: those rows were written by the process that read the stream, so
    they say what the table it was masking with actually suppressed, and they exist
    even when the run never alarmed — which is the case the whole record exists for
    (a suppression leaves no mask, no label and no hit record, so silence downstream).

    Second, and only when the run left no such row, today's table re-derived over the
    corpus. That is a proxy: it answers what the CURRENT rule would decline to read in
    text the run put in front of the model, which for a run that predates wq#2677 is
    the only answer available. `derived` marks the difference, because a carve-out
    added since the run would show up here and could not have been what went silent.
    """
    rows, run = [], os.path.join(RUNS_ROOT, f"wq{ticket_id}")
    for path in sorted(glob.glob(os.path.join(run, "mask-hits-*.json"))):
        try:
            with open(path) as fh:
                written = json.load(fh)
        except (OSError, ValueError):
            continue
        rows += [dict(r, source=os.path.basename(path), derived=False)
                 for r in written if isinstance(r, dict) and r.get("suppressed")]
    if rows:
        return rows
    for source, text in (corpus(ticket_id) if items is None else items).items():
        rows += [dict(r, source=source, derived=True)
                 for r in masking.suppressions(text)]
    seen, out = set(), []
    for row in rows:
        key = (row.get("suppressed"), row.get("value_sha256_12"))
        if key in seen:
            continue
        seen.add(key)
        out.append(row)
    return out


def report_suppressed(ticket_id, rows, out=sys.stdout):
    """One line per suppressed candidate. Never a rotation: a suppression is the rule
    working as written, and the question it answers is whether it was written right —
    a pile of rows under one reason is an overbroad carve-out, which is what wq#2288's
    lookahead was and what nothing could see until these rows existed."""
    if not rows:
        return
    kind = "derived from today's table" if rows[0].get("derived") else "recorded by the run"
    print(f"\nwq#{ticket_id}: {len(rows)} candidate(s) the kv-secret rule suppressed "
          f"({kind}) — audit only, no rotation:", file=out)
    for row in rows:
        print(f"  [{row['suppressed']}] {row.get('label', 'kv-secret')} "
              f"len={row['value_len']} sha256={row['value_sha256_12']} "
              f"prefix={row.get('prefix', '')!r} in {row.get('source', '?')}", file=out)


def report(ticket_id, records, out=sys.stdout):
    loose = unaccounted(records)
    print(f"wq#{ticket_id}: {len(records)} credential-shaped values in the run's "
          f"model context, {len(loose)} unaccounted", file=out)
    for rec in records:
        if rec.get("rotated"):
            mark = "ROTATED"
        elif not rec.get("faithful", True):
            mark = "re-cut"
        elif rec["accounted_in"]:
            mark = "tracked"
        elif rec.get("today_closed", not rec["still_matches_today"]):
            mark = "rule-fixed"
        else:
            mark = "BURNED?"
        # wq#2695: a prefix is only non-secret under the rule that cut it. wq#2677's
        # branch name (`...mask-alarm-record...`) was a legacy sk-key prefix, today's
        # sk-key rule reads that as a key, and printing it raw paged the replay run.
        prefix = masking.mask_text(rec["prefix"])[0]
        print(f"  [{mark}] {rec['label']} len={rec['value_len']} "
              f"sha256={rec['value_sha256_12']} prefix={prefix!r}", file=out)
        print(f"      seen in : {rec['first_seen_in']}", file=out)
        print(f"      accounted: {rec['accounted_in']}", file=out)
        if rec.get("rotated"):
            print(f"      rotated  : {rec['rotated'].get('rotated_at')} "
                  f"({rec['rotated'].get('ticket')})", file=out)
        print(f"      line     : {rec['line']}", file=out)
    if loose:
        print("\nROTATE: the values above marked BURNED? appear nowhere in the tracked "
              "tree. Hash a candidate against value_sha256_12 to name one.", file=out)
    else:
        print("\nNo rotation: every value is committed text, or a string today's "
              "masking.PATTERNS no longer reads as a credential.", file=out)
    recut = [r for r in records if not r.get("faithful", True)]
    if recut:
        print(f"{len(recut)} value(s) marked re-cut were scored off a file that has "
              f"changed since the run read it, with no revision left at the size it "
              f"read; they are neither evidence of a burn nor of a fixture.", file=out)
    return 1 if loose else 0


def main_record_rotated(argv):
    """`record-rotated` subcommand: the operator/orch's tool for settling a burn.

    Run once a rotation is CONFIRMED (the old value now returns 401). Takes the
    hash off a prior mask_replay BURNED? record — never the value — so this tool
    cannot leak the credential it is settling.
    """
    ap = argparse.ArgumentParser(
        prog="mask_replay.py record-rotated",
        description=main_record_rotated.__doc__.splitlines()[0])
    ap.add_argument("--sha256-12", required=True, dest="sha256_12",
                    help="value_sha256_12 from a prior mask_replay BURNED? record")
    ap.add_argument("--ticket", required=True,
                    help="ticket that confirmed the rotation")
    ap.add_argument("--note", default=None)
    ap.add_argument("--rotated-at", default=None,
                    help="UTC timestamp the rotation was confirmed at "
                         "(%%Y-%%m-%%dT%%H:%%M:%%SZ); default: now")
    ap.add_argument("--rotated-ledger", default=ROTATED_LEDGER_PATH)
    args = ap.parse_args(argv)
    record = record_rotated(args.sha256_12, args.ticket, note=args.note,
                            path=args.rotated_ledger, rotated_at=args.rotated_at)
    print(f"recorded rotation: sha256={record['value_sha256_12']} "
          f"ticket={record['ticket']} at={record['rotated_at']}")
    return 0


def main(argv=None):
    argv = sys.argv[1:] if argv is None else list(argv)
    if argv[:1] == ["record-rotated"]:
        return main_record_rotated(argv[1:])

    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("ticket", type=int, help="ticket id of the run that fired")
    ap.add_argument("--rules", choices=("auto", "legacy", "current"), default="auto",
                    help="auto (default, wq#2715): current for a run that started at "
                         f"or after the legacy table's {LEGACY_CUTOFF_ISO} retirement, "
                         "legacy for one before it or one whose start can't be dated; "
                         "legacy/current: force a table regardless of the run's date")
    ap.add_argument("--repo", default=REPO_ROOT)
    ap.add_argument("--extra-tracked", action="append", default=DOCTRINE_TRACKED)
    ap.add_argument("--json", action="store_true", help="records as JSON, no report")
    ap.add_argument("--suppressed", action="store_true",
                    help="only what the kv-secret rule declined to alarm on in this "
                         "run (wq#2677): audit trail, always exits 0")
    ap.add_argument("--rotated-ledger", default=ROTATED_LEDGER_PATH,
                    help="path to the rotated-value ledger (default: %(default)s)")
    args = ap.parse_args(argv)

    if args.suppressed:
        # wq#2677: answered before the expensive half, and before the corpus is even
        # built — a run that recorded its own suppressions needs neither the corpus nor
        # the tracked tree, since nothing here is a verdict. Only the fallback for a run
        # older than wq#2677 pays for the corpus, inside suppressed_records.
        dropped = suppressed_records(args.ticket)
        if args.json:
            json.dump(dropped, sys.stdout, indent=2)
            print()
        else:
            report_suppressed(args.ticket, dropped)
        return 0
    items = corpus(args.ticket)
    if not items:
        print(f"wq#{args.ticket}: no run artefacts under {RUNS_ROOT} — nothing to "
              f"replay", file=sys.stderr)
        return 2
    started = earliest_event_ts(items)
    same_day = started is not None and started >= LEGACY_CUTOFF_S
    if args.rules == "auto":
        rules = "current" if same_day else "legacy"
        started_str = (time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(started))
                       if started is not None else "unknown")
        print(f"wq#{args.ticket}: --rules auto -> {rules} (run started {started_str}, "
              f"legacy table retired {LEGACY_CUTOFF_ISO})", file=sys.stderr)
    else:
        rules = args.rules
        if rules == "legacy" and same_day:
            print(f"wq#{args.ticket}: WARNING --rules legacy forced on a run that "
                  f"started {time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(started))}, "
                  f"at or after the table's {LEGACY_CUTOFF_ISO} retirement — this "
                  "replays the WRONG table for this ticket's own date (ISSUE-LOG row "
                  "793, wq#2696)", file=sys.stderr)
    tracked = tracked_text(args.repo, args.extra_tracked)
    for common in foreign_repos(items, args.repo):
        tracked.update(committed_text(common))
    records = replay(
        args.ticket,
        patterns=LEGACY_PATTERNS if rules == "legacy" else CURRENT_PATTERNS,
        tracked=tracked,
        items=items,
        rotated=load_rotated_ledger(args.rotated_ledger))
    if args.json:
        # wq#2677: `--json` keeps its list shape — a caller reading it today gets what
        # it got yesterday, and asks for the suppression trail with --suppressed.
        json.dump(records, sys.stdout, indent=2)
        print()
        return 1 if unaccounted(records) else 0
    code = report(args.ticket, records)
    # Printed after the verdict and never mixed into it: a suppression is audit, so it
    # must not be able to move the exit code, which is the rotation verdict.
    report_suppressed(args.ticket, suppressed_records(args.ticket, items=items))
    return code


if __name__ == "__main__":
    sys.exit(main())
