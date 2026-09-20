"""Engine adapters: fetch, start, warm, complete (server-side counters only), stop.

`complete()` returns one dict shape for both engines. Every number in it comes from the
server's own counters (ollama prompt_eval_*/eval_*, llama-server timings), never from a
wall clock around the request.
"""
import json
import shlex
import time
import urllib.error
import urllib.request

from mb_node import Refused

REQUEST_TIMEOUT_S = 3000


class Http:
    def get(self, url, timeout=15):
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return json.load(r)

    def post(self, url, body, timeout=REQUEST_TIMEOUT_S):
        req = urllib.request.Request(url, data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.load(r)


def _ratio(n, secs):
    return round(n / secs, 2) if n and secs else None


class OllamaEngine:
    name = "ollama"

    def __init__(self, node, row, http=None):
        self.node, self.row, self.http = node, row, http or Http()
        self.base = node.ollama_url
        self.tag = row.tag
        self._think_rejected = False

    def _options(self, num_predict=None):
        f = self.row.flags
        o = {"temperature": 0}
        for key, opt in (("threads", "num_thread"), ("ctx", "num_ctx"), ("batch", "num_batch")):
            if f.get(key):
                o[opt] = f[key]
        if num_predict:
            o["num_predict"] = num_predict
        return o

    def has_model(self):
        tags = self.http.get(self.base + "/api/tags")
        return any(m.get("name") == self.tag or m.get("model") == self.tag
                   for m in tags.get("models", []))

    def fetch(self):
        r = self.http.post(self.base + "/api/pull", {"model": self.tag, "stream": False},
                           timeout=6 * 3600)
        if r.get("status") != "success":
            raise RuntimeError(f"pull {self.tag}: {r}")

    def residents(self):
        """[{name, ctx, keep_s}] of models currently loaded, to restore afterwards."""
        out = []
        for m in self.http.get(self.base + "/api/ps").get("models", []):
            out.append({"name": m["name"], "ctx": m.get("context_length"),
                        "expires_at": m.get("expires_at")})
        return out

    def start(self):
        self.http.post(self.base + "/api/generate", {
            "model": self.tag, "prompt": "", "stream": False, "keep_alive": "30m",
            "options": self._options()}, timeout=1800)

    def complete(self, system, user, max_tokens):
        body = {"model": self.tag, "stream": False, "keep_alive": "30m",
                "options": self._options(max_tokens),
                "messages": [{"role": "system", "content": system},
                             {"role": "user", "content": user}]}
        if self.row.flags.get("thinking") == "off" and not self._think_rejected:
            body["think"] = False
        try:
            d = self.http.post(self.base + "/api/chat", body)
        except urllib.error.HTTPError as e:
            # models without a thinking mode may reject `think` outright: retry once without it
            if e.code != 400 or "think" not in body:
                raise
            self._think_rejected = True
            del body["think"]
            d = self.http.post(self.base + "/api/chat", body)
        pn, pd = d.get("prompt_eval_count", 0), d.get("prompt_eval_duration", 0) / 1e9
        en, ed = d.get("eval_count", 0), d.get("eval_duration", 0) / 1e9
        return {"prompt_new_n": pn, "prompt_total_n": None, "cached_n": None,
                "prompt_s": round(pd, 3), "prompt_tps": _ratio(pn, pd),
                "decode_n": en, "decode_s": round(ed, 3), "decode_tps": _ratio(en, ed),
                "load_s": round(d.get("load_duration", 0) / 1e9, 2)}

    def resident_bytes(self):
        for m in self.http.get(self.base + "/api/ps").get("models", []):
            if m["name"] == self.tag or m.get("model") == self.tag:
                return m.get("size")
        return None

    def unload(self, name=None):
        self.http.post(self.base + "/api/generate",
                       {"model": name or self.tag, "prompt": "", "stream": False, "keep_alive": 0},
                       timeout=120)

    stop = unload

    def restore(self, residents):
        for r in residents:
            keep = 1800
            opts = {"num_ctx": r["ctx"]} if r.get("ctx") else {}
            self.http.post(self.base + "/api/generate", {
                "model": r["name"], "prompt": "", "stream": False,
                "keep_alive": f"{keep}s", "options": opts}, timeout=1800)


class LlamaServerEngine:
    name = "llama-server"

    def __init__(self, node, row, http=None):
        self.node, self.row, self.http = node, row, http or Http()
        self.base = f"http://{node.host}:{node.bench_port}"
        self.pid = None
        self.relative_without_dir = not row.gguf.startswith("/") and not node.models_dir
        self.path = row.gguf if row.gguf.startswith("/") else \
            f"{(node.models_dir or '<models_dir unset>').rstrip('/')}/{row.gguf}"
        self.pidfile = f"/tmp/macbench-{node.name}-{node.bench_port}.pid"

    def argv(self):
        f = self.row.flags
        a = [self.node.llama_bin, "--model", self.path, "--host", self.node.host,
             "--port", str(self.node.bench_port), "--jinja"]
        for key, opt in (("threads", "--threads"), ("ctx", "--ctx-size"), ("slots", "--parallel"),
                         ("batch", "--batch-size")):
            if f.get(key):
                a += [opt, str(f[key])]
        if f.get("kv_type") and f["kv_type"] != "f16":
            a += ["--cache-type-k", f["kv_type"], "--cache-type-v", f["kv_type"]]
        return a + [str(x) for x in f.get("extra_args") or []]

    def has_model(self):
        if self.relative_without_dir:
            raise Refused(f"{self.row.id}: gguf {self.row.gguf!r} is relative but "
                          f"{self.node.name} has no models_dir in nodes.yaml")
        rc, _ = self.node.run(f"test -s {shlex.quote(self.path)}")
        return rc == 0

    def fetch(self):
        f = self.row.fetch
        dest = shlex.quote(self.path)
        if f["kind"] == "url":
            cmd = f"curl -fL -C - --retry 3 -o {dest} {shlex.quote(f['url'])}"
        elif f["kind"] == "copy_from":
            src = f"{f['ssh']}:{f['path']}"
            cmd = f"rsync -a --partial -e 'ssh -o BatchMode=yes' {shlex.quote(src)} {dest}"
        else:
            raise RuntimeError(f"fetch kind {f['kind']} is not valid for llama-server")
        rc, _ = self.node.run(cmd, timeout=6 * 3600)
        if rc != 0:
            raise RuntimeError(f"fetch of {self.row.gguf} failed rc={rc}")

    def active_units(self):
        rc, out = self.node.run("systemctl list-units 'llama*' --state=active --no-legend --plain")
        return [ln.split()[0] for ln in out.splitlines() if ln.strip()]

    def kill_leftover(self):
        """Stop a bench server a previous (crashed) run left behind - only ours, by pidfile+port."""
        self.node.run(
            f"p=$(cat {self.pidfile} 2>/dev/null); [ -n \"$p\" ] && "
            f"tr '\\0' ' ' </proc/$p/cmdline | grep -q -- '--port {self.node.bench_port}' && "
            f"kill $p; rm -f {self.pidfile}")

    def residents(self):
        return []

    def start(self):
        self.kill_leftover()
        log = f"/tmp/macbench-{self.node.name}-{self.node.bench_port}.log"
        cmd = (f"nohup setsid {' '.join(shlex.quote(x) for x in self.argv())} "
               f">{log} 2>&1 </dev/null & echo $! | tee {self.pidfile}")
        rc, out = self.node.run(cmd)
        if rc != 0 or not out.strip().isdigit():
            raise RuntimeError(f"llama-server did not launch: rc={rc}")
        self.pid = int(out.strip())
        deadline = time.time() + 1800
        while time.time() < deadline:
            try:
                if self.http.get(self.base + "/health", timeout=10).get("status") == "ok":
                    return
            except (urllib.error.URLError, OSError, ValueError):
                pass
            time.sleep(5)
        raise RuntimeError("llama-server never became healthy")

    def complete(self, system, user, max_tokens):
        body = {"model": self.row.model, "max_tokens": max_tokens, "temperature": 0,
                "cache_prompt": True,
                "chat_template_kwargs": {"enable_thinking": False},
                "messages": [{"role": "system", "content": system},
                             {"role": "user", "content": user}]}
        d = self.http.post(self.base + "/v1/chat/completions", body)
        t = d.get("timings") or {}
        pn, cache = t.get("prompt_n", 0), t.get("cache_n")
        total = (d.get("usage") or {}).get("prompt_tokens") or (pn + (cache or 0))
        return {"prompt_new_n": pn, "prompt_total_n": total, "cached_n": cache,
                "prompt_s": round(t.get("prompt_ms", 0) / 1000, 3),
                "prompt_tps": round(t["prompt_per_second"], 2) if t.get("prompt_per_second") else None,
                "decode_n": t.get("predicted_n", 0),
                "decode_s": round(t.get("predicted_ms", 0) / 1000, 3),
                "decode_tps": round(t["predicted_per_second"], 2) if t.get("predicted_per_second") else None,
                "load_s": 0.0}

    def resident_bytes(self):
        if not self.pid:
            return None
        rc, out = self.node.run(f"awk '/VmRSS/{{print $2*1024}}' /proc/{int(self.pid)}/status")
        return int(out.strip()) if rc == 0 and out.strip().isdigit() else None

    def stop(self):
        if not self.pid:
            return
        self.kill_leftover()
        self.node.run(f"for i in $(seq 30); do [ -d /proc/{int(self.pid)} ] || exit 0; sleep 1; done; "
                      f"kill -9 {int(self.pid)}")
        self.pid = None

    def restore(self, residents):
        return None


def make_engine(node, row, http=None):
    return (OllamaEngine if row.engine == "ollama" else LlamaServerEngine)(node, row, http)


def check_can_start(engine):
    """Refuse when the node already runs a llama-server unit we cannot displace or restore."""
    if isinstance(engine, LlamaServerEngine):
        units = engine.active_units()
        if units:
            raise Refused(f"{engine.node.name} runs {units}; macbench does not stop services")
