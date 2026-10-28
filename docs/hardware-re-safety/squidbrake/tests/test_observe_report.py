"""Tests for the Squidbrake observe-mode report tool (wq#4750, wq#4744 T6).

Pinned to the committed rules.yaml + events.sample.jsonl so the run is re-runnable
evidence. Per the acceptance-probe doctrine these probe INPUTS from outside the default
scope (a non-/re cwd, a headless_fworker event) so the scope filter can actually fail.
"""
import importlib.util
import os
import sys

import pytest

_HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_RULES = os.path.join(_HERE, "rules.yaml")
_LOG = os.path.join(_HERE, "events.sample.jsonl")

_spec = importlib.util.spec_from_file_location(
    "observe_report", os.path.join(_HERE, "observe_report.py"))
observe_report = importlib.util.module_from_spec(_spec)
sys.modules["observe_report"] = observe_report  # dataclasses resolve cls.__module__
_spec.loader.exec_module(observe_report)


@pytest.fixture(scope="module")
def rules():
    return observe_report.load_rules(_RULES)


@pytest.fixture(scope="module")
def report(rules):
    events = observe_report.read_log(_LOG)
    return observe_report.build_report(events, rules)


def classify(rules, **ev):
    return observe_report.classify_event(ev, rules)


# --- scope boundary (these can falsify the scope filter) --------------------

def test_hardware_mcp_pulls_non_re_cwd_into_scope(rules):
    d = classify(rules, session_cwd="/home/lumen/work",
                 mcp_tool="mcp__openocd__flash_firmware")
    assert d.in_scope and d.action == "hold"


def test_shell_flash_outside_re_without_hw_mcp_is_out_of_scope(rules):
    d = classify(rules, session_cwd="/home/lumen/work",
                 command="flashrom -p ch341a_spi -w fw.bin")
    assert not d.in_scope and d.action == "out_of_scope"


def test_headless_fworker_is_never_in_scope(rules):
    d = classify(rules, session_cwd="/home/lumen/re",
                 mcp_tool="mcp__openocd__flash_firmware", headless_fworker=True)
    assert not d.in_scope


# --- hold vs allow per surface ----------------------------------------------

@pytest.mark.parametrize("tool", [
    "mcp__openocd__flash_firmware",
    "mcp__openocd__write_target_memory",
    "mcp__limpet-flashrom__erase_chip",
    "mcp__serial-maksoor__send",
])
def test_energize_tools_hold(rules, tool):
    assert classify(rules, session_cwd="/home/lumen/re", mcp_tool=tool).action == "hold"


@pytest.mark.parametrize("tool", [
    "mcp__openocd__read_target_memory",
    "mcp__limpet-flashrom__read_flash",
    "mcp__serial-maksoor__list_ports",
    "mcp__sigrok__capture",
])
def test_readonly_and_passive_tools_allow(rules, tool):
    assert classify(rules, session_cwd="/home/lumen/re", mcp_tool=tool).action == "allow"


def test_unknown_hardware_mcp_tool_defaults_to_hold(rules):
    d = classify(rules, session_cwd="/home/lumen/re",
                 mcp_tool="mcp__serial-maksoor__flush")
    assert d.action == "hold" and d.rule_id == "unknown-hw-mcp-default-hold"


def test_flash_write_cmd_holds_but_read_cmd_allows(rules):
    assert classify(rules, session_cwd="/home/lumen/re",
                    command="flashrom -p ch341a_spi -w fw.bin").action == "hold"
    assert classify(rules, session_cwd="/home/lumen/re",
                    command="flashrom -p ch341a_spi -r dump.bin").action == "allow"


def test_efuse_burn_holds(rules):
    assert classify(rules, session_cwd="/home/lumen/re",
                    command="espefuse.py burn_efuse FLASH_CRYPT_CNT").action == "hold"


# --- aggregate report over the pinned fixture -------------------------------

def test_report_counts_and_rates(report):
    s = report.summary()
    assert s["mode"] == "observe"
    assert s["total_events"] == 15
    assert s["in_scope"] == 13
    assert s["out_of_scope"] == 2
    assert s["would_have_held"] == 7
    assert s["allowed"] == 6
    assert s["false_positives"] == 2
    assert s["false_positive_rate"] == pytest.approx(2 / 7, abs=1e-4)
    assert s["would_have_held_rate"] == pytest.approx(7 / 13, abs=1e-4)
    assert s["true_catch_rate"] == pytest.approx(2 / 7, abs=1e-4)


def test_report_per_rule_breakdown(report):
    pr = report.per_rule
    assert pr["hw-mcp-openocd-energize"] == 2
    assert pr["unknown-hw-mcp-default-hold"] == 1
    assert pr["hw-cli-flash-erase"] == 1
    assert pr["hw-cli-fuse"] == 1


def test_markdown_renders(report):
    md = observe_report.render_markdown(report)
    assert "Squidbrake observe-mode pilot report" in md
    assert "false positives: 2" in md
