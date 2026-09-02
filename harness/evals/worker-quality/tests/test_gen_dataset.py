import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import gen_dataset  # noqa: E402


def make_db(tmp_path, rows):
    """rows: list of (id, title, detail, notes, reopened_count, created_at, status)"""
    db_path = str(tmp_path / "queue.db")
    con = sqlite3.connect(db_path)
    con.execute("""
        CREATE TABLE tasks (
            id INTEGER PRIMARY KEY, title TEXT, detail TEXT, notes TEXT,
            reopened_count INTEGER, created_at TEXT, done_at TEXT, status TEXT
        )
    """)
    for tid, title, detail, notes, reopened, created_at, status in rows:
        done_at = "2026-08-25 00:00:00" if status == "done" else None
        con.execute(
            "INSERT INTO tasks VALUES (?,?,?,?,?,?,?,?)",
            (tid, title, detail, notes, reopened, created_at, done_at, status),
        )
    con.commit()
    con.close()
    return db_path


def test_reopened_ticket_labeled_rework(tmp_path):
    db = make_db(tmp_path, [
        (1, "t1", "detail1", "claim1", 1, "2026-08-24 00:00:00", "done"),
    ])
    cases = gen_dataset.load_real_cases(db)
    assert len(cases) == 1
    assert cases[0]["expected_verdict"] == "rework"
    assert cases[0]["worker_claim"] == "claim1"


def test_clean_ticket_labeled_done(tmp_path):
    db = make_db(tmp_path, [
        (2, "t2", "detail2", "claim2", 0, "2026-08-24 00:00:00", "done"),
    ])
    cases = gen_dataset.load_real_cases(db)
    assert len(cases) == 1
    assert cases[0]["expected_verdict"] == "done"


def test_pre_epoch_ticket_excluded(tmp_path):
    db = make_db(tmp_path, [
        (3, "t3", "detail3", "claim3", 5, "2026-08-01 00:00:00", "done"),
    ])
    cases = gen_dataset.load_real_cases(db)
    assert cases == []


def test_non_done_or_empty_notes_excluded(tmp_path):
    db = make_db(tmp_path, [
        (4, "t4", "detail4", "claim4", 0, "2026-08-24 00:00:00", "open"),
        (5, "t5", "detail5", "", 0, "2026-08-24 00:00:00", "done"),
        (6, "t6", "detail6", None, 0, "2026-08-24 00:00:00", "done"),
    ])
    cases = gen_dataset.load_real_cases(db)
    assert cases == []


def test_limit_applied(tmp_path):
    rows = [
        (i, f"t{i}", f"detail{i}", f"claim{i}", 0, "2026-08-24 00:00:00", "done")
        for i in range(10, 15)
    ]
    db = make_db(tmp_path, rows)
    cases = gen_dataset.load_real_cases(db, limit=2)
    assert len(cases) == 2


def test_synthetic_cases_have_both_labels():
    labels = {c["expected_verdict"] for c in gen_dataset.SYNTHETIC_CASES}
    assert labels == {"done", "rework"}
    assert len(gen_dataset.SYNTHETIC_CASES) >= 6


def test_to_promptfoo_tests_shape():
    tests = gen_dataset.to_promptfoo_tests(gen_dataset.SYNTHETIC_CASES[:2])
    assert len(tests) == 2
    for t in tests:
        assert "vars" in t
        for key in ("ticket_title", "ticket_detail", "worker_claim", "expected_verdict"):
            assert key in t["vars"]


def test_build_no_real_is_synthetic_only():
    cases = gen_dataset.build(db_path="/nonexistent/queue.db", include_real=False, real_limit=None)
    assert cases == gen_dataset.SYNTHETIC_CASES


def test_main_writes_yaml(tmp_path):
    db = make_db(tmp_path, [
        (7, "t7", "detail7", "claim7", 2, "2026-08-24 00:00:00", "done"),
    ])
    out = str(tmp_path / "dataset.yaml")
    rc = gen_dataset.main(["--db", db, "--out", out])
    assert rc == 0
    assert os.path.exists(out)
    import yaml
    with open(out) as f:
        data = yaml.safe_load(f)
    assert len(data) == len(gen_dataset.SYNTHETIC_CASES) + 1
