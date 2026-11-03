import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import hashbench  # noqa: E402


def test_run_reports_every_backend_and_marks_missing_ones():
    res = hashbench.run(mib=1, iso_gib=6.3, rounds=1)
    names = set(res["results"])
    assert {"sha256", "blake2b", "blake3", "xxh3_128", "cli:b3sum"} <= names
    sha = res["results"]["sha256"]
    assert sha["mib_s"] > 0
    assert sha["iso_s"] > 0
    for name, row in res["results"].items():
        assert row == "unavailable" or row["mib_s"] > 0, name


def test_missing_module_is_unavailable_not_a_crash(monkeypatch):
    monkeypatch.setattr(hashbench, "_module", lambda mod, attr: None)
    res = hashbench.run(mib=1, iso_gib=1.0, rounds=1)
    assert res["results"]["blake3"] == "unavailable"
    assert res["results"]["xxh3_128"] == "unavailable"


def test_json_output_parses(capsys):
    assert hashbench.main(["--mib", "1", "--rounds", "1", "--json"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["buffer_mib"] == 1
