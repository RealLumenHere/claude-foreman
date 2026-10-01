"""CLASS 5 — provider down on a dead key (wq#3354).

litellm-completion-probe (wq#3169) pages when a host fails, but nothing takes the rung
out of rotation, so fworkers keep landing on a stale key and discover the 401 mid-task.

Every `full_interval_s` this probes one chat model per api_base host through the
LiteLLM gateway; hosts already failing auth are re-probed every `recheck_interval_s`.
A host is AUTH-DOWN when the gateway answers 401/403 and the body is a provider error,
not a gateway one (LiteLLM's gateway rejections say "Invalid proxy server token" and
name no provider: docs.litellm.ai/docs/proxy/error_diagnosis). Once a host has been
auth-down for `disable_after_s` (600):

  * every model_name on that host goes into dispatcher/runtime/backend_cooldown.json
    with source=fleet-selfheal, refreshed each sweep while it stays down; router.py's
    availability_check skips a cooled rung in every pool (wq#3354 widened it from
    local-only), so dispatch routes to the next rung;
  * one rotation ticket + aq row + page.

When the host answers again our cooldown entries are removed (only ours) and the
ticket episode closes. If EVERY probed host is auth-down the problem is our own
gateway key, not N providers: page once, disable nothing.
"""
import collections
import json
import os
import re
import urllib.error
import urllib.request
from urllib.parse import urlparse

CLS = "providers"
SKIP_SUBSTRINGS = ("embed", "rerank", "whisper", "tts")
GATEWAY_MARKERS = ("invalid proxy server token", "proxy server token", "no api key passed in")
SOURCE = "fleet-selfheal"


def hosts_from_config(path):
    """OrderedDict host -> [model_name, ...] over chat-capable deployments."""
    import yaml
    with open(path) as f:
        cfg = yaml.safe_load(f) or {}
    by_host = collections.OrderedDict()
    for m in cfg.get("model_list", []) or []:
        lp = m.get("litellm_params", {}) or {}
        model_id = str(lp.get("model", ""))
        if any(s in model_id for s in SKIP_SUBSTRINGS) or not m.get("model_name"):
            continue
        host = urlparse(str(lp.get("api_base", ""))).netloc or model_id.split("/")[0]
        names = by_host.setdefault(host, [])
        if m["model_name"] not in names:
            names.append(m["model_name"])
    return by_host


def read_master_key(env_path):
    with open(env_path) as f:
        m = re.search(r"^LITELLM_MASTER_KEY=['\"]?([^'\"\s]+)", f.read(), re.M)
    if not m:
        raise RuntimeError("LITELLM_MASTER_KEY not found in " + env_path)
    return m.group(1)


def classify(status, body):
    """'ok' | 'auth' (provider rejected the key) | 'gateway-auth' | 'error'."""
    if status is not None and 200 <= status < 300:
        return "ok"
    if status in (401, 403):
        low = (body or "").lower()
        return "gateway-auth" if any(g in low for g in GATEWAY_MARKERS) else "auth"
    return "error"


def probe(url, key, model, timeout=60):
    """(status, body[:300]). The key goes in a header and nowhere else."""
    body = json.dumps({"model": model, "max_tokens": 5,
                       "messages": [{"role": "user", "content": "Reply: pong"}]}).encode()
    req = urllib.request.Request(url, body, {"Authorization": "Bearer " + key,
                                             "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, ""
    except urllib.error.HTTPError as e:
        try:
            return e.code, e.read().decode(errors="replace")[:300]
        except OSError:
            return e.code, ""
    except (OSError, ValueError) as e:
        return None, str(e)[:300]


def _write_cooldown(path, set_entries, drop_names):
    """Merge into backend_cooldown.json: add/refresh `set_entries`, remove `drop_names`
    only where WE wrote them. Other writers' rows are carried through untouched."""
    try:
        with open(path) as f:
            data = json.load(f)
        if not isinstance(data, dict):
            data = {}
    except (OSError, ValueError):
        data = {}
    for name in drop_names:
        v = data.get(name)
        if isinstance(v, dict) and v.get("source") == SOURCE:
            data.pop(name)
    data.update(set_entries)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".selfheal.tmp"
    with open(tmp, "w") as f:
        json.dump(data, f, indent=1, sort_keys=True)
    os.replace(tmp, path)
    return True


def sweep(ctx, prober=probe, key_reader=read_master_key, hosts_reader=hosts_from_config):
    p = ctx.cfg.get("providers", {})
    st = ctx.section("providers")
    hosts_state = st.setdefault("hosts", {})
    full_due = ctx.now - st.get("last_full", 0) >= int(p.get("full_interval_s", 1800))
    recheck_due = ctx.now - st.get("last_recheck", 0) >= int(p.get("recheck_interval_s", 300))
    failing = {h for h, s in hosts_state.items() if s.get("first_auth")}
    if not full_due and not (failing and recheck_due):
        return
    hosts = hosts_reader(p.get("litellm_config", "/home/lumen/agent/litellm_config.yaml"))
    targets = list(hosts) if full_due else [h for h in hosts if h in failing]
    key = key_reader(p.get("env_path", "/home/lumen/agent/.env"))
    url = p.get("gateway_url", "http://localhost:4000/v1/chat/completions")
    results = {h: classify(*prober(url, key, hosts[h][0])) for h in targets}
    st["last_recheck"] = int(ctx.now)
    if full_due:
        st["last_full"] = int(ctx.now)

    every_host_auth = full_due and len(results) > 1 and all(
        v in ("auth", "gateway-auth") for v in results.values())
    if every_host_auth or "gateway-auth" in results.values():
        ctx.file_once(CLS, "provider-gateway-key",
                      "[selfheal] LiteLLM gateway rejects our own master key",
                      "Every probed host (or the gateway itself) answered 401/403 with a "
                      "gateway-auth body, so the fault is LITELLM_MASTER_KEY in "
                      "agent/.env vs the proxy, not the providers. No rung disabled.",
                      page="urgent")
        return
    ctx.resolve(CLS, "provider-gateway-key")

    disable_after = int(p.get("disable_after_s", 600))
    hold_s = int(p.get("cooldown_s", 6 * 3600))
    cooldown_path = p.get("cooldown_path",
                          "/home/lumen/foreman/dispatcher/runtime/backend_cooldown.json")
    to_set, to_drop = {}, []
    for host, verdict in results.items():
        hs = hosts_state.setdefault(host, {})
        models = hosts[host]
        if verdict == "auth":
            first = hs.setdefault("first_auth", int(ctx.now))
            if ctx.now - first >= disable_after:
                hs["disabled"] = True
                for m in models:
                    to_set[m] = {"until": int(ctx.now) + hold_s, "source": SOURCE,
                                 "reason": f"{host} 401/403 since {first} (wq#3354)"}
                ctx.file_once(CLS, f"provider-auth:{host}",
                              f"[selfheal] rotate key: {host} rejects auth "
                              f"({int((ctx.now - first) // 60)} min)",
                              f"LiteLLM upstream {host} has answered 401/403 for "
                              f"{int((ctx.now - first) // 60)} min. Rungs {', '.join(models)} "
                              f"are cooled down in backend_cooldown.json so dispatch skips "
                              f"them. Rotate the provider key (rotate-provider-keys), "
                              f"restart LiteLLM; fleet-selfheal re-enables the rungs on the "
                              f"first good probe.",
                              page="high")
        elif verdict == "ok":
            if hs.pop("disabled", None):
                to_drop.extend(models)
            if hs.pop("first_auth", None) is not None:
                ctx.resolve(CLS, f"provider-auth:{host}", f"{host} answers again; "
                            f"rungs {', '.join(models)} re-enabled.")
        # 'error' (timeouts, 5xx, 429) is not a key problem: leave the episode as is.
    if to_set or to_drop:
        ctx.act(CLS, f"cooldown set={sorted(to_set)} drop={sorted(to_drop)}",
                _write_cooldown, cooldown_path, to_set, to_drop)
