#!/usr/bin/env python3
"""
lumen_self_healer.py — Lumen Self-Healing Service Monitor
Watches all Lumen services. On failure: attempts restart, waits, verifies,
escalates to Frank via ntfy if still down after 3 attempts.
Runs as lumen-self-healer.service.
"""
import os, subprocess, time, logging, requests
from datetime import datetime
from pathlib import Path
import sys
sys.path.insert(0, "/home/lumen/agent")
import holddown_lib  # wq#967 stop/restore contract
# wq#1054 (wq#392/#1043 helper): hang detection for the RESTART BRAIN itself --
# highest blast radius of the 5-service rollout. PROGRESS-TIED (mark() once
# per completed check_all() cycle), never plain liveness, so a wedged healer
# (stuck subprocess call, hung requests.get, etc.) actually trips the
# watchdog. holddown= ORs in a check of the healer's OWN unit against the
# wq#967 shielded-units set, so if Frank (or a session) deliberately pauses
# the healer via a holddown flag, it keeps beating and is never self-killed
# for being paused on purpose. Fail-open no-op if NOTIFY_SOCKET unset.
from lumen_watchdog_notify import Watchdog, beat_forever, ProgressTracker, progress_or_holddown
_wd = Watchdog()
_progress = ProgressTracker()
_wd_started = False

LOG = Path("/home/lumen/scratch/self_healer.log")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.FileHandler(LOG), logging.StreamHandler()]
)
log = logging.getLogger("self_healer")

NTFY_URL = os.getenv("NTFY_URL_ALERTS", "http://localhost:8080/lumen-updates")
POLL_SEC = 60  # check every 60s

# ── wq#971 (2026-08-27, R2 §6 ACTUATOR): the hardcoded SYSTEMD_SERVICES /
# DOCKER_CONTAINERS / PORT_CHECKS lists are replaced by a DECLARATIVE watch
# table (unit -> policy/kind/holddown_flag/owner/restart_cmd) in
# /home/lumen/agent/healer_watch_table.yaml. Edit the TABLE to change
# coverage. History that used to live in the list comments (lumen-agent
# retired 2026-08-21, lumen-scout oneshot removal, litellm docker move) is
# preserved in the table's comments. Fail-safe: unreadable/malformed table ->
# keep last good table (or the baked FALLBACK on first load) + page once.
import yaml

WATCH_TABLE = Path("/home/lumen/agent/healer_watch_table.yaml")
FALLBACK_TABLE = {   # wq#1650: pruned ghost units — lumen-prompt-engine/
    # lumen-vip-api/lumen-vip-watcher retired 2026-08-30 (wq#1112, 8-unit
    # research-mill removal). This baked table is ONLY used if the YAML is
    # unreadable at startup; it must track real live units, not history.
    "units": {**{u: {"kind": "systemd", "policy": "restart"} for u in
                 ["redis-server", "lumen-messenger", "cloudflared",
                  "lumen-memoryd", "mcp-gateway", "desktop-commander-mcp",
                  "ollama"]},
              "ntfy": {"kind": "docker", "policy": "restart"},
              "litellm": {"kind": "docker", "policy": "restart"}},
    "ports": {"lumen-memoryd": 8139, "lumen-messenger": 8765,
              "ollama": 11434, "ntfy": 8080},
    "stall": {}, "ram": {"crit_pct": 90},
}
_table_cache = {"mtime": None, "table": None, "paged_bad": False}

def load_table():
    """Reload on mtime change; malformed table keeps last good + pages once."""
    try:
        mtime = WATCH_TABLE.stat().st_mtime
    except Exception:
        mtime = None
    if _table_cache["table"] is not None and mtime == _table_cache["mtime"]:
        return _table_cache["table"]
    try:
        t = yaml.safe_load(WATCH_TABLE.read_text())
        assert isinstance(t.get("units"), dict) and t["units"], "units missing/empty"
        for u, spec in t["units"].items():
            assert spec.get("kind") in ("systemd", "docker"), f"{u}: bad kind"
            assert spec.get("policy") in ("restart", "alert-only"), f"{u}: bad policy"
        _table_cache.update(mtime=mtime, table=t, paged_bad=False)
        log.info(f"watch table loaded: {len(t['units'])} units, "
                 f"{len(t.get('stall', {}) or {})} stall rule(s)")
    except Exception as e:
        if _table_cache["table"] is None:
            _table_cache.update(mtime=mtime, table=FALLBACK_TABLE)
            log.error(f"watch table unreadable at startup ({e}) — BAKED FALLBACK in use")
        else:
            log.error(f"watch table reload failed ({e}) — keeping last good table")
        if not _table_cache["paged_bad"]:
            _table_cache["paged_bad"] = True
            ntfy("Lumen healer: watch table unreadable",
                 f"{WATCH_TABLE}: {e}\nRunning on last-good/fallback coverage. Fix the YAML.",
                 "high")
    return _table_cache["table"]

_fail_counts: dict = {}

# wq#1021 sub-defect (b): _fail_counts zeroes on every recovery, so a unit that
# fails-recovers-fails-recovers never reaches the count>=3 escalation — a flap is
# invisible to the ladder. Track recovery timestamps separately and page (at most
# once per cooldown) when a unit recovers too many times in a short window. This
# is ADDITIVE: the 3-attempt ladder and its counter are untouched.
_recover_times: dict = {}          # name -> [recovery epoch within window]
_flap_cooldown: dict = {}          # name -> epoch until which flap-paging is suppressed
FLAP_RECOVERIES = 5                # >= this many recoveries ...
FLAP_WINDOW_S = 30 * 60            # ... within this window = flapping
FLAP_COOLDOWN_S = 30 * 60          # suppress repeat flap pages this long

def ntfy(title: str, msg: str, priority: str = "high"):
    try:
        requests.post(NTFY_URL, data=msg.encode(),
                      headers={"Title": title, "Priority": priority},
                      auth=(os.getenv("NTFY_USER", "lumen"), os.getenv("NTFY_PASS") or ""),
                      timeout=5)
    except Exception as e:
        log.warning(f"ntfy failed: {e}")

def is_systemd_active(svc: str) -> bool:
    """Check if a systemd service is healthy.

    For Type=simple/notify/forking services: 'active' is healthy.
    For Type=oneshot services: 'active' (RemainAfterExit=yes) OR 'inactive' (cleanly exited)
    are BOTH healthy. Only 'failed' is bad.
    """
    r = subprocess.run(f"systemctl is-active {svc}", shell=True,
                       capture_output=True, text=True, timeout=5)
    state = r.stdout.strip()
    if state == "active":
        return True
    if state == "failed":
        return False
    # inactive/activating/deactivating — check if oneshot (expected resting state)
    t = subprocess.run(f"systemctl show -p Type --value {svc}", shell=True,
                       capture_output=True, text=True, timeout=5)
    svc_type = t.stdout.strip()
    if svc_type == "oneshot":
        # oneshot is healthy as long as it's not 'failed'
        return True
    # simple/notify/forking services in inactive state = broken
    return False

# ── wq#1112: stale-target guard (same class as sentinel's wq#971 guard) ─────
# A unit in the watch table whose systemd unit file has been retired
# (LoadState=not-found) or deliberately masked can never recover via
# restart -- without this guard the healer burns the 3-attempt restart
# ladder against it every time it's checked, forever, for a target that
# structurally cannot come back (the research-mill retirement left
# lumen-vip-api/lumen-vip-watcher/lumen-prompt-engine in the table this way,
# wq#1112). Skip it entirely and file ONE build-lane ticket (deduped for the
# process lifetime) so a human removes the row from the watch table.
WQ_BIN = os.environ.get("HEALER_WQ_BIN", "/home/lumen/bin/wq")
_stale_alerted = set()   # units whose retired-target ticket has been filed
_stale_seen = {}         # wq#2713: consecutive confirmed not-found/masked observations
STALE_CONFIRM = 3        # wq#2713: need this many in a row before filing the delete-row ticket

def _unit_load_state(unit: str) -> str:
    # wq#2713: a lookup FAILURE is not evidence of retirement. The old body
    # returned 'not-found' on timeout/exception/empty stdout, so a transient
    # systemctl hiccup filed "retired unit, delete the row" tickets against LIVE
    # units (redis-server, active since 08-31) -- and a worker built a PR to do it.
    # Only a successful systemctl answer may say not-found/masked.
    try:
        r = subprocess.run(["systemctl", "show", unit, "-p", "LoadState"],
                           capture_output=True, text=True, timeout=10)
    except Exception:
        return "unknown"
    if r.returncode != 0:
        return "unknown"
    return dict(l.split("=", 1) for l in r.stdout.strip().splitlines()
                if "=" in l).get("LoadState") or "unknown"

def _file_stale_unit_ticket(unit: str, load_state: str):
    if unit in _stale_alerted:
        return
    _stale_seen[unit] = _stale_seen.get(unit, 0) + 1
    if _stale_seen[unit] < STALE_CONFIRM:
        log.warning(f"{unit}: LoadState={load_state} ({_stale_seen[unit]}/{STALE_CONFIRM}) -- skipping, not filing yet")
        return
    _stale_alerted.add(unit)
    title = f"[healer] watch table targets retired unit {unit} ({load_state}) -- remove from healer_watch_table.yaml"
    detail = (f"lumen_self_healer.py watches systemd unit '{unit}' but LoadState={load_state} "
              f"-- the unit no longer exists to restart ({STALE_CONFIRM} consecutive successful systemctl reads). Auto-detected by the wq#1112 "
              f"stale-target guard (same shape as sentinel's wq#1092). Remove its row "
              f"from {WATCH_TABLE}.")
    try:
        subprocess.run([WQ_BIN, "add", "--lane", "build", "--pri", "2",
                        "--title", title, "--detail", detail],
                       capture_output=True, text=True, timeout=20)
    except Exception as e:
        log.warning(f"stale-unit ticket file failed (non-fatal): {e}")
    log.warning(f"{unit}: LoadState={load_state} -- retired target, skipping (ticket filed)")

def is_docker_running(name: str) -> bool:
    r = subprocess.run(f"sudo docker inspect --format='{{{{.State.Status}}}}' {name}",
                       shell=True, capture_output=True, text=True, timeout=10)
    return r.stdout.strip() == "running"

def is_port_open(port: int) -> bool:
    import socket
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=2):
            return True
    except Exception:
        return False

def restart_systemd(svc: str) -> bool:
    log.info(f"Restarting systemd service: {svc}")
    r = subprocess.run(f"sudo systemctl restart {svc}", shell=True,
                       capture_output=True, text=True, timeout=30)
    time.sleep(5)
    return is_systemd_active(svc)

def restart_docker(name: str) -> bool:
    log.info(f"Restarting docker container: {name}")
    r = subprocess.run(f"sudo docker restart {name}", shell=True,
                       capture_output=True, text=True, timeout=30)
    time.sleep(5)
    return is_docker_running(name)

def restart_via_cmd(name: str, cmd: str) -> bool:
    """wq#971: sanctioned per-unit restart tool (e.g. lpp-restart takes the
    slock itself, refuses under RESTART_FREEZE, verifies a fresh event)."""
    log.info(f"Restarting {name} via sanctioned cmd: {cmd}")
    try:
        r = subprocess.run([cmd], capture_output=True, text=True, timeout=180)
        if r.returncode != 0:
            log.warning(f"{cmd} exit={r.returncode}: {(r.stderr or r.stdout or '')[-200:]}")
    except Exception as e:
        log.warning(f"{cmd} failed to run: {e}")
    time.sleep(5)
    return is_systemd_active(name)

def _note_recovery(name: str):
    """wq#1021 (b): flap watch. Record a down->up recovery; if the unit has
    recovered FLAP_RECOVERIES+ times within FLAP_WINDOW_S it is flapping (each
    recovery zeroes the ladder counter, so count>=3 never sees it). Page urgent,
    at most once per FLAP_COOLDOWN_S. Called from both recovery paths (healer
    restart + observed self-recovery)."""
    now = time.time()
    times = [t for t in _recover_times.get(name, []) if now - t <= FLAP_WINDOW_S]
    times.append(now)
    _recover_times[name] = times
    if len(times) >= FLAP_RECOVERIES and now >= _flap_cooldown.get(name, 0):
        _flap_cooldown[name] = now + FLAP_COOLDOWN_S
        span = (now - times[0]) / 60
        ntfy(f"URGENT: {name} flapping",
             f"{name} has recovered {len(times)} times in {span:.0f}m. It is "
             f"oscillating up/down — each recovery resets the restart-ladder "
             f"counter, so it never escalates on its own. Investigate.",
             "urgent")
        log.error(f"{name} FLAPPING: {len(times)} recoveries in {span:.0f}m — paged urgent")

def handle_failure(name: str, spec: dict):
    """wq#971: table-driven. policy=alert-only pages (edge + every-10th) and
    never touches the unit; policy=restart keeps the historic 3-attempt ladder,
    routed through spec['restart_cmd'] when the unit has a sanctioned tool."""
    _fail_counts[name] = _fail_counts.get(name, 0) + 1
    count = _fail_counts[name]
    log.warning(f"{name} is DOWN (attempt {count}, policy={spec.get('policy')})")

    if spec.get("policy") == "alert-only":
        if count == 1 or count % 10 == 0:
            ntfy(f"Lumen: {name} DOWN (alert-only)",
                 f"{name} is down for {count} check(s). Policy is alert-only — "
                 f"healer will NOT restart it (owner: {spec.get('owner','?')}).",
                 "high" if count == 1 else "urgent")
        return

    if count > 3:
        # Already escalated — don't spam
        if count % 10 == 0:
            ntfy(f"Lumen: {name} still down", f"{name} has been down for {count} checks. Manual intervention needed.", "urgent")
        return

    # Attempt restart
    if spec.get("restart_cmd"):
        recovered = restart_via_cmd(name, spec["restart_cmd"])
    elif spec.get("kind") == "docker":
        recovered = restart_docker(name)
    else:
        recovered = restart_systemd(name)

    if recovered:
        log.info(f"{name} recovered after restart")
        ntfy(f"Lumen: {name} recovered", f"{name} was down, auto-restarted successfully.", "low")
        _fail_counts[name] = 0
        _note_recovery(name)
    else:
        log.error(f"{name} still down after restart attempt {count}")
        if count >= 3:
            ntfy(f"URGENT: {name} won't restart", f"{name} failed to restart after {count} attempts. Check logs.", "urgent")

# --- wq#971: research-stall rule + RAM watch, absorbed from the retired ------
# lumen-smart-watchdog daemon. History: liveness-restart ownership moved to
# this healer 2026-07-01 (A6) to kill the two-restarter race; the stall rule
# was smart-watchdog's one unique trigger (active-but-hung agent). Since the
# 2026-08-21 lumen-agent retirement the daemon bailed early EVERY cycle (1,909
# consecutive checks by 08-27), so stall+RAM were dead code there. Here the
# stall rule is a no-op while its unit is inactive (respects the retirement,
# self-arms if Frank revives the unit) and RAM watch actually runs.
def check_stall(stall_rules: dict):
    import json as _json, glob as _glob
    for unit, rule in (stall_rules or {}).items():
        try:
            if not is_systemd_active(unit):
                continue    # retired/stopped unit: stall rule is a deliberate no-op
            sf = Path(rule["state_file"])
            try:
                st = _json.loads(sf.read_text())
            except Exception:
                st = {}
            now = time.time()
            try:
                d = _json.loads(Path(rule["status_file"]).read_text())
                count = d.get("research", {}).get("count", 0)
            except Exception as e:
                log.warning(f"stall[{unit}]: status file unreadable: {e}")
                continue
            if count != st.get("last_research_count", 0):
                st["last_research_count"] = count
                st["last_research_change_ts"] = now
            else:
                pending = len(_glob.glob(rule["pending_glob"]))
                stall_s = now - st.get("last_research_change_ts", now)
                if pending > 0 and stall_s > rule.get("threshold_s", 3600):
                    log.warning(f"stall[{unit}]: {int(stall_s//60)}min frozen with {pending} pending — restarting")
                    if rule.get("restart_cmd"):
                        ok = restart_via_cmd(unit, rule["restart_cmd"])
                    else:
                        ok = restart_systemd(unit)
                    ntfy(f"Lumen: {unit} stall-restarted",
                         f"research stalled {int(stall_s//60)}min with {pending} pending prompts; "
                         f"restart {'succeeded' if ok else 'FAILED'}.",
                         "default" if ok else "urgent")
                    st["last_research_change_ts"] = now
                    st["restarts"] = st.get("restarts", 0) + 1
                elif pending == 0 and stall_s > rule.get("threshold_s", 3600):
                    st["last_research_change_ts"] = now   # quiet queue = healthy idle
            st["last_check_ts"] = now
            sf.write_text(_json.dumps(st, indent=2))
        except Exception as e:
            log.warning(f"stall[{unit}] cycle error: {e}")

_ram_alerted = [False]

def check_ram(ram_cfg: dict):
    try:
        crit = (ram_cfg or {}).get("crit_pct", 90)
        m = {}
        for line in open("/proc/meminfo"):
            k, v = line.split(":", 1)
            m[k] = int(v.strip().split()[0])
        pct = 100 * (m["MemTotal"] - m["MemAvailable"]) / m["MemTotal"]
        if pct >= crit:
            if not _ram_alerted[0]:
                _ram_alerted[0] = True
                ntfy("Lumen RAM critical", f"RAM at {pct:.0f}% (threshold {crit}%)", "high")
                log.warning(f"RAM at {pct:.0f}% — paged")
        else:
            if _ram_alerted[0]:
                log.info(f"RAM back under threshold ({pct:.0f}%)")
            _ram_alerted[0] = False
    except Exception as e:
        log.warning(f"ram check error: {e}")

# --- foreman worker crash visibility (added 2026-08-21 per Frank) ------------
# The 2026-08-20 failure mode: fworker tickets crashed to `blocked`, or their
# supervisor died leaving a `claimed` ticket with no running unit, and NOBODY
# was told — work sat idle for hours until Frank noticed. This check makes both
# states LOUD within one poll cycle. It deliberately does NOT auto-requeue:
# blocked-reason classification (INFRA_CRASH vs WORK_BLOCKED) is wq#725's job;
# a healer blindly re-running a worker that fails deterministically would just
# burn attempts. Alert-once dedup per (ticket, state fingerprint).
WQ_DB = "/home/lumen/agents/work-queue/queue.db"
WORKER_CHECK_EVERY = 3          # run every Nth poll cycle (3min at POLL_SEC=60)
CLAIM_GRACE_SEC = 180           # a just-claimed ticket may not have its unit yet
_worker_alerted = {}            # (ticket_id, fingerprint) -> True
_worker_cycle = [0]
_worker_baselined = [False]     # first pass swallows the 40+ PARKED blocked tickets
                                # (Frank-gated / hardware-blocked backlog) silently;
                                # only blocks that appear AFTER healer start alert.

def _running_fworker_tickets():
    r = subprocess.run(["systemctl", "list-units", "lumen-job-fworker-*",
                        "--state=running,activating", "--no-legend", "--plain"],
                       capture_output=True, text=True, timeout=15)
    import re as _re
    ids = set()
    for line in (r.stdout or "").splitlines():
        m = _re.search(r"fworker-wq(\d+)", line)
        if m:
            ids.add(int(m.group(1)))
    # 2026-08-21 fix: chain-runner.sh executes fworker.py INSIDE a chain unit, so
    # unit names alone miss live workers (false ORPHANED page at 14:02Z). Also scan
    # process cmdlines for the authoritative signal.
    p2 = subprocess.run(["pgrep", "-af", "fworker.py run"], capture_output=True, text=True, timeout=10)
    for line in (p2.stdout or "").splitlines():
        m = _re.search(r"--ticket\s+(\d+)", line)
        if m:
            ids.add(int(m.group(1)))
    return ids

def check_foreman_workers():
    _worker_cycle[0] += 1
    if _worker_cycle[0] % WORKER_CHECK_EVERY != 0:
        return
    import sqlite3, time as _t, datetime as _dt
    try:
        con = sqlite3.connect(f"file:{WQ_DB}?mode=ro", uri=True, timeout=5)
        rows = con.execute(
            "SELECT id, lane, title, status, claimed_at, notes FROM tasks "
            "WHERE status IN ('blocked','claimed') AND lane != 'user'").fetchall()
        con.close()
    except Exception as e:
        log.warning(f"worker-check: queue.db unreadable: {e}")
        return
    try:
        running = _running_fworker_tickets()
    except Exception as e:
        log.warning(f"worker-check: unit list failed: {e}")
        return
    now = _dt.datetime.now(_dt.timezone.utc).replace(tzinfo=None)
    for tid, lane, title, status, claimed_at, notes in rows:
        if status == "blocked":
            key = (tid, "blocked", (notes or "")[:120])
            if not _worker_baselined[0]:
                _worker_alerted[key] = True   # pre-existing block — known, silent
            elif key not in _worker_alerted:
                _worker_alerted[key] = True
                ntfy(f"wq#{tid} BLOCKED [{lane}]",
                     f"{title}\nreason: {(notes or 'no reason recorded')[:300]}",
                     "high")
                log.warning(f"worker-check: wq#{tid} blocked — alerted")
        elif status == "claimed" and tid not in running:
            try:
                age = (now - _dt.datetime.strptime(claimed_at, "%Y-%m-%d %H:%M:%S")).total_seconds()
            except Exception:
                age = CLAIM_GRACE_SEC + 1
            if age < CLAIM_GRACE_SEC:
                continue
            key = (tid, "orphan", claimed_at)
            if key not in _worker_alerted:
                _worker_alerted[key] = True
                ntfy(f"wq#{tid} ORPHANED CLAIM [{lane}]",
                     f"{title}\nclaimed {claimed_at}Z but no fworker unit is running — "
                     f"supervisor died. Lease will auto-expire; investigate before respawn.",
                     "urgent")
                log.warning(f"worker-check: wq#{tid} claimed with no running unit — alerted")
    if not _worker_baselined[0]:
        _worker_baselined[0] = True
        n = sum(1 for k in _worker_alerted if k[1] == "blocked")
        log.info(f"worker-check: baselined {n} pre-existing blocked ticket(s) silently")


# --- stop/restore contract (wq#967, generalizes LPP RESTART_FREEZE) ----------
# Agents that stop a product unit drop a hold-down flag (see `holddown` CLI /
# agent/holddown_lib.py). Contract here: NEVER restart a unit with an ACTIVE
# flag (deliberate stop is not an outage); RESTORE the unit once the flag
# expires — restore_by passed, or the owning wq lease is gone (the wq#863
# class: worker stopped LPP, lease expired, nothing restored it for 3 days).
# Malformed flags shield their unit and page once; they never license restarts.
_holddown_alerted = set()   # units whose malformed-flag/restore-fail was paged

def check_holddowns(held: set):
    for unit, flag, err in holddown_lib.list_flags():
        if err is not None:
            if unit not in _holddown_alerted:
                _holddown_alerted.add(unit)
                ntfy(f"Lumen: malformed hold-down for {unit}",
                     f"{err}\nUnit is SHIELDED from healer restarts until the "
                     f"flag is fixed or removed (holddown drop {unit}).", "high")
                log.warning(f"holddown: malformed flag for {unit} — shielded, paged")
            continue
        if unit in held:
            continue  # active — deliberately down, leave it alone
        # expired -> restore
        key = f"holddown:{unit}"
        log.info(f"holddown: {unit} hold expired (wq#{flag.get('owner_ticket')}, "
                 f"restore_by {flag.get('restore_by')}) — restoring")
        try:
            ok, detail = holddown_lib.restore_unit(unit, flag)
        except Exception as e:
            ok, detail = False, f"restore raised: {e}"
        if ok:
            holddown_lib.archive_flag(unit, "restored")
            _fail_counts[key] = 0
            ntfy(f"Lumen: {unit} restored after hold-down",
                 f"Hold by {flag.get('owner_agent')} (wq#{flag.get('owner_ticket')}) "
                 f"lapsed; unit restored and verified active.", "low")
            log.info(f"holddown: {unit} restored, flag archived")
        else:
            _fail_counts[key] = _fail_counts.get(key, 0) + 1
            n = _fail_counts[key]
            log.error(f"holddown: restore of {unit} FAILED (attempt {n}): {detail}")
            if n == 3 or (n > 3 and n % 10 == 0):
                ntfy(f"URGENT: {unit} hold-down expired but restore fails",
                     f"{detail}\nFlag kept; retrying each cycle. Attempt {n}.",
                     "urgent")

def check_all():
    # Pause window removed 2026-06-15 (Frank: 22-core box, no perceptible
    # contention — daily 13-16 UTC pause deleted).
    table = load_table()   # wq#971: declarative coverage, reloads on edit
    try:
        held = holddown_lib.shielded_units()      # active + malformed: never touch
        flagged = holddown_lib.flagged_units()    # ANY flag: generic loops defer
    except Exception as e:
        held, flagged = set(), set()
        log.warning(f"holddown: shield scan failed ({e}) — proceeding unshielded")

    for name, spec in table["units"].items():
        if name in flagged:
            log.info(f"holddown: {name} is flagged — deferring to check_holddowns")
            continue
        try:
            if spec.get("kind") == "systemd":
                load = _unit_load_state(name)
                if load in ("not-found", "masked"):
                    _file_stale_unit_ticket(name, load)
                    continue
                _stale_seen.pop(name, None)   # wq#2713: streak broken (loaded or unknown)
            healthy = is_docker_running(name) if spec.get("kind") == "docker" \
                      else is_systemd_active(name)
            if not healthy:
                handle_failure(name, spec)
            else:
                if _fail_counts.get(name, 0) > 0:
                    log.info(f"{name} back online")
                    _note_recovery(name)   # wq#1021 (b): self-recovery counts toward flap watch
                _fail_counts[name] = 0
        except Exception as e:
            log.warning(f"Check error for {name}: {e}")

    try:
        check_stall(table.get("stall"))
    except Exception as e:
        log.warning(f"stall cycle error: {e}")

    try:
        check_ram(table.get("ram"))
    except Exception as e:
        log.warning(f"ram cycle error: {e}")

    try:
        check_foreman_workers()
    except Exception as e:
        log.warning(f"worker-check cycle error: {e}")

    try:
        check_holddowns(held)
    except Exception as e:
        log.warning(f"holddown cycle error: {e}")

    # Port sanity checks (log-only, as before)
    for svc, port in (table.get("ports") or {}).items():
        try:
            if not is_port_open(port):
                log.warning(f"Port {port} ({svc}) not responding")
        except Exception:
            pass

def main():
    global _wd_started
    log.info("Lumen Self-Healer started")
    ntfy("Lumen Self-Healer", "Service monitor started", "low")
    if not _wd_started:
        # stall_after=200s ~ 3x the 60s POLL_SEC cycle (POLL_SEC + measured
        # sub-second-to-low-single-digit-second check_all() duration), per
        # the wq#1043 design ("stall_after ~= 3x cycle"). WatchdogSec below
        # gives further margin on top of that.
        _holddown = lambda: "lumen-self-healer" in holddown_lib.shielded_units()
        beat_forever(_wd, 10, progress_or_holddown(_progress, stall_after=200,
                                                     holddown=_holddown))
        _wd_started = True
    while True:
        try:
            check_all()
            _progress.mark()
        except Exception as e:
            log.error(f"Check cycle error: {e}")
        time.sleep(POLL_SEC)

if __name__ == "__main__":
    main()
