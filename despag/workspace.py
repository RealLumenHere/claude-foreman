"""wq#1810: despag P1-5 — workspace = git worktree per session.

PHASE0_WIRING_SPEC.md RECOMMENDATION step 5 + C.3 + D.1 + E.4/E.6. Mirrors the
SEMANTICS of research/harnessrouter-ref/src/runner/server.py's workspace/checkpoint/
collection-cursor machinery (HR:127-189 reap+identity, HR:4827-4950 checkpoint+
cursor), not its code: HR's session workspace is a standalone `git init` per session
because HR has no "declared repo" concept. despag does — the harness a session runs
under names a real repo (harnesses.yaml `repo:` key, a despag-local
additionalProperties field, same standing as `enabled`), so a session's workspace is
a `git worktree add` of THAT repo, not an unrelated fresh history. The conformance
harnesses have no product repo to point at, so they share one scratch repo this
module initialises once under DESPAG_DATA_DIR/scratch-repo.

Because the workspace is a LINKED worktree, its `.git` is a one-line pointer file
into the declared repo's `.git/worktrees/<name>` bookkeeping, not a self-contained
history — unlike HR's standalone-repo design, that pointer is only valid on the same
host with the same declared repo still registered. `.git` is therefore excluded from
the checkpoint tarball (CHECKPOINT_EXCLUDE) and rehydrate() always re-establishes the
worktree link (via ensure_workspace) before untarring everything else on top of it,
rather than trusting a `.git` pointer that travelled in a tarball taken before a
reap pruned the original registration.

Five pieces, each independently testable (despag/tests/test_workspace.py):
  identity   — sanitize_sid/workspace_path/ensure_workspace: session id (HR's
               _SID_SAFE substitution, not a strict validator) IS the directory name
               under DESPAG_WORKTREES_ROOT (default fworker.WORKTREES_ROOT + "/despag",
               duplicated rather than imported — same standing as despag/app.py's
               LOCAL_POOL_MODELS).
  TTL reap   — reap_stale(): directory mtime is the record (touch() on every turn,
               E.6); never reaps a session with a live control-store lease; runs at
               session-create time (ensure_workspace) and via the `despag reap` CLI
               (despag/cli.py). A reaped session is remembered (was_reaped) so a
               later ensure_workspace raises SessionExpired instead of silently
               creating a same-named but discontinuous workspace.
  checkpoint/
  rehydrate  — checkpoint(): commit, tar (credentials/scratch/.git excluded, any
               depth), spool to disk, sha256, BlobStore sessions/<sid>/workspace.tgz,
               sha marker. rehydrate(): probe=<sha> short-circuits against the
               marker; otherwise the blob is fully read into memory (FileBlobStore
               has no partial-read failure mode, but the spool-to-disk-before-wipe
               shape is kept anyway so a future streaming BlobStore doesn't have to
               change this function) before anything already on disk is touched.
  cursor     — refs/lumen/collected, advanced ONLY by advance_cursor() — never by
               checkpoint()'s own commit (E.4: HEAD is not a collection cursor,
               `foreman/harness/fworker.py:668-684`'s bug this generalises the fix
               for). build_snapshot() diffs against this ref, so a crash between
               capture and ack reproduces the identical delta next time.
  D.1        — build_snapshot()/capture_and_store_snapshot()/previous_snapshot():
               the canonical workspace_snapshot frame, stored as a blob and fed
               forward to exactly the next task in the same session.
"""
from __future__ import annotations

import contextlib
import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

from despag.backing import BlobStore
from despag.backing.control_sqlite import ControlStore

# ── identity ──────────────────────────────────────────────────────────────────
# Substitution, not validation (research/harnessrouter-ref/src/runner/server.py:79) —
# any identifier becomes A directory name, never rejected outright.
_SID_SAFE = re.compile(r"[^A-Za-z0-9._-]")

TTL_HOURS = float(os.environ.get("DESPAG_WORKSPACE_TTL_HOURS", "72") or 0)


def sanitize_sid(identifier: str) -> str:
    return _SID_SAFE.sub("_", (identifier or "").strip())[:120] or "_default"


def _fworker_worktrees_root() -> str:
    """Mirrors foreman/harness/fworker.py:98 WORKTREES_ROOT's default. despag does not
    import fworker — that module is the ticket-execution runner, an unrelated
    dependency surface — so the constant is duplicated small and static, exactly like
    despag/app.py:41 LOCAL_POOL_MODELS."""
    return "/home/lumen/worktrees"


def workspace_root() -> Path:
    root = os.environ.get("DESPAG_WORKTREES_ROOT") or os.path.join(_fworker_worktrees_root(), "despag")
    p = Path(root).expanduser()
    p.mkdir(parents=True, exist_ok=True)
    return p


def workspace_path(sid: str) -> Path:
    return workspace_root() / sanitize_sid(sid)


def data_dir() -> Path:
    """Read DESPAG_DATA_DIR fresh on every call (same convention as
    despag.backing.make_backing() and despag.app's token/control paths) rather than
    freezing it at import time, so tests can point separate sessions at separate
    tmp_path roots via monkeypatch without module-reload games."""
    p = Path(os.environ.get("DESPAG_DATA_DIR", "~/despag-data")).expanduser()
    p.mkdir(parents=True, exist_ok=True, mode=0o700)
    return p


def _spool_dir() -> Path:
    p = data_dir() / "spool"
    p.mkdir(parents=True, exist_ok=True)
    return p


class SessionExpired(Exception):
    """404 session_expired (lifecycle.md), distinct from 404 session_not_found: the
    sid is well-formed and once had a workspace, but the TTL reaper already collected
    it. session_not_found (a sid despag never issued at all) is not this module's
    call — that lookup lives wherever sessions are recorded (control store / graph)."""


_GIT_ENV = {"GIT_AUTHOR_NAME": "despag", "GIT_AUTHOR_EMAIL": "despag@lumen.local",
            "GIT_COMMITTER_NAME": "despag", "GIT_COMMITTER_EMAIL": "despag@lumen.local"}


def _git(cwd, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True,
                          env={**os.environ, **_GIT_ENV})


# ── scratch repo (conformance harnesses have no product repo of their own) ─────────
def ensure_scratch_repo() -> str:
    path = data_dir() / "scratch-repo"
    if not (path / ".git").exists():
        path.mkdir(parents=True, exist_ok=True)
        _git(path, "init", "-q")
        _git(path, "config", "user.email", _GIT_ENV["GIT_AUTHOR_EMAIL"])
        _git(path, "config", "user.name", _GIT_ENV["GIT_AUTHOR_NAME"])
        (path / "README.md").write_text("despag scratch repo — conformance harness workspaces.\n")
        _git(path, "add", "-A")
        _git(path, "commit", "-q", "-m", "scratch repo init", "--allow-empty")
    return str(path)


def repo_for_harness(harness: dict) -> str:
    repo = (harness or {}).get("repo") or "scratch"
    return ensure_scratch_repo() if repo == "scratch" else repo


# ── TTL reap (E.6: mtime is the record; a running turn keeps it fresh via touch) ───
def touch(sid: str) -> None:
    p = workspace_path(sid)
    if p.exists():
        os.utime(p, None)


def _reaped_marker_path(sid: str) -> Path:
    return data_dir() / "reaped" / f"{sanitize_sid(sid)}.json"


def _mark_reaped(sid: str) -> None:
    p = _reaped_marker_path(sid)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"reaped_at": time.time()}))


def was_reaped(sid: str) -> bool:
    return _reaped_marker_path(sid).exists()


def _repo_marker_path(sid: str) -> Path:
    return data_dir() / "workspace_repo" / f"{sanitize_sid(sid)}.repo"


def remove_workspace(sid: str) -> None:
    """Drop a session's worktree registration from its declared repo, then remove the
    directory. Always records `was_reaped` so a later ensure_workspace for the same
    sid raises SessionExpired instead of silently starting a discontinuous session."""
    sid = sanitize_sid(sid)
    path = workspace_path(sid)
    marker = _repo_marker_path(sid)
    repo = marker.read_text().strip() if marker.exists() else ""
    if repo:
        _git(repo, "worktree", "remove", "--force", str(path))
    if path.exists():
        shutil.rmtree(path, ignore_errors=True)
    marker.unlink(missing_ok=True)
    _checkpoint_marker_path(sid).unlink(missing_ok=True)
    _mark_reaped(sid)


async def reap_stale(control: ControlStore, *, keep: str = "", ttl_hours: float | None = None) -> list[str]:
    """C.3/E.6: delete session workspaces untouched (by mtime) for longer than the
    TTL, UNLESS a control-store lease is currently live on them (a running turn must
    never be reaped out from under itself). `keep` is the sid about to be
    (re)created, so a create can never race its own reap. ttl_hours<=0 disables."""
    ttl = TTL_HOURS if ttl_hours is None else ttl_hours
    if ttl <= 0:
        return []
    cutoff = time.time() - ttl * 3600
    keep_sid = sanitize_sid(keep) if keep else ""
    try:
        entries = list(os.scandir(workspace_root()))
    except OSError:
        return []
    reaped = []
    for e in entries:
        if not e.is_dir() or e.name == keep_sid:
            continue
        try:
            if e.stat().st_mtime >= cutoff:
                continue
        except OSError:
            continue
        if await control.lease_is_live(e.name):
            continue  # never reap a directory with a live lease
        remove_workspace(e.name)
        reaped.append(e.name)
    return reaped


# ── identity: session implicit on first task ────────────────────────────────────
async def ensure_workspace(sid: str, harness: dict, control: ControlStore) -> Path:
    """Session implicit on first task (lifecycle.md). First call for a fresh sid
    creates the workspace as a `git worktree add` of the harness's declared repo. A
    later call for the same sid whose directory is gone because the TTL reaper
    collected it raises SessionExpired rather than quietly starting a new,
    discontinuous session under the old id."""
    sid = sanitize_sid(sid)
    path = workspace_path(sid)
    if path.exists():
        touch(sid)
        return path
    if was_reaped(sid):
        raise SessionExpired(sid)

    await reap_stale(control, keep=sid)  # C.3: reap runs "at the moment a session is known to start fresh"

    repo = repo_for_harness(harness)
    branch = f"despag/{sid}"
    p = _git(repo, "worktree", "add", str(path), "-b", branch, "HEAD")
    if p.returncode != 0 and "already exists" in (p.stderr or ""):
        p = _git(repo, "worktree", "add", str(path), branch)
    if p.returncode != 0:
        raise RuntimeError(f"git worktree add failed for session {sid!r}: {p.stderr.strip()}")

    marker = _repo_marker_path(sid)
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(repo)

    (path / ".harness" / "home").mkdir(parents=True, exist_ok=True)
    (path / ".gitignore").write_text("\n".join([
        "# despag: never persist secrets/scratch in the session checkpoint",
        ".claude/", ".credentials.json", ".env", "tmp/", "node_modules/", ".venv/", "__pycache__/", "",
    ]))
    _git(path, "add", "-A")
    _git(path, "commit", "-q", "-m", "workspace init", "--allow-empty")
    touch(sid)
    return path


def harness_home(sid: str) -> Path:
    """HOME redirect target: ~/.claude/projects transcripts land under here so they
    travel inside the checkpoint tarball and `--resume` works after a rehydrate."""
    return workspace_path(sid) / ".harness" / "home"


def resume_lost(sid: str, prior_response_id: str) -> bool:
    """E.8: best-effort check for whether `--resume` targeting prior_response_id would
    silently start a fresh conversation because no transcript mentioning it survived
    (HOME redirect broken, or rehydrated from a checkpoint taken before that resume
    target existed). fworker's --resume path (FW:2237-2260) checks CLI-liveness only,
    not transcript presence — this is the check E.8 says it is missing."""
    projects = harness_home(sid) / ".claude" / "projects"
    if not projects.is_dir():
        return True
    needle = prior_response_id.encode()
    for jsonl in projects.rglob("*.jsonl"):
        try:
            if needle in jsonl.read_bytes():
                return False
        except OSError:
            continue
    return True


# ── checkpoint / rehydrate ──────────────────────────────────────────────────────
# `.claude` is anchored to the workspace ROOT (`./.claude`) on purpose. GNU tar's
# --exclude=NAME with no slash in it matches EVERY path component, so the bare
# `.claude` form also stripped harness_home()/.claude/projects/*.jsonl — the CLI
# transcripts that travel inside the tarball so `--resume` survives a rehydrate, and
# the exact files resume_lost() exists to detect the loss of (wq#1861). The secrets
# under a HOME-redirect .claude are `.credentials.json` and the CLI's `config.json`
# (the same two files a root `.claude` holds): both stay excluded at any depth by
# their own entries below — a pattern WITH a slash is matched against the whole
# member name, so `.claude/config.json` strips only that one file and leaves
# `.claude/projects/*.jsonl` alone.
CHECKPOINT_EXCLUDE = [
    f"--exclude={p}" for p in
    (".git", "./.claude", ".claude/config.json", ".credentials.json",
     ".env", "tmp", "node_modules", ".venv", "__pycache__")
]


def _checkpoint_marker_path(sid: str) -> Path:
    return data_dir() / "checkpoints" / f"{sanitize_sid(sid)}.sha"


def _set_checkpoint_marker(sid: str, sha: str) -> None:
    p = _checkpoint_marker_path(sid)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(sha)


def checkpoint_marker(sid: str) -> str | None:
    p = _checkpoint_marker_path(sid)
    return p.read_text().strip() if p.exists() else None


async def checkpoint(sid: str, blob: BlobStore) -> dict:
    """git add -A && commit --allow-empty, tar (credentials/scratch/.git excluded at
    any depth, the root `.claude` excluded at the root only — see CHECKPOINT_EXCLUDE)
    spooled to disk not RAM, sha256 over the spooled file, stored via
    BlobStore as sessions/<sid>/workspace.tgz; the marker records the sha so a
    rehydrate probe can short-circuit without re-fetching."""
    sid = sanitize_sid(sid)
    path = workspace_path(sid)
    if not path.exists():
        raise SessionExpired(sid)
    _git(path, "add", "-A")
    _git(path, "commit", "-q", "-m", f"checkpoint {int(time.time())}", "--allow-empty")

    fd, tar_path = tempfile.mkstemp(suffix=".tgz", dir=str(_spool_dir()))
    try:
        with os.fdopen(fd, "wb") as out:
            proc = subprocess.run(
                ["tar", "-I", "gzip -1", "-cf", "-", *CHECKPOINT_EXCLUDE, "-C", str(path), "."],
                stdout=out, stderr=subprocess.PIPE)
        if proc.returncode != 0:
            raise RuntimeError(f"checkpoint tar failed for {sid!r}: {proc.stderr.decode(errors='replace')[:300]}")
        h = hashlib.sha256()
        nbytes = 0
        with open(tar_path, "rb") as f:
            for chunk in iter(lambda: f.read(1024 * 1024), b""):
                h.update(chunk)
                nbytes += len(chunk)
        sha = h.hexdigest()
        with open(tar_path, "rb") as f:
            ok = await blob.put("sessions", f"{sid}/workspace.tgz", f.read())
        if not ok:
            raise RuntimeError(f"checkpoint blob store failed for {sid!r}")
        head = _git(path, "rev-parse", "--short", "HEAD").stdout.strip()
        _set_checkpoint_marker(sid, sha)
        return {"sha256": sha, "bytes": nbytes, "head": head}
    finally:
        with contextlib.suppress(OSError):
            os.unlink(tar_path)


async def rehydrate(sid: str, harness: dict, control: ControlStore, blob: BlobStore, *,
                     probe: str | None = None) -> dict:
    """probe=<sha> short-circuits when it matches the marker (warm resume, no fetch/
    untar). Otherwise the checkpoint is spooled fully to disk BEFORE anything on disk
    is wiped, so a failed/partial blob read leaves the current workspace untouched.
    Always re-establishes the worktree link via ensure_workspace first (never trusts
    a `.git` pointer that may have travelled from a tarball taken before a reap)."""
    sid = sanitize_sid(sid)
    if probe and checkpoint_marker(sid) == probe:
        return {"ok": True, "skipped": True, "restored": False}

    data = await blob.get("sessions", f"{sid}/workspace.tgz")
    path = await ensure_workspace(sid, harness, control)
    if data is None:
        return {"ok": True, "skipped": False, "restored": False, "workspace": str(path)}

    sha = hashlib.sha256(data).hexdigest()
    fd, spool_path = tempfile.mkstemp(suffix=".tgz", dir=str(_spool_dir()))
    try:
        with os.fdopen(fd, "wb") as out:
            out.write(data)  # fully spooled before the wipe below touches the workspace
        for child in path.iterdir():
            if child.name == ".git":
                continue
            if child.is_dir() and not child.is_symlink():
                shutil.rmtree(child, ignore_errors=True)
            else:
                child.unlink(missing_ok=True)
        with open(spool_path, "rb") as f:
            proc = subprocess.run(["tar", "xzf", "-", "-C", str(path)], stdin=f, capture_output=True)
        if proc.returncode != 0:
            raise RuntimeError(f"rehydrate untar failed for {sid!r}: {proc.stderr.decode(errors='replace')[:300]}")
        _set_checkpoint_marker(sid, sha)
        touch(sid)
        return {"ok": True, "skipped": False, "restored": True, "bytes_in": len(data), "workspace": str(path)}
    finally:
        with contextlib.suppress(OSError):
            os.unlink(spool_path)


# ── collection cursor (E.4: advanced ONLY here, never by checkpoint()'s commit) ───
_COLLECTED_REF = "refs/lumen/collected"


def collected_init(sid: str) -> None:
    """Point the cursor at the current HEAD the first time it's read — what arrived at
    workspace-init time was not produced by any turn here."""
    path = workspace_path(sid)
    if _git(path, "rev-parse", "-q", "--verify", _COLLECTED_REF).returncode != 0:
        head = _git(path, "rev-parse", "-q", "--verify", "HEAD")
        if head.returncode == 0:
            _git(path, "update-ref", _COLLECTED_REF, head.stdout.strip())


def advance_cursor(sid: str) -> str:
    """E.4: the gateway calls this ONLY after the D.1 snapshot blob for the turn is
    durable — never whoever commits. A separate commit from checkpoint()'s: the two
    must never share one, or an in-turn checkpoint would silently also mark the turn
    collected before the gateway ever saw it."""
    sid = sanitize_sid(sid)
    path = workspace_path(sid)
    collected_init(sid)
    _git(path, "add", "-A")
    _git(path, "commit", "-q", "-m", f"collected {int(time.time())}", "--allow-empty")
    head = _git(path, "rev-parse", "HEAD").stdout.strip()
    _git(path, "update-ref", _COLLECTED_REF, head)
    return head


# ── D.1: workspace_snapshot ──────────────────────────────────────────────────────
_SNAPSHOT_MAX_BYTES = 64 * 1024


def _tail_bytes(text: str, limit: int) -> tuple[str, bool]:
    raw = text.encode()
    if len(raw) <= max(limit, 0):
        return text, False
    return raw[-limit:].decode(errors="replace") if limit > 0 else "", True


def _cap_diff(diff_text: str, budget: int) -> tuple[str, bool]:
    """Largest hunks dropped first (D.1 §2): split the diff on file-section
    boundaries, drop whole sections biggest-first until the total fits."""
    if len(diff_text.encode()) <= budget:
        return diff_text, False
    sections = [s for s in re.split(r"(?=^diff --git )", diff_text, flags=re.MULTILINE) if s]
    order = sorted(range(len(sections)), key=lambda i: len(sections[i]), reverse=True)
    keep = set(range(len(sections)))
    total = sum(len(s.encode()) for s in sections)
    for i in order:
        if total <= budget:
            break
        total -= len(sections[i].encode())
        keep.discard(i)
    kept = "".join(sections[i] for i in range(len(sections)) if i in keep)
    return kept, True


def _porcelain_delta(porcelain: str) -> list[dict]:
    out = []
    for line in porcelain.splitlines():
        if len(line) < 4:
            continue
        status, path = line[:2], line[3:]
        if " -> " in path:
            old, _, new = path.partition(" -> ")
            out.append({"status": "R", "path": new.strip().strip('"'), "from": old.strip().strip('"')})
            continue
        out.append({"status": status.strip() or "?", "path": path.strip().strip('"')})
    return out


def _shrink_to_budget(frame: dict) -> dict:
    if len(json.dumps(frame).encode()) <= _SNAPSHOT_MAX_BYTES:
        return frame
    frame["truncated"] = True
    # Drop order: diff, then tool tails, then diff --stat — porcelain status and the
    # file-tree delta are the cheapest, highest-signal parts; they survive longest.
    for field, empty in (("diff", ""), ("tool_result_tails", []), ("diff_stat", "")):
        frame[field] = empty
        if len(json.dumps(frame).encode()) <= _SNAPSHOT_MAX_BYTES:
            return frame
    frame["status_porcelain"], _ = _tail_bytes(frame.get("status_porcelain", ""), _SNAPSHOT_MAX_BYTES - 512)
    return frame


def build_snapshot(sid: str, resp_id: str, *, tool_results: list[dict] | None = None,
                    assistant_texts: list[str] | None = None) -> dict:
    """D.1 §1-2: called on the canonical result frame (or eof/kill), before the turn
    is marked done. Bounded to <=64KB total, truncation marked (§6: synchronous, no
    re-execution — tool_results are the turn's own already-captured events)."""
    sid = sanitize_sid(sid)
    path = workspace_path(sid)
    collected_init(sid)
    porcelain = _git(path, "status", "--porcelain", "-uall").stdout
    diff_stat = _git(path, "diff", "--stat", _COLLECTED_REF).stdout
    diff_full = _git(path, "diff", _COLLECTED_REF).stdout
    diff, diff_truncated = _cap_diff(diff_full, _SNAPSHOT_MAX_BYTES)

    tool_tails = []
    for tr in (tool_results or []):
        if tr.get("exit_code", 0) not in (0, None) or tr.get("stderr"):
            tail, _ = _tail_bytes(str(tr.get("stdout") or tr.get("stderr") or ""), 2048)
            tool_tails.append({"tool": tr.get("tool"), "exit_code": tr.get("exit_code"), "tail": tail})

    frame = {
        "type": "system",
        "subtype": "workspace_snapshot",
        "session_id": sid,
        "response_id": resp_id,
        "status_porcelain": porcelain,
        "diff_stat": diff_stat,
        "diff": diff,
        "file_tree_delta": _porcelain_delta(porcelain),
        "tool_result_tails": tool_tails,
        "assistant_texts": list(assistant_texts or [])[-2:],
        "truncated": diff_truncated,
    }
    return _shrink_to_budget(frame)


def _snapshot_blob_id(sid: str, resp_id: str) -> str:
    return f"{sanitize_sid(sid)}/snapshots/{resp_id}.json"


def _last_snapshot_marker_path(sid: str) -> Path:
    return data_dir() / "snapshots" / f"{sanitize_sid(sid)}.last"


async def capture_and_store_snapshot(sid: str, resp_id: str, blob: BlobStore, *,
                                      tool_results: list[dict] | None = None,
                                      assistant_texts: list[str] | None = None) -> dict:
    """D.1 §3/§6: synchronous with the turn, completes before the terminal event.
    Stores the frame as sessions/<sid>/snapshots/<resp_id>.json AND records it as the
    "previous snapshot" for this session's NEXT task — independent of whether the
    gateway ever acks collection (advance_cursor), so a crash between this call and
    the ack still feeds the same snapshot forward (§5's "reproduces the same delta"
    is about the DIFF; this marker is about what the next prompt sees)."""
    frame = build_snapshot(sid, resp_id, tool_results=tool_results, assistant_texts=assistant_texts)
    blob_id = _snapshot_blob_id(sid, resp_id)
    ok = await blob.put("sessions", blob_id, json.dumps(frame).encode())
    if not ok:
        raise RuntimeError(f"snapshot blob store failed for {sid!r}/{resp_id!r}")
    marker = _last_snapshot_marker_path(sid)
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(json.dumps({"resp_id": resp_id, "blob_id": blob_id}))
    frame["lumen_snapshot_id"] = blob_id
    return frame


async def previous_snapshot(sid: str, blob: BlobStore) -> dict | None:
    """D.1 §4 feed-forward: the next task in this session gets exactly this ONE
    snapshot — the marker is overwritten each capture, so an older snapshot is never
    resent (the collection cursor already makes each one a delta from the last)."""
    marker = _last_snapshot_marker_path(sanitize_sid(sid))
    if not marker.exists():
        return None
    rec = json.loads(marker.read_text())
    data = await blob.get("sessions", rec["blob_id"])
    return json.loads(data) if data else None


def render_snapshot_for_prompt(frame: dict) -> str:
    """D.1 §4: the text block prepended through the prompt seam (fworker's rendered
    prompt in prompts/, the equivalent of HR's agent_doc → CLAUDE.md write)."""
    lines = [f"# Workspace snapshot from {frame.get('response_id')}", ""]
    if frame.get("status_porcelain"):
        lines += ["## git status --porcelain -uall", "```", frame["status_porcelain"].rstrip(), "```", ""]
    if frame.get("diff_stat"):
        lines += ["## diff --stat since last collection", "```", frame["diff_stat"].rstrip(), "```", ""]
    for t in frame.get("tool_result_tails") or []:
        lines += [f"## non-zero tool result: {t.get('tool')} (exit {t.get('exit_code')})",
                  "```", str(t.get("tail", "")).rstrip(), "```", ""]
    if frame.get("assistant_texts"):
        lines += ["## last assistant summary", frame["assistant_texts"][-1], ""]
    return "\n".join(lines)
