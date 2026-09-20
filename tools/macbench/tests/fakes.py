"""Fake node + fake ollama server for offline runner tests."""
import re

from mb_node import Node, screen


class FakeNode(Node):
    def __init__(self, name="wadjet", host="100.116.217.15", free_gb=500, plug=None):
        super().__init__(name, {"host": host, "ssh": f"{name}@{host}", "plug": plug})
        self.free_gb = free_gb
        self.commands = []

    def run(self, cmd, timeout=60):
        screen(cmd)
        self.commands.append(cmd)
        if cmd.startswith("df "):
            return 0, f"Avail\n{int(self.free_gb * 1e9)}\n"
        return 0, ""

    def sensors(self):
        return {"pkg_c": 61, "fan_rpm": 1800}

    def watts(self):
        return 88.0 if self.plug else None


class FakeOllama:
    """Just enough of the ollama HTTP API; token counts are len(text)//4, rates fixed."""

    def __init__(self, tags=(), resident=()):
        self.tags = set(tags)
        self.loaded = {n: {"ctx": 32768} for n in resident}
        self.calls = []
        self.cache = set()

    def get(self, url, timeout=15):
        path = re.sub(r"^https?://[^/]+", "", url)
        if path == "/api/tags":
            return {"models": [{"name": t} for t in sorted(self.tags)]}
        if path == "/api/ps":
            return {"models": [{"name": n, "context_length": v["ctx"], "size": 9 * 10**9,
                                "expires_at": "2099-01-01T00:00:00Z"}
                               for n, v in self.loaded.items()]}
        raise AssertionError(path)

    def post(self, url, body, timeout=0):
        path = re.sub(r"^https?://[^/]+", "", url)
        self.calls.append((path, dict(body)))
        if path == "/api/pull":
            self.tags.add(body["model"])
            return {"status": "success"}
        if path == "/api/generate":
            if body.get("keep_alive") == 0:
                self.loaded.pop(body["model"], None)
            else:
                self.loaded[body["model"]] = {"ctx": (body.get("options") or {}).get("num_ctx", 32768)}
            return {}
        if path == "/api/chat":
            text = "".join(m["content"] for m in body["messages"])
            n = max(1, len(text) // 4)
            key = body["messages"][0]["content"]
            if key in self.cache:
                n = 2
            self.cache.add(key)
            return {"prompt_eval_count": n, "prompt_eval_duration": int(n / 20 * 1e9),
                    "eval_count": body["options"].get("num_predict", 16),
                    "eval_duration": int(body["options"].get("num_predict", 16) / 5 * 1e9),
                    "load_duration": 0}
        raise AssertionError(path)
