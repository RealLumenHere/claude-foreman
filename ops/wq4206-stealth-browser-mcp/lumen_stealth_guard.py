"""Argument and URL policy for stealth-browser-mcp on Lumen (wq#4206).

Upstream treats the MCP client as fully trusted (README "Trust Model"): navigate takes
any URL, spawn_browser takes arbitrary Chrome flags and profile dirs, take_screenshot
writes to any path. On Lumen the client is an agent reading hostile web pages, so a
prompt-injected page could ask it to open file:///home/lumen/... or a tailnet service and
read the result back (the chrome-mcp file:// + eval finding, ISSUE-LOG #838/#909).

Argument checks alone cannot cover page JavaScript: execute_script, or the hostile page
itself, can fetch() a private host without naming it in any tool argument (wq#4218). So
every Chrome is also forced through lumen_egress_proxy.py, which applies the same
public-only rule to each connection the browser opens (wq#4290).

This module is pure (no fastmcp/nodriver imports) so the policy is unit-testable;
lumen_stealth_mcp.py applies it to every tool call.
"""
import ipaddress
import os
import socket
from pathlib import Path
from urllib.parse import urlsplit

# The audited --minimal surface at the pin (upstream's browser-management and
# element-interaction sections). Every other tool is refused, so a fastmcp that ignores
# --minimal (the raw :8900 deployment listed 101 tools, wq#4290) or a pin bump cannot
# hand agents execute_cdp_command, inject_and_execute_script, execute_python_in_browser
# or the dynamic-hooks exec().
ALLOWED_TOOLS = frozenset({
    "spawn_browser", "navigate", "close_instance", "list_instances", "get_instance_state",
    "go_back", "go_forward", "reload_page",
    "click_element", "type_text", "paste_text", "press_key", "select_option", "scroll_page",
    "query_elements", "get_element_state", "wait_for_element", "get_page_content",
    "execute_script", "take_screenshot", "file_upload",
})
URL_ARGS = ("url", "target_url")
URL_LIST_ARGS = ("urls",)
PATH_ARGS = ("file_path", "filepath", "filename", "user_data_dir")
PATH_LIST_ARGS = ("paths",)
WEB_SCHEMES = ("http", "https")
# Chrome flags an agent may pass. Everything else (remote debugging, file access,
# web security, extensions, proxy/resolver overrides) is refused.
SAFE_BROWSER_ARG_PREFIXES = ("--lang=", "--window-size=", "--disable-gpu", "--mute-audio",
                             "--force-device-scale-factor=", "--hide-scrollbars")
# Added to every spawn so nothing skips the egress proxy: Chrome sends loopback direct
# unless told otherwise, and WebRTC would send UDP direct.
EGRESS_BROWSER_ARGS = ("--proxy-bypass-list=<-loopback>",
                       "--force-webrtc-ip-handling-policy=disable_non_proxied_udp")


class PolicyError(ValueError):
    pass


def _resolve(host):
    try:
        return {info[4][0] for info in socket.getaddrinfo(host, None)}
    except (socket.gaierror, UnicodeError) as exc:
        raise PolicyError(f"cannot resolve host {host!r}: {exc}") from None


def _addresses(host):
    host = host.strip("[]")
    try:
        ipaddress.ip_address(host)
        return {host}
    except ValueError:
        # getaddrinfo also decodes the numeric spellings (2130706433, 0x7f.1) to 127.0.0.1.
        return _resolve(host)


def host_is_public(host):
    """True when every address `host` names is globally routable. Loopback, RFC1918,
    link-local and the 100.64/10 range the tailnet lives in are all non-global."""
    addrs = _addresses(host)
    return bool(addrs) and all(_is_global(a) for a in addrs)


def public_addresses(host):
    """The addresses `host` resolves to, IPv4 first, or PolicyError unless all are
    public. Callers connect to these instead of resolving again, so a DNS answer that
    changes after the check (rebinding) cannot reach a private host."""
    addrs = _addresses(host)
    if not addrs or not all(_is_global(a) for a in addrs):
        raise PolicyError(f"host {host!r} is not a public address")
    return sorted(addrs, key=lambda a: (":" in a, a))


def _is_global(addr):
    ip = ipaddress.ip_address(addr.split("%")[0])
    if getattr(ip, "ipv4_mapped", None):
        ip = ip.ipv4_mapped
    return ip.is_global


def check_url(url, schemes=WEB_SCHEMES):
    """Raise PolicyError unless `url` is about:blank or a URL with an allowed scheme
    whose host resolves only to public addresses."""
    if url == "about:blank":
        return
    parts = urlsplit(str(url).strip())
    scheme = parts.scheme.lower()
    if scheme not in schemes:
        raise PolicyError(f"scheme {scheme or '(none)'!r} not allowed (allowed: {', '.join(schemes)})")
    if not parts.hostname:
        raise PolicyError(f"no host in {url!r}")
    if not host_is_public(parts.hostname):
        raise PolicyError(f"host {parts.hostname!r} is not a public address")


def landed_somewhere_private(url):
    """Post-call check on the page a tab actually shows (after redirects or page JS).
    Unlike check_url, unknown schemes (data:, chrome-error:) pass: only local files,
    browser-internal pages and private http(s) hosts count as a leak."""
    parts = urlsplit(str(url or ""))
    scheme = parts.scheme.lower()
    if scheme in ("file", "chrome", "view-source", "devtools", "chrome-extension"):
        return True
    if scheme in WEB_SCHEMES and parts.hostname:
        try:
            return not host_is_public(parts.hostname)
        except PolicyError:
            return False
    return False


def check_path(value, roots):
    resolved = Path(os.path.expanduser(str(value))).resolve()
    for root in roots:
        if resolved == root or root in resolved.parents:
            return
    raise PolicyError(f"path {str(value)!r} is outside the allowed dirs: {', '.join(map(str, roots))}")


def check_browser_args(args):
    for arg in args or []:
        if not str(arg).startswith(SAFE_BROWSER_ARG_PREFIXES):
            raise PolicyError(f"browser arg {arg!r} not allowed (allowed prefixes: {', '.join(SAFE_BROWSER_ARG_PREFIXES)})")


def check_arguments(tool, arguments, roots, has_display, egress_proxy):
    """Validate one tool call's arguments; returns the (possibly adjusted) arguments.
    Raises PolicyError naming the refused tool or argument. `egress_proxy` is the
    lumen_egress_proxy URL every spawned Chrome is pointed at."""
    if tool not in ALLOWED_TOOLS:
        raise PolicyError(f"tool {tool!r} is not on the lumen allowlist")
    arguments = dict(arguments)
    for name in URL_ARGS:
        if arguments.get(name) is not None:
            check_url(arguments[name])
    for name in URL_LIST_ARGS:
        for url in arguments.get(name) or []:
            check_url(url)
    for name in PATH_ARGS:
        if arguments.get(name):
            check_path(arguments[name], roots)
    for name in PATH_LIST_ARGS:
        for path in arguments.get(name) or []:
            check_path(path, roots)
    if tool == "spawn_browser":
        check_browser_args(arguments.get("browser_args"))
        if arguments.get("proxy"):
            raise PolicyError("proxy not allowed: Chrome egress always goes through the lumen egress proxy")
        arguments["proxy"] = egress_proxy
        arguments["browser_args"] = [*(arguments.get("browser_args") or []), *EGRESS_BROWSER_ARGS]
        if not has_display:
            arguments["headless"] = True
    return arguments
