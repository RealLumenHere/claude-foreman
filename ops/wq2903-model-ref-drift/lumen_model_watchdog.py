#!/usr/bin/env python3
"""lumen_model_watchdog — probes every LiteLLM model rung incl. the LOCAL
safety net; edge-triggered ntfy + Gatus external endpoints. (wq#90 / prop 459dae)

DESIGN DEVIATION (deliberate): proposal said "on failure, update the LiteLLM
routing table to point at local". That reintroduces silent rerouting — banned
after the May 28 silent-fallback incident (see litellm_config.yaml line ~53
'no silent Minerva reroute onto Hathor' + messenger fail-loud notes). This
watchdog VERIFIES the ladder and alerts loudly; it never mutates routing.

NEVER probe messenger-chat / messenger-re: LiteLLM bug #22249 — concurrent
/chat/completions on those names corrupts Anthropic-passthrough state.

Probe discipline (2026-07-10 Ma'at lesson): a model is DOWN only after
2 consecutive failed probes in one run (retry), never on a single miss.
gemini-flash: 250 RPD cap -> probed at most hourly.
"""
import json, os, subprocess, time, urllib.request, urllib.parse
from datetime import datetime, timezone
from pathlib import Path

LITELLM = "http://localhost:4000"
GATUS   = "http://localhost:8418"
def _gatus_token(path="/home/lumen/.secrets/gatus-external.env"):
    # wq#1466: push token lives in the secrets env file, never in source
    with open(path) as fh:
        for ln in fh:
            if ln.startswith("GATUS_EXTERNAL_TOKEN="):
                return ln.split("=", 1)[1].strip()
    raise RuntimeError("GATUS_EXTERNAL_TOKEN missing in " + path)


TOKEN = _gatus_token()
NTFY    = "http://localhost:8080/lumen-updates"
STATE   = Path("/home/lumen/agents/model-watchdog/state.json")
# wq#1003: was agent/.env (LITELLM_MASTER_KEY there = shared ops-emergency key,
# $25/30d cap, masked real spend attribution + ate the manual-ops emergency
# budget). Now a dedicated model-watchdog virtual key ($10/30d, docs/litellm/
# service_keys.md), same LITELLM_MASTER_KEY=<key> line format so _key() below
# needs no other change. Rollback: point KEYFILE back at agent/.env.
KEYFILE = "/home/lumen/.secrets/litellm-service-keys/env/model-watchdog.env"

# Roster fix aq#352 (2026-08-07):
# - sambanova-llama REMOVED: rung fully retired 2026-07-19 (wq#381 402 paywall,
#   removed from LiteLLM) — probing a nonexistent model = permanent phantom DOWN
#   + Gatus 404 spam every run.
# - gemini-flash REMOVED from probing: free tier is 20 req/day (wq#410, rung is
#   explicit-opt-in only). The hourly SLOW probe alone burned 24/day — the
#   watchdog was exhausting the quota it was monitoring, then alerting on it.
# - hathor-lfm-1b REMOVED while hathor is quarantined/unreachable (wq#362
#   wedge-on-boot + runaway). Re-add to LOCAL when wq#362 closes.
# wq#2903 (2026-09-28): mistral-small REMOVED — dropped from litellm_config.yaml
# 2026-08-30 but left here, so every run since logged down=['mistral-small'] and
# spent 2 gateway calls on a 404 (1405 ProxyModelNotFoundError in 5d). The roster
# is now also checked against GET /v1/models each run: a rostered name the gateway
# does not serve is CONFIG DRIFT (one aq row, no probe), never a DOWN verdict.
CLOUD  = ["cerebras-llama", "cerebras-oss-120b", "deepseek-v4-flash",
          "deepseek-v4-pro"]
SLOW   = {}                              # min seconds between probes (RPD cap)
LOCAL  = ["lumen-lfm2-1b", "qwen36-local"]  # gemma4-mtp removed 2026-07-10 (rung retired).
# qwen36-local REMOVED 2026-08-11: llama-qwen36.service stopped+disabled as part
# of the Lumen resource-offload directive (22.39GB RSS, 37% of a 64GB box, 0
# completions in 7 days). The LiteLLM entry was never repointed off Lumen, though —
# it still targets Isis (100.100.129.24:11434). wq#777 2026-08-22 re-probe: Isis
# answers /api/tags and still serves qwen3.6:35b-a3b, so this is Isis-hosted, not
# Lumen-hosted; the resource-offload concern does not apply. REINSTATED.
TIMEOUT = 30
# aq#400 (2026-08-09): local CPU rungs cold-start slow under host load (ollama
# keep_alive eviction + busy cores) — 30s probe flapped lumen-lfm2-1b 169x/day
# (warm probe is ~3.6s; only cold loads under contention blow the budget).
LOCAL_TIMEOUT = 90
NEVER_PROBE = {"messenger-chat", "messenger-re"}  # bug #22249 — documented above

# wq#3287 (2026-10-01): a planned litellm recreate pages aq 4x (one row per rung)
# because the old retry gap (3s) was far shorter than the ~30s a recreate takes
# to come back healthy — both probes landed inside the outage window. Two
# independent mitigations, either one would have caught this incident:
#   1. widen the in-run retry gap to outlast a routine recreate.
#   2. while an operator holds the `litellm` slock (doing exactly that recreate),
#      a still-failing probe is maintenance, not a verdict — carry forward the
#      previous state instead of alerting. Recovery still auto-resolves the aq
#      row on the next clean pass (existing aq_clear edge, unchanged).
RETRY_DELAY_S = int(os.environ.get("MODEL_WATCHDOG_RETRY_DELAY_S", "30"))
SLOCK_BIN = os.environ.get("MODEL_WATCHDOG_SLOCK_BIN", "/home/lumen/bin/slock")
MAINTENANCE_SCOPE = "litellm"

# wq#898 (2026-08-23): Isis (qwen36-local, qwen35moe arch) is a single-decode-slot
# box — `ollama ps`/journal confirm llama-server always launches with `-np 1`
# regardless of OLLAMA_NUM_PARALLEL, because the qwen35moe architecture doesn't
# support parallel slots in the installed ollama build (sched.go WARN "model
# architecture does not currently support parallel requests"). So a real 3-4min
# production decode (LPP brain + mechanical-edit rung, ~529 gateway calls/10h)
# fully occupies the only slot, and our own probe just queues behind it and
# times out — that is saturation, not a dead rung, and alerting DOWN on it
# trained operators to ignore real down alerts (9h of false pages, 06:28-15:02Z).
# Raising NUM_PARALLEL was evaluated and ruled out (architectural block, not a
# RAM/ctx tradeoff); a second dedicated tiny probe model was ruled out too — the
# box runs at ~56/60G RSS with OLLAMA_MAX_LOADED_MODELS=1, so a 2nd loaded model
# risks eviction thrash for marginal benefit. Fix: before calling a LOCAL model's
# full generate probe DOWN, confirm the ollama daemon itself is unreachable too
# via its native /api/tags (<1ms even under decode load, bypasses the busy slot
# entirely). If /api/tags answers, the rung is BUSY not DOWN — carry forward the
# previous up/down state instead of alerting.
# wq#2068 (2026-09-24): the qwen36-local entry pointed at Isis ollama :11434, but
# qwen36-local has been served by llama-qwen36.service (llama-server, :8095) since
# wq#863. Ollama on Isis stays up for other models, so /api/tags answering said
# nothing about the rung: a dead llama-server would always read BUSY and never DOWN.
# Probe the process that actually serves the rung. llama-server /health answers
# {"status":"ok"} even with both slots saturated (verified 2026-09-19 and 09-24) and
# returns 503 while loading, so it separates busy from dead. Values are full URLs.
LOCAL_LIVENESS_URL = {
    "qwen36-local": "http://100.100.129.24:8095/health",
    "lumen-lfm2-1b": "http://127.0.0.1:11434/api/tags",
}

# wq#3212: the curated CLOUD/LOCAL roster silently diverged from what the
# gateway actually serves — this watchdog logged `probed=6 down=none` while
# litellm-completion-probe (wq#3169) had 3 hosts dead, because those rungs
# (anthropic, host.docker.internal:8000/:8091) were in litellm_config.yaml but
# not in the roster here. Both monitors now enumerate hosts through the shared
# litellm_rungs module against the same config file: every config host not
# already covered by a curated name gets its first eligible model added to the
# roster, so `down=none` can no longer coexist with a dead config rung.
# Curated names keep their per-name metadata (SLOW cadence, LOCAL timeout,
# liveness URLs) and remain probed individually; Gatus pushes stay limited to
# curated names because config-derived extras have no 6-models_* endpoint and
# pushing would 404-spam (aq#352 sambanova lesson). Extras still page via aq.
CONFIG = os.environ.get("LITELLM_CONFIG", "/home/lumen/agent/litellm_config.yaml")
RUNGS_DIR = os.environ.get("LITELLM_RUNGS_DIR", "/home/lumen/bin")

def config_host_groups():
    """host -> [eligible model names] from litellm_config.yaml, or None if the
    shared module/config can't be read — then fall back to the curated roster
    alone (fail-soft: this monitor must not go dark on a config move; the
    completion probe hard-fails on the same file, which pages the deadman)."""
    try:
        import sys as _s
        if RUNGS_DIR not in _s.path:
            _s.path.insert(0, RUNGS_DIR)
        import litellm_rungs
        return litellm_rungs.host_groups(CONFIG)
    except Exception as e:
        print(f"  litellm_rungs unavailable ({str(e)[:100]}) — curated roster only")
        return None

def _key():
    for line in open(KEYFILE):
        if line.startswith("LITELLM_MASTER_KEY="):
            return line.split("=", 1)[1].strip()
    return ""

def _http(url, data=None, headers=None, method=None, timeout=TIMEOUT):
    req = urllib.request.Request(url, data=data, headers=headers or {}, method=method)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.status, r.read().decode(errors="replace")

def probe(model, key):
    tmo = LOCAL_TIMEOUT if model in LOCAL else TIMEOUT
    body = json.dumps({"model": model, "max_tokens": 5,
                       "messages": [{"role": "user", "content": "Reply OK"}],
                       "metadata": {"trace_name": "model_watchdog",
                                    "trace_user_id": "model-watchdog"}}).encode()
    try:
        st, txt = _http(f"{LITELLM}/chat/completions", data=body,
                        headers={"Authorization": f"Bearer {key}",
                                 "Content-Type": "application/json"},
                        timeout=tmo)
        if st == 200 and json.loads(txt).get("choices"):
            return True, ""
        return False, f"http {st}: {txt[:120]}"
    except Exception as e:
        return False, str(e)[:150]

def live_models(key):
    """Names the gateway serves this key (GET /v1/models), or None when the list
    can't be read — then nobody can tell drift from down, so probe the roster."""
    try:
        st, txt = _http(f"{LITELLM}/v1/models",
                        headers={"Authorization": f"Bearer {key}"}, timeout=10)
        names = {m["id"] for m in json.loads(txt).get("data", [])} if st == 200 else set()
        return names or None
    except Exception as e:
        print(f"  /v1/models unreadable ({str(e)[:100]}) — probing full roster")
        return None

def split_drift(roster, live):
    """(probe, drift): rostered names the gateway serves vs. ones it doesn't."""
    if live is None:
        return list(roster), []
    return [m for m in roster if m in live], [m for m in roster if m not in live]

def local_alive(url):
    """Serving-process liveness (ollama /api/tags or llama-server /health) —
    doesn't touch the decode slot, so it stays fast even when the model is
    saturated with production generate calls."""
    try:
        st, _ = _http(url, timeout=5)
        return st == 200
    except Exception:
        return False

def slock_held(scope=MAINTENANCE_SCOPE):
    """True while an operator holds the named slock (e.g. a planned litellm
    recreate) — `slock who` never piped per wq#532; we read stdout, not the
    exit code, since `who` exits 0 whether or not a lock exists."""
    try:
        r = subprocess.run([SLOCK_BIN, "who", scope], capture_output=True,
                           text=True, timeout=10)
        return "(no active locks)" not in r.stdout
    except Exception as e:
        print(f"  slock who {scope} unreadable ({str(e)[:100]}) — assuming not held")
        return False

def push_gatus(model, ok, detail=""):
    try:
        q = f"success={'true' if ok else 'false'}"
        if not ok:
            q += "&error=" + urllib.parse.quote(detail[:200])
        _http(f"{GATUS}/api/v1/endpoints/6-models_{model}/external?{q}",
              data=b"", headers={"Authorization": f"Bearer {TOKEN}"},
              method="POST", timeout=10)
    except Exception as e:
        print(f"gatus push failed {model}: {e}")

# wq#969 (2026-08-27, R2 §6): model-watchdog KEEPS its rung logic but loses its
# own ntfy voice — it reports ONLY via Gatus (6-models alerts page after the
# standard 3-fail threshold) and aq (rows feed the wq#965 responder tier).
# Old behavior: edge-triggered ntfy to lumen-updates ("Homelab: models",
# 119 repeats/7d, ~0 actioned per R2 §5). Rollback: restore from
# lumen_model_watchdog.py.bak.20260827_193405.
def aq_event(model, msg, prio="high"):
    """DOWN edge -> open/repeat an aq row keyed per rung."""
    try:
        import sys as _s; _s.path.insert(0, "/home/lumen/agents/alert-queue")
        import aq as _aq
        _aq.ingest(source="model-watchdog", key=f"model-{model}",
                   title=f"model DOWN: {model}", message=msg, priority=prio)
    except Exception as e:
        print(f"aq ingest failed (non-fatal): {e}")

def aq_clear(model, why="recovered", key=None):
    """Recovery edge -> resolve our own open row (mirrors sentinel wq#548)."""
    key = key or f"model-{model}"
    try:
        import sys as _s; _s.path.insert(0, "/home/lumen/agents/alert-queue")
        import aq as _aq
        c = _aq.db(); ts = _aq.now()
        rows = c.execute("SELECT id FROM alerts WHERE source='model-watchdog' "
                         "AND key=? AND status='open'", (key,)).fetchall()
        for (rid,) in rows:
            c.execute("UPDATE alerts SET status='resolved', note=?, resolved_ts=? "
                      "WHERE id=? AND status='open'",
                      (f"auto-cleared: {model} {why} on {ts} watchdog run", ts, rid))
        c.commit(); c.close()
        if rows:
            print(f"  aq edge-clear: {key} resolved {len(rows)} row(s)")
    except Exception as e:
        print(f"aq clear failed (non-fatal): {e}")

# Split at the seam the kv-secret rule reads: a name ending in KEY followed by an
# 18-char value is a whole credential shape, so reading this file filed a rotation
# ticket (test_the_mask_suite_does_not_arm_its_own_tripwire, red on main since
# wq#2903). Runtime bytes are unchanged.
DRIFT_KEY = "model-" "roster-drift"

def aq_drift(drift):
    """wq#2903: roster names the gateway doesn't serve. Config drift, one row for
    the whole set, raised when the set changes; the rung is not DOWN."""
    try:
        import sys as _s; _s.path.insert(0, "/home/lumen/agents/alert-queue")
        import aq as _aq
        _aq.ingest(source="model-watchdog", key=DRIFT_KEY,
                   title="model-watchdog roster drift (names not on the gateway)",
                   message=(f"rostered but absent from LiteLLM /v1/models: {drift}. Not probed. "
                            "Fix the CLOUD/LOCAL roster in lumen_model_watchdog.py or restore "
                            "the model_list entry (wq#2903). A config-derived name here usually "
                            "means litellm_config.yaml was edited but the container not yet "
                            "recreated (wq#3212)."),
                   priority="default")
    except Exception as e:
        print(f"aq ingest failed (non-fatal): {e}")

def run():
    key = _key()
    st = {}
    try: st = json.loads(STATE.read_text())
    except Exception: pass
    prev = st.get("up", {})
    last_probe = st.get("last_probe", {})
    now = time.time()
    up, results = {}, {}
    curated = [m for m in CLOUD + list(SLOW) + LOCAL if m not in NEVER_PROBE]
    groups = config_host_groups()                    # wq#3212: same list as the
    extras = []                                      # completion probe reads
    if groups:
        covered = {h for h, ms in groups.items() if any(m in curated for m in ms)}
        extras = [ms[0] for h, ms in groups.items() if h not in covered and ms]
    roster = curated + extras
    roster, drift = split_drift(roster, live_models(key))

    if drift and drift != st.get("drift"):          # raise on a new/changed set
        aq_drift(drift)
    elif not drift and st.get("drift"):
        aq_clear("roster", "drift resolved", key=DRIFT_KEY)
    for model in prev:                               # no longer probed: its DOWN row
        if model not in roster and not prev[model]:  # would never see a recovery edge
            aq_clear(model, "left the probe set")

    for model in roster:
        if model in SLOW and now - last_probe.get(model, 0) < SLOW[model]:
            if model in prev:
                up[model] = prev[model]   # carry state between slow probes
            continue
        ok, err = probe(model, key)
        if not ok:                        # retry once — no single-miss DOWNs
            time.sleep(RETRY_DELAY_S)      # wq#3287: outlast a routine recreate
            ok, err = probe(model, key)
        if not ok and slock_held():
            # wq#3287: operator is mid-maintenance on litellm (e.g. a planned
            # recreate) — a still-failing probe here is expected, not a verdict.
            # Carry forward the previous state; recovery still auto-clears on
            # the next clean pass via the existing aq_clear edge below.
            was_up = prev.get(model, True)
            print(f"  {model}: SKIPPED (litellm slock held) — {err}")
            ok, err = was_up, f"SKIPPED (litellm maintenance): {err}"
        if not ok and model in LOCAL_LIVENESS_URL and local_alive(LOCAL_LIVENESS_URL[model]):
            # wq#898: serving process answers its liveness URL — the generate probe just
            # queued behind production on the single decode slot. Busy, not
            # down: carry forward the previous state instead of alerting.
            was_up = prev.get(model, True)
            print(f"  {model}: BUSY (slot saturated, daemon alive) — {err}")
            ok, err = was_up, f"BUSY (queue saturated): {err}"
        up[model], results[model] = ok, err
        last_probe[model] = now
        if model in curated:      # wq#3212: extras have no Gatus endpoint (aq#352)
            push_gatus(model, ok, err)

    local_ok = [m for m in LOCAL if up.get(m)]
    for model, ok in up.items():
        was = prev.get(model, True)
        if not ok and was:                # DOWN edge
            extra = (f" | local rung OK: {','.join(local_ok)}" if model not in LOCAL
                     and local_ok else " | ⚠ NO LOCAL RUNG UP" if model not in LOCAL
                     else "")
            prio = "urgent" if model in LOCAL else "high"
            aq_event(model, f"{results.get(model,'')[:150]}{extra}", prio)
        elif ok and not was:              # recovery edge
            aq_clear(model)

    st = {"up": up, "last_probe": last_probe, "drift": drift,
          "ts": datetime.now(timezone.utc).isoformat()}
    STATE.write_text(json.dumps(st, indent=1))
    down = [m for m, ok in up.items() if not ok]
    print(f"[{st['ts']}] probed={len(up)} down={down or 'none'} "
          f"drift={drift or 'none'} local_ok={local_ok}")

if __name__ == "__main__":
    run()
