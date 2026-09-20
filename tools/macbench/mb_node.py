"""One bench node: command transport, sensors, and the safety guards.

Every command sent to a node is a fixed template screened by `screen()`; nothing here
can reboot, suspend or power-cycle anything, and the plug tool is only ever asked for watts.
"""
import json
import os
import re
import shlex
import subprocess
import threading
import time
from urllib.parse import urlparse

import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
DISPATCH_CONFIG = os.path.join(REPO, "dispatcher", "config.json")
PLUG_CTL = "/home/lumen/bin/plug-ctl"

MIN_FREE_GB = 30
THOR = ("thor", "100.112.151.100")

# power actions and service-state changes are never sent to a node
_DENY = re.compile(
    r"\b(reboot|shutdown|poweroff|halt|suspend|hibernate|rtcwake|kexec)\b"
    r"|\binit\s+[06]\b"
    r"|\bsystemctl\s+(?!is-active\b|list-units\b|show\b|cat\b|status\b)"
    r"|\bip\s+link\b|\bdd\s+if=|\bmkfs")

SMC_SENSORS = (
    "cat /sys/devices/platform/applesmc.768/fan1_input; "
    "for h in /sys/class/hwmon/hwmon*; do "
    '[ "$(cat $h/name 2>/dev/null)" = coretemp ] && cat $h/temp1_input; done')


class Refused(RuntimeError):
    """A safety guard said no. Never retried."""


def screen(cmd):
    m = _DENY.search(cmd)
    if m:
        raise Refused(f"command refused by macbench deny-list ({m.group(0)!r}): {cmd[:80]}")
    return cmd


def load_nodes(path=None):
    with open(path or os.path.join(HERE, "nodes.yaml")) as fh:
        return yaml.safe_load(fh)["nodes"]


def _host_of(url):
    return urlparse(url).hostname if url else None


def serving_group_hosts(config_path=DISPATCH_CONFIG):
    """Hosts of every backend in the dispatcher's local-worker group (from health_url)."""
    with open(config_path) as fh:
        backends = json.load(fh)["local_backends"]
    hosts = {}
    for name in backends.get("local-worker", {}).get("group", []):
        h = _host_of((backends.get(name) or {}).get("health_url"))
        if h:
            hosts[h] = name
    return hosts


class Node:
    def __init__(self, name, spec):
        self.name = name
        self.spec = spec
        self.host = spec["host"]
        self.ssh = spec.get("ssh")            # user@host; None = run locally
        self.ollama_url = spec.get("ollama_url") or f"http://{self.host}:11434"
        self.bench_port = int(spec.get("bench_port", 8097))
        self.llama_bin = spec.get("llama_server_bin", "/usr/local/bin/llama-server")
        self.models_dir = spec.get("models_dir")
        self.plug = spec.get("plug")

    def guard_identity(self):
        if self.name.lower() == THOR[0] or self.host == THOR[1]:
            raise Refused("thor is never benched")

    def guard_serving_group(self, allow, config_path=DISPATCH_CONFIG):
        member = serving_group_hosts(config_path).get(self.host)
        if member and not allow:
            raise Refused(
                f"{self.name} ({self.host}) serves the local-worker group as {member}; "
                "pass --allow-serving-group to bench it anyway")
        return member

    def run(self, cmd, timeout=60):
        """(rc, stdout) of one screened command on the node."""
        screen(cmd)
        argv = ["bash", "-c", cmd] if not self.ssh else [
            "ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=8", self.ssh, cmd]
        try:
            p = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            return 124, ""
        return p.returncode, p.stdout

    def free_bytes(self):
        """Smallest free space over `/` and the models dir (fail-safe when the dir is unknown)."""
        paths = ["/"] + ([self.models_dir] if self.models_dir else [])
        rc, out = self.run("df --output=avail -B1 " + " ".join(shlex.quote(p) for p in paths))
        if rc != 0:
            raise Refused(f"{self.name}: df failed, cannot prove {MIN_FREE_GB} GB free")
        vals = [int(x) for x in out.split() if x.isdigit()]
        if not vals:
            raise Refused(f"{self.name}: df returned nothing")
        return min(vals)

    def guard_disk(self, need_gb=0):
        free = self.free_bytes() / 1e9
        if free < MIN_FREE_GB + (need_gb or 0):
            raise Refused(f"{self.name}: {free:.1f} GB free, need {MIN_FREE_GB} GB "
                          f"+ {need_gb or 0} GB for the model")
        return free

    def sensors(self):
        """{'fan_rpm', 'pkg_c'} from the SMC sysfs; missing values are simply absent."""
        rc, out = self.run(SMC_SENSORS, timeout=20)
        vals = out.split()
        res = {}
        try:
            res["fan_rpm"] = int(vals[0])
            res["pkg_c"] = int(vals[1]) // 1000
        except (IndexError, ValueError):
            pass
        return res

    def watts(self):
        """Wall watts from the plug tool, or None. Only the read-only `watts` verb is used."""
        if not self.plug:
            return None
        try:
            p = subprocess.run([PLUG_CTL, str(self.plug), "watts"], capture_output=True,
                               text=True, timeout=20)
        except (OSError, subprocess.TimeoutExpired):
            return None
        m = re.search(r"([0-9]+(?:\.[0-9]+)?)\s*$", p.stdout.strip())
        return float(m.group(1)) if p.returncode == 0 and m else None


class Sampler:
    """Background thermal/power sampling; `window()` summarises a measurement's span."""

    def __init__(self, node, interval=10.0):
        self.node, self.interval = node, interval
        self.samples = []
        self._stop = threading.Event()
        self._t = threading.Thread(target=self._loop, daemon=True)

    def start(self):
        self._t.start()
        return self

    def stop(self):
        self._stop.set()

    def _loop(self):
        while not self._stop.is_set():
            s = self.node.sensors()
            w = self.node.watts()
            if w is not None:
                s["watts"] = w
            s["t"] = time.time()
            self.samples.append(s)
            self._stop.wait(self.interval)

    def window(self, t0, t1):
        inside = [s for s in self.samples if t0 - self.interval <= s["t"] <= t1 + self.interval]
        out = {}
        for key, name in (("pkg_c", "pkg_c_max"), ("fan_rpm", "fan_rpm_max"), ("watts", "watts_max")):
            vals = [s[key] for s in inside if key in s]
            if vals:
                out[name] = max(vals)
        w = [s["watts"] for s in inside if "watts" in s]
        if w:
            out["watts_mean"] = round(sum(w) / len(w), 1)
        return out
