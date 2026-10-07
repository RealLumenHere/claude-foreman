"""wq#3471: tbl_sweep2.py false-positive classes behind probe-dead-references."""
import pathlib
import sqlite3
import subprocess
import sys

SWEEP = pathlib.Path(__file__).resolve().parents[1] / "tbl_sweep2.py"


def _db(path, *tables):
    c = sqlite3.connect(path)
    for t in tables:
        c.execute(f"CREATE TABLE {t} (x)")
    c.commit()
    c.close()


def _sweep(root):
    r = subprocess.run([sys.executable, str(SWEEP), str(root)], capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    return r.stdout.splitlines()


def _tree(tmp_path, body):
    _db(tmp_path / "other.db", "other_t")
    _db(tmp_path / "fx.db", "real_t")
    (tmp_path / "mod.py").write_text(f"Q = '{tmp_path}/other.db'\nR = '{tmp_path}'\nDB = f'{{R}}/fx.db'\n" + body)
    return _sweep(tmp_path)


def test_fstring_db_path_resolves(tmp_path):
    assert _tree(tmp_path, 'S = "SELECT x FROM real_t"\n') == []


def test_trigger_grammar_is_not_a_table(tmp_path):
    body = 'S = """CREATE TRIGGER a BEFORE UPDATE ON real_t BEGIN SELECT 1; END;\nCREATE TRIGGER b AFTER UPDATE OF x ON real_t BEGIN SELECT 1; END;"""\n'
    assert _tree(tmp_path, body) == []


def test_docstring_prose_is_not_sql(tmp_path):
    body = 'def heal():\n    """an UPDATE that sets x to NULL is never shorthand"""\n    return "SELECT x FROM real_t"\n'
    assert _tree(tmp_path, body) == []


def test_real_missing_table_still_fires(tmp_path):
    out = _tree(tmp_path, 'def f():\n    """doc"""\n    return "SELECT x FROM ghost_t"\n')
    assert len(out) == 1 and "MISSING table 'ghost_t'" in out[0], out


def test_unparseable_file_keeps_checking(tmp_path):
    out = _tree(tmp_path, 'def broken(:\n    """SELECT x FROM ghost_t"""\n')
    assert any("ghost_t" in l for l in out), out
