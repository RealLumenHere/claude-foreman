import pytest

from cli_wrapper import CliToolError, build_cli_tool
from registry import CliToolEntry


def test_invoke_runs_command_and_captures_output():
    entry = CliToolEntry(name="echoer", command=["/bin/echo", "{message}"])
    tool = build_cli_tool(entry)
    result = tool.invoke({"message": "hello limpet"})
    assert result["exit_code"] == 0
    assert result["stdout"].strip() == "hello limpet"


def test_missing_required_argument_raises():
    entry = CliToolEntry(name="echoer", command=["/bin/echo", "{message}"])
    tool = build_cli_tool(entry)
    with pytest.raises(CliToolError, match="missing required argument 'message'"):
        tool.invoke({})


def test_unknown_command_raises_cli_tool_error():
    entry = CliToolEntry(name="ghost", command=["/no/such/binary-wq3808"])
    tool = build_cli_tool(entry)
    with pytest.raises(CliToolError, match="command not found"):
        tool.invoke({})


def test_timeout_raises_cli_tool_error():
    entry = CliToolEntry(name="sleeper", command=["/bin/sleep", "5"], timeout_sec=0.1)
    tool = build_cli_tool(entry)
    with pytest.raises(CliToolError, match="timed out"):
        tool.invoke({})


def test_no_shell_injection_via_argument():
    # the arg is passed as a single argv entry (shell=False), never interpolated
    # into a shell string, so shell metacharacters in it do nothing special.
    entry = CliToolEntry(name="echoer", command=["/bin/echo", "{message}"])
    tool = build_cli_tool(entry)
    result = tool.invoke({"message": "safe; rm -rf /tmp/should-not-run"})
    assert result["stdout"].strip() == "safe; rm -rf /tmp/should-not-run"
