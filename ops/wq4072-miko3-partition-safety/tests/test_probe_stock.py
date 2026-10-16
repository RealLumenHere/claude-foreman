"""Tests for ops/wq4072-miko3-partition-safety/probe_stock.py (wq#4072)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import probe_stock as ps  # noqa: E402


SAMPLE_GETVAR = """\
(bootloader) version-bootloader: MT8167-v1.2
(bootloader) partition-type:boot: raw
(bootloader) partition-size:boot: 0x2000000
(bootloader) secure: yes
all:
finished. total time: 0.012s
"""


def test_parse_getvar_output_extracts_known_params():
    params = ps.parse_getvar_output(SAMPLE_GETVAR)
    assert params["version-bootloader"] == "MT8167-v1.2"
    assert params["partition-size:boot"] == "0x2000000"
    assert params["secure"] == "yes"


def test_parse_getvar_output_ignores_non_bootloader_lines():
    params = ps.parse_getvar_output(SAMPLE_GETVAR)
    assert "all" not in params
    assert not any("finished" in k for k in params)


def test_parse_getvar_output_empty_on_blank_input():
    assert ps.parse_getvar_output("") == {}
