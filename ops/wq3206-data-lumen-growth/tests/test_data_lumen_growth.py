import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import data_lumen_growth as g  # noqa: E402


def test_snapshot_missing_path_returns_none():
    assert g.snapshot("/no/such/path/wq3206") is None


def test_snapshot_real_tmp_dir(tmp_path):
    snap = g.snapshot(str(tmp_path))
    assert snap is not None
    used, total, pct = snap
    assert total > 0
    assert 0 <= pct <= 100


def test_record_appends_row_and_header(tmp_path):
    log = str(tmp_path / "growth.tsv")
    rc = g.record(log_path=log, path=str(tmp_path), now=1000)
    assert rc == 0
    with open(log) as f:
        lines = f.read().splitlines()
    assert lines[0].split("\t") == list(g.FIELDS)
    assert len(lines) == 2
    assert lines[1].split("\t")[0] == "1000"


def test_record_missing_mount_returns_1_and_writes_nothing(tmp_path):
    log = str(tmp_path / "growth.tsv")
    rc = g.record(log_path=log, path="/no/such/path/wq3206", now=1000)
    assert rc == 1
    assert not os.path.exists(log)


def test_slope_none_with_no_history(tmp_path):
    log = str(tmp_path / "growth.tsv")
    assert g.slope_gb_per_day(log_path=log) is None


def test_slope_none_with_single_row(tmp_path):
    log = str(tmp_path / "growth.tsv")
    g.record(log_path=log, path=str(tmp_path), now=1000)
    assert g.slope_gb_per_day(log_path=log, now=1000) is None


def test_slope_computes_gb_per_day(tmp_path):
    log = str(tmp_path / "growth.tsv")
    one_gb = 1024**3
    with open(log, "w") as f:
        f.write("ts\tused_bytes\ttotal_bytes\tpct\n")
        f.write(f"0\t{0}\t{100 * one_gb}\t0\n")
        f.write(f"{10 * 86400}\t{20 * one_gb}\t{100 * one_gb}\t20\n")
    slope = g.slope_gb_per_day(log_path=log, days=30, now=10 * 86400)
    assert slope == 2.0


def test_slope_ignores_rows_outside_window(tmp_path):
    log = str(tmp_path / "growth.tsv")
    one_gb = 1024**3
    now = 100 * 86400
    with open(log, "w") as f:
        f.write("ts\tused_bytes\ttotal_bytes\tpct\n")
        # ancient row, outside the 30d window -- must not skew the slope
        f.write(f"0\t{0}\t{100 * one_gb}\t0\n")
        f.write(f"{(now - 10 * 86400)}\t{10 * one_gb}\t{100 * one_gb}\t10\n")
        f.write(f"{now}\t{15 * one_gb}\t{100 * one_gb}\t15\n")
    slope = g.slope_gb_per_day(log_path=log, days=30, now=now)
    assert slope == 0.5


def test_main_slope_with_no_log(tmp_path, capsys, monkeypatch):
    monkeypatch.setenv("DATA_LUMEN_GROWTH_LOG", str(tmp_path / "missing.tsv"))
    import importlib

    importlib.reload(g)
    rc = g.main(["slope"])
    assert rc == 0
    assert "not enough history" in capsys.readouterr().out
