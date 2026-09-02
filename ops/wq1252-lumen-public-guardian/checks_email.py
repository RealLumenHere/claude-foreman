#!/usr/bin/env python3
"""checks_email.py — E1..E6, the email surface (lumen@lumennode.uk). wq#1252.

E1  deployed worker version matches the version the guardian has validated,
    and remote D1's schema matches the union of mailbot/migrations/*.sql
E2  synthetic end-to-end round trip through routing -> worker -> DeepSeek ->
    reply -> D1, proven by a matched inbound/outbound pair carrying an HMAC token
E3  DeepSeek key is valid and the account has balance
E4  lumen-mail-sync is running and local mail.db tracks remote D1
E5  the messenger's email_recall path still executes
E6  burst of suppressed inbound (auth failures / canary trips)

Read-only D1 access reuses the existing 'D1 Read' token minted 2026-09-01
(mailbot/mail_recall.py:38 documents the same three files). Anything that
WRITES to Cloudflare — applying a migration, rolling a deploy back — needs a
separate deploy-scoped token; where that token is absent the check reports
UNCONFIGURED rather than pretending to cover the surface.
"""
import hashlib
import hmac
import json
import os
import subprocess
import time
import urllib.error
import urllib.request

import schema_parse
from guardian import OK, RED, UNCONFIGURED, Result, run_heal

CF_API = "https://api.cloudflare.com/client/v4"


# --------------------------------------------------------------------------
# small helpers


def _read(path):
    with open(path) as f:
        return f.read().strip()


def _cfg(cfg):
    return cfg["email"]


def d1_query(cfg, sql, params=None):
    """Read-only D1 query over the REST API. Same shape as
    mailbot/mail_recall.py:38, but raising instead of SystemExit so a guardian
    pass degrades to one RED check rather than killing the whole run."""
    e = _cfg(cfg)
    account = _read(e["cf_account_id_file"])
    dbid = _read(e["d1_id_file"])
    url = f"{CF_API}/accounts/{account}/d1/database/{dbid}/query"
    req = urllib.request.Request(
        url, method="POST",
        data=json.dumps({"sql": sql, "params": params or []}).encode(),
        headers={"Authorization": f"Bearer {_read(e['d1_token_file'])}",
                 "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        out = json.load(r)
    if not out.get("success"):
        raise RuntimeError(f"D1 error: {out.get('errors')}")
    return out["result"][0]["results"]


def _missing_read_creds(cfg):
    e = _cfg(cfg)
    return [p for p in (e["cf_account_id_file"], e["d1_id_file"], e["d1_token_file"])
            if not os.path.exists(p)]


def _deploy_token(cfg):
    path = _cfg(cfg).get("cf_deploy_token_file")
    return path if path and os.path.exists(path) else None


def probe_token(secret, when=None, window_s=1800):
    """Deterministic per-window token. The probe worker computes the same value
    from the same secret, so a token in D1 proves the mail came from our worker
    and not from anyone who can spell probe@lumennode.uk."""
    bucket = int((when if when is not None else time.time()) // window_s)
    return hmac.new(secret.encode(), str(bucket).encode(),
                    hashlib.sha256).hexdigest()[:16]


def valid_probe_tokens(secret, now=None, window_s=1800, back=2):
    """Current window plus `back` previous ones — mail in flight across a
    window boundary must still validate."""
    now = now if now is not None else time.time()
    return {probe_token(secret, now - i * window_s, window_s) for i in range(back + 1)}


# --------------------------------------------------------------------------
# E1 — deploy version + D1 schema


def _live_version(cfg):
    """Newest deployment version id for the mailbot worker, or None if we have
    no token that can read it."""
    e = _cfg(cfg)
    token_file = e.get("cf_deploy_token_file")
    if not token_file or not os.path.exists(token_file):
        return None
    url = (f"{CF_API}/accounts/{_read(e['cf_account_id_file'])}"
           f"/workers/scripts/{e['worker_name']}/deployments")
    req = urllib.request.Request(
        url, headers={"Authorization": f"Bearer {_read(token_file)}"})
    with urllib.request.urlopen(req, timeout=30) as r:
        out = json.load(r)
    deployments = (out.get("result") or {}).get("deployments") or []
    if not deployments:
        return None
    versions = deployments[0].get("versions") or []
    return versions[0].get("version_id") if versions else deployments[0].get("id")


def check_e1_version(cfg):
    """A version the guardian has never seen a probe round-trip succeed under
    is an unvalidated deploy. That is what happened on 2026-09-02: the code
    deployed fine and stored nothing for 7.5h."""
    e = _cfg(cfg)
    if _missing_read_creds(cfg):
        return Result("E1-version", UNCONFIGURED,
                      f"missing D1 credential files: {_missing_read_creds(cfg)}")
    if not _deploy_token(cfg):
        return Result("E1-version", UNCONFIGURED,
                      f"no Workers-read token at {e.get('cf_deploy_token_file')} — "
                      f"deployed version cannot be read")
    live = _live_version(cfg)
    if not live:
        return Result("E1-version", RED, "Cloudflare returned no deployments for "
                      f"{e['worker_name']}")

    vstate = _version_state(cfg)
    validated = vstate.get("validated_version")
    if validated is None:
        _write_version_state(cfg, {**vstate, "validated_version": live,
                                   "seen_at": time.time()})
        return Result("E1-version", OK, f"baseline recorded: {live[:12]}")
    if live == validated:
        return Result("E1-version", OK, f"live == validated ({live[:12]})")

    pending_since = vstate.get("pending_since")
    if vstate.get("pending_version") != live:
        pending_since = time.time()
        _write_version_state(cfg, {**vstate, "pending_version": live,
                                   "pending_since": pending_since})
    age = time.time() - (pending_since or time.time())
    grace = e.get("deploy_grace_s", 1800)
    detail = (f"deployed {live[:12]} has not passed a probe round-trip; last "
              f"validated {str(validated)[:12]} ({age / 60:.0f} min ago)")
    return Result("E1-version", RED if age > grace else OK, detail,
                  {"live": live, "validated": validated, "age_s": int(age)})


def _version_state_path(cfg):
    return _cfg(cfg).get(
        "version_state_path",
        os.path.join(os.path.dirname(cfg["state_path"]),
                     "lumen-public-guardian-mailbot-version.json"))


def _version_state(cfg):
    try:
        with open(_version_state_path(cfg)) as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}


def _write_version_state(cfg, doc):
    """Guardian-owned file is the source of truth for rollback. It is mirrored
    into lumen_state (subject mailbot, predicate version) best-effort so the
    fleet's fact store agrees; a mirror failure never fails the check."""
    path = _version_state_path(cfg)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(doc, f, indent=2, sort_keys=True)
    os.replace(tmp, path)
    cli = _cfg(cfg).get("lumen_state_cli")
    value = doc.get("validated_version")
    if cli and value and os.path.exists(cli):
        try:
            subprocess.run(["python3", cli, "ingest", "mailbot", "version",
                            str(value), "--source", "lumen-public-guardian"],
                           capture_output=True, text=True, timeout=30)
        except (OSError, subprocess.TimeoutExpired):
            pass


def check_e1_schema(cfg):
    """The 2026-09-02 root cause, checked directly."""
    e = _cfg(cfg)
    missing_creds = _missing_read_creds(cfg)
    if missing_creds:
        return Result("E1-schema", UNCONFIGURED,
                      f"missing D1 credential files: {missing_creds}")
    expected = schema_parse.expected_schema(e["migrations_dir"])
    actual = {}
    for table in expected:
        rows = d1_query(cfg, f"PRAGMA table_info({table})")
        if rows:
            actual[table] = {r["name"] for r in rows}
    missing_tables, missing_columns = schema_parse.diff_schema(expected, actual)
    if not missing_tables and not missing_columns:
        return Result("E1-schema", OK,
                      f"remote D1 matches {len(expected)} migrated table(s)")
    pending = schema_parse.pending_migrations(
        e["migrations_dir"], missing_tables, missing_columns)
    return Result(
        "E1-schema", RED,
        f"remote D1 is missing tables={missing_tables} columns={missing_columns} "
        f"— writes touching them fail silently (worker.js storeMessage swallows "
        f"D1 errors); pending migrations: {pending}",
        {"missing_tables": missing_tables, "missing_columns": missing_columns,
         "pending_migrations": pending},
    )


def heal_apply_migrations(cfg, dry_run=False):
    """Back up first (house rule: never change a schema without a backup), then
    apply migrations remotely. Refuses without a deploy-scoped token."""
    e = _cfg(cfg)
    token_file = _deploy_token(cfg)
    if not token_file:
        return False, ("no deploy token — cannot apply migrations; "
                       "manual step is in the runbook")
    env_extra = {"CLOUDFLARE_API_TOKEN": _read(token_file)}
    backup_dir = e.get("backup_dir", "/home/lumen/mailbot/backups")
    os.makedirs(backup_dir, exist_ok=True)
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    backup = os.path.join(backup_dir, f"lumen-mail-{stamp}.sql")

    ok, out = _wrangler(cfg, ["npx", "wrangler", "d1", "export",
                              e["d1_database_name"], "--remote",
                              "--output", backup],
                        env_extra, dry_run)
    if not ok:
        return False, f"backup failed, migrations NOT applied: {out}"
    ok2, out2 = _wrangler(cfg, ["npx", "wrangler", "d1", "migrations", "apply",
                                e["d1_database_name"], "--remote"],
                          env_extra, dry_run)
    return ok2, f"backup={backup}; apply={out2}"


def _wrangler(cfg, argv, env_extra, dry_run):
    """run_heal with the mailbot dir as cwd and the deploy token in the env —
    the token never appears in argv, so it cannot leak into a process listing."""
    e = _cfg(cfg)
    if dry_run:
        return run_heal(argv, cwd=e["mailbot_dir"], dry_run=True)
    old = {k: os.environ.get(k) for k in env_extra}
    os.environ.update(env_extra)
    try:
        return run_heal(argv, timeout=300, cwd=e["mailbot_dir"])
    finally:
        for k, v in old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


# --------------------------------------------------------------------------
# E2 — synthetic round trip


def match_roundtrip(rows, tokens, window_s):
    """(inbound, outbound) for the newest probe inbound carrying a valid token
    that got a reply within window_s. Pure so the matching rule is testable
    without D1; rows are dicts with direction/ts/body/to_addr/from_addr."""
    inbound = [r for r in rows if r.get("direction") == "in"
               and any(t in (r.get("body") or "") for t in tokens)]
    if not inbound:
        return None, None
    inbound.sort(key=lambda r: r["ts"])
    latest = inbound[-1]
    for r in sorted(rows, key=lambda r: r["ts"]):
        if r.get("direction") != "out":
            continue
        if r["ts"] < latest["ts"]:
            continue
        if _iso_delta(latest["ts"], r["ts"]) <= window_s:
            return latest, r
    return latest, None


def _iso_delta(a, b):
    """Seconds between two ISO-8601 stamps; inf if either is unparseable."""
    def parse(s):
        s = (s or "").replace("Z", "+00:00")
        try:
            return time.mktime(time.strptime(s[:19], "%Y-%m-%dT%H:%M:%S"))
        except ValueError:
            return None
    pa, pb = parse(a), parse(b)
    if pa is None or pb is None:
        return float("inf")
    return abs(pb - pa)


def check_e2_roundtrip(cfg):
    e = _cfg(cfg)
    if _missing_read_creds(cfg):
        return Result("E2-roundtrip", UNCONFIGURED,
                      f"missing D1 credential files: {_missing_read_creds(cfg)}")
    secret_file = e["probe_secret_file"]
    if not os.path.exists(secret_file):
        return Result("E2-roundtrip", UNCONFIGURED,
                      f"no probe HMAC secret at {secret_file}; lumen-mail-probe "
                      f"is not deployed yet (see runbook)")
    window = e.get("probe_window_s", 1800)
    tokens = valid_probe_tokens(_read(secret_file), window_s=window)
    since = time.strftime("%Y-%m-%dT%H:%M:%SZ",
                          time.gmtime(time.time() - 3 * window))
    rows = d1_query(
        cfg,
        "SELECT direction, ts, from_addr, to_addr, body FROM messages "
        "WHERE ts >= ? AND (lower(from_addr) = ? OR lower(to_addr) = ?) "
        "ORDER BY ts",
        [since, e["probe_sender"], e["probe_sender"]],
    )
    inbound, outbound = match_roundtrip(rows, tokens, e.get("roundtrip_s", 300))
    if inbound and outbound:
        _promote_validated(cfg)
        _arm_probe(cfg)
        return Result("E2-roundtrip", OK,
                      f"probe {inbound['ts']} replied at {outbound['ts']}")

    # Until one round trip has ever completed, the probe worker simply is not
    # deployed yet: that is UNCONFIGURED (daily nag, no heals, no fire), not an
    # outage. Once armed it never disarms, so a probe that stops working after
    # it has worked once is a real RED.
    if not _version_state(cfg).get("probe_armed"):
        return Result("E2-roundtrip", UNCONFIGURED,
                      "no probe round trip has ever completed — lumen-mail-probe "
                      "is not deployed yet (runbook: UNCONFIGURED table)")

    if inbound and not outbound:
        return Result("E2-roundtrip", RED,
                      f"probe mail arrived at {inbound['ts']} but no reply stored "
                      f"within {e.get('roundtrip_s', 300)}s — routing works, the "
                      f"worker/DeepSeek/D1 path does not",
                      {"inbound_ts": inbound["ts"]})
    return Result("E2-roundtrip", RED,
                  f"no probe mail from {e['probe_sender']} with a valid token in "
                  f"the last {3 * window // 60} min — the probe worker, email "
                  f"routing, or D1 storage is down",
                  {"rows_seen": len(rows)})


def _arm_probe(cfg):
    """Record that the round trip has worked at least once."""
    vstate = _version_state(cfg)
    if not vstate.get("probe_armed"):
        _write_version_state(cfg, {**vstate, "probe_armed": True})


def _promote_validated(cfg):
    """A green round trip is what makes the live version trustworthy — and what
    gives E2's rollback a last-good target to roll back TO."""
    vstate = _version_state(cfg)
    pending = vstate.get("pending_version")
    if not pending or pending == vstate.get("validated_version"):
        return
    _write_version_state(cfg, {
        **vstate,
        "validated_version": pending,
        "previous_validated": vstate.get("validated_version"),
        "pending_version": None,
        "pending_since": None,
        "seen_at": time.time(),
    })


def heal_rollback(cfg, dry_run=False):
    """Two consecutive missed round trips -> roll the worker back to the last
    version a probe actually round-tripped under."""
    e = _cfg(cfg)
    vstate = _version_state(cfg)
    target = vstate.get("previous_validated") or vstate.get("validated_version")
    if not target:
        return False, "no last-good version recorded — refusing a blind rollback"
    token_file = _deploy_token(cfg)
    if not token_file:
        return False, "no deploy token — cannot roll back; manual command in runbook"
    return _wrangler(cfg, ["npx", "wrangler", "rollback", target,
                           "--message", "lumen-public-guardian E2 (wq#1252)"],
                     {"CLOUDFLARE_API_TOKEN": _read(token_file)}, dry_run)


# --------------------------------------------------------------------------
# E3 — DeepSeek key + balance


def read_env_key(path, name):
    """One KEY=value out of a dotenv file. Returns None rather than raising so
    a missing file becomes UNCONFIGURED, and never logs the value."""
    try:
        with open(path) as f:
            for line in f:
                line = line.strip()
                if line.startswith(name + "="):
                    return line.split("=", 1)[1].strip().strip('"').strip("'")
    except OSError:
        return None
    return None


def check_e3_deepseek(cfg):
    e = _cfg(cfg)
    key = read_env_key(e["deepseek_env_file"], "DEEPSEEK_API_KEY")
    if not key:
        return Result("E3-deepseek", UNCONFIGURED,
                      f"DEEPSEEK_API_KEY not found in {e['deepseek_env_file']}")
    req = urllib.request.Request(
        e.get("deepseek_balance_url", "https://api.deepseek.com/user/balance"),
        headers={"Authorization": f"Bearer {key}"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            out = json.load(r)
    except urllib.error.HTTPError as exc:
        return Result("E3-deepseek", RED,
                      f"balance check HTTP {exc.code} — key rejected or revoked; "
                      f"the mailbot cannot generate replies")
    except (urllib.error.URLError, OSError) as exc:
        return Result("E3-deepseek", RED, f"DeepSeek unreachable: {exc}")
    infos = out.get("balance_infos") or []
    total = sum(float(i.get("total_balance", 0) or 0) for i in infos)
    if not out.get("is_available", True) or total <= e.get("min_balance", 0.5):
        return Result("E3-deepseek", RED,
                      f"DeepSeek balance {total:.2f} (available="
                      f"{out.get('is_available')}) — replies will start failing",
                      {"total_balance": total})
    return Result("E3-deepseek", OK, f"key valid, balance {total:.2f}")


# --------------------------------------------------------------------------
# E4 — local mirror tracks remote


def check_e4_sync(cfg):
    e = _cfg(cfg)
    if _missing_read_creds(cfg):
        return Result("E4-sync", UNCONFIGURED,
                      f"missing D1 credential files: {_missing_read_creds(cfg)}")
    import sqlite3
    db = e["mail_db"]
    if not os.path.exists(db):
        return Result("E4-sync", RED, f"local mirror {db} does not exist")
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        local_max = conn.execute("SELECT max(ts) FROM messages").fetchone()[0]
    finally:
        conn.close()
    remote = d1_query(cfg, "SELECT max(ts) AS m FROM messages")
    remote_max = (remote[0] or {}).get("m") if remote else None
    lag_limit = e.get("sync_lag_s", 600)
    if remote_max and (not local_max or local_max < remote_max):
        lag = _iso_delta(local_max, remote_max)
        if lag > lag_limit:
            return Result("E4-sync", RED,
                          f"mail.db is {lag:.0f}s behind D1 (local {local_max}, "
                          f"remote {remote_max}) — lumen-mail-sync is not keeping up",
                          {"lag_s": lag})

    # Liveness comes from the timer, NOT from mail.db's mtime: a sync that
    # finds no new rows writes nothing, so on a quiet inbox the file is hours
    # old while everything is fine (observed 2026-09-02, mtime 7.7h old with
    # the timer having fired 4 minutes earlier).
    age = timer_age_s(e.get("sync_timer", "lumen-mail-sync.timer"))
    if age is None:
        return Result("E4-sync", RED,
                      f"{e.get('sync_timer')} has never triggered or does not exist "
                      f"— nothing is mirroring D1 to mail.db")
    if age > lag_limit * 2:
        return Result("E4-sync", RED,
                      f"{e.get('sync_timer')} last fired {age:.0f}s ago "
                      f"(limit {lag_limit * 2}s) — the mirror is not being refreshed",
                      {"timer_age_s": int(age)})
    return Result("E4-sync", OK,
                  f"timer fired {age:.0f}s ago, mirror current "
                  f"(local {local_max}, remote {remote_max})")


def timer_age_s(unit, now=None):
    """Seconds since a systemd timer last triggered; None if never/unknown."""
    try:
        p = subprocess.run(["systemctl", "show", unit, "-p", "LastTriggerUSec",
                            "--value", "--timestamp=unix", "--no-pager"],
                           capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.TimeoutExpired):
        return None
    raw = p.stdout.strip()
    if not raw.startswith("@"):
        return None
    try:
        ts = int(raw[1:])
    except ValueError:
        return None
    return None if ts <= 0 else (now if now is not None else time.time()) - ts


def heal_sync(cfg, dry_run=False):
    return run_heal(["/usr/bin/python3", _cfg(cfg)["mail_recall"], "sync"],
                    timeout=120, dry_run=dry_run)


# --------------------------------------------------------------------------
# E5 — messenger recall path


def check_e5_recall(cfg):
    """Runs the exact code path the messenger's email_recall tool shells out to
    (DESIGN_v2.md:57). A tool that errors when Frank asks "did you talk to X"
    is a public-surface failure even though no mail is lost."""
    e = _cfg(cfg)
    recall = e["mail_recall"]
    if not os.path.exists(recall):
        return Result("E5-recall", UNCONFIGURED, f"{recall} not found")
    try:
        p = subprocess.run(["/usr/bin/python3", recall, "today"],
                           capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return Result("E5-recall", RED, f"mail_recall today failed to run: {exc}")
    if p.returncode != 0:
        return Result("E5-recall", RED,
                      f"mail_recall today exit {p.returncode}: "
                      f"{p.stderr.strip()[:160]}")
    bridge = e.get("agent_bridge")
    if bridge and os.path.exists(bridge):
        with open(bridge) as f:
            if "email_recall" not in f.read():
                return Result("E5-recall", RED,
                              f"email_recall is no longer registered in {bridge} — "
                              f"the messenger cannot reach mail history")
    return Result("E5-recall", OK, "mail_recall today exit 0, tool registered")


# --------------------------------------------------------------------------
# E6 — suppressed-table burst


def burst_verdict(counts, auth_fail_threshold=5, canary_threshold=3):
    """Pure threshold rule, shared with the tests. counts maps reason -> n."""
    hits = []
    if counts.get("auth_fail", 0) >= auth_fail_threshold:
        hits.append(f"auth_fail x{counts['auth_fail']}")
    canary = sum(n for r, n in counts.items() if "canary" in r)
    if canary >= canary_threshold:
        hits.append(f"canary x{canary}")
    return hits


def check_e6_suppressed(cfg):
    e = _cfg(cfg)
    if _missing_read_creds(cfg):
        return Result("E6-suppressed", UNCONFIGURED,
                      f"missing D1 credential files: {_missing_read_creds(cfg)}")
    since = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - 3600))
    rows = d1_query(
        cfg, "SELECT reason, count(*) AS n FROM suppressed WHERE ts >= ? "
             "GROUP BY reason", [since])
    counts = {r["reason"]: r["n"] for r in rows}
    hits = burst_verdict(counts, e.get("auth_fail_threshold", 5),
                         e.get("canary_threshold", 3))
    if hits:
        return Result("E6-suppressed", RED,
                      f"suppression burst in the last hour: {', '.join(hits)} "
                      f"— someone is probing the inbox (wq#1250)", counts)
    return Result("E6-suppressed", OK, f"1h suppressions: {counts or 'none'}")


# --------------------------------------------------------------------------


def registry(cfg):
    e = _cfg(cfg)
    return {
        "E1-version": {"fn": check_e1_version, "interval_s": 300, "heals": []},
        "E1-schema": {"fn": check_e1_schema, "interval_s": 0,
                      "heals": [("apply-missing-migrations", heal_apply_migrations)]},
        "E2-roundtrip": {"fn": check_e2_roundtrip,
                         "interval_s": e.get("probe_window_s", 1800),
                         "heals": [("wait-one-cycle", _noop_heal),
                                   ("rollback-to-last-good", heal_rollback)]},
        "E3-deepseek": {"fn": check_e3_deepseek, "interval_s": 3600, "heals": []},
        "E4-sync": {"fn": check_e4_sync, "interval_s": 300,
                    "heals": [("run-mail-sync", heal_sync)]},
        "E5-recall": {"fn": check_e5_recall, "interval_s": 3600, "heals": []},
        "E6-suppressed": {"fn": check_e6_suppressed, "interval_s": 900, "heals": []},
    }


def _noop_heal(cfg, dry_run=False):
    """E2's first 'heal' is deliberately nothing: one missed round trip is a
    miss, two is an incident. This burns a ladder step so the rollback happens
    on the second miss, as the ticket specifies, without a special case."""
    return True, "first miss — waiting one cycle before rolling back"
