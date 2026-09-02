#!/usr/bin/env python3
"""lumen-public-guardian — watchdog for the two surfaces the public reaches
Lumen through: email (lumen@lumennode.uk) and the LPP stream
(pokemon.lumennode.uk). wq#1252.

Why this exists: on 2026-09-02 a mailbot deploy shipped without its D1
migrations. storeMessage() logs and swallows D1 errors, so every inbound and
outbound message failed to store for ~7.5h and nothing noticed until a human
tested by hand. Liveness ("the worker responds") was green the whole time.
Every check here is therefore a *correctness* check with an explicit freshness
or equality assertion, not a ping.

Structure:
  guardian.py        this file — check registry, state machine, alerting,
                     heal ladder, fire tickets, status publishing
  checks_email.py    E1..E6
  checks_stream.py   S1..S4
  schema_parse.py    migrations -> expected D1 schema

Run:
  guardian.py run [--dry-run] [--only ID[,ID...]] [--force]
  guardian.py status          print last published status
  guardian.py selftest        config + dependency sanity, no side effects
"""
import argparse
import json
import os
import shlex
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

DEFAULT_CONFIG = os.path.join(HERE, "config.json")
NTFY_BIN = "/home/lumen/bin/ntfy-send"

OK = "ok"
RED = "red"
UNCONFIGURED = "unconfigured"   # check cannot run — missing credential/path

# Every command the guardian is allowed to run as a heal. The ticket's rule is
# "heals only via listed commands, anything else -> fire ticket + research", so
# this is a prefix allowlist checked at call time, not a convention. Adding a
# heal means editing this table and the runbook together.
HEAL_ALLOWLIST = [
    ["sudo", "systemctl", "restart", "lumen-pokemon-live.service"],
    ["sudo", "systemd-run", "--collect", "--unit"],   # cloudflared restart (instinct 4)
    ["npx", "wrangler", "d1", "migrations", "apply"],
    ["npx", "wrangler", "rollback"],
    ["/usr/bin/python3", "/home/lumen/mailbot/mail_recall.py", "sync"],
]


class HealNotAllowed(Exception):
    """Raised when a heal tries to run a command outside HEAL_ALLOWLIST."""


class Result:
    """One check outcome. `detail` is what lands in the ntfy body and the fire
    ticket, so it must name the observed value, not just the verdict."""

    def __init__(self, check_id, status, detail, evidence=None):
        self.id = check_id
        self.status = status
        self.detail = detail
        self.evidence = evidence or {}

    def __repr__(self):
        return f"Result({self.id}, {self.status}, {self.detail!r})"


# --------------------------------------------------------------------------
# config / state


def load_config(path=DEFAULT_CONFIG):
    with open(path) as f:
        cfg = json.load(f)
    cfg.setdefault("_config_path", path)
    return cfg


def load_state(path):
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return {"checks": {}}


def save_state(path, state):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(state, f, indent=2, sort_keys=True)
    os.replace(tmp, path)


def check_state(state, check_id):
    return state.setdefault("checks", {}).setdefault(check_id, {
        "status": OK,
        "consecutive_red": 0,
        "heal_step": 0,
        "last_run": 0,
        "last_alert": 0,
        "fire_ticket": None,
    })


# --------------------------------------------------------------------------
# alerting


def ntfy(cfg, priority, title, message, dry_run=False):
    if dry_run:
        print(f"[dry-run] ntfy-send -p {priority} -T {title!r} {message!r}")
        return True
    argv = [NTFY_BIN, "-p", priority, "-T", title, message]
    topic = cfg.get("ntfy_topic")
    if topic:
        argv[1:1] = ["-t", topic]
    try:
        p = subprocess.run(argv, capture_output=True, text=True, timeout=30)
        if p.returncode != 0:
            print(f"guardian: ntfy-send exit {p.returncode}: {p.stderr.strip()[:200]}",
                  file=sys.stderr)
        return p.returncode == 0
    except (OSError, subprocess.TimeoutExpired) as exc:
        print(f"guardian: ntfy-send failed: {exc}", file=sys.stderr)
        return False


def open_fire(cfg, result, heal_log, dry_run=False):
    """Open a fleet-fires ticket pointing at the runbook. fleet-fires has
    requires_approval=false (dispatcher/config.json), so the dispatcher spawns
    a worker on its next pass without waiting for Frank — that worker is the
    'one focused on him' the ticket asks for. Returns the ticket id or None."""
    runbook = cfg["runbook"]
    title = f"FIRE {result.id}: {result.detail[:90]}"
    detail = (
        f"lumen-public-guardian check {result.id} is RED and did not recover after "
        f"its heal ladder.\n\n"
        f"Observed: {result.detail}\n"
        f"Evidence: {json.dumps(result.evidence, sort_keys=True)[:1200]}\n"
        f"Heals attempted: {heal_log or 'none'}\n\n"
        f"READ {runbook} FIRST — it explains what {result.id} means, which heals are "
        f"safe, the rollback commands, and when to page Frank. Do not invent a heal "
        f"that is not in that runbook; research and report instead.\n"
        f"Guardian state: {cfg['state_path']}\n"
        f"Guardian source: {HERE}/\n"
    )
    # wq enforces a canonical subsystem set and REFUSES anything else, so this
    # maps onto it rather than inventing a label: email checks are the
    # messenger surface, stream checks ride cloudflared.
    subsystem = "messenger" if result.id.startswith("E") else "cloudflared"
    argv = [
        cfg["wq_bin"], "add", "--lane", cfg["fire_lane"], "--pri", "1",
        "--title", title, "--detail", detail,
        "--subsystems", subsystem,
    ]
    if dry_run:
        print("[dry-run] " + " ".join(shlex.quote(a) for a in argv[:8]) + " ...")
        return None
    try:
        p = subprocess.run(argv, capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired) as exc:
        print(f"guardian: wq add failed: {exc}", file=sys.stderr)
        return None
    if p.returncode != 0:
        print(f"guardian: wq add exit {p.returncode}: {p.stderr.strip()[:200]}",
              file=sys.stderr)
        return None
    for tok in p.stdout.split():
        if tok.isdigit():
            return tok
    return p.stdout.strip()[:60] or "opened"


# --------------------------------------------------------------------------
# heals


def run_heal(argv, timeout=180, cwd=None, dry_run=False):
    """Run one heal command after checking it against HEAL_ALLOWLIST."""
    if not any(argv[:len(pref)] == pref for pref in HEAL_ALLOWLIST):
        raise HealNotAllowed(" ".join(argv[:4]))
    if dry_run:
        return True, "[dry-run] " + " ".join(shlex.quote(a) for a in argv)
    try:
        p = subprocess.run(argv, capture_output=True, text=True,
                           timeout=timeout, cwd=cwd)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, f"{type(exc).__name__}: {exc}"
    out = (p.stdout + p.stderr).strip()[:400]
    return p.returncode == 0, out


# --------------------------------------------------------------------------
# check registry


def build_registry(cfg):
    """check_id -> {fn, interval_s, heals, desc}.

    interval_s throttles checks that cost money or a remote round trip; the
    timer itself fires every 2 min and cheap checks run every pass.

    Imported here rather than at module top because the check modules import
    Result/run_heal back out of this one — a top-level import would see a
    half-initialised guardian module.
    """
    import checks_email
    import checks_stream

    reg = {}
    reg.update(checks_email.registry(cfg))
    reg.update(checks_stream.registry(cfg))
    return reg


# --------------------------------------------------------------------------
# main loop


def run_once(cfg, only=None, dry_run=False, force=False, now=None):
    now = now if now is not None else time.time()
    state = load_state(cfg["state_path"])
    registry = build_registry(cfg)
    results = []

    for check_id, spec in sorted(registry.items()):
        if only and check_id not in only:
            continue
        cs = check_state(state, check_id)
        interval = spec.get("interval_s", 0)
        if not force and interval and now - cs["last_run"] < interval:
            continue
        cs["last_run"] = now
        try:
            result = spec["fn"](cfg)
        except Exception as exc:                      # noqa: BLE001
            # A check that raises is itself a fault worth surfacing: the
            # silent-failure class is exactly what this service exists to stop.
            result = Result(check_id, RED,
                            f"check raised {type(exc).__name__}: {exc}"[:200])
        results.append(result)
        _apply(cfg, state, cs, spec, result, dry_run, now)

    _publish(cfg, state, results, dry_run, now)
    if not dry_run:
        save_state(cfg["state_path"], state)
    return results, state


def _apply(cfg, state, cs, spec, result, dry_run, now):
    """Transition the check's state, alert, and walk the heal ladder."""
    prev = cs["status"]

    if result.status == UNCONFIGURED:
        cs["status"] = UNCONFIGURED
        cs["consecutive_red"] = 0
        cs["heal_step"] = 0
        _unconfigured_nag(cfg, cs, result, dry_run, now)
        return

    if result.status == OK:
        if prev == RED:
            ntfy(cfg, "high", f"RECOVERED {result.id}",
                 f"{result.id} is green again: {result.detail} (wq#1252)", dry_run)
        cs.update(status=OK, consecutive_red=0, heal_step=0,
                  fire_ticket=None, last_alert=0)
        return

    # RED
    cs["status"] = RED
    cs["consecutive_red"] += 1
    n = cs["consecutive_red"]

    realert = cfg.get("realert_cycles", 15)
    if prev != RED or (n - 1) % realert == 0:
        ntfy(cfg, "urgent", f"RED {result.id}",
             f"{result.id}: {result.detail} (cycle {n}, wq#1252)", dry_run)
        cs["last_alert"] = now

    heals = spec.get("heals", [])
    heal_log = ""
    if cs["heal_step"] < len(heals):
        name, fn = heals[cs["heal_step"]]
        cs["heal_step"] += 1
        try:
            ok, out = fn(cfg, dry_run)
            heal_log = f"{name}: {'ok' if ok else 'FAILED'} — {out[:200]}"
        except HealNotAllowed as exc:
            heal_log = f"{name}: REFUSED (not in HEAL_ALLOWLIST): {exc}"
            ok = False
        print(f"guardian: heal {result.id} step {cs['heal_step']}/{len(heals)} {heal_log}")
        cs["last_heal"] = heal_log
        return   # give the heal a cycle to take effect before escalating

    # Ladder exhausted and still red -> fire, once per red episode.
    if cs.get("fire_ticket"):
        return
    ticket = open_fire(cfg, result, cs.get("last_heal", ""), dry_run)
    cs["fire_ticket"] = ticket or "unopened"
    ntfy(cfg, "urgent", f"FIRE {result.id}",
         f"{result.id} still red after {n} cycles and every listed heal. "
         f"fleet-fires ticket {ticket}. Runbook: {cfg['runbook']} (wq#1252)", dry_run)


def _unconfigured_nag(cfg, cs, result, dry_run, now):
    """An unrunnable check is not green. Say so once a day rather than letting
    it sit silently disabled — silent disablement is the 7.5h failure mode."""
    last = cs.get("last_alert") or 0
    if last and now - last < cfg.get("unconfigured_nag_s", 86400):
        return
    cs["last_alert"] = now
    ntfy(cfg, "default", f"UNCONFIGURED {result.id}",
         f"{result.id} cannot run: {result.detail}. It is NOT covering anything "
         f"until this is fixed — see {cfg['runbook']} (wq#1252)", dry_run)


# --------------------------------------------------------------------------
# status publishing


def _publish(cfg, state, results, dry_run, now):
    doc = {
        "generated": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now)),
        "checks": {
            cid: {
                "status": cs.get("status", OK),
                "consecutive_red": cs.get("consecutive_red", 0),
                "fire_ticket": cs.get("fire_ticket"),
                "last_run": cs.get("last_run", 0),
            }
            for cid, cs in state.get("checks", {}).items()
        },
        "last_pass": {r.id: {"status": r.status, "detail": r.detail} for r in results},
    }
    doc["overall"] = (
        RED if any(c["status"] == RED for c in doc["checks"].values())
        else UNCONFIGURED if any(c["status"] == UNCONFIGURED for c in doc["checks"].values())
        else OK
    )
    if dry_run:
        print(json.dumps(doc, indent=2, sort_keys=True))
        return doc

    os.makedirs(os.path.dirname(cfg["status_path"]), exist_ok=True)
    tmp = cfg["status_path"] + ".tmp"
    with open(tmp, "w") as f:
        json.dump(doc, f, indent=2, sort_keys=True)
    os.replace(tmp, cfg["status_path"])
    _push_status(cfg, results)
    return doc


def _push_status(cfg, results):
    """Mirror per-check state to status.lumennode.uk via push URLs.

    The URL map is a separate 0600 file rather than config, because each push
    monitor's URL is a credential. No map file = no push, silently: this path
    must never be able to page anyone, it is a display.
    """
    path = cfg.get("status_push_map")
    if not path or not os.path.exists(path):
        return
    try:
        with open(path) as f:
            urls = json.load(f)
    except (OSError, json.JSONDecodeError) as exc:
        print(f"guardian: status push map unreadable: {exc}", file=sys.stderr)
        return
    for r in results:
        url = urls.get(r.id)
        if not url:
            continue
        sep = "&" if "?" in url else "?"
        full = f"{url}{sep}status={'up' if r.status == OK else 'down'}" \
               f"&msg={urllib.parse.quote(r.detail[:120])}"
        try:
            urllib.request.urlopen(full, timeout=10).read(1)
        except (urllib.error.URLError, OSError) as exc:
            print(f"guardian: status push {r.id} failed: {exc}", file=sys.stderr)


# --------------------------------------------------------------------------
# CLI


def selftest(cfg):
    """Config sanity without side effects: every path the checks depend on,
    reported as present/absent. Exits non-zero only on a broken config, not on
    a missing optional credential (those show up as UNCONFIGURED at runtime)."""
    registry = build_registry(cfg)
    print(f"checks registered: {len(registry)} -> {', '.join(sorted(registry))}")
    problems = 0
    required = ["state_path", "status_path", "wq_bin", "fire_lane", "runbook"]
    for key in required:
        if not cfg.get(key):
            print(f"MISSING config key: {key}")
            problems += 1
    for label, path in [
        ("wq", cfg.get("wq_bin")), ("runbook", cfg.get("runbook")),
        ("ntfy-send", NTFY_BIN),
        ("migrations", cfg.get("email", {}).get("migrations_dir")),
        ("mail_recall", cfg.get("email", {}).get("mail_recall")),
    ]:
        exists = bool(path) and os.path.exists(path)
        print(f"{'ok  ' if exists else 'MISS'} {label}: {path}")
        if label in ("wq", "ntfy-send") and not exists:
            problems += 1
    for check_id, spec in sorted(registry.items()):
        heals = ", ".join(n for n, _ in spec.get("heals", [])) or "-"
        print(f"  {check_id}: every {spec.get('interval_s', 0)}s heals=[{heals}]")
    return problems


def main(argv=None):
    ap = argparse.ArgumentParser(description="lumen-public-guardian (wq#1252)")
    ap.add_argument("--config", default=DEFAULT_CONFIG)
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="one guardian pass")
    r.add_argument("--dry-run", action="store_true")
    r.add_argument("--only", help="comma-separated check ids")
    r.add_argument("--force", action="store_true", help="ignore per-check intervals")
    sub.add_parser("status", help="print the last published status document")
    sub.add_parser("selftest", help="config/dependency sanity, no side effects")
    a = ap.parse_args(argv)
    cfg = load_config(a.config)

    if a.cmd == "status":
        try:
            with open(cfg["status_path"]) as f:
                print(f.read())
        except OSError as exc:
            print(f"no status yet: {exc}")
            return 1
        return 0

    if a.cmd == "selftest":
        return 1 if selftest(cfg) else 0

    only = set(a.only.split(",")) if a.only else None
    results, _ = run_once(cfg, only=only, dry_run=a.dry_run, force=a.force)
    for res in sorted(results, key=lambda x: x.id):
        print(f"{res.status.upper():13} {res.id}: {res.detail}")
    return 1 if any(r.status == RED for r in results) else 0


if __name__ == "__main__":
    sys.exit(main())
