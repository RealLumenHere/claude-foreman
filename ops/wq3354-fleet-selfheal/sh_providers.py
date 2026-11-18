"""CLASS 5 — provider down on a dead key (wq#3354).

litellm-completion-probe (wq#3169) pages when a host fails, but nothing takes the rung
out of rotation, so fworkers keep landing on a stale key and discover the 401 mid-task.

Every `full_interval_s` this probes one chat model per api_base host through the
LiteLLM gateway; hosts already failing auth are re-probed every `recheck_interval_s`.
A host is AUTH-DOWN when the gateway answers 401/403 and the body is a provider error,
not a gateway one (LiteLLM's gateway rejections say "Invalid proxy server token" and
name no provider: docs.litellm.ai/docs/proxy/error_diagnosis). A host is BILLING-DOWN
when the gateway answers 402 (provider prepaid balance exhausted, e.g. deepseek's
"Insufficient Balance", wq#3560) — a funding problem, not a key problem, so it gets a
"top up" ticket instead of a "rotate key" one. Once a host has been auth-down or
billing-down for `disable_after_s` (600):

  * every model_name on that host goes into dispatcher/runtime/backend_cooldown.json
    with source=fleet-selfheal, refreshed each sweep while it stays down; router.py's
    availability_check skips a cooled rung in every pool (wq#3354 widened it from
    local-only), so dispatch routes to the next rung;
  * one rotation (or top-up) ticket + aq row + page. A rotation ticket files in the
    human lane frank-cred: the wq#3747 gate refuses rotate-provider-keys to headless
    workers, so a build-lane ticket only ever dispatched a worker that had to block
    (wq#4782..wq#4937). A top-up ticket files in frank-funding for the same reason:
    only Frank can pay a provider.

When the host answers again our cooldown entries are removed (only ours) and the
ticket episode closes. If EVERY probed host is auth-down the problem is our own
gateway key, not N providers: page once, disable nothing. (Billing-down hosts are
each their own provider's problem, so they are excluded from that all-hosts check.)

A billing-down host before filing a top-up ticket, checks wq for an already-open,
non-done/released ticket whose title names the same provider + "balance" (e.g.
litellm-completion-probe, wq#3169, files its own under a different episode key, so
file_once's per-key dedup never sees it) and reuses that ticket's id for the episode
instead of filing a duplicate (wq#3577 duplicated wq#3557 this way on 2026-10-07).

A 429 is not a key problem (wq#4807): a free-tier RPD cap (Gemini's RESOURCE_EXHAUSTED,
quotaMetric in the body) is QUOTA — cool the rung down for the provider's own retryDelay
(parsed from the body, clamped to [30s, 6h] with +-10% jitter so every rung on the same
host doesn't wake up in the same instant, falling back to `quota_cooldown_s` when the
body has no retryDelay) and only file a ticket if it recurs more than
`quota_ticket_after_per_day` times in 24h — that's a capacity signal (upgrade tier or add
a fallback rung), not a "rotate the key" ticket, since rotating a key never clears a quota.
A 429 with no quota markers is a generic RATE_LIMITED: a short fixed backoff, never a
ticket. Both cooldowns are stored as an absolute `until` epoch, so they self-expire the
next time router.py checks them — unlike auth/billing, nothing here needs to persist
until a human rotates a key or tops up a balance.

The probe model is the host's first model_name, which for a wildcard route is the
literal pattern (`anthropic/*`), so upstream sees model `*`. That is a free auth check:
the provider checks the key first (wq#5003's episode answered 401 to it for 11 h), so a
provider 404 "model not found" means the key was ACCEPTED and counts as ok. Anthropic
reports an empty prepaid balance as a 400 "credit balance is too low", not a 402, and
that counts as billing. Any other answer on a host with an open episode is noted with
its status and provider error type: from 2026-11-15 18:47 UTC the anthropic episode sat
three days on unlogged 'error' verdicts, neither resolving nor refreshing its cooldown.
"""
import collections
import json
import os
import random
import re
import urllib.error
import urllib.request
from urllib.parse import urlparse

CLS = "providers"
SKIP_SUBSTRINGS = ("embed", "rerank", "whisper", "tts")
GATEWAY_MARKERS = ("invalid proxy server token", "proxy server token", "no api key passed in")
QUOTA_MARKERS = ("quotaMetric", "RESOURCE_EXHAUSTED")
MODEL_NOT_FOUND_MARKERS = ("not_found_error", "notfounderror")
BILLING_400_MARKERS = ("credit balance is too low",)
SOURCE = "fleet-selfheal"
MIN_COOLDOWN_S = 30
MAX_COOLDOWN_S = 6 * 3600
QUOTA_HIT_WINDOW_S = 24 * 3600


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
    """'ok' | 'auth' (provider rejected the key) | 'billing' (provider balance
    exhausted) | 'quota' (provider-side rate/quota cap, e.g. RESOURCE_EXHAUSTED —
    cools down and clears on its own, no key problem) | 'rate_limited' (generic 429,
    short backoff, never a ticket) | 'gateway-auth' | 'error' (timeouts, 5xx).
    A provider 404 model-not-found is 'ok': the key got past auth (wildcard probe)."""
    low = (body or "").lower()
    if status is not None and 200 <= status < 300:
        return "ok"
    if status == 404 and any(m in low for m in MODEL_NOT_FOUND_MARKERS):
        return "ok"
    if status == 402 or (status == 400 and any(m in low for m in BILLING_400_MARKERS)):
        return "billing"
    if status == 429:
        return "quota" if any(m in (body or "") for m in QUOTA_MARKERS) else "rate_limited"
    if status in (401, 403):
        return "gateway-auth" if any(g in low for g in GATEWAY_MARKERS) else "auth"
    return "error"


def _parse_retry_delay_s(body):
    """Seconds from a provider RetryInfo body (Google's `"retryDelay": "34s"`), or
    None when absent/unparseable."""
    m = re.search(r'"retryDelay"\s*:\s*"(\d+(?:\.\d+)?)s?"', body or "")
    return float(m.group(1)) if m else None


def _retry_cooldown_s(body, fallback_s, rand=random.random):
    """Cooldown duration: the provider's own retryDelay when present, else
    `fallback_s`, clamped to [MIN_COOLDOWN_S, MAX_COOLDOWN_S] and jittered +-10% so
    every rung on one host doesn't wake up in the same instant."""
    delay = _parse_retry_delay_s(body)
    base = max(MIN_COOLDOWN_S, min(delay if delay is not None else fallback_s, MAX_COOLDOWN_S))
    jittered = base + (rand() * 2 - 1) * 0.10 * base
    return max(MIN_COOLDOWN_S, min(jittered, MAX_COOLDOWN_S))


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


def _file_or_reuse(ctx, key, contains, title, detail, **kw):
    """file_once, unless a ticket for this outage is already open: another detector's
    (wq#3577 duplicated wq#3557) or our own from an earlier episode of a flapping host
    (gemini 403 -> 429 -> 200 -> 403 filed wq#4936..wq#5089, six open at once)."""
    dup = ctx.section("tickets").get(key) is None and ctx.out.find_open_ticket(contains)
    if dup:
        ctx.note(CLS, f"{key} already tracked by wq#{dup}; skip duplicate ticket")
        ctx.section("tickets")[key] = {"id": dup, "at": int(ctx.now),
                                       "title": f"(duplicate of wq#{dup})"}
        return dup
    return ctx.file_once(CLS, key, title, detail, **kw)


def sweep(ctx, prober=probe, key_reader=read_master_key, hosts_reader=hosts_from_config):
    p = ctx.cfg.get("providers", {})
    st = ctx.section("providers")
    hosts_state = st.setdefault("hosts", {})
    full_due = ctx.now - st.get("last_full", 0) >= int(p.get("full_interval_s", 1800))
    recheck_due = ctx.now - st.get("last_recheck", 0) >= int(p.get("recheck_interval_s", 300))
    failing = {h for h, s in hosts_state.items()
              if s.get("first_auth") or s.get("first_billing")}
    if not full_due and not (failing and recheck_due):
        return
    hosts = hosts_reader(p.get("litellm_config", "/home/lumen/agent/litellm_config.yaml"))
    targets = list(hosts) if full_due else [h for h in hosts if h in failing]
    key = key_reader(p.get("env_path", "/home/lumen/agent/.env"))
    url = p.get("gateway_url", "http://localhost:4000/v1/chat/completions")
    probed = {h: prober(url, key, hosts[h][0]) for h in targets}
    results = {h: classify(*probed[h]) for h in targets}
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
    quota_fallback_s = int(p.get("quota_cooldown_s", 1800))
    quota_ticket_after = int(p.get("quota_ticket_after_per_day", 3))
    rate_limited_fallback_s = int(p.get("rate_limited_cooldown_s", 30))
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
                _file_or_reuse(ctx, f"provider-auth:{host}",
                               [f"rotate key: {host} rejects auth"],
                               f"[selfheal] rotate key: {host} rejects auth "
                               f"({int((ctx.now - first) // 60)} min)",
                               f"LiteLLM upstream {host} has answered 401/403 for "
                               f"{int((ctx.now - first) // 60)} min. Rungs "
                               f"{', '.join(models)} are cooled down in "
                               f"backend_cooldown.json so dispatch skips them. Physical "
                               f"task for an attended operator: mint a new key in the "
                               f"{host} dashboard, run python3 /home/lumen/foreman/ops/"
                               f"wq3747-credential-rotation-safety-gate/"
                               f"vault_rotation_backup.py rotate-provider-keys (the wq#3747 "
                               f"gate refuses rotate-provider-keys to any headless worker "
                               f"without it), then rotate-provider-keys and restart "
                               f"LiteLLM. fleet-selfheal re-enables the rungs on the first "
                               f"good probe; later flaps of {host} reuse this ticket.",
                               lane=p.get("auth_ticket_lane", "frank-cred"), page="high")
        elif verdict == "billing":
            first = hs.setdefault("first_billing", int(ctx.now))
            if ctx.now - first >= disable_after:
                hs["disabled_billing"] = True
                for m in models:
                    to_set[m] = {"until": int(ctx.now) + hold_s, "source": SOURCE,
                                 "reason": f"{host} 402 since {first} (wq#3560)"}
                label = host.split(".")[-2] if host.count(".") >= 1 else host
                _file_or_reuse(ctx, f"provider-billing:{host}", [label, "balance"],
                               f"[selfheal] top up {host}: provider balance exhausted "
                               f"({int((ctx.now - first) // 60)} min)",
                               f"LiteLLM upstream {host} has answered 402 (insufficient "
                               f"balance) for {int((ctx.now - first) // 60)} min. Rungs "
                               f"{', '.join(models)} are cooled down in "
                               f"backend_cooldown.json so dispatch skips them. This is "
                               f"not a key problem — top up the account balance for "
                               f"{host} (physical task; funding tracked in wq#3557). "
                               f"fleet-selfheal re-enables the rungs on the first good "
                               f"probe.",
                               lane=p.get("billing_ticket_lane", "frank-funding"),
                               page="high")
        elif verdict == "quota":
            hits = [t for t in hs.get("quota_hits", []) if ctx.now - t < QUOTA_HIT_WINDOW_S]
            hits.append(int(ctx.now))
            hs["quota_hits"] = hits
            hs["quota_models"] = models
            cooldown = _retry_cooldown_s(probed[host][1], quota_fallback_s)
            for m in models:
                to_set[m] = {"until": int(ctx.now + cooldown), "source": SOURCE,
                             "reason": f"{host} 429 quota exhausted (wq#4807)"}
            ctx.note(CLS, f"{host} quota exhausted; cooldown {int(cooldown)}s "
                          f"({len(hits)}x in 24h)")
            if len(hits) > quota_ticket_after:
                ctx.file_once(CLS, f"provider-quota:{host}",
                              f"[selfheal] {host} quota exhausted {len(hits)}x in 24h "
                              f"— capacity, not a key problem",
                              f"LiteLLM upstream {host} has answered 429 RESOURCE_EXHAUSTED "
                              f"{len(hits)} times in the last 24h. Rungs {', '.join(models)} "
                              f"are cooled down until the provider's own retry window "
                              f"passes; no action is required unless this keeps recurring. "
                              f"If it does, it's a capacity signal — upgrade the provider "
                              f"tier or add a fallback rung. Rotating the key will not "
                              f"help: this is a quota cap, not an auth failure.",
                              pri="p3")
        elif verdict == "rate_limited":
            cooldown = _retry_cooldown_s(probed[host][1], rate_limited_fallback_s)
            for m in models:
                to_set[m] = {"until": int(ctx.now + cooldown), "source": SOURCE,
                             "reason": f"{host} 429 rate limited (wq#4807)"}
            ctx.note(CLS, f"{host} rate limited; short backoff {int(cooldown)}s")
        elif verdict == "ok":
            if hs.pop("disabled", None):
                to_drop.extend(models)
            if hs.pop("first_auth", None) is not None:
                ctx.resolve(CLS, f"provider-auth:{host}", f"{host} answers again; "
                            f"rungs {', '.join(models)} re-enabled.")
            if hs.pop("disabled_billing", None):
                to_drop.extend(models)
            if hs.pop("first_billing", None) is not None:
                ctx.resolve(CLS, f"provider-billing:{host}", f"{host} answers again; "
                            f"rungs {', '.join(models)} re-enabled.")
            if hs.pop("quota_models", None):
                to_drop.extend(models)
                ctx.resolve(CLS, f"provider-quota:{host}", f"{host} answers again; "
                            f"rungs {', '.join(models)} re-enabled.")
        elif hs.get("first_auth") or hs.get("first_billing"):
            # 'error' (timeouts, 5xx) is not a key problem: leave the episode as is,
            # but say what the host answered so a stuck episode is diagnosable.
            status, body = probed[host]
            etype = re.search(r"\b([a-z]+(?:_[a-z]+)*_error)\b", body or "")
            ctx.note(CLS, f"{host} answered {status} "
                          f"({etype.group(1) if etype else 'no provider error type'}); "
                          f"neither auth nor ok, episode stays open")
    if to_set or to_drop:
        ctx.act(CLS, f"cooldown set={sorted(to_set)} drop={sorted(to_drop)}",
                _write_cooldown, cooldown_path, to_set, to_drop)
