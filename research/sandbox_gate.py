#!/usr/bin/env python3
"""sandbox_gate — research pipeline v2 sandbox quality gate (wq#3759, design/wq3755 §3).

No sandbox evidence, no Frank. This runs a draft proposal's own install steps, then its
own smoke test with egress cut, and writes the result as evidence JSON the bridge
(research/bridge.py) re-checks before anything reaches wq.

Proposal file (JSON, written by the trigger service into research/proposals/pending/):
    {"id": "<slug>", "title": "...", "arch": "amd64"|"arm64",
     "install": "<shell text>", "smoke": "<shell text>", ...free-form fields}
`proposal_sha256` is the sha256 of the file's bytes, so evidence pins the exact text.

Backends:
  container    the real gate. Install runs in a container on the brief's arch (qemu-user
               binfmt covers arm64 on this x86 host), is committed to a throwaway image, and
               the smoke test runs from that image with --network none. No host mounts, no
               env passthrough, 15-minute wall clock. Runs ONLY under
               lumen-research-sandbox.service: the guard denies the container runtime to
               worker sessions (ISSUE-LOG 1262), and one auditable unit is the only code
               path that executes proposal text.
  local-bwrap  host-arch fixtures and tests: bubblewrap with a read-only /usr, a throwaway
               /work, cleared env; egress shared for install, unshared for smoke. The bridge
               refuses its evidence unless told otherwise, so it can't stand in for the gate.

Usage:
  sandbox_gate.py run <proposal.json> [--backend container|local-bwrap] [--evidence-dir D]
  sandbox_gate.py drain [--inbox D] [--gated D] [--evidence-dir D]   # the unit's entry point
"""
import argparse
import datetime
import hashlib
import json
import os
import platform
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
import uuid

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "harness"))
import masking  # noqa: E402
sys.path.insert(0, HERE)
import ledger  # noqa: E402

SCHEMA_VERSION = 1
EVIDENCE_FIELDS = {  # design §3 schema; bridge.py validates against this
    "proposal_sha256": str, "image": str, "arch": str, "install_exit": int,
    "smoke_cmd": str, "smoke_exit": (int, type(None)), "duration_s": (int, float),
    "stdout_tail": str, "install_size_kb": (int, type(None)),
}
EVIDENCE_DIR = os.path.join(HERE, "evidence")
INBOX = os.path.join(HERE, "proposals", "pending")
GATED = os.path.join(HERE, "proposals", "gated")

WALL_CLOCK_S = 15 * 60
TAIL_CHARS = 4000
TIMEOUT_EXIT = 124  # coreutils timeout(1) convention
RUNTIME = "/usr/bin/docker"
IMAGES = {"amd64": "debian:bookworm-slim", "arm64": "debian:bookworm-slim"}
ARCH_ALIASES = {"x86_64": "amd64", "amd64": "amd64", "aarch64": "arm64", "arm64": "arm64"}
ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
# The runtime CLI itself gets this env, never the unit's: nothing ambient can leak in.
CLEAN_ENV = {"PATH": "/usr/local/bin:/usr/bin:/bin", "HOME": "/nonexistent", "LANG": "C.UTF-8"}
HARDENING = ["--security-opt", "no-new-privileges", "--pids-limit", "512",
             "--memory", "2g", "--cpus", "2"]

# wq#4205: a C/make project cloned from GitHub (flashrom, etc.) isn't pip- or
# apt-installable -- it needs its own build step before the smoke test can run it.
# Detected by file presence in the install's own cwd, never by project name (a name
# guess would violate the no-guessing read doctrine).
BUILD_DETECT = (
    "if [ -f CMakeLists.txt ]; then cmake -S . -B .sandbox-build && cmake --build .sandbox-build; "
    "elif [ -f Makefile ]; then make; "
    "fi"
)
SIZE_MARKER = "SANDBOX_INSTALL_SIZE_KB"
SIZE_RE = re.compile(rf"^{SIZE_MARKER}=(\d+)$", re.MULTILINE)


def _wrap_install(install):
    """The proposal's own install text, then build detection, then a du-based install size
    probe -- one shell invocation, so a failed build still fails install_exit the same way
    a failed install already does, and the probe output rides the existing stdout capture.
    "." is wherever the install text's own cwd ended up: the container's default workdir,
    or bwrap's --chdir /work -- so a relative-path install (e.g. a git clone) is measured
    and built in place, with no new assumption about where it put its files."""
    probe = f'echo "{SIZE_MARKER}=$(du -sk . 2>/dev/null | cut -f1)"'
    return f"{install} && ({BUILD_DETECT}); _rc=$?; {probe}; exit $_rc"


class ProposalError(ValueError):
    pass


def load_proposal(path):
    with open(path, "rb") as f:
        raw = f.read()
    try:
        prop = json.loads(raw)
    except ValueError as e:
        raise ProposalError(f"not JSON: {e}")
    if not isinstance(prop, dict):
        raise ProposalError("proposal must be a JSON object")
    for key in ("id", "arch", "install", "smoke"):
        if not isinstance(prop.get(key), str) or not prop[key].strip():
            raise ProposalError(f"missing or empty field: {key}")
    if not ID_RE.match(prop["id"]):
        raise ProposalError(f"id must match {ID_RE.pattern}")
    if prop["arch"] not in IMAGES:
        raise ProposalError(f"arch {prop['arch']!r} not in {sorted(IMAGES)}")
    return prop, hashlib.sha256(raw).hexdigest()


def host_arch():
    return ARCH_ALIASES.get(platform.machine(), platform.machine())


def container_argvs(prop, tag):
    """(install, commit, smoke, cleanup...) argv lists for the container backend."""
    arch, cname = prop["arch"], f"rsg-{tag}"
    image = IMAGES[arch]
    common = ["--platform", f"linux/{arch}", *HARDENING]
    return {
        "install": [RUNTIME, "run", "--name", cname, "--pull=missing", "--network", "bridge",
                    *common, image, "/bin/sh", "-c", _wrap_install(prop["install"])],
        "commit": [RUNTIME, "commit", cname, f"rsg-img:{tag}"],
        "smoke": [RUNTIME, "run", "--rm", "--name", f"{cname}-smoke", "--network", "none",
                  *common, f"rsg-img:{tag}", "/bin/sh", "-c", prop["smoke"]],
        "kill": [RUNTIME, "kill", cname, f"{cname}-smoke"],
        "cleanup": [[RUNTIME, "rm", "-f", cname, f"{cname}-smoke"],
                    [RUNTIME, "rmi", "-f", f"rsg-img:{tag}"]],
    }, image


def bwrap_argv(script, workdir, net):
    return ["/usr/bin/bwrap", "--ro-bind", "/usr", "/usr", "--ro-bind", "/etc", "/etc",
            "--symlink", "usr/bin", "/bin", "--symlink", "usr/sbin", "/sbin",
            "--symlink", "usr/lib", "/lib", "--symlink", "usr/lib64", "/lib64",
            "--proc", "/proc", "--dev", "/dev", "--tmpfs", "/tmp",
            "--bind", workdir, "/work", "--chdir", "/work",
            "--unshare-all", *(["--share-net"] if net else []), "--die-with-parent",
            "--clearenv", "--setenv", "PATH", CLEAN_ENV["PATH"], "--setenv", "HOME", "/work",
            "/bin/sh", "-c", script]


def _run(argv, deadline, on_timeout=None):
    """(exit, output). A blown deadline is exit 124 and kills via on_timeout."""
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        return TIMEOUT_EXIT, "[sandbox_gate] wall clock exhausted before this phase\n"
    try:
        r = subprocess.run(argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                           stdin=subprocess.DEVNULL, env=CLEAN_ENV, timeout=remaining)
        return r.returncode, r.stdout.decode("utf-8", "replace")
    except subprocess.TimeoutExpired as e:
        if on_timeout:
            subprocess.run(on_timeout, env=CLEAN_ENV, capture_output=True)
        out = (e.stdout or b"").decode("utf-8", "replace")
        return TIMEOUT_EXIT, out + f"\n[sandbox_gate] timed out after {WALL_CLOCK_S}s wall clock\n"


def run_container(prop, deadline):
    argvs, image = container_argvs(prop, uuid.uuid4().hex[:12])
    try:
        install_exit, install_out = _run(argvs["install"], deadline, argvs["kill"])
        smoke_exit, smoke_out = None, ""
        if install_exit == 0:
            commit_exit, commit_out = _run(argvs["commit"], deadline)
            if commit_exit != 0:
                install_exit, install_out = commit_exit, install_out + commit_out
            else:
                smoke_exit, smoke_out = _run(argvs["smoke"], deadline, argvs["kill"])
    finally:
        for argv in argvs["cleanup"]:
            subprocess.run(argv, env=CLEAN_ENV, capture_output=True)
    return image, install_exit, install_out, smoke_exit, smoke_out


def run_local_bwrap(prop, deadline):
    if prop["arch"] != host_arch():
        raise ProposalError(f"local-bwrap runs host arch {host_arch()} only, not {prop['arch']}")
    with tempfile.TemporaryDirectory(prefix="rsg-") as workdir:
        install_exit, install_out = _run(
            bwrap_argv(_wrap_install(prop["install"]), workdir, True), deadline)
        smoke_exit, smoke_out = None, ""
        if install_exit == 0:
            smoke_exit, smoke_out = _run(bwrap_argv(prop["smoke"], workdir, False), deadline)
    return "bwrap:host", install_exit, install_out, smoke_exit, smoke_out


BACKENDS = {"container": run_container, "local-bwrap": run_local_bwrap}


def gate(path, backend="container", evidence_dir=EVIDENCE_DIR):
    """Run one proposal, write evidence/<id>.json atomically, return the evidence dict."""
    prop, sha = load_proposal(path)
    t0 = time.monotonic()
    image, install_exit, install_out, smoke_exit, smoke_out = BACKENDS[backend](
        prop, t0 + WALL_CLOCK_S)
    # wq#4205: the size probe's own echo line rides install_out, win or lose (lost on a
    # blown deadline, since _run's timeout text replaces it -- install_size_kb is None then).
    size_match = SIZE_RE.search(install_out)
    install_size_kb = int(size_match.group(1)) if size_match else None
    # Mask the whole transcript before tailing: a cut can't split a credential past the masker.
    combined = f"== install (exit {install_exit}) ==\n{install_out}"
    if smoke_exit is not None:
        combined += f"\n== smoke (exit {smoke_exit}) ==\n{smoke_out}"
    evidence = {
        "schema": SCHEMA_VERSION,
        "proposal_id": prop["id"],
        "proposal_sha256": sha,
        "image": image,
        "arch": prop["arch"],
        "backend": backend,
        "install_exit": install_exit,
        "install_size_kb": install_size_kb,
        "smoke_cmd": masking.mask_text(prop["smoke"])[0],
        "smoke_exit": smoke_exit,
        "duration_s": round(time.monotonic() - t0, 2),
        "stdout_tail": masking.mask_text(combined)[0][-TAIL_CHARS:],
        "ts": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    os.makedirs(evidence_dir, exist_ok=True)
    out = os.path.join(evidence_dir, f"{prop['id']}.json")
    fd, tmp = tempfile.mkstemp(dir=evidence_dir, prefix=f".{prop['id']}.")
    with os.fdopen(fd, "w") as f:
        json.dump(evidence, f, indent=2, sort_keys=True)
        f.write("\n")
    os.replace(tmp, out)
    return evidence


def _ledger(db):
    """The rejection ledger (wq#3760), or None if queue.db can't be opened. Fails open: the
    bridge re-checks the ledger before anything reaches wq, so a miss here costs only a
    sandbox run, never a re-proposal reaching Frank."""
    try:
        return ledger.connect(db)
    except sqlite3.Error as e:
        print(json.dumps({"ledger": "unavailable", "error": str(e)}), file=sys.stderr)
        return None


def drain(inbox=INBOX, gated=GATED, evidence_dir=EVIDENCE_DIR, db=None):
    """Gate every pending proposal, then move every inbox entry to gated/ — always, so the
    .path unit's DirectoryNotEmpty can't spin on an entry that keeps failing. A proposal
    that repeats a rejection-ledger row is refused before any sandbox spend."""
    os.makedirs(gated, exist_ok=True)
    failures = 0
    rej = _ledger(db)
    for name in sorted(os.listdir(inbox)) if os.path.isdir(inbox) else []:
        src = os.path.join(inbox, name)
        try:
            if not name.endswith(".json") or not os.path.isfile(src):
                raise ProposalError("not a .json proposal file")
            prop, sha = load_proposal(src)
            rid = rej and ledger.hit(rej, prop.get("title"), sha)
            if rid:
                print(json.dumps({"proposal": name, "refused": f"rejection ledger row {rid}"}))
                shutil.move(src, os.path.join(gated, name))
                continue
            ev = gate(src, "container", evidence_dir)
            print(json.dumps({"proposal": name, "install_exit": ev["install_exit"],
                              "smoke_exit": ev["smoke_exit"]}))
        except (ProposalError, OSError) as e:
            failures += 1
            print(json.dumps({"proposal": name, "error": str(e)}), file=sys.stderr)
        shutil.move(src, os.path.join(gated, name))
    if rej:
        rej.close()
    return failures


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="gate one proposal file")
    r.add_argument("proposal")
    r.add_argument("--backend", choices=sorted(BACKENDS), default="container")
    r.add_argument("--evidence-dir", default=EVIDENCE_DIR)
    d = sub.add_parser("drain", help="gate every proposal in the inbox (unit entry point)")
    d.add_argument("--inbox", default=INBOX)
    d.add_argument("--gated", default=GATED)
    d.add_argument("--evidence-dir", default=EVIDENCE_DIR)
    d.add_argument("--db", default=None, help="queue.db holding the rejection ledger")
    a = ap.parse_args(argv)
    if a.cmd == "drain":
        return 1 if drain(a.inbox, a.gated, a.evidence_dir, a.db) else 0
    try:
        ev = gate(a.proposal, a.backend, a.evidence_dir)
    except ProposalError as e:
        print(f"sandbox_gate: refused: {e}", file=sys.stderr)
        return 2
    print(json.dumps({k: ev[k] for k in ("proposal_id", "install_exit", "smoke_exit",
                                         "duration_s")}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
