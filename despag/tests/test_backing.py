"""wq#1807: despag P1-4 — Protocol impls (SqliteGraphStore, FileBlobStore,
FileSecretStore, LocalWorkspaceFiles) + make_backing()."""
from __future__ import annotations

import asyncio

import pytest

from despag.backing import (
    Backing,
    FileBlobStore,
    FileSecretStore,
    LocalWorkspaceFiles,
    SecretsNotConfigured,
    SqliteGraphStore,
    make_backing,
)


def run(coro):
    return asyncio.run(coro)


# ── GraphStore ──────────────────────────────────────────────────────────────
def test_graph_upsert_is_merge_not_replace(tmp_path):
    g = SqliteGraphStore(str(tmp_path / "graph.db"))
    run(g.upsert("session", "hsess_a", {"owner": "frank"}))
    run(g.upsert("session", "hsess_a", {"model": "sonnet-5"}))
    got = run(g.get("hsess_a"))
    assert got == {"id": "hsess_a", "owner": "frank", "model": "sonnet-5"}


def test_graph_get_label_filter_rejects_mismatched_label(tmp_path):
    g = SqliteGraphStore(str(tmp_path / "graph.db"))
    run(g.upsert("session", "vid1", {"a": "1"}))
    assert run(g.get("vid1", label="harness")) is None
    assert run(g.get("vid1", label="session")) is not None


def test_graph_add_edge_idempotent(tmp_path):
    g = SqliteGraphStore(str(tmp_path / "graph.db"))
    run(g.add_edge("owns", "src1", "dst1"))
    run(g.add_edge("owns", "src1", "dst1"))
    with g._conn() as c:
        n = c.execute("SELECT COUNT(*) FROM edges").fetchone()[0]
    assert n == 1


def test_graph_find_equality_and_inequality(tmp_path):
    g = SqliteGraphStore(str(tmp_path / "graph.db"))
    run(g.upsert("session", "s1", {"owner": "frank", "status": "active"}))
    run(g.upsert("session", "s2", {"owner": "frank", "status": "expired"}))
    run(g.upsert("session", "s3", {"owner": "other", "status": "active"}))
    got = run(g.find("session", eq={"owner": "frank"}, neq={"status": "expired"}))
    assert [r["id"] for r in got] == ["s1"]


def test_graph_probe_ok(tmp_path):
    g = SqliteGraphStore(str(tmp_path / "graph.db"))
    run(g.probe())  # must not raise


# ── BlobStore ───────────────────────────────────────────────────────────────
def test_blob_roundtrip(tmp_path):
    b = FileBlobStore(str(tmp_path / "blobs"))
    assert run(b.put("kb1", "file_id_1", b"hello")) is True
    assert run(b.get("kb1", "file_id_1")) == b"hello"


def test_blob_put_empty_rejected(tmp_path):
    b = FileBlobStore(str(tmp_path / "blobs"))
    assert run(b.put("kb1", "f1", b"")) is False
    assert run(b.get("kb1", "f1")) is None


def test_blob_get_missing_returns_none(tmp_path):
    b = FileBlobStore(str(tmp_path / "blobs"))
    assert run(b.get("kb1", "nope")) is None


def test_blob_delete(tmp_path):
    b = FileBlobStore(str(tmp_path / "blobs"))
    run(b.put("kb1", "f1", b"x"))
    assert run(b.delete("kb1", "f1")) is True
    assert run(b.get("kb1", "f1")) is None


def test_blob_path_escape_refused(tmp_path):
    b = FileBlobStore(str(tmp_path / "blobs"))
    assert run(b.put("kb1", "../../etc/passwd", b"x")) is False
    assert run(b.get("kb1", "../../etc/passwd")) is None


def test_blob_list_prefix_and_ordering(tmp_path):
    b = FileBlobStore(str(tmp_path / "blobs"))
    for fid in ("a/2", "a/1", "a/3", "b/1"):
        run(b.put("kb1", fid, b"x"))
    got = run(b.list("kb1", "a/"))
    assert [i["file_id"] for i in got["items"]] == ["a/1", "a/2", "a/3"]


def test_blob_probe_creates_root(tmp_path):
    root = tmp_path / "blobs"
    b = FileBlobStore(str(root))
    run(b.probe())
    assert root.is_dir()


# ── SecretStore ─────────────────────────────────────────────────────────────
def test_secret_env_first(tmp_path, monkeypatch):
    monkeypatch.setenv("DESPAG_SECRET_ACME_DBPASS", "from-env")
    s = FileSecretStore(str(tmp_path / "secrets"))
    assert run(s.get("acme", "dbpass")) == "from-env"


def test_secret_plaintext_roundtrip_without_key(tmp_path, monkeypatch):
    monkeypatch.delenv("DESPAG_SECRET_KEY", raising=False)
    s = FileSecretStore(str(tmp_path / "secrets"))
    run(s.put("acme", "apikey", "sekret-value"))
    assert run(s.get("acme", "apikey")) == "sekret-value"
    raw = (tmp_path / "secrets" / "acme" / "apikey").read_text()
    assert raw == "sekret-value"          # actually plaintext on disk — no key configured


def test_secret_require_encryption_refuses_without_key(tmp_path, monkeypatch):
    monkeypatch.delenv("DESPAG_SECRET_KEY", raising=False)
    s = FileSecretStore(str(tmp_path / "secrets"))
    with pytest.raises(SecretsNotConfigured):
        run(s.put("acme", "customer-db-url", "postgres://...", require_encryption=True))


def test_secret_encrypted_roundtrip_with_key(tmp_path, monkeypatch):
    monkeypatch.setenv("DESPAG_SECRET_KEY", "correct horse battery staple")
    s = FileSecretStore(str(tmp_path / "secrets"))
    run(s.put("acme", "customer-db-url", "postgres://user:pw@host/db", require_encryption=True))
    raw = (tmp_path / "secrets" / "acme" / "customer-db-url").read_text()
    assert raw.startswith("despagenc1:")
    assert "postgres://" not in raw
    assert run(s.get("acme", "customer-db-url")) == "postgres://user:pw@host/db"


def test_secret_encrypted_unreadable_without_key(tmp_path, monkeypatch):
    monkeypatch.setenv("DESPAG_SECRET_KEY", "key-one")
    s1 = FileSecretStore(str(tmp_path / "secrets"))
    run(s1.put("acme", "k", "v", require_encryption=True))
    monkeypatch.delenv("DESPAG_SECRET_KEY", raising=False)
    s2 = FileSecretStore(str(tmp_path / "secrets"))
    with pytest.raises(SecretsNotConfigured):
        run(s2.get("acme", "k"))


def test_secret_file_permissions_0600(tmp_path):
    s = FileSecretStore(str(tmp_path / "secrets"))
    run(s.put("acme", "k", "v"))
    mode = (tmp_path / "secrets" / "acme" / "k").stat().st_mode & 0o777
    assert mode == 0o600


# ── WorkspaceFiles ────────────────────────────────────────────────────────────
def test_workspace_roundtrip(tmp_path):
    w = LocalWorkspaceFiles(str(tmp_path / "workspaces"))
    assert run(w.write("hsess_a", "notes.txt", b"hi")) is True
    assert run(w.read("hsess_a", "notes.txt")) == b"hi"


def test_workspace_missing_read_returns_none(tmp_path):
    w = LocalWorkspaceFiles(str(tmp_path / "workspaces"))
    assert run(w.read("hsess_a", "nope.txt")) is None


def test_workspace_path_traversal_refused(tmp_path):
    w = LocalWorkspaceFiles(str(tmp_path / "workspaces"))
    assert run(w.write("hsess_a", "../escape.txt", b"x")) is False
    assert run(w.read("hsess_a", "../escape.txt")) is None


def test_workspace_sid_isolated(tmp_path):
    w = LocalWorkspaceFiles(str(tmp_path / "workspaces"))
    run(w.write("hsess_a", "f.txt", b"a"))
    run(w.write("hsess_b", "f.txt", b"b"))
    assert run(w.read("hsess_a", "f.txt")) == b"a"
    assert run(w.read("hsess_b", "f.txt")) == b"b"


# ── make_backing / composition ─────────────────────────────────────────────
def test_make_backing_default_local(tmp_path, monkeypatch):
    monkeypatch.delenv("DESPAG_BACKING", raising=False)
    monkeypatch.setenv("DESPAG_DATA_DIR", str(tmp_path / "data"))
    b = make_backing()
    assert isinstance(b, Backing)
    assert b.mode == "local"
    assert (tmp_path / "data").stat().st_mode & 0o777 == 0o700


def test_make_backing_rejects_unknown_mode(tmp_path, monkeypatch):
    monkeypatch.setenv("DESPAG_BACKING", "vg")
    monkeypatch.setenv("DESPAG_DATA_DIR", str(tmp_path / "data"))
    with pytest.raises(ValueError):
        make_backing()


def test_make_backing_wires_all_four_stores(tmp_path, monkeypatch):
    monkeypatch.setenv("DESPAG_BACKING", "local")
    monkeypatch.setenv("DESPAG_DATA_DIR", str(tmp_path / "data"))
    b = make_backing()
    run(b.graph.upsert("session", "s1", {"a": "1"}))
    assert run(b.graph.get("s1")) == {"id": "s1", "a": "1"}
    run(b.blob.put("kb", "f1", b"x"))
    assert run(b.blob.get("kb", "f1")) == b"x"
    run(b.secrets.put("t", "n", "v"))
    assert run(b.secrets.get("t", "n")) == "v"
    run(b.workspace.write("sid1", "p.txt", b"y"))
    assert run(b.workspace.read("sid1", "p.txt")) == b"y"
