"""node_ports.py — every side effect `fleet-node add|retire` has on the real fleet, in one class.
research/INFERENCE_NODE_REGISTRY.md §5; wq#2253 (C4 of wq#2241).

node_lifecycle.py holds the phase logic and touches the world only through a Ports object, so the
tests hand it a subclass that fakes ssh, the backend and the gateway while the real health probe
(dispatcher/local_capacity.py:68) still runs against a local HTTP stub.

Nothing here prints a credential: the gateway key is read from its 0600 file by node_render_gateway,
sent in an Authorization header, and every message that could carry it goes through its `_mask`.
Long or disruptive remote actions are detached `systemd-run` units, never foreground (instinct 5).
"""
import json
import os
import re
import shlex
import subprocess
import sys
import threading
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import inference_registry as ir  # noqa: E402
import node_render_gateway as ng  # noqa: E402

LUMEN_STATE = os.environ.get("FOREMAN_LUMEN_STATE", "/home/lumen/minerva/lumen_state.py")
SLOT_CTX = 32768                     # QWEN36_LOCAL_WORKER_SERVING.md §2.3: 32k per slot
BENCH_PROMPT_TOKENS = 4000           # §3: warm prompt eval on a ~4k-token prompt
CONCURRENT_REQUESTS, CONCURRENT_PROMPT_TOKENS = 4, 1000
QUARANTINE_DAYS = 30
PLUG_CTL = os.environ.get("FOREMAN_PLUG_CTL", "/home/lumen/bin/plug-ctl")


class PortError(Exception):
    pass


def unit_name(fam_name, fam):
    """The llama-server systemd unit for a family: `unit:` if the registry names one, else the rule
    that reproduces the one real case (qwen36-local -> llama-qwen36)."""
    return fam.get("unit") or "llama-" + re.sub(r"-local$", "", fam_name)


def set_slots(unit_text, slots):
    """The one per-box delta §2.2 allows: --parallel and a 32k-per-slot --ctx-size."""
    text = re.sub(r"--parallel\s+\d+", f"--parallel {slots}", unit_text)
    return re.sub(r"--ctx-size\s+\d+", f"--ctx-size {slots * SLOT_CTX}", text)


def blob_path_from_unit(unit_text):
    """The GGUF path from the unit's ExecStart (-m / --model); path only, never the env block."""
    for line in unit_text.splitlines():
        if line.startswith("ExecStart="):
            argv = shlex.split(line[len("ExecStart="):])
            for i, a in enumerate(argv[:-1]):
                if a in ("-m", "--model"):
                    return argv[i + 1]
    return None


def _prompt(tokens, salt):
    """~`tokens` tokens of filler. The salt leads, so a second prompt shares no prefix with the first
    and a backend's prompt cache cannot flatter the prefill rate."""
    return f"[run {salt}] " + "The quick brown fox jumps over the lazy dog. " * (tokens // 10)


def _post(url, body, timeout):
    req = urllib.request.Request(url, data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode())


def _get(url, timeout=30):
    with urllib.request.urlopen(url, timeout=timeout) as resp:
        return json.loads(resp.read().decode())


class SystemPorts:
    def __init__(self, inventory=None):
        self.inv = inventory or ir.FleetInventory()
        self._blob_paths = {}

    # --- inventory (the authority for ip / plug / hardware) ------------------------------------
    def plugs(self):
        return self.inv.plugs()

    def tailscale_ip(self, node):
        return self.inv.tailscale_ip(node)

    def declared_hardware(self, node):
        """Cores / RAM GiB as fleet-inv records them (the declared side of the precheck)."""
        c, r = self.inv.get(node, "hardware.cpu.cores"), self.inv.get(node, "hardware.ram.size_gb")
        return {"cores": int(c) if c and c.isdigit() else None, "ram_gb": int(r) if r and r.isdigit() else None}

    def hardware(self, node):
        """Physical cores and RAM GiB the ssh'd box reports. None per field the box did not answer."""
        rc, out = self.ssh(node, "lscpu -p=CORE,SOCKET | grep -v '^#' | sort -u | wc -l; "
                                 "awk '/MemTotal/{print $2}' /proc/meminfo")
        nums = out.split()
        if rc != 0 or len(nums) < 2 or not all(n.isdigit() for n in nums[:2]):
            return {"cores": None, "ram_gb": None}
        return {"cores": int(nums[0]), "ram_gb": round(int(nums[1]) / 1048576)}

    # --- remote execution ----------------------------------------------------------------------
    def ssh(self, node, cmd, timeout=120, input=None):
        ip, user = self.inv.tailscale_ip(node), self.inv.get(node, "network.ssh.user") or node
        if not ip:
            return 255, ""
        try:
            p = subprocess.run(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", f"{user}@{ip}", cmd],
                               capture_output=True, text=True, timeout=timeout, input=input)
        except (OSError, subprocess.TimeoutExpired):
            return 255, ""
        return p.returncode, p.stdout

    def _detached(self, node, remote_cmd, prefix):
        unit = f"{prefix}-{int(time.time())}"
        rc, _ = self.ssh(node, f"sudo -n systemd-run --unit={unit} --collect --no-block {remote_cmd}")
        if rc != 0:
            raise PortError(f"{node}: systemd-run {prefix} refused (rc {rc})")

    def _blob_path(self, source, fam_name, fam):
        key = (source, fam_name)
        if key not in self._blob_paths:
            rc, out = self.ssh(source, f"systemctl cat {shlex.quote(unit_name(fam_name, fam))}.service"
                                       " | grep '^ExecStart='")
            self._blob_paths[key] = blob_path_from_unit(out) if rc == 0 else None
        return self._blob_paths[key]

    def blob_sha(self, node, fam_name, fam, source):
        """sha256 of the family's blob on `node`; None when it is not there. llama-server: the GGUF
        whose path the SOURCE node's unit names. ollama: the content digest in the blob's file name."""
        if fam["serving"] == "ollama":
            rc, out = self.ssh(node, f"ollama show {shlex.quote(fam['blob'])} --modelfile | grep -m1 '^FROM /'")
            m = re.search(r"sha256-([0-9a-f]{64})", out)
            return m.group(1) if rc == 0 and m else None
        path = self._blob_path(source, fam_name, fam)
        if not path:
            raise PortError(f"cannot read the blob path from {source}'s {unit_name(fam_name, fam)} unit")
        rc, out = self.ssh(node, f"sha256sum {shlex.quote(path)}", timeout=3600)
        return out.split()[0] if rc == 0 and out.split() else None

    def port_free(self, node, port):
        rc, out = self.ssh(node, f"ss -ltnH 'sport = :{int(port)}' | wc -l")
        return rc == 0 and out.strip() == "0"

    # --- serving -------------------------------------------------------------------------------
    def serve(self, node, fam_name, fam, slots, source):
        if fam["serving"] == "ollama":
            self._detached(node, f"ollama pull {shlex.quote(fam['blob'])}", "fleet-node-pull")
            return
        unit = unit_name(fam_name, fam)
        rc, text = self.ssh(source, f"cat /etc/systemd/system/{shlex.quote(unit)}.service")
        if rc != 0 or not text.strip():
            raise PortError(f"cannot read {unit}.service from {source}")
        rc, _ = self.ssh(node, f"sudo -n tee /etc/systemd/system/{shlex.quote(unit)}.service >/dev/null "
                               "&& sudo -n systemctl daemon-reload", input=set_slots(text, slots))
        if rc != 0:
            raise PortError(f"{node}: installing {unit}.service failed (rc {rc})")
        self._detached(node, f"systemctl enable --now {shlex.quote(unit)}.service", "fleet-node-serve")

    def unserve(self, node, fam_name, fam):
        """Undo `serve`. ollama models are left pulled (the daemon is shared with other families)."""
        if fam["serving"] == "ollama":
            return
        self._detached(node, f"systemctl disable --now {shlex.quote(unit_name(fam_name, fam))}.service",
                       "fleet-node-unserve")

    def probe(self, url):
        sys.path.insert(0, os.path.join(ir.REPO_ROOT, "dispatcher"))
        import local_capacity
        return local_capacity.http_probe(url)

    def model_ready(self, node, fam):
        """llama-server: the unit's /health is the whole story. ollama: the pull must have finished."""
        if fam["serving"] != "ollama":
            return True
        rc, out = self.ssh(node, "ollama list")
        return rc == 0 and any(ln.split()[:1] == [fam["blob"]] for ln in out.splitlines())

    def backend_complete(self, ip, fam):
        """One real completion straight at the backend's OpenAI endpoint (the gateway does not know a
        new node until phase 6, which re-checks through the gateway)."""
        try:
            r = _post(f"http://{ip}:{fam['port']}/v1/chat/completions",
                      {"model": fam.get("gateway_model") or fam["blob"], "max_tokens": 8,
                       "messages": [{"role": "user", "content": "Reply with the word ok."}]}, 600)
        except Exception:  # noqa: BLE001 — any failure is a failed completion
            return False
        return bool(r.get("choices"))

    # --- bench (QWEN36_LOCAL_WORKER_SERVING.md §3) ---------------------------------------------
    def bench(self, node, ip, fam_name, fam, slots, load_probe_s):
        base = f"http://{ip}:{fam['port']}"
        ollama = fam["serving"] == "ollama"
        notes = []
        first = self._one_shot(base, fam, _prompt(BENCH_PROMPT_TOKENS, "cold"), ollama)   # loads the model
        warm = self._one_shot(base, fam, _prompt(BENCH_PROMPT_TOKENS, "warm"), ollama)   # measured
        if warm["prompt_n"] < 0.75 * BENCH_PROMPT_TOKENS:
            notes.append(f"prompt truncated to {warm['prompt_n']} tokens; prefill rate is not a 4k-prompt rate")
        load_s = load_probe_s if not ollama else (round(first["load_s"], 1) if first["load_s"] > 1 else None)
        return {"prefill_tps": round(warm["prefill_tps"], 1), "decode_tps": round(warm["decode_tps"], 1),
                "served_ctx": self._served_ctx(base, fam, ollama), "load_s": load_s,
                "concurrent_ok": self._concurrent(base, fam, ollama),
                "oom": self._oom(node, fam_name, fam), "notes": notes}

    @staticmethod
    def _one_shot(base, fam, prompt, ollama):
        if ollama:
            r = _post(f"{base}/api/generate", {"model": fam["blob"], "prompt": prompt, "stream": False,
                                               "options": {"num_predict": 64}}, 3600)
            return {"prompt_n": r["prompt_eval_count"], "load_s": r.get("load_duration", 0) / 1e9,
                    "prefill_tps": r["prompt_eval_count"] / (r["prompt_eval_duration"] / 1e9),
                    "decode_tps": r["eval_count"] / (r["eval_duration"] / 1e9)}
        r = _post(f"{base}/completion", {"prompt": prompt, "n_predict": 64, "cache_prompt": False}, 3600)
        t = r["timings"]
        return {"prompt_n": t["prompt_n"], "load_s": 0.0, "prefill_tps": t["prompt_per_second"],
                "decode_tps": t["predicted_per_second"]}

    @staticmethod
    def _served_ctx(base, fam, ollama):
        """What the backend really serves per request: /api/ps context_length (ollama), the slot n_ctx
        llama-server reports in /props."""
        if ollama:
            models = _get(f"{base}/api/ps").get("models") or []
            hit = next((m for m in models if m.get("name") == fam["blob"] or m.get("model") == fam["blob"]),
                       models[0] if models else {})
            return hit.get("context_length")
        return _get(f"{base}/props")["default_generation_settings"]["n_ctx"]

    def _concurrent(self, base, fam, ollama):
        """4 simultaneous ~1k-token requests must all complete."""
        results = []

        def one(i):
            try:
                self._one_shot(base, fam, _prompt(CONCURRENT_PROMPT_TOKENS, f"par{i}"), ollama)
                results.append(True)
            except Exception:  # noqa: BLE001
                results.append(False)
        threads = [threading.Thread(target=one, args=(i,)) for i in range(CONCURRENT_REQUESTS)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        return len(results) == CONCURRENT_REQUESTS and all(results)

    def _oom(self, node, fam_name, fam):
        unit = "ollama" if fam["serving"] == "ollama" else unit_name(fam_name, fam)
        rc, out = self.ssh(node, f"journalctl -u {shlex.quote(unit)} --since -30min --no-pager "
                                 "| grep -ciE 'out of memory|oom-kill|cannot allocate'")
        return out.strip().isdigit() and int(out.strip()) > 0

    # --- gateway and key allowlist -------------------------------------------------------------
    def key_models(self):
        scoped = None
        try:
            scoped = ng._read_secret(ng.KEY_FILE)
            return ng._key_state(ng._http, ng.LITELLM_BASE_URL.rstrip("/"), scoped)
        except ng.GatewayError as e:
            raise PortError(ng._mask(e, scoped)) from None

    def apply_key_allowlist(self, reg):
        return ng.apply_key_allowlist(reg)

    def restore_key_models(self, models):
        """Put an exact models list back on the scoped key (the pre-run list from the snapshot)."""
        scoped = ng._read_secret(ng.KEY_FILE)
        admin = ng._read_secret(ng.ADMIN_KEY_FILE) if ng.ADMIN_KEY_FILE else scoped
        try:
            status, _ = ng._http("POST", f"{ng.LITELLM_BASE_URL.rstrip('/')}/key/update",
                                 {"Authorization": f"Bearer {admin}", "Content-Type": "application/json"},
                                 {"key": scoped, "models": models})
        except ng.GatewayError as e:
            raise PortError(ng._mask(e, scoped, admin)) from None
        if status != 200:
            raise PortError(f"/key/update answered {status} restoring the key allowlist")

    def restart_gateway(self):
        return ng.restart_gateway()

    def gateway_complete(self, model):
        scoped = ng._read_secret(ng.KEY_FILE)
        try:
            status, body = ng._http("POST", f"{ng.LITELLM_BASE_URL.rstrip('/')}/v1/chat/completions",
                                    {"Authorization": f"Bearer {scoped}", "Content-Type": "application/json"},
                                    {"model": model, "max_tokens": 8,
                                     "messages": [{"role": "user", "content": "Reply with the word ok."}]},
                                    timeout=600)
        except ng.GatewayError:
            return False
        return status == 200 and isinstance(body, dict) and bool(body.get("choices"))

    # --- dispatcher view, lumen_state, quarantine, power ---------------------------------------
    def running_local_models(self):
        """Model of every running local fworker unit — what LocalCapacity subtracts from a backend."""
        import concurrency
        models = []
        for u in concurrency.list_running_units():
            if not concurrency.is_local_unit(u):
                continue
            m = concurrency.TICKET_UNIT_RE.match(u)
            models.append(concurrency.unit_model(u) or (concurrency.ticket_model(m.group("id")) if m else None))
        return models

    def state_write(self, subject, predicate, value):
        p = subprocess.run([sys.executable, LUMEN_STATE, "ingest", subject, predicate, value,
                            "--source", "fleet-node", "--source-kind", "agent_written",
                            "--new-predicate", "fleet-node bench mirror (wq#2253)"],
                           capture_output=True, text=True, timeout=60)
        if p.returncode != 0:
            raise PortError(f"lumen_state ingest exited {p.returncode}: {p.stderr.strip()[:200]}")

    def quarantine(self, backend_id, clear=False):
        """A NO-GO backend can still answer its health probe (ollama's /api/ps always does); a long
        dispatcher cooldown is what actually keeps work off it (dispatcher/local_capacity.py:91).
        `clear` lifts it again when a later add passes the gate."""
        sys.path.insert(0, os.path.join(ir.REPO_ROOT, "dispatcher"))
        import local_capacity
        path = local_capacity.COOLDOWN_PATH
        try:
            with open(path) as f:
                data = json.load(f)
        except (OSError, ValueError):
            data = {}
        if clear:
            data.pop(backend_id, None)
        else:
            data[backend_id] = time.time() + QUARANTINE_DAYS * 86400
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            json.dump(data, f)

    def plug(self, plug_id, verb):
        """`plug-ctl <pN> on|off|watts` -> (rc, stdout). plug-ctl names plugs in lower case; the registry
        copies fleet-inv's spelling verbatim."""
        try:
            p = subprocess.run([PLUG_CTL, plug_id.lower(), verb], capture_output=True, text=True, timeout=90)
        except (OSError, subprocess.TimeoutExpired):
            return 255, ""
        return p.returncode, p.stdout

    def power_off(self, node):
        """`retire --power off`: the §6.3 sequence minus the state moves (retire owns `state: retired`),
        interlock first, plug-ctl off last (dispatcher/node_power.py)."""
        sys.path.insert(0, os.path.join(ir.REPO_ROOT, "dispatcher"))
        import node_power
        result = node_power.NodePower(ports=self, registry_path=ir.REGISTRY_YAML).power_down(
            node, "fleet-node retire --power off", manage_state=False)
        if not result["ok"]:
            raise PortError(f"{node}: power-down refused or aborted; the plug was left ON (see the power_* log lines)")

    def now(self):
        return time.time()

    def sleep(self, seconds):
        time.sleep(seconds)

    def clock(self):
        return time.monotonic()
