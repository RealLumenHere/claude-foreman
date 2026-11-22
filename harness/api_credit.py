#!/usr/bin/env python3
"""api_credit — monthly Claude API credit tracker for the fworker SDK runtime (wq#5211).

Max/Ultra plans carry a monthly Claude API credit (Oct 7 2026; platform docs
"API credits for Max and Team plans"). It covers the Messages API / Agent SDK on
a key from the linked Console org, NOT Claude Code: a request carrying the
`anthropic-beta: claude-code-*` header is refused with credit_balance_too_low on
the same key that serves a plain Messages call (measured 2026-11-21, wq#5211).
So the credit can only be spent by a non-Claude-Code client — fworker_sdk.py.

Anthropic exposes no balance endpoint, so this module is the ledger:
  * every SDK API call is priced locally (PRICES, from the platform pricing page)
    and appended to ledger.jsonl; state.json carries per-cycle spend
  * a cycle starts on cfg.anchor_day each month (credits land shortly after the
    plan payment); its grant comes from cfg.grant_schedule (Ultra $200 until the
    Dec 13 downgrade, Max $100 after)
  * the runtime is available while spend < grant - reserve and the cycle is not
    flagged exhausted; credit_balance_too_low (or LiteLLM's key budget) flags the
    cycle exhausted; a new cycle id clears it by construction
  * ntfy alerts at cfg.alert_pcts of the grant, once per cycle each
  * choose_runtime() is the one SDK-vs-CLI decision, used by fworker (claim time)
    and auto-dispatch (budget-floor bypass)

State lives in /home/lumen/foreman/api-credit/ (FOREMAN_API_CREDIT_DIR). Fail
CLOSED for the SDK runtime: unreadable config/state means "not available", which
routes the ticket to the existing CLI worker — never to unmetered spend.

CLI:
  api_credit.py status [--json] [--no-litellm]
  api_credit.py choose <ticket-id> [--model M] [--archetype A]
  api_credit.py mark-exhausted <reason>
  api_credit.py clear-exhausted
  api_credit.py set-prior <usd>          # spend already on the console this cycle
"""
import argparse
import contextlib
import fcntl
import json
import os
import re
import subprocess
import sys
import time
import urllib.request
from datetime import date, datetime, timedelta, timezone

STATE_DIR = os.environ.get("FOREMAN_API_CREDIT_DIR", "/home/lumen/foreman/api-credit")
CONFIG_PATH = os.path.join(STATE_DIR, "config.json")
STATE_PATH = os.path.join(STATE_DIR, "state.json")
LEDGER_PATH = os.path.join(STATE_DIR, "ledger.jsonl")
LOCK_PATH = os.path.join(STATE_DIR, ".lock")
NTFY_BIN = os.environ.get("FOREMAN_NTFY_BIN", "/home/lumen/bin/ntfy-send")
LITELLM_BASE_URL = os.environ.get("FOREMAN_LITELLM_BASE_URL", "http://localhost:4000")

DEFAULT_CONFIG = {
    # off unless config.json says otherwise: a missing/unreadable config never spends
    "enabled": False,
    # auto_select: fworker picks the SDK runtime for eligible tickets on its own when no
    # --runtime/FOREMAN_RUNTIME was given. False = only an explicit --runtime sdk uses it.
    "auto_select": True,
    "sdk_model": "anthropic/claude-opus-5-5",
    "key_file": "/home/lumen/.secrets/foreman-apicredit.token",
    "base_url": LITELLM_BASE_URL,
    "anchor_day": 13,
    "grant_schedule": [
        {"from": "2026-11-13", "grant_usd": 200.0, "plan": "Ultra"},
        {"from": "2026-12-13", "grant_usd": 100.0, "plan": "Max"},
    ],
    "reserve_usd": 5.0,
    "alert_pcts": [50, 80, 95],
    # routed models the SDK runtime may stand in for. Fable/Mythos stay on the CLI
    # (the plan carries its own Fable pool, and Opus would be a silent downgrade).
    "eligible_model_re": r"^(anthropic/)?claude-(opus|sonnet|haiku)|^(opus|sonnet|haiku)",
    "exclude_archetypes": ["fire-response"],
    "exclude_lanes": [],
    # prior_spend_usd[<cycle id>]: spend the console shows that this ledger never saw
    "prior_spend_usd": {},
}

# USD per million tokens, platform.claude.com pricing page (verified 2026-11-21).
# cache_write = 5-minute writes (the only kind fworker_sdk asks for).
PRICES = {
    "claude-opus-5-5": {"in": 4.00, "out": 20.00, "cache_read": 0.20, "cache_write": 5.00},
    "claude-sonnet-5-5": {"in": 2.00, "out": 10.00, "cache_read": 0.10, "cache_write": 2.50},
    "claude-sonnet-4-6": {"in": 3.00, "out": 15.00, "cache_read": 0.30, "cache_write": 3.75},
    # Haiku 5.5 is priced by prompt length: a prompt over 100K tokens pays the high tier
    # for the WHOLE request.
    "claude-haiku-5-5": {"in": 0.10, "out": 0.50, "cache_read": 0.01, "cache_write": 0.125,
                         "long_threshold": 100_000,
                         "long": {"in": 0.50, "out": 2.50, "cache_read": 0.05,
                                  "cache_write": 0.625}},
    "claude-haiku-4-5": {"in": 1.00, "out": 5.00, "cache_read": 0.10, "cache_write": 1.25},
}
# an unknown model is priced at the dearest known rate: over-counting stops early,
# under-counting overspends
FALLBACK_PRICE = {"in": 10.00, "out": 50.00, "cache_read": 1.00, "cache_write": 12.50}

CREDIT_EXHAUSTED_RE = re.compile(
    r"credit balance is too low|credit_balance_too_low|budget has been exceeded"
    r"|exceeded budget|max_budget", re.I)


def ensure_dir():
    os.makedirs(STATE_DIR, mode=0o700, exist_ok=True)


@contextlib.contextmanager
def _locked():
    ensure_dir()
    with open(LOCK_PATH, "a") as lf:
        fcntl.flock(lf, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lf, fcntl.LOCK_UN)


def load_config(path=None):
    cfg = json.loads(json.dumps(DEFAULT_CONFIG))
    p = path or CONFIG_PATH
    if os.path.exists(p):
        with open(p) as f:
            over = json.load(f)
        if not isinstance(over, dict):
            raise ValueError(f"{p}: config must be a JSON object")
        cfg.update(over)
    return cfg


def _read_state():
    if not os.path.exists(STATE_PATH):
        return {"cycles": {}}
    with open(STATE_PATH) as f:
        st = json.load(f)
    st.setdefault("cycles", {})
    return st


def _write_state(st):
    tmp = STATE_PATH + f".tmp.{os.getpid()}"
    with open(tmp, "w") as f:
        json.dump(st, f, indent=1, sort_keys=True)
    os.replace(tmp, STATE_PATH)


# ---------------------------------------------------------------------------
# cycles
# ---------------------------------------------------------------------------

def cycle_start(today=None, anchor_day=13):
    """Start date of the billing cycle containing `today` (UTC date)."""
    d = today or datetime.now(timezone.utc).date()
    day = min(anchor_day, 28)
    if d.day >= day:
        return date(d.year, d.month, day)
    y, m = (d.year - 1, 12) if d.month == 1 else (d.year, d.month - 1)
    return date(y, m, day)


def cycle_end(start):
    """Last day of the cycle that starts on `start` (inclusive)."""
    y, m = (start.year + 1, 1) if start.month == 12 else (start.year, start.month + 1)
    return date(y, m, start.day) - timedelta(days=1)


def grant_for(start, cfg):
    g = None
    for e in sorted(cfg.get("grant_schedule") or [], key=lambda e: e["from"]):
        if date.fromisoformat(e["from"]) <= start:
            g = e
    return g


def current_cycle(cfg, today=None):
    start = cycle_start(today, int(cfg.get("anchor_day", 13)))
    g = grant_for(start, cfg)
    return {"id": start.isoformat(), "start": start.isoformat(),
            "end": cycle_end(start).isoformat(),
            "grant_usd": float(g["grant_usd"]) if g else 0.0,
            "plan": (g or {}).get("plan")}


# ---------------------------------------------------------------------------
# pricing + recording
# ---------------------------------------------------------------------------

def _bare_model(model):
    m = (model or "").split("/")[-1]
    return re.sub(r"-20\d{6}$", "", m)


def price_for(model):
    return PRICES.get(_bare_model(model))


def cost_usd(model, usage):
    """USD for one Messages call. `usage` is the API usage dict (input_tokens excludes
    cache reads/writes, as the API reports it)."""
    u = usage or {}
    inp = int(u.get("input_tokens") or 0)
    out = int(u.get("output_tokens") or 0)
    cr = int(u.get("cache_read_input_tokens") or 0)
    cw = int(u.get("cache_creation_input_tokens") or 0)
    p = price_for(model) or FALLBACK_PRICE
    if "long_threshold" in p and (inp + cr + cw) > p["long_threshold"]:
        p = p["long"]
    return (inp * p["in"] + out * p["out"] + cr * p["cache_read"]
            + cw * p["cache_write"]) / 1_000_000


def _ntfy(title, msg, priority="default"):
    try:
        subprocess.run([NTFY_BIN, "-T", title, "-p", priority, msg],
                       capture_output=True, text=True, timeout=10)
    except Exception:
        pass


def _cycle_rec(st, cyc, cfg):
    rec = st["cycles"].setdefault(cyc["id"], {})
    rec.setdefault("spend_usd", 0.0)
    rec.setdefault("calls", 0)
    rec.setdefault("alerts", [])
    rec["grant_usd"] = cyc["grant_usd"]
    rec["plan"] = cyc["plan"]
    rec["prior_spend_usd"] = float((cfg.get("prior_spend_usd") or {}).get(cyc["id"], 0.0))
    return rec


def _total(rec):
    return float(rec.get("spend_usd", 0.0)) + float(rec.get("prior_spend_usd", 0.0))


def record(model, usage, *, ticket=None, session=None, attempt=None, request_id=None,
           cfg=None, now=None):
    """Price + ledger one API call; returns {"cost", "spend", "grant", "pct"}. Alerts
    fire here so they track the spend that crossed the line."""
    cfg = cfg or load_config()
    c = cost_usd(model, usage)
    now = now or time.time()
    cyc = current_cycle(cfg, datetime.fromtimestamp(now, timezone.utc).date())
    alerts = []
    with _locked():
        st = _read_state()
        rec = _cycle_rec(st, cyc, cfg)
        rec["spend_usd"] = round(float(rec["spend_usd"]) + c, 6)
        rec["calls"] = int(rec["calls"]) + 1
        rec["last_call_ts"] = now
        total = _total(rec)
        pct = 100.0 * total / cyc["grant_usd"] if cyc["grant_usd"] else 100.0
        for a in sorted(cfg.get("alert_pcts") or []):
            if pct >= a and a not in rec["alerts"]:
                rec["alerts"].append(a)
                alerts.append(a)
        u = usage or {}
        with open(LEDGER_PATH, "a") as f:
            f.write(json.dumps({
                "ts": round(now, 3), "cycle": cyc["id"], "model": model,
                "input_tokens": int(u.get("input_tokens") or 0),
                "output_tokens": int(u.get("output_tokens") or 0),
                "cache_read_input_tokens": int(u.get("cache_read_input_tokens") or 0),
                "cache_creation_input_tokens": int(u.get("cache_creation_input_tokens") or 0),
                "cost_usd": round(c, 6), "ticket": ticket, "attempt": attempt,
                "session": session, "request_id": request_id,
                # set only under pytest (repo conftest): lets the leak tripwire tell a
                # test's spend from a production worker's
                **({"test_run": os.environ["FOREMAN_API_CREDIT_TEST_RUN"]}
                   if os.environ.get("FOREMAN_API_CREDIT_TEST_RUN") else {})}) + "\n")
        _write_state(st)
    for a in alerts:
        _ntfy("API credits %d%% used" % a,
              "cycle %s (%s): $%.2f of $%.0f spent by the fworker SDK runtime"
              % (cyc["id"], cyc["plan"], total, cyc["grant_usd"]),
              priority="high" if a >= 80 else "default")
    return {"cost": c, "spend": total, "grant": cyc["grant_usd"], "pct": pct}


def mark_exhausted(reason, cfg=None, now=None):
    cfg = cfg or load_config()
    now = now or time.time()
    cyc = current_cycle(cfg, datetime.fromtimestamp(now, timezone.utc).date())
    first = False
    with _locked():
        st = _read_state()
        rec = _cycle_rec(st, cyc, cfg)
        if not rec.get("exhausted"):
            first = True
            rec["exhausted"] = {"ts": now, "reason": str(reason)[:500]}
            _write_state(st)
    if first:
        _ntfy("API credits EXHAUSTED",
              "cycle %s: %s. SDK workers stop; tickets fall back to the CLI worker "
              "(subscription pool) until the next cycle (%s+1d)."
              % (cyc["id"], str(reason)[:200], cyc["end"]), priority="high")
    return first


def clear_exhausted(cfg=None):
    cfg = cfg or load_config()
    cyc = current_cycle(cfg)
    with _locked():
        st = _read_state()
        rec = _cycle_rec(st, cyc, cfg)
        had = rec.pop("exhausted", None)
        _write_state(st)
    return had


def is_credit_exhausted_error(text):
    return bool(text) and bool(CREDIT_EXHAUSTED_RE.search(str(text)))


# ---------------------------------------------------------------------------
# availability + runtime choice
# ---------------------------------------------------------------------------

def status(cfg=None, now=None):
    """Snapshot dict. Never raises: a broken config/state reads as unavailable."""
    try:
        cfg = cfg or load_config()
        now = now or time.time()
        cyc = current_cycle(cfg, datetime.fromtimestamp(now, timezone.utc).date())
        st = _read_state()
        rec = st["cycles"].get(cyc["id"], {})
        prior = float((cfg.get("prior_spend_usd") or {}).get(cyc["id"], 0.0))
        spend = float(rec.get("spend_usd", 0.0)) + prior
        cap = max(0.0, cyc["grant_usd"] - float(cfg.get("reserve_usd", 0.0)))
        reasons = []
        if not cfg.get("enabled", True):
            reasons.append("disabled in config")
        if cyc["grant_usd"] <= 0:
            reasons.append("no grant for cycle %s" % cyc["id"])
        if rec.get("exhausted"):
            reasons.append("exhausted: %s" % rec["exhausted"].get("reason"))
        if spend >= cap:
            reasons.append("local cap: $%.2f >= $%.2f (grant $%.0f - reserve)"
                           % (spend, cap, cyc["grant_usd"]))
        if not os.path.exists(cfg.get("key_file") or ""):
            reasons.append("key file missing: %s" % cfg.get("key_file"))
        return {"available": not reasons, "reasons": reasons, "cycle": cyc,
                "spend_usd": round(spend, 4), "ledger_spend_usd": round(spend - prior, 4),
                "prior_spend_usd": prior, "cap_usd": cap,
                "remaining_usd": round(max(0.0, cap - spend), 4),
                "calls": int(rec.get("calls", 0)), "alerts": rec.get("alerts", []),
                "exhausted": rec.get("exhausted"), "sdk_model": cfg.get("sdk_model"),
                "auto_select": bool(cfg.get("auto_select", True))}
    except Exception as e:  # fail closed
        return {"available": False, "reasons": ["api_credit status error: %s" % e]}


def available(cfg=None):
    s = status(cfg)
    return s["available"], "; ".join(s.get("reasons") or []) or "ok"


def choose_runtime(ticket, model=None, archetype=None, *, explicit=None, resume_session=None,
                   cfg=None):
    """-> (runtime, model, reason). runtime is "sdk" or None (= leave the caller's
    default, the claude CLI). Order: explicit runtime > session continuity > ticket
    markers > config > routed-model/archetype/lane eligibility > credit availability."""
    def _v(x):  # `wq show` renders an empty column as the string "None"
        return None if x in (None, "", "None") else x
    model, archetype, resume_session = _v(model), _v(archetype), _v(resume_session)
    if explicit:
        return (explicit if explicit == "sdk" else None), model, "explicit runtime %s" % explicit
    if resume_session:
        # a session can only be resumed by the runtime that created it
        if str(resume_session).startswith("sdk-"):
            return "sdk", _sdk_model(cfg, model), "resuming SDK session %s" % resume_session
        return None, model, "resuming a Claude Code session"
    t = ticket or {}
    text = " ".join(str(t.get(k) or "") for k in ("title", "notes"))
    if re.search(r"\[no-sdk\]", text, re.I):
        return None, model, "ticket marked [no-sdk]"
    try:
        cfg = cfg or load_config()
    except Exception as e:
        return None, model, "api_credit config unreadable (%s)" % e
    forced = bool(re.search(r"\[sdk\]", text, re.I))
    if not forced and not cfg.get("auto_select", True):
        return None, model, "auto_select off"
    if (archetype or "") in (cfg.get("exclude_archetypes") or []):
        return None, model, "archetype %s excluded" % archetype
    if (t.get("lane") or "") in (cfg.get("exclude_lanes") or []):
        return None, model, "lane %s excluded" % t.get("lane")
    if not forced and model and not re.search(cfg.get("eligible_model_re") or "^$", model):
        return None, model, "routed model %s not credit-eligible" % model
    ok, why = available(cfg)
    if not ok:
        return None, model, "credits unavailable: %s" % why
    return "sdk", _sdk_model(cfg, model), ("ticket marked [sdk]" if forced
                                            else "credits available (routed %s)" % model)


def _sdk_model(cfg, model):
    try:
        cfg = cfg or load_config()
        return cfg.get("sdk_model") or DEFAULT_CONFIG["sdk_model"]
    except Exception:
        return DEFAULT_CONFIG["sdk_model"]


# ---------------------------------------------------------------------------
# LiteLLM cross-check (the key's own spend, priced by LiteLLM's cost map)
# ---------------------------------------------------------------------------

def litellm_key_spend(cfg=None, timeout=10):
    cfg = cfg or load_config()
    with open(cfg["key_file"]) as f:
        key = f.read().strip()
    req = urllib.request.Request(cfg.get("base_url", LITELLM_BASE_URL).rstrip("/") + "/key/info",
                                 headers={"Authorization": "Bearer " + key})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        info = json.load(r).get("info") or {}
    return {"spend": float(info.get("spend") or 0.0), "max_budget": info.get("max_budget"),
            "budget_reset_at": info.get("budget_reset_at")}


def ledger_since(ts):
    tot = 0.0
    n = 0
    if os.path.exists(LEDGER_PATH):
        with open(LEDGER_PATH) as f:
            for line in f:
                try:
                    e = json.loads(line)
                except ValueError:
                    continue
                if e.get("ts", 0) >= ts:
                    tot += float(e.get("cost_usd") or 0)
                    n += 1
    return tot, n


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("status")
    s.add_argument("--json", action="store_true")
    s.add_argument("--no-litellm", action="store_true")
    c = sub.add_parser("choose")
    c.add_argument("ticket", type=int)
    c.add_argument("--model")
    c.add_argument("--archetype")
    m = sub.add_parser("mark-exhausted")
    m.add_argument("reason")
    sub.add_parser("clear-exhausted")
    p = sub.add_parser("set-prior")
    p.add_argument("usd", type=float)
    a = ap.parse_args(argv)

    if a.cmd == "status":
        st = status()
        if not a.no_litellm:
            try:
                st["litellm_key"] = litellm_key_spend()
            except Exception as e:
                st["litellm_key"] = {"error": str(e)}
        if a.json:
            print(json.dumps(st, indent=1, sort_keys=True))
            return 0
        cy = st.get("cycle") or {}
        print("api-credit: %s" % ("AVAILABLE" if st["available"] else "UNAVAILABLE"))
        for r in st.get("reasons") or []:
            print("  reason: %s" % r)
        if cy:
            print("  cycle %s..%s plan=%s grant=$%.0f cap=$%.2f" % (
                cy["start"], cy["end"], cy["plan"], cy["grant_usd"], st["cap_usd"]))
            print("  spend $%.4f (ledger $%.4f + prior $%.2f)  remaining $%.4f  calls %d" % (
                st["spend_usd"], st["ledger_spend_usd"], st["prior_spend_usd"],
                st["remaining_usd"], st["calls"]))
            print("  model %s  auto_select=%s  alerts sent %s" % (
                st["sdk_model"], st["auto_select"], st["alerts"]))
        lk = st.get("litellm_key")
        if lk:
            if "error" in lk:
                print("  litellm key: error %s" % lk["error"])
            else:
                print("  litellm key spend $%.4f (LiteLLM cost map; budget $%s resets %s)" % (
                    lk["spend"], lk["max_budget"], lk["budget_reset_at"]))
        return 0 if st["available"] else 1
    if a.cmd == "choose":
        raw = subprocess.run(["/home/lumen/bin/wq", "show", str(a.ticket)],
                             capture_output=True, text=True, timeout=30).stdout
        t = {}
        for line in raw.splitlines():
            k, sep, v = line.partition(":")
            if sep:
                t[k.strip()] = v.strip()
        model = a.model or (t.get("model") if t.get("model") not in (None, "", "None") else None)
        print(json.dumps(dict(zip(("runtime", "model", "reason"),
                                  choose_runtime(t, model, a.archetype or t.get("archetype"))))))
        return 0
    if a.cmd == "mark-exhausted":
        print("marked" if mark_exhausted(a.reason) else "already marked")
        return 0
    if a.cmd == "clear-exhausted":
        print("cleared: %s" % clear_exhausted())
        return 0
    if a.cmd == "set-prior":
        cfg = load_config()
        cyc = current_cycle(cfg)
        over = {}
        if os.path.exists(CONFIG_PATH):
            with open(CONFIG_PATH) as f:
                over = json.load(f)
        over.setdefault("prior_spend_usd", {})[cyc["id"]] = a.usd
        ensure_dir()
        with open(CONFIG_PATH + ".tmp", "w") as f:
            json.dump(over, f, indent=1, sort_keys=True)
        os.replace(CONFIG_PATH + ".tmp", CONFIG_PATH)
        print("prior spend for cycle %s = $%.2f" % (cyc["id"], a.usd))
        return 0
    return 2


if __name__ == "__main__":
    sys.exit(main())
