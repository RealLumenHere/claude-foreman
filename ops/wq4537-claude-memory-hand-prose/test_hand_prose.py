"""wq#4537: claude_memory.md hand-prose region survives context_builder regen."""
import importlib.util
import os
import shutil
import sys
import types

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
FIXTURES = os.path.join(HERE, "fixtures")


def _load(name, filename):
    spec = importlib.util.spec_from_file_location(name, os.path.join(HERE, filename))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class _Conn:
    def close(self):
        pass


@pytest.fixture
def cb(monkeypatch):
    monkeypatch.setitem(sys.modules, "fm_db", types.SimpleNamespace(connect=_Conn))
    mod = _load("cb_wq4537", "context_builder.py")
    calls = iter(range(1000))
    # Each regen renders different generated text, as a new timestamp does live.
    monkeypatch.setattr(mod, "build", lambda conn: "# GENERATED run %d\n" % next(calls))
    return mod


def _regen(cb, monkeypatch, out):
    monkeypatch.setattr(sys, "argv", ["context_builder.py", "--out", str(out)])
    cb.main()
    return out.read_bytes()


def _region(data):
    b = data.index(b"<!-- hand-prose:begin -->")
    e = data.index(b"<!-- hand-prose:end -->") + len(b"<!-- hand-prose:end -->")
    return data[b:e]


def test_region_survives_two_regens_byte_identical(cb, monkeypatch, tmp_path):
    out = tmp_path / "claude_memory.md"
    shutil.copy(os.path.join(FIXTURES, "claude_memory_with_prose.md"), out)
    before = _region(out.read_bytes())
    first = _regen(cb, monkeypatch, out)
    second = _regen(cb, monkeypatch, out)
    assert first != second  # generated part did change
    assert _region(first) == before
    assert _region(second) == before
    assert b"stale generated text" not in second


def test_legacy_file_without_markers_gets_empty_region(cb, monkeypatch, tmp_path):
    out = tmp_path / "claude_memory.md"
    out.write_text("# old file, no markers\nlost prose\n")
    data = _regen(cb, monkeypatch, out)
    assert data.endswith(cb.EMPTY_HAND_PROSE.encode())
    assert b"lost prose" not in data
    # The fresh region is then itself preserved.
    out.write_bytes(data.replace(b"\n<!-- hand-prose:end -->",
                                 b"\nnew note\n<!-- hand-prose:end -->"))
    assert b"new note\n<!-- hand-prose:end -->" in _regen(cb, monkeypatch, out)


def test_missing_file_gets_empty_region(cb, monkeypatch, tmp_path):
    data = _regen(cb, monkeypatch, tmp_path / "new.md")
    assert data == b"# GENERATED run 0\n\n" + cb.EMPTY_HAND_PROSE.encode()


def test_deleted_end_marker_keeps_prose_to_eof(cb, monkeypatch, tmp_path):
    out = tmp_path / "claude_memory.md"
    out.write_text("gen\n<!-- hand-prose:begin -->\nkeep me\nand me\n")
    data = _regen(cb, monkeypatch, out)
    assert data.endswith(b"<!-- hand-prose:begin -->\nkeep me\nand me\n"
                         b"<!-- hand-prose:end -->\n")


def test_unreadable_file_is_not_replaced(cb, monkeypatch, tmp_path):
    out = tmp_path / "claude_memory.md"
    out.write_text("<!-- hand-prose:begin -->\nprecious\n<!-- hand-prose:end -->\n")
    real_open = open

    def deny(path, *a, **kw):
        if str(path) == str(out):
            raise PermissionError(13, "denied", str(path))
        return real_open(path, *a, **kw)

    monkeypatch.setattr("builtins.open", deny)
    with pytest.raises(PermissionError):
        _regen(cb, monkeypatch, out)
    monkeypatch.setattr("builtins.open", real_open)
    assert b"precious" in out.read_bytes()


def test_stdout_mode_unchanged(cb, monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["context_builder.py"])
    cb.main()
    assert capsys.readouterr().out == "# GENERATED run 0\n"


@pytest.fixture
def docs_root(tmp_path):
    root = tmp_path / "root"
    for rel in ("home/lumen/ORCHESTRATOR-HANDOFF.md",
                "home/lumen/minerva/context/fleet_tools.yaml"):
        dst = root / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(os.path.join(FIXTURES, os.path.basename(rel)), dst)
    return root


def test_apply_docs_applies_then_idempotent(docs_root):
    ad = _load("ad_wq4537", "apply_docs.py")
    assert ad.main(["--check", "--root", str(docs_root)]) == 1
    assert ad.main(["--root", str(docs_root)]) == 0
    assert ad.main(["--check", "--root", str(docs_root)]) == 0
    orch = (docs_root / "home/lumen/ORCHESTRATOR-HANDOFF.md").read_text()
    assert "DO NOT hand-edit" not in orch
    assert orch.count("wq#4537") == 2
    snap = orch
    assert ad.main(["--root", str(docs_root)]) == 0
    assert (docs_root / "home/lumen/ORCHESTRATOR-HANDOFF.md").read_text() == snap


def test_apply_docs_drifted_anchor_writes_nothing(docs_root):
    ad = _load("ad_wq4537", "apply_docs.py")
    yaml = docs_root / "home/lumen/minerva/context/fleet_tools.yaml"
    yaml.write_text("memory_state: rewritten by someone else\n")
    orch = docs_root / "home/lumen/ORCHESTRATOR-HANDOFF.md"
    before = orch.read_text()
    assert ad.main(["--root", str(docs_root)]) == 1
    assert orch.read_text() == before
