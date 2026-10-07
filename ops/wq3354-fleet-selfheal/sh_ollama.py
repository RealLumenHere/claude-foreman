"""CLASS 7 — ollama keep_alive pin wedges the scheduler (wq#3558).

Lumen's ollama runs OLLAMA_MAX_LOADED_MODELS=1. To load a chat model the scheduler
marks the resident model for unload (sessionDuration=0) and blocks its single
pending loop until that unload lands. A request for the resident model that
carries its own keep_alive takes the fast path (useLoadedRunner, server/sched.go
v0.34.4), which overwrites the mark with the request's keep_alive. The unload then
waits for that timer, every later embed re-arms it, and every request for any other
model queues behind the stuck loop: chat hangs while /api/embed and /api/ps answer
(2026-10-01 aq#3140, 2026-10-07 07:15-07:59Z). Reproduced live in wq#3558.

Signature: a loaded model whose expires_at is further out than the server's own
keep_alive allows, which only a client-set keep_alive can do. On the host
(ollama.hosts):

  pinned (expires_at > now + max_ttl_s)
      -> POST /api/generate {model, keep_alive: 0}. That is ollama's own unload
         call (expireRunner): it clears the pin and, once in-flight requests on
         the model finish, unloads it, which releases a waiting load. No restart,
         nothing in flight is cut off, and the next request reloads the model.
  still pinned on the next sweep after `ticket_after` breaks in a row
      -> build-lane ticket + page (and keep breaking it): a client re-pins
         faster than the sweep, or the runner is stuck unloading.

A host whose /api/ps does not answer is left alone; ollama being down is
CLASS 2 and lumen-failed-units territory.
"""
import datetime
import json
import re
import urllib.request

CLS = "ollama"


def _http_json(url, body=None, timeout=10):
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(url, data, {"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read() or b"{}")


def fetch_ps(host):
    """Loaded models from /api/ps, or None when the host does not answer."""
    try:
        return _http_json(host.rstrip("/") + "/api/ps").get("models", [])
    except (OSError, ValueError):
        return None


def expire(host, model):
    try:
        _http_json(host.rstrip("/") + "/api/generate", {"model": model, "keep_alive": 0})
        return True
    except (OSError, ValueError):
        return False


def parse_expires(s):
    """ollama's RFC 3339 with nanoseconds -> epoch seconds, or None."""
    if not s:
        return None
    s = re.sub(r"(\.\d{6})\d+", r"\1", s.strip()).replace("Z", "+00:00")
    try:
        return datetime.datetime.fromisoformat(s).timestamp()
    except ValueError:
        return None


def sweep(ctx, ps_fn=fetch_ps, expire_fn=expire):
    ocfg = ctx.cfg.get(CLS, {})
    max_ttl = int(ocfg.get("max_ttl_s", 120))
    ticket_after = int(ocfg.get("ticket_after", 3))
    streaks = ctx.section("ollama_pin_streak")
    for host in ocfg.get("hosts", ["http://127.0.0.1:11434"]):
        models = ps_fn(host)
        if models is None:
            ctx.note(CLS, f"{host} /api/ps did not answer; skipped")
            continue
        pinned = {}
        for m in models:
            exp = parse_expires(m.get("expires_at"))
            if exp is not None and exp - ctx.now > max_ttl:
                pinned[m.get("name") or m.get("model")] = int(exp - ctx.now)
        for key in [k for k in streaks if k.startswith(host + " ") and
                    k[len(host) + 1:] not in pinned]:
            streaks.pop(key)
            ctx.resolve(CLS, f"ollama-pin-stuck:{key}", f"{key} no longer pinned.")
        for model, ttl in sorted(pinned.items()):
            key = f"{host} {model}"
            n = streaks.get(key, 0) + 1
            if not ctx.dry_run:
                streaks[key] = n
            if n > ticket_after:
                ctx.file_once(CLS, f"ollama-pin-stuck:{key}",
                              f"[selfheal] ollama {model} stays pinned on {host} after "
                              f"{ticket_after} keep_alive:0 breaks",
                              f"{model} on {host} still expires {ttl}s out (policy "
                              f"max {max_ttl}s) after fleet-selfheal sent "
                              f"keep_alive:0 {ticket_after} sweeps in a row. Either a "
                              f"client re-pins it every minute (find it: `journalctl -u "
                              f"ollama | grep /api/embed` and grep callers for "
                              f"keep_alive) or the runner is stuck unloading (upstream "
                              f"ollama#17428), which needs `systemctl restart ollama`.",
                              page="high")
            ok = ctx.act(CLS, f"expire {model} on {host} (pinned {ttl}s > "
                              f"{max_ttl}s; break {n}/{ticket_after})",
                         expire_fn, host, model)
            if not ctx.dry_run:
                ctx.out.alert(f"ollama-pin-broken:{host}:{model}",
                              f"ollama {model} pinned {ttl}s on {host}; "
                              + ("sent keep_alive:0" if ok else "keep_alive:0 FAILED"),
                              "A client-set keep_alive on a resident model blocks "
                              "every other model's load under MAX_LOADED_MODELS=1 "
                              "(wq#3558).")
