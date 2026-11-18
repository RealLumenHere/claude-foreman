#!/usr/bin/env python3
"""verify_seneca — read-only Phase A check of the Seneca node (wq#4934).

Runs ON Lumen and probes the node that now holds the Horus NVMe. Exit 0 iff:
  1. it answers with the expected hostname,
  2. its CPU is the Seneca's, so the NVMe really is in the new chassis,
  3. an NVMe block device is present,
  4. the install is not minimized: none of the files `unminimize` removes exist, and a
     man page the minimized image excludes is back on disk,
  5. tailscale is logged in,
  6. cloudflared and dc-gateway match --carrier (standby: neither active;
     live: both active and enabled).

Every probe is one argv from fleet-exec's read-only set (hostname, cat, ls, stat,
systemctl is-*, tailscale status), so the same checks run over plain ssh or through
`--via fleet-exec` under a granted ticket. Nothing on the node is changed.

    python3 verify_seneca.py                       # ssh horus-lan
    python3 verify_seneca.py --via fleet-exec --target horus --json
    python3 verify_seneca.py --carrier live        # after Phase B step B2
"""

import argparse
import json
import shlex
import subprocess
import sys

FLEET_EXEC = "/home/lumen/bin/fleet-exec"
# What `unminimize` deletes or un-diverts when it finishes. Any of them present means the
# node is still minimized or the run was interrupted.
MINIMIZED_MARKERS = (
    "/etc/dpkg/dpkg.cfg.d/excludes",
    "/etc/dpkg/dpkg.cfg.d/excludes.dpkg-tmp",
    "/etc/update-motd.d/60-unminimize",
    "/usr/bin/man.REAL",
)
# Excluded by the minimized image's dpkg config, reinstalled by `unminimize`.
RESTORED_FILE = "/usr/share/man/man1/ls.1.gz"
CARRIER_UNITS = ("cloudflared.service", "dc-gateway.service")
SSH_TRANSPORT_RC = 255


def _exec(cmd):
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=45)
    except (OSError, subprocess.TimeoutExpired) as e:
        return SSH_TRANSPORT_RC, "", str(e)
    return p.returncode, p.stdout.strip(), p.stderr.strip()


def ssh_runner(target):
    def run(argv):
        return _exec(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=8", target, "--",
                      *[shlex.quote(a) for a in argv]])
    return run


def fleet_exec_runner(node):
    def run(argv):
        return _exec([FLEET_EXEC, "run", node, "--", *argv])
    return run


def _first_line(text):
    return text.splitlines()[0] if text else ""


def check_hostname(run, expect):
    rc, out, err = run(["hostname"])
    if rc != 0:
        return ("node:hostname", False, f"unreachable: {_first_line(err) or rc}")
    return ("node:hostname", out == expect, f"{out} (want {expect})")


def check_cpu(run, expect):
    rc, out, _ = run(["cat", "/proc/cpuinfo"])
    models = {line.split(":", 1)[1].strip() for line in out.splitlines()
              if line.startswith("model name") and ":" in line}
    model = ", ".join(sorted(models)) or "unknown"
    return ("hw:cpu", rc == 0 and any(expect in m for m in models), f"{model} (want {expect})")


def check_nvme(run):
    rc, out, _ = run(["ls", "/sys/block"])
    disks = [d for d in out.split() if d.startswith("nvme")]
    return ("hw:nvme", rc == 0 and bool(disks), ", ".join(disks) or "no nvme block device")


def _exists(run, path):
    """True, False, or None when the probe could not tell. Absence needs the remote
    stat to say so, so an unreachable node never reads as 'file not there'."""
    rc, _, err = run(["stat", "-c", "%n", path])
    if rc == 0:
        return True
    if rc != SSH_TRANSPORT_RC and "No such file or directory" in err:
        return False
    return None


def check_unminimized(run, markers=MINIMIZED_MARKERS, restored=RESTORED_FILE):
    state = {path: _exists(run, path) for path in (*markers, restored)}
    present = [p for p in markers if state[p]]
    unknown = [p for p, s in state.items() if s is None]
    if unknown:
        return ("os:unminimized", False, f"probe failed for {', '.join(unknown)}")
    if present:
        return ("os:unminimized", False, f"still minimized: {', '.join(present)}")
    if not state[restored]:
        return ("os:unminimized", False, f"{restored} missing: packages not reinstalled")
    return ("os:unminimized", True, "no minimized markers, man pages restored")


def check_tailscale(run):
    rc, out, err = run(["tailscale", "status"])
    text = out or err
    logged_out = "Logged out" in text or "NeedsLogin" in text
    return ("net:tailscale", rc == 0 and not logged_out,
            "logged in" if rc == 0 and not logged_out else _first_line(text) or f"rc {rc}")


def check_carrier(run, mode, units=CARRIER_UNITS):
    out = []
    for unit in units:
        _, active, _ = run(["systemctl", "is-active", unit])
        _, enabled, _ = run(["systemctl", "is-enabled", unit])
        if mode == "live":
            ok = active == "active" and enabled == "enabled"
        else:
            # An empty answer is a failed probe, not proof the unit is down.
            ok = bool(active) and active not in ("active", "activating", "reloading")
        out.append((f"carrier:{unit}", ok, f"{active or '?'}/{enabled or '?'} (want {mode})"))
    return out


def run_checks(run, args):
    results = [check_hostname(run, args.hostname)]
    if not results[0][1] and results[0][2].startswith("unreachable"):
        return results
    results += [check_cpu(run, args.cpu), check_nvme(run), check_unminimized(run),
                check_tailscale(run)]
    return results + check_carrier(run, args.carrier)


def main(argv=None, runner=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--target", default=None,
                    help="ssh destination (default horus-lan) or fleet-exec node (default horus)")
    ap.add_argument("--via", choices=("ssh", "fleet-exec"), default="ssh")
    ap.add_argument("--hostname", default="seneca")
    ap.add_argument("--cpu", default="i3-1115G4", help="substring of the expected CPU model")
    ap.add_argument("--carrier", choices=("standby", "live"), default="standby")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    if runner is None:
        if args.via == "fleet-exec":
            runner = fleet_exec_runner(args.target or "horus")
        else:
            runner = ssh_runner(args.target or "horus-lan")
    results = run_checks(runner, args)
    ok = all(r[1] for r in results)
    if args.json:
        print(json.dumps({"ok": ok, "checks": [
            {"name": n, "ok": o, "detail": d} for n, o, d in results]}))
    else:
        for name, o, detail in results:
            print(f"{'OK  ' if o else 'FAIL'} {name}: {detail}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
