#!/usr/bin/env python3
"""labgrid-mcp-launch: stdio launcher for labgrid-mcp with one identity per wq worker (wq#4908).

Closes the launcher-side findings of the wq#4875 pilot
(/home/lumen/docs/mcp-acquisition-analysis/labgrid-pilot-report.md):
  F2  same LG_HOSTNAME/LG_USERNAME on two servers = both own the device
      -> identity is WQ_AGENT, which must be a live wq session or the holder of a claimed
         ticket; a second server under the same identity is refused (flock).
  F4  ssh_run/put_file/get_file/forward_* on by default
      -> LABGRID_MCP_ALLOW is always set from a profile; ssh/flash/metadata/place_delete never.
  F6  coordinator has no auth -> loopback or tailnet address only.
  F7  120 s default acquire timeout -> 30 s cap.
F3 (release_place kick=true, release_from, allow_place) is denied by harness/action_gate.py
(wq#4905), which keys on an MCP server name containing "labgrid": register this launcher
under the name `mcp-config` prints.

  labgrid-mcp-launch install                        build the pinned, hash-checked venv
  labgrid-mcp-launch serve --profile analyst        what the MCP client spawns (stdio)
  labgrid-mcp-launch check --profile bench          run every serve check, print the env
  labgrid-mcp-launch mcp-config --profile observer  print the .mcp.json entry
"""

import argparse
import fcntl
import hashlib
import ipaddress
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path

PACKAGE = "labgrid-mcp"
PACKAGE_VERSION = "0.1.5"
WHEEL_SHA256 = "f467ad303f702781eb6388bf0cd3c317676ea62e666b1ea70e9e1c9d2ad5148e"
LOCK_FILE = Path(__file__).resolve().with_name("requirements.lock")
STAMP_NAME = ".labgrid-mcp-launch.lock-sha256"
DEFAULT_VENV = Path.home() / ".local/share/labgrid-mcp-launch" / f"venv-{PACKAGE_VERSION}"
DEFAULT_WQ_DB = "/home/lumen/agents/work-queue/queue.db"
LAUNCH_BIN = "/home/lumen/bin/labgrid-mcp-launch"
MCP_SERVER_NAME = "labgrid"  # action_gate.classify_mcp_tool matches "labgrid" in the server name

DEFAULT_COORDINATOR_PORT = 20408  # labgrid_mcp.config.DEFAULT_COORDINATOR_PORT
DEFAULT_COORDINATOR = f"127.0.0.1:{DEFAULT_COORDINATOR_PORT}"
MAX_ACQUIRE_TIMEOUT = 30.0
TAILNET_NETS = (ipaddress.ip_network("100.64.0.0/10"), ipaddress.ip_network("fd7a:115c:a1e0::/48"))

# labgrid_mcp.policy.Category values that no agent profile may carry.
FORBIDDEN_CATEGORIES = frozenset({"ssh", "flash", "metadata", "place_delete"})
IDENT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


@dataclass(frozen=True)
class Profile:
    readonly: bool
    allow: tuple
    tools: int  # tools/list count measured in the pilot; tests re-measure it


PROFILES = {
    "observer": Profile(True, ("read",), 8),
    "analyst": Profile(False, ("read", "acquire", "reservation", "console"), 19),
    "bench": Profile(False, ("read", "acquire", "reservation", "power", "io", "mux", "console"), 26),
}


class LaunchError(Exception):
    pass


def resolve_identity(env, wq_db):
    ident = env.get("WQ_AGENT", "").strip()
    if not ident:
        raise LaunchError("WQ_AGENT is not set; refusing to start without a wq session identity. "
                          "Register first: eval \"$(/home/lumen/bin/wq whoami --register <name> --eval)\"")
    if not IDENT_RE.match(ident):
        raise LaunchError(f"WQ_AGENT {ident!r} is not a usable labgrid identity "
                          f"(must match {IDENT_RE.pattern})")
    if not is_live_wq_identity(ident, wq_db):
        raise LaunchError(f"WQ_AGENT {ident!r} is neither a registered wq session nor the holder "
                          f"of a claimed ticket in {wq_db}")
    return ident


def is_live_wq_identity(ident, wq_db):
    """A string is a session id only if the registry says so (wq is_session); an fworker
    identity is live while it holds a claimed ticket."""
    try:
        con = sqlite3.connect(Path(wq_db).resolve().as_uri() + "?mode=ro", uri=True, timeout=5)
        with closing(con):
            if con.execute("SELECT 1 FROM agent_sessions WHERE session_id=?", (ident,)).fetchone():
                return True
            return con.execute("SELECT 1 FROM tasks WHERE claimed_by=? AND status='claimed'",
                               (ident,)).fetchone() is not None
    except sqlite3.Error as e:
        raise LaunchError(f"cannot read wq db {wq_db}: {e}") from e


def check_coordinator(raw):
    """Return host:port for LG_COORDINATOR, refusing anything off loopback/tailnet."""
    raw = raw.strip()
    if raw.startswith("["):
        host, _, rest = raw[1:].partition("]")
        port = rest[1:] if rest.startswith(":") else ""
    elif raw.count(":") == 1:
        host, port = raw.split(":")
    elif ":" in raw:
        raise LaunchError(f"coordinator {raw!r}: bracket an IPv6 address, e.g. [::1]:{DEFAULT_COORDINATOR_PORT}")
    else:
        host, port = raw, ""
    port = port or str(DEFAULT_COORDINATOR_PORT)
    if not port.isdigit() or not 0 < int(port) < 65536:
        raise LaunchError(f"coordinator {raw!r}: bad port {port!r}")
    if host == "localhost":
        return f"{host}:{port}"
    try:
        addr = ipaddress.ip_address(host)
    except ValueError as e:
        raise LaunchError(f"coordinator {raw!r}: give a loopback or tailnet IP literal, not a "
                          f"hostname (look it up: lumen_state.py current <node>)") from e
    if not (addr.is_loopback or any(addr in net for net in TAILNET_NETS)):
        raise LaunchError(f"coordinator {raw!r} is off loopback/tailnet; the labgrid coordinator "
                          f"has no auth, so it must not be reached over the LAN (pilot F6)")
    return f"[{addr}]:{port}" if addr.version == 6 else f"{addr}:{port}"


def check_acquire_timeout(raw):
    try:
        value = float(raw)
    except ValueError as e:
        raise LaunchError(f"acquire timeout {raw!r} is not a number") from e
    if not 0 < value <= MAX_ACQUIRE_TIMEOUT:
        raise LaunchError(f"acquire timeout {value:g}s outside (0, {MAX_ACQUIRE_TIMEOUT:g}] (pilot F7)")
    return value


def build_env(base_env, ident, profile, coordinator, acquire_timeout):
    """Child env: inherited LG_*/LABGRID_MCP_* dropped (a stray LABGRID_MCP_ALLOW=ssh or
    LABGRID_MCP_SSH_KEYFILE must not reach the server), then set from the profile only."""
    leaked = FORBIDDEN_CATEGORIES.intersection(profile.allow)
    if leaked:
        raise LaunchError(f"profile allows forbidden categories {sorted(leaked)}")
    env = {k: v for k, v in base_env.items() if not k.startswith(("LG_", "LABGRID_MCP_"))}
    env.update({
        "LG_USERNAME": ident,
        "LG_HOSTNAME": f"wq-{ident}",  # owner shows as wq-<id>/<id>; the lease janitor keys on it
        "LG_COORDINATOR": coordinator,
        "LABGRID_MCP_ALLOW": ",".join(profile.allow),
        "LABGRID_MCP_ACQUIRE_TIMEOUT": f"{acquire_timeout:g}",
    })
    if profile.readonly:
        env["LABGRID_MCP_READONLY"] = "1"
    return env


def lock_sha256():
    return hashlib.sha256(LOCK_FILE.read_bytes()).hexdigest()


def check_lock_pins_wheel(text):
    m = re.search(rf"^{re.escape(PACKAGE)}=={re.escape(PACKAGE_VERSION)}\s*\\\s*\n\s*--hash=sha256:([0-9a-f]{{64}})",
                  text, re.M)
    if not m or m.group(1) != WHEEL_SHA256:
        raise LaunchError(f"{LOCK_FILE} does not pin {PACKAGE}=={PACKAGE_VERSION} to sha256 {WHEEL_SHA256}")


def check_venv(venv):
    exe = venv / "bin" / PACKAGE
    if not os.access(exe, os.X_OK):
        raise LaunchError(f"{exe} missing; build it: {LAUNCH_BIN} install")
    stamp = venv / STAMP_NAME
    built_from = stamp.read_text().strip() if stamp.is_file() else ""
    if built_from != lock_sha256():
        raise LaunchError(f"{venv} was not built from the current {LOCK_FILE.name}; "
                          f"rebuild: {LAUNCH_BIN} install --force")
    dists = sorted(p.name for p in venv.glob("lib/python*/site-packages/labgrid_mcp-*.dist-info"))
    if dists != [f"labgrid_mcp-{PACKAGE_VERSION}.dist-info"]:
        raise LaunchError(f"{venv} has {dists or 'no labgrid_mcp'}, want {PACKAGE}=={PACKAGE_VERSION}")
    return exe


def hold_identity_lock(ident, env):
    """One server per identity (pilot F2). The flock lives on an inheritable fd, so it is held
    by labgrid-mcp itself after exec and released by the kernel when that process dies."""
    lock_dir = Path(env.get("XDG_RUNTIME_DIR") or tempfile.gettempdir()) / "labgrid-mcp-launch"
    lock_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    path = lock_dir / f"{ident}.lock"
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        os.close(fd)
        raise LaunchError(f"another labgrid-mcp is already serving identity {ident!r} ({path}); "
                          f"two servers under one identity both own the device (pilot F2)") from None
    os.set_inheritable(fd, True)
    return fd


def prepare(args, env):
    profile = PROFILES[args.profile]
    ident = resolve_identity(env, args.wq_db)
    coordinator = check_coordinator(args.coordinator or env.get("LG_COORDINATOR") or DEFAULT_COORDINATOR)
    timeout = check_acquire_timeout(args.acquire_timeout)
    exe = check_venv(args.venv)
    return ident, exe, build_env(env, ident, profile, coordinator, timeout)


def cmd_serve(args):
    env = dict(os.environ)
    ident, exe, child_env = prepare(args, env)
    hold_identity_lock(ident, env)
    os.execve(exe, [str(exe)], child_env)


def cmd_check(args):
    env = dict(os.environ)
    ident, exe, child_env = prepare(args, env)
    os.close(hold_identity_lock(ident, env))
    shown = {k: v for k, v in sorted(child_env.items()) if k.startswith(("LG_", "LABGRID_MCP_"))}
    print(json.dumps({"ok": True, "profile": args.profile, "expected_tools": PROFILES[args.profile].tools,
                      "exe": str(exe), "env": shown}, indent=2))


def cmd_mcp_config(args):
    argv = ["serve", "--profile", args.profile]
    if args.coordinator:
        argv += ["--coordinator", check_coordinator(args.coordinator)]
    print(json.dumps({"mcpServers": {MCP_SERVER_NAME: {"type": "stdio", "command": LAUNCH_BIN,
                                                        "args": argv}}}, indent=2))


def cmd_install(args):
    venv = args.venv
    check_lock_pins_wheel(LOCK_FILE.read_text())
    if not args.force:
        try:
            check_venv(venv)
            print(f"already installed: {venv}")
            return
        except LaunchError:
            if venv.exists():
                raise LaunchError(f"{venv} exists but fails checks; rebuild with --force") from None
    venv.parent.mkdir(parents=True, exist_ok=True)
    # Built at its final path: console-script shebangs hardcode it, so a venv cannot be moved.
    backup = None
    if venv.exists():
        backup = venv.with_name(f"{venv.name}.bak.{time.strftime('%Y%m%d_%H%M%S')}")
        venv.rename(backup)
        print(f"previous venv moved to {backup}")
    try:
        subprocess.run([args.python, "-m", "venv", str(venv)], check=True)
        subprocess.run([str(venv / "bin" / "python"), "-m", "pip", "install", "--no-input",
                        "--disable-pip-version-check", "--require-hashes", "--no-deps",
                        "--only-binary", ":all:", "-r", str(LOCK_FILE)], check=True)
        (venv / STAMP_NAME).write_text(lock_sha256() + "\n")
        check_venv(venv)
    except (subprocess.CalledProcessError, OSError, LaunchError):
        shutil.rmtree(venv, ignore_errors=True)
        if backup:
            backup.rename(venv)
            print(f"install failed; restored {venv}", file=sys.stderr)
        raise
    print(f"installed {PACKAGE}=={PACKAGE_VERSION} into {venv}")


def main(argv=None):
    p = argparse.ArgumentParser(prog="labgrid-mcp-launch", description=__doc__.split("\n\n")[0])
    p.add_argument("--venv", type=Path, default=Path(os.environ.get("LABGRID_MCP_LAUNCH_VENV") or DEFAULT_VENV))
    sub = p.add_subparsers(dest="cmd", required=True)
    for name, fn in (("serve", cmd_serve), ("check", cmd_check)):
        s = sub.add_parser(name)
        s.add_argument("--profile", required=True, choices=sorted(PROFILES))
        s.add_argument("--coordinator", help=f"host:port, loopback/tailnet only (default LG_COORDINATOR or {DEFAULT_COORDINATOR})")
        s.add_argument("--acquire-timeout", default=str(MAX_ACQUIRE_TIMEOUT), help=f"seconds, <= {MAX_ACQUIRE_TIMEOUT:g}")
        s.add_argument("--wq-db", default=os.environ.get("WQ_DB") or DEFAULT_WQ_DB)
        s.set_defaults(fn=fn)
    s = sub.add_parser("mcp-config")
    s.add_argument("--profile", required=True, choices=sorted(PROFILES))
    s.add_argument("--coordinator")
    s.set_defaults(fn=cmd_mcp_config)
    s = sub.add_parser("install")
    s.add_argument("--python", default="python3")
    s.add_argument("--force", action="store_true")
    s.set_defaults(fn=cmd_install)
    args = p.parse_args(argv)
    try:
        args.fn(args)
    except LaunchError as e:
        # serve speaks MCP on stdout; diagnostics go to stderr only
        print(f"labgrid-mcp-launch: REFUSED: {e}", file=sys.stderr)
        return 2
    except subprocess.CalledProcessError as e:
        print(f"labgrid-mcp-launch: FAILED: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
