import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import lumen_stealth_guard as guard  # noqa: E402


@pytest.fixture
def roots(tmp_path):
    return [tmp_path / "uploads", tmp_path / "screenshots", tmp_path / "profiles"]


@pytest.mark.parametrize("url", [
    "file:///home/lumen/kb/ISSUE-LOG.md",
    "chrome://settings",
    "view-source:https://example.com/",
    "javascript:alert(1)",
    "http://127.0.0.1:4000/",
    "http://localhost/",
    "http://[::1]/",
    "http://[::ffff:127.0.0.1]/",
    "http://2130706433/",
    "http://10.0.0.5/",
    "http://192.168.1.1/",
    "http://169.254.169.254/latest/meta-data/",
    "http://100.117.66.102/",
    "https://",
])
def test_check_url_refuses(url):
    with pytest.raises(guard.PolicyError):
        guard.check_url(url)


@pytest.mark.parametrize("url", ["about:blank", "https://93.184.215.14/", "http://8.8.8.8/"])
def test_check_url_allows_public(url):
    guard.check_url(url)


def test_check_url_resolves_hostnames(monkeypatch):
    monkeypatch.setattr(guard, "_resolve", lambda host: {"100.112.151.100"} if host == "thor" else {"93.184.215.14"})
    with pytest.raises(guard.PolicyError, match="not a public address"):
        guard.check_url("http://thor:8080/")
    guard.check_url("https://example.com/")


def test_mixed_resolution_is_refused(monkeypatch):
    monkeypatch.setattr(guard, "_resolve", lambda host: {"93.184.215.14", "127.0.0.1"})
    with pytest.raises(guard.PolicyError):
        guard.check_url("https://rebind.example/")


@pytest.mark.parametrize("url,private", [
    ("file:///etc/passwd", True),
    ("chrome://version", True),
    ("http://127.0.0.1:9/", True),
    ("https://8.8.8.8/", False),
    ("about:blank", False),
    ("chrome-error://chromewebdata/", False),
    ("data:text/html,hi", False),
])
def test_landed_somewhere_private(url, private):
    assert guard.landed_somewhere_private(url) is private


def test_check_path(roots, tmp_path):
    guard.check_path(roots[1] / "shot.png", roots)
    for bad in ("/home/lumen/kb/x.png", roots[1] / ".." / ".." / "escape.png", tmp_path / "uploads-evil" / "x"):
        with pytest.raises(guard.PolicyError):
            guard.check_path(bad, roots)


def test_check_path_follows_symlinks(roots, tmp_path):
    roots[1].mkdir(parents=True)
    (roots[1] / "link").symlink_to(tmp_path.parent)
    with pytest.raises(guard.PolicyError):
        guard.check_path(roots[1] / "link" / "x.png", roots)


EGRESS = "http://127.0.0.1:40000"


def test_spawn_browser_policy(roots):
    out = guard.check_arguments("spawn_browser", {"headless": False, "browser_args": ["--lang=en-US"]},
                                roots, False, EGRESS)
    assert out["headless"] is True
    assert guard.check_arguments("spawn_browser", {"headless": False}, roots, True, EGRESS)["headless"] is False
    for args in ({"browser_args": ["--remote-debugging-address=0.0.0.0"]},
                 {"browser_args": ["--allow-file-access-from-files"]},
                 {"browser_args": ["--proxy-bypass-list=*"]},
                 {"user_data_dir": "/home/lumen/.config/chromium"},
                 {"proxy": "http://127.0.0.1:8080"},
                 {"proxy": "http://8.8.8.8:3128"}):
        with pytest.raises(guard.PolicyError):
            guard.check_arguments("spawn_browser", args, roots, False, EGRESS)


def test_spawn_browser_is_forced_through_egress_proxy(roots):
    out = guard.check_arguments("spawn_browser", {"browser_args": ["--lang=en-US"]}, roots, False, EGRESS)
    assert out["proxy"] == EGRESS
    assert out["browser_args"] == ["--lang=en-US", *guard.EGRESS_BROWSER_ARGS]
    assert "--proxy-bypass-list=<-loopback>" in guard.check_arguments("spawn_browser", {}, roots, False, EGRESS)["browser_args"]


@pytest.mark.parametrize("tool", ["execute_cdp_command", "inject_and_execute_script", "execute_python_in_browser",
                                  "call_javascript_function", "create_dynamic_hook", "new_tab", "get_cookies",
                                  "hot_reload", "not_a_tool"])
def test_tools_outside_the_minimal_surface_are_refused(roots, tool):
    with pytest.raises(guard.PolicyError, match="allowlist"):
        guard.check_arguments(tool, {"instance_id": "i"}, roots, False, EGRESS)


def test_check_arguments_covers_list_and_path_args(roots):
    with pytest.raises(guard.PolicyError):
        guard.check_arguments("file_upload", {"paths": ["/home/lumen/.ssh/id_ed25519"]}, roots, False, EGRESS)
    with pytest.raises(guard.PolicyError):
        guard.check_arguments("take_screenshot", {"file_path": "/tmp/x.png"}, roots, False, EGRESS)
    args = {"instance_id": "i", "selector": "a"}
    assert guard.check_arguments("click_element", args, roots, False, EGRESS) == args


def test_public_addresses(monkeypatch):
    assert guard.public_addresses("8.8.8.8") == ["8.8.8.8"]
    monkeypatch.setattr(guard, "_resolve", lambda host: {"2606:4700::1", "93.184.215.14"})
    assert guard.public_addresses("example.com") == ["93.184.215.14", "2606:4700::1"]
    for addrs in ({"93.184.215.14", "127.0.0.1"}, {"100.117.66.102"}, set()):
        monkeypatch.setattr(guard, "_resolve", lambda host, addrs=addrs: addrs)
        with pytest.raises(guard.PolicyError):
            guard.public_addresses("rebind.example")
