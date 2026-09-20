"""Shared stubs for the fleet-node add/retire tests (design §8 T5, T6; wq#2253).

FakePorts replaces ssh, fleet-inv, the bench, the gateway and the key allowlist with in-memory stand-ins.
It deliberately keeps SystemPorts.probe: the health probe is the dispatcher's own
(dispatcher/local_capacity.py:68) and runs against a real HTTP stub server on loopback.
Time is virtual (`sleep` advances `clock`), so deadlines cost nothing.
"""
import http.server
import os
import shutil
import sys
import threading

import yaml

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
HARNESS_DIR = os.path.dirname(TESTS_DIR)
REPO = os.path.dirname(HARNESS_DIR)
sys.path.insert(0, HARNESS_DIR)
sys.path.insert(0, os.path.join(REPO, "dispatcher"))
import inference_registry as ir  # noqa: E402
import node_lifecycle as nl  # noqa: E402
import node_render as nr  # noqa: E402
import node_render_gateway as ng  # noqa: E402
from node_ports import SystemPorts  # noqa: E402

TARGETS = (nr.DISPATCHER_CONFIG, nr.MODELS_YAML) + nr.PREFIX_FILES
DEEPSEEK = ("- model_name: deepseek-v4-flash\n  litellm_params:\n    model: deepseek/deepseek-v4-flash\n"
            "    api_key: os.environ/DEEPSEEK_API_KEY\n  model_info:\n    mode: chat\n")
KEY_BASE = ["deepseek-v4-flash"]
SHA = "a" * 64
GOOD_BENCH = {"prefill_tps": 62.0, "decode_tps": 10.5, "served_ctx": 32768, "load_s": 41.2,
              "concurrent_ok": True, "oom": False, "notes": []}
# What `fleet-inv plugs` / `fleet-inv get NODE network.tailscale_ip` say for the seeded registry.
PLUGS = {"hathor": "P3", "nephthys": "P2", "isis": "p1", "seshat": "P6"}
IPS = {"hathor": "100.80.71.13", "nephthys": "100.98.74.87", "isis": "100.100.129.24", "seshat": "127.0.0.1"}


class Stub(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"ok")

    def log_message(self, *a):
        pass


def start_stub():
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Stub)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


class FakePorts(SystemPorts):
    def __init__(self):
        self.calls, self.now = [], 0.0
        self.plug_map, self.ips = dict(PLUGS), dict(IPS)
        self.live_hw, self.declared = {}, {}
        self.shas = {"isis": SHA, "seshat": SHA}
        self.bench_result = dict(GOOD_BENCH)
        self.model_is_ready, self.completes, self.gateway_ok = True, True, True
        self.key = list(KEY_BASE)
        self.fail_key_apply = False
        self.up_urls = set()
        self.running = lambda: []
        self.states, self.quarantined = [], []

    def _log(self, *what):
        self.calls.append(what)

    def plugs(self):
        return dict(self.plug_map)

    def tailscale_ip(self, node):
        return self.ips.get(node)

    def declared_hardware(self, node):
        return self.declared.get(node, {"cores": 12, "ram_gb": 64})

    def hardware(self, node):
        return self.live_hw.get(node, {"cores": 12, "ram_gb": 62})

    def blob_sha(self, node, fam_name, fam, source):
        return self.shas.get(node)

    def port_free(self, node, port):
        return True

    def serve(self, node, fam_name, fam, slots, source):
        self._log("serve", node, slots, source)

    def unserve(self, node, fam_name, fam):
        self._log("unserve", node)

    def probe(self, url):
        if url.startswith("http://127.0.0.1"):
            return SystemPorts.probe(self, url)   # the real dispatcher probe, against the HTTP stub
        return url in self.up_urls

    def model_ready(self, node, fam):
        return self.model_is_ready

    def backend_complete(self, ip, fam):
        return self.completes

    def bench(self, node, ip, fam_name, fam, slots, load_probe_s):
        self._log("bench", node)
        return dict(self.bench_result)

    def key_models(self):
        return list(self.key)

    def apply_key_allowlist(self, reg):
        if self.fail_key_apply:
            raise ng.GatewayError("/key/update answered 500")
        self.key = ng.merge_allowlist(self.key, reg)
        return {"changed": True}

    def restore_key_models(self, models):
        self._log("restore_key")
        self.key = list(models)

    def restart_gateway(self):
        self._log("restart_gateway")

    def gateway_complete(self, model):
        return self.gateway_ok

    def running_local_models(self):
        return self.running()

    def state_write(self, subject, predicate, value):
        self.states.append((subject, predicate, value))

    def quarantine(self, backend_id, clear=False):
        self.quarantined.append((backend_id, clear))

    def power_off(self, node):
        raise NotImplementedError("C6")

    def sleep(self, seconds):
        self.now += seconds

    def clock(self):
        return self.now

    def names(self):
        return [c[0] for c in self.calls]


class Env:
    """A scratch repo root + registry + gateway config + FakePorts, seeded from the real repo files."""

    def __init__(self, tmp_path, monkeypatch, stub_port):
        self.root = str(tmp_path / "repo")
        self.registry = os.path.join(self.root, "research", "inference_nodes.yaml")
        self.litellm = str(tmp_path / "agent" / "litellm_config.yaml")
        self.state_dir = str(tmp_path / "state")
        os.makedirs(os.path.dirname(self.registry))
        os.makedirs(os.path.dirname(self.litellm))
        for rel in TARGETS:
            os.makedirs(os.path.dirname(os.path.join(self.root, rel)), exist_ok=True)
            shutil.copy(os.path.join(REPO, rel), os.path.join(self.root, rel))
        text = open(os.path.join(REPO, "research", "inference_nodes.yaml")).read()
        text = text.replace("port: 8095", f"port: {stub_port}")   # qwen36-local's health probe hits the stub
        with open(self.registry, "w") as f:
            f.write(text)
        reg = yaml.safe_load(text)
        nr.apply(reg, self.root)
        with open(self.litellm, "w") as f:
            f.write("model_list:\n" + ng.render_litellm(reg) + DEEPSEEK + "litellm_settings:\n  drop_params: true\n")
        self.ports = FakePorts()
        self.ports.key = ng.merge_allowlist(list(KEY_BASE), reg)
        import local_capacity
        monkeypatch.setattr(local_capacity, "COOLDOWN_PATH", str(tmp_path / "no-cooldown.json"))
        monkeypatch.setenv("FOREMAN_FLEET_NODE_STATE", self.state_dir)

    def tracked(self):
        """{path: bytes-or-None} of every file the run may touch, plus the key allowlist."""
        paths = [os.path.join(self.root, r) for r in TARGETS + (nl.BLOCK_REL,)] + [self.registry, self.litellm]
        out = {p: (open(p).read() if os.path.exists(p) else None) for p in paths}
        out["key allowlist"] = list(self.ports.key)
        return out

    def cli(self, verb, *args):
        return nl.main([verb, *args, "--path", self.registry, "--root", self.root,
                        "--litellm-config", self.litellm], ports=self.ports)

    def read(self, rel):
        return open(os.path.join(self.root, rel)).read()

    def reg(self):
        return yaml.safe_load(open(self.registry).read())

    def healthy(self, *nodes):
        """Mark these seeded nodes' health URLs as answering their probe."""
        reg = self.reg()
        fams = reg["families"]
        for n in nodes:
            for m in reg["nodes"][n]["models"]:
                f = fams[m["family"]]
                self.ports.up_urls.add(f"http://{reg['nodes'][n]['tailscale_ip']}:{f['port']}{f['health_path']}")
