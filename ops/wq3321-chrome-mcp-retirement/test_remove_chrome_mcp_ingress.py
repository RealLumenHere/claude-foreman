"""wq#3328 — assert the surgical removal is byte-identical except for the
removed entry's own lines (wq#3326 found the prior yaml.safe_load +
yaml.safe_dump round-trip stripped every comment and reformatted every
untouched entry)."""
import importlib.util
import sys
from pathlib import Path

MODULE_PATH = Path(__file__).parent / "remove_chrome_mcp_ingress.py"
spec = importlib.util.spec_from_file_location("remove_chrome_mcp_ingress", MODULE_PATH)
mod = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = mod
spec.loader.exec_module(mod)

FIXTURE = """tunnel: abc123
credentials-file: /etc/cloudflared/abc123.json

ingress:
  # keep: primary dashboard
  - hostname: dashboard.lumennode.uk
    service: http://localhost:3000

  # chrome-mcp: retired 2026-09-29, see wq#2751/wq#2768 RCE remediation
  - hostname: chrome-mcp.lumennode.uk
    service: http://localhost:3103

  # do NOT repoint this host without re-adding its CF Access policy (wq#766)
  - hostname: other.lumennode.uk
    service: http://localhost:4000

  - service: http_status:404
"""

EXPECTED = """tunnel: abc123
credentials-file: /etc/cloudflared/abc123.json

ingress:
  # keep: primary dashboard
  - hostname: dashboard.lumennode.uk
    service: http://localhost:3000

  # chrome-mcp: retired 2026-09-29, see wq#2751/wq#2768 RCE remediation

  # do NOT repoint this host without re-adding its CF Access policy (wq#766)
  - hostname: other.lumennode.uk
    service: http://localhost:4000

  - service: http_status:404
"""


def test_removal_is_byte_identical_except_removed_entry():
    lines = FIXTURE.splitlines(keepends=True)
    block = mod.find_block(lines, "chrome-mcp.lumennode.uk")
    assert block is not None, "expected to locate the chrome-mcp ingress block"
    start, end = block
    result = "".join(lines[:start] + lines[end:])
    assert result == EXPECTED


def test_removed_lines_are_exactly_the_matching_entry():
    lines = FIXTURE.splitlines(keepends=True)
    start, end = mod.find_block(lines, "chrome-mcp.lumennode.uk")
    removed = "".join(lines[start:end])
    assert removed == (
        "  - hostname: chrome-mcp.lumennode.uk\n"
        "    service: http://localhost:3103\n"
    )


def test_other_comments_and_entries_untouched():
    lines = FIXTURE.splitlines(keepends=True)
    start, end = mod.find_block(lines, "chrome-mcp.lumennode.uk")
    result = "".join(lines[:start] + lines[end:])
    assert "# do NOT repoint this host without re-adding its CF Access policy (wq#766)" in result
    assert "# keep: primary dashboard" in result
    assert "hostname: dashboard.lumennode.uk" in result
    assert "hostname: other.lumennode.uk" in result
    assert "http_status:404" in result


def test_no_match_returns_none():
    lines = FIXTURE.splitlines(keepends=True)
    assert mod.find_block(lines, "nonexistent.lumennode.uk") is None
