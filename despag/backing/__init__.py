"""wq#1807: despag P1-4 — the pluggable backing seam.

PHASE0_WIRING_SPEC.md RECOMMENDATION step 4 + C.4 + E.5. Interfaces lifted in SHAPE
(not code) from research/harnessrouter-ref/src/gateway/backing.py:48-104: four
typing.Protocol interfaces the rest of despag talks to, never a concrete store.

  GraphStore     — records + ownership edges. SEMANTIC, not query-shaped: get/upsert/
                   add_edge/find/probe by label + property equality. Call sites never
                   build a query string, so an implementation is free to be SQLite, a
                   real graph db, or anything else that can look up by equality.
  BlobStore      — content-addressed-by-caller bytes, keyed (kb, file_id).
  SecretStore    — get/put(tenant, name, value, require_encryption=). A store that
                   cannot encrypt MUST raise SecretsNotConfigured rather than write a
                   customer-shaped credential in the clear (C.4).
  WorkspaceFiles — read/write(sid, path), one interface over however a session's
                   workspace actually lives (today: a local directory; step 5 of the
                   RECOMMENDATION swaps in a git-worktree-backed impl without any
                   caller change).

Only a local backing exists for Lumen (C.4: "local impls only, but point them at the
fleet") — there is no despag consumer for a cloud/vg-shaped backing, so unlike HR this
module does not carry that branch at all. `make_backing()` reads DESPAG_BACKING (only
"local" is implemented; unset = local) and DESPAG_DATA_DIR (default ~/despag-data,
created 0700).
"""
from __future__ import annotations

import base64
import contextlib
import hashlib
import json
import mimetypes
import os
import posixpath
import re
import secrets as secrets_mod
import sqlite3
import time
import uuid
from pathlib import Path
from typing import Protocol


# ── interfaces ──────────────────────────────────────────────────────────────
class GraphStore(Protocol):
    async def get(self, vid: str, label: str | None = None) -> dict | None: ...
    async def upsert(self, label: str, vid: str, props: dict, *, raise_on_fail: bool = False) -> None: ...
    async def add_edge(self, label: str, src: str, dst: str) -> None: ...
    async def find(self, label: str, eq: dict | None = None, neq: dict | None = None) -> list[dict]: ...
    async def probe(self) -> None:
        """Raise if the store is unreachable/misconfigured. Readiness checks must FAIL
        on a broken plane; the normal read paths (get/find) swallow errors instead and
        return empty, because a caller looking up a record should not 500 on a hiccup."""
        ...


class BlobStore(Protocol):
    async def get(self, kb: str, file_id: str) -> bytes | None: ...
    async def put(self, kb: str, file_id: str, data: bytes) -> bool: ...
    async def delete(self, kb: str, file_id: str) -> bool: ...
    async def list(self, kb: str, prefix: str, limit: int = 20, cursor: str | None = None) -> dict: ...
    async def probe(self) -> None: ...


class SecretsNotConfigured(RuntimeError):
    """Raised when a secret must be written but there is nowhere safe to write it.

    Deliberately an error, not a silent plaintext fallback: the caller passing
    require_encryption=True is saying "this is customer-shaped, not an operator key
    injected read-only through the environment", and that distinction is not something
    to decide on the operator's behalf."""


class SecretStore(Protocol):
    async def get(self, tenant: str, name: str) -> str | None: ...

    async def put(self, tenant: str, name: str, value: str, *,
                  require_encryption: bool = False) -> None:
        """A store that cannot encrypt must raise SecretsNotConfigured rather than
        write require_encryption=True data in the clear. Part of the interface, not
        of one implementation."""
        ...


class WorkspaceFiles(Protocol):
    async def read(self, sid: str, path: str) -> bytes | None: ...
    async def write(self, sid: str, path: str, data: bytes) -> bool: ...


class Backing:
    def __init__(self, graph: GraphStore, blob: BlobStore, secrets: SecretStore, mode: str,
                 workspace: "WorkspaceFiles"):
        self.graph, self.blob, self.secrets, self.mode = graph, blob, secrets, mode
        self.workspace = workspace


# ── local: SQLite graph ──────────────────────────────────────────────────────
class SqliteGraphStore:
    """Vertices/edges in one SQLite file. merge-not-replace upsert (property() sets
    the listed props and keeps the rest); edge ids are uuid5(src|label|dst) so a
    re-add is idempotent; find() filters by property equality in Python — dev-scale
    data, no json_extract dialect games (C.4)."""

    def __init__(self, path: str):
        self._path = path
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with self._conn() as c:
            c.execute("CREATE TABLE IF NOT EXISTS vertices (vid TEXT PRIMARY KEY, label TEXT NOT NULL, props TEXT NOT NULL)")
            c.execute("CREATE INDEX IF NOT EXISTS idx_vertices_label ON vertices(label)")
            c.execute("CREATE TABLE IF NOT EXISTS edges (eid TEXT PRIMARY KEY, label TEXT NOT NULL, src TEXT NOT NULL, dst TEXT NOT NULL)")

    def _conn(self) -> sqlite3.Connection:
        c = sqlite3.connect(self._path, timeout=30)
        c.execute("PRAGMA journal_mode=WAL")
        return c

    async def get(self, vid: str, label: str | None = None) -> dict | None:
        with self._conn() as c:
            row = c.execute("SELECT vid, label, props FROM vertices WHERE vid=?", (vid,)).fetchone()
        if not row:
            return None
        if label is not None and row[1] != label:
            return None      # label filter is load-bearing where the id is caller-controlled
        return {"id": row[0], **json.loads(row[2])}

    async def upsert(self, label: str, vid: str, props: dict, *, raise_on_fail: bool = False) -> None:
        clean = {k: str(v) for k, v in props.items()}
        try:
            with self._conn() as c:
                row = c.execute("SELECT props FROM vertices WHERE vid=?", (vid,)).fetchone()
                merged = {**(json.loads(row[0]) if row else {}), **clean}
                c.execute("INSERT INTO vertices(vid,label,props) VALUES(?,?,?) "
                          "ON CONFLICT(vid) DO UPDATE SET props=excluded.props",
                          (vid, label, json.dumps(merged)))
        except sqlite3.Error:
            if raise_on_fail:
                raise RuntimeError("graph write failed")

    async def add_edge(self, label: str, src: str, dst: str) -> None:
        if not (src and dst):
            return
        eid = str(uuid.uuid5(uuid.NAMESPACE_URL, f"{src}|{label}|{dst}"))
        with contextlib.suppress(sqlite3.Error):
            with self._conn() as c:
                c.execute("INSERT OR IGNORE INTO edges(eid,label,src,dst) VALUES(?,?,?,?)",
                          (eid, label, src, dst))

    async def find(self, label: str, eq: dict | None = None, neq: dict | None = None) -> list[dict]:
        try:
            with self._conn() as c:
                rows = c.execute("SELECT vid, props FROM vertices WHERE label=?", (label,)).fetchall()
        except sqlite3.Error:
            return []
        out = []
        for vid, props_json in rows:
            p = json.loads(props_json)
            if eq and any(str(p.get(k, "")) != str(v) for k, v in eq.items()):
                continue
            if neq and any(str(p.get(k, "")) == str(v) for k, v in neq.items()):
                continue
            out.append({"id": vid, **p})
        return out

    async def probe(self) -> None:
        with self._conn() as c:
            c.execute("SELECT 1 FROM vertices LIMIT 1").fetchone()


# ── local: filesystem blobs ──────────────────────────────────────────────────
class FileBlobStore:
    """Objects as plain files under {root}/{kb}/{file_id}. Listing is ascending-
    lexical over the relative keys."""

    def __init__(self, root: str):
        self._root = Path(root)

    def _p(self, kb: str, file_id: str) -> Path:
        p = (self._root / kb / file_id).resolve()
        base = (self._root / kb).resolve()
        # Path.is_relative_to, not a string prefix: "…/kb1x/secret" starts with the
        # STRING "…/kb1", so a prefix test lets kb1 read every sibling kb whose name
        # merely extends its own (wq#1851).
        if not p.is_relative_to(base):
            raise ValueError(f"blob id escapes store: {file_id!r}")
        return p

    async def get(self, kb: str, file_id: str) -> bytes | None:
        try:
            return self._p(kb, file_id).read_bytes()
        except (OSError, ValueError):
            return None

    async def put(self, kb: str, file_id: str, data: bytes) -> bool:
        if not data:
            return False
        try:
            p = self._p(kb, file_id)
            p.parent.mkdir(parents=True, exist_ok=True)
            tmp = p.with_suffix(p.suffix + ".tmp")
            tmp.write_bytes(data)
            tmp.replace(p)          # atomic: a torn write never replaces a good blob
            return True
        except (OSError, ValueError):
            return False

    async def delete(self, kb: str, file_id: str) -> bool:
        try:
            self._p(kb, file_id).unlink(missing_ok=True)
            return True
        except (OSError, ValueError):
            return False

    async def list(self, kb: str, prefix: str, limit: int = 20, cursor: str | None = None) -> dict:
        base = (self._root / kb).resolve()
        dir_part, _, _ = prefix.rpartition("/")
        top = (base / dir_part).resolve() if dir_part else base
        if not top.is_relative_to(base) or not top.is_dir():
            return {"items": [], "cursor": None}
        root_len = len(str(base)) + 1
        keys: list[str] = []
        for root, _dirs, files in os.walk(top):
            rel = root[root_len:].replace(os.sep, "/") if len(root) > root_len else ""
            for name in files:
                if name.endswith(".tmp"):
                    continue
                key = f"{rel}/{name}" if rel else name
                if key.startswith(prefix) and (not cursor or key > cursor):
                    keys.append(key)
        keys.sort()
        page = keys[:limit]
        nxt = page[-1] if len(keys) > limit else None
        return {"items": [{"file_id": k} for k in page], "cursor": nxt}

    async def probe(self) -> None:
        self._root.mkdir(parents=True, exist_ok=True)
        if not os.access(self._root, os.W_OK):
            raise RuntimeError(f"blob root not writable: {self._root}")


# ── local: env/file secrets ────────────────────────────────────────────────
class FileSecretStore:
    """Reads DESPAG_SECRET_{TENANT}_{NAME} from the environment first (read-only
    injection), then falls back to files under {root}/{tenant}/{name}.

    Files are ENCRYPTED when DESPAG_SECRET_KEY is set: AES-256-GCM under a key
    derived from it via scrypt, random nonce per write, stored as
    `despagenc1:<b64 nonce||ciphertext>`. Without DESPAG_SECRET_KEY,
    put(..., require_encryption=True) REFUSES with SecretsNotConfigured."""

    _MAGIC = "despagenc1:"

    def __init__(self, root: str):
        self._root = Path(root)
        self._key = self._derive(os.environ.get("DESPAG_SECRET_KEY", ""))

    @property
    def encrypts(self) -> bool:
        return self._key is not None

    @staticmethod
    def _derive(passphrase: str) -> bytes | None:
        if not passphrase:
            return None
        return hashlib.scrypt(passphrase.encode(), salt=b"despag.secret.v1",
                              n=2 ** 14, r=8, p=1, dklen=32)

    def _seal(self, value: str) -> str:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        nonce = secrets_mod.token_bytes(12)
        blob = nonce + AESGCM(self._key).encrypt(nonce, value.encode(), None)
        return self._MAGIC + base64.b64encode(blob).decode()

    def _open(self, raw: str) -> str:
        if not raw.startswith(self._MAGIC):
            return raw              # written before encryption existed
        if self._key is None:
            raise SecretsNotConfigured(
                "This secret is encrypted but DESPAG_SECRET_KEY is not set. Set it to "
                "the passphrase used when the secret was stored.")
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        blob = base64.b64decode(raw[len(self._MAGIC):])
        return AESGCM(self._key).decrypt(blob[:12], blob[12:], None).decode()

    @staticmethod
    def _env_key(tenant: str, name: str) -> str:
        return "DESPAG_SECRET_" + re.sub(r"[^A-Za-z0-9]", "_", f"{tenant}_{name}").upper()

    def _p(self, tenant: str, name: str) -> Path:
        safe = re.sub(r"[^A-Za-z0-9._-]", "_", name)
        return self._root / re.sub(r"[^A-Za-z0-9._-]", "_", tenant) / safe

    async def get(self, tenant: str, name: str) -> str | None:
        v = os.environ.get(self._env_key(tenant, name))
        if v:
            return v
        try:
            raw = self._p(tenant, name).read_text()
        except OSError:
            return None
        return self._open(raw)

    async def put(self, tenant: str, name: str, value: str, *, require_encryption: bool = False) -> None:
        if require_encryption and self._key is None:
            raise SecretsNotConfigured(
                "Refusing to store this credential: DESPAG_SECRET_KEY is not set, so it "
                "could only be written to disk in plaintext. Set DESPAG_SECRET_KEY to a "
                "passphrase and restart.")
        body = self._seal(value) if self._key is not None else value
        p = self._p(tenant, name)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body)
        with contextlib.suppress(OSError):
            p.chmod(0o600)          # belt to the encryption's braces


# ── local: workspace files ───────────────────────────────────────────────────
def _safe_member(path: str) -> str | None:
    """A workspace-relative path, or None if it tries to leave the workspace.
    Rejected rather than sanitised: a path that needed cleaning was not the path the
    caller meant."""
    p = (path or "").strip().lstrip("/")
    if not p or p != posixpath.normpath(p) or p.startswith("../") or "\\" in p:
        return None
    return p


class LocalWorkspaceFiles:
    """Self-hosted, single-process despag: a session's workspace is a live directory
    under {root}/{sid}/. This is the placeholder impl for C.4/step 4 — step 5 of the
    RECOMMENDATION swaps the workspace identity to a git worktree per session; callers
    only ever see read(sid, path)/write(sid, path, data), so that swap is invisible
    here."""

    def __init__(self, root: str):
        self._root = Path(root)

    def _p(self, sid: str, path: str) -> Path | None:
        member = _safe_member(path)
        if not member or not sid or sid != re.sub(r"[^A-Za-z0-9._-]", "_", sid):
            return None
        return self._root / sid / member

    async def read(self, sid: str, path: str) -> bytes | None:
        p = self._p(sid, path)
        if p is None:
            return None
        try:
            return p.read_bytes()
        except OSError:
            return None

    async def write(self, sid: str, path: str, data: bytes) -> bool:
        p = self._p(sid, path)
        if p is None:
            return False
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
            tmp = p.with_suffix(p.suffix + ".tmp")
            tmp.write_bytes(data)
            tmp.replace(p)
            return True
        except OSError:
            return False


# ── selection ─────────────────────────────────────────────────────────────────
def make_backing() -> Backing:
    """DESPAG_BACKING: only "local" is implemented (unset = local). There is no
    despag consumer for a cloud-shaped backing yet, unlike HR's vg branch (C.4:
    "local impls only, but point them at the fleet"). DESPAG_DATA_DIR defaults to
    ~/despag-data, created 0700 (it holds secrets and session content)."""
    mode = os.environ.get("DESPAG_BACKING", "").strip().lower() or "local"
    if mode != "local":
        raise ValueError(f"DESPAG_BACKING={mode!r} is not supported; only 'local' is implemented")
    data_dir = Path(os.environ.get("DESPAG_DATA_DIR", "~/despag-data")).expanduser()
    data_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    with contextlib.suppress(OSError):
        data_dir.chmod(0o700)
    return Backing(
        graph=SqliteGraphStore(str(data_dir / "graph.db")),
        blob=FileBlobStore(str(data_dir / "blobs")),
        secrets=FileSecretStore(str(data_dir / "secrets")),
        mode="local",
        workspace=LocalWorkspaceFiles(str(data_dir / "workspaces")),
    )
