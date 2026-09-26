#!/usr/bin/env python3
"""failure_drills.py — the wq#1252 "test to failure" set, run against the real
world rather than mocks wherever that is safe.

  python3 tests/failure_drills.py            all drills
  python3 tests/failure_drills.py schema     one drill

Drills that would take a public surface down (stopping lumen-pokemon-live,
revoking the live DeepSeek key) are reproduced by pointing the check at a
broken copy instead — same code path, same verdict, no outage. The stream
drill deliberately talks to a dead port, which is exactly what the check sees
when the live agent is gone.
"""
import json
import os
import sqlite3
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.dirname(HERE)
sys.path.insert(0, SRC)

import guardian          # noqa: E402
import checks_email      # noqa: E402
import checks_stream     # noqa: E402
import schema_parse      # noqa: E402

FAILURES = []


def expect(label, condition, detail):
    mark = "PASS" if condition else "FAIL"
    if not condition:
        FAILURES.append(label)
    print(f"[{mark}] {label}: {detail}")


def drill_schema():
    """Break D1's schema on a local copy -> E1-schema detects it.

    A local sqlite standing in for remote D1 is a faithful test: the check
    compares migration-derived columns against PRAGMA table_info output, and
    PRAGMA is the same in both engines.
    """
    mig = "/home/lumen/mailbot/migrations"
    expected = schema_parse.expected_schema(mig)
    with tempfile.TemporaryDirectory() as tmp:
        db = os.path.join(tmp, "broken.db")
        conn = sqlite3.connect(db)
        # Pre-0002 schema: exactly the state production was in on 2026-09-02.
        with open(os.path.join(mig, "0001_init.sql")) as f:
            conn.executescript(f.read())
        conn.commit()
        actual = {}
        for table in expected:
            rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
            if rows:
                actual[table] = {r[1] for r in rows}
        conn.close()

    missing_tables, missing_columns = schema_parse.diff_schema(expected, actual)
    pending = schema_parse.pending_migrations(mig, missing_tables, missing_columns)
    expect("schema/detects-missing-column",
           "messages.dodge_style" in missing_columns,
           f"missing columns {missing_columns}")
    expect("schema/detects-missing-table", "suppressed" in missing_tables,
           f"missing tables {missing_tables}")
    expect("schema/names-the-heal",
           pending == ["0002_dodge_style.sql", "0003_suppressed.sql"],
           f"pending migrations {pending}")

    # And the healthy case must stay quiet.
    ok_tables, ok_cols = schema_parse.diff_schema(expected, expected)
    expect("schema/no-false-positive", not ok_tables and not ok_cols,
           "a matching schema reports nothing")


def drill_deepseek():
    """Block the DeepSeek key -> E3 goes RED.

    Uses a throwaway env file with an invalid key so the live key is never
    touched; the request really does go to api.deepseek.com.
    """
    cfg = json.load(open(os.path.join(SRC, "config.json")))
    with tempfile.TemporaryDirectory() as tmp:
        env = os.path.join(tmp, ".env")
        with open(env, "w") as f:
            f.write("DEEPSEEK_API_KEY=sk-" + "invalid-lpg-drill\n")
        cfg["email"]["deepseek_env_file"] = env
        res = checks_email.check_e3_deepseek(cfg)
    expect("deepseek/invalid-key-is-red", res.status == guardian.RED, res.detail)

    cfg2 = json.load(open(os.path.join(SRC, "config.json")))
    cfg2["email"]["deepseek_env_file"] = "/nonexistent/.env"
    res2 = checks_email.check_e3_deepseek(cfg2)
    expect("deepseek/missing-key-is-unconfigured",
           res2.status == guardian.UNCONFIGURED, res2.detail)


def drill_stream():
    """Stream down -> S1/S4 RED and the heal ladder fires the restart first.

    Points the checks at a closed port rather than stopping the live agent:
    connection-refused is what the check sees when lumen-pokemon-live is gone,
    and nobody watching the stream gets interrupted to prove it.
    """
    cfg = json.load(open(os.path.join(SRC, "config.json")))
    cfg["stream"] = dict(cfg["stream"],
                         public_url="http://127.0.0.1:9",
                         local_url="http://127.0.0.1:9")
    s1 = checks_stream.check_s1_public(cfg)
    s4 = checks_stream.check_s4_local(cfg)
    expect("stream/dead-port-is-red",
           s1.status == guardian.RED and s4.status == guardian.RED, s1.detail)

    heals = []
    state = {"checks": {}}
    cs = guardian.check_state(state, "S1-public")
    spec = {"heals": [(n, lambda c, d=False, n=n: (heals.append(n), (True, n))[1])
                      for n, _ in checks_stream.STREAM_HEALS]}
    fires = []
    real_ntfy, real_fire = guardian.ntfy, guardian.open_fire
    guardian.ntfy = lambda *a, **k: True
    guardian.open_fire = lambda c, r, l, d=False: fires.append(r.id) or "drill"
    try:
        for cycle in range(4):
            guardian._apply(cfg, state, cs, spec, s1, dry_run=True, now=cycle)
    finally:
        guardian.ntfy, guardian.open_fire = real_ntfy, real_fire
    expect("stream/heal-ladder-order",
           heals == ["restart-lumen-pokemon-live", "restart-cloudflared"],
           f"heals ran {heals}")
    expect("stream/fire-after-ladder", fires == ["S1-public"],
           f"fires opened {fires}")

    # A BRB splash returns 200 and must still be RED — that is the whole point
    # of checking freshness instead of status codes.
    expect("stream/brb-splash-is-red",
           checks_stream.newest_event_age(b"<html>BRB</html>") is None,
           "a 200 that is not an event feed does not count as up")


def drill_deadman():
    """Kill the guardian -> its dead man's switch fires.

    Coverage comes from wq#1184: the unit stamps /home/lumen/logs/.stamp.<unit>
    on every successful pass, and lumen-cron-deadman alerts when a stamp is
    older than 2x the unit's timer cadence. Verified structurally here (the
    wiring exists and the stamp helper works); end-to-end proof is the
    install-time step in the runbook.
    """
    unit_file = os.path.join(SRC, "lumen-public-guardian.service")
    text = open(unit_file).read()
    expect("deadman/unit-stamps-on-success",
           "lumen-cron-stamp %n" in text and "ExecStartPost" in text,
           "ExecStartPost=-/home/lumen/bin/lumen-cron-stamp %n present")
    expect("deadman/stamp-helper-installed",
           os.path.exists("/home/lumen/bin/lumen-cron-stamp"),
           "/home/lumen/bin/lumen-cron-stamp")
    expect("deadman/watcher-enabled",
           os.path.exists("/etc/systemd/system/lumen-cron-deadman.timer"),
           "lumen-cron-deadman.timer installed (wq#1184)")
    timer = open(os.path.join(SRC, "lumen-public-guardian.timer")).read()
    expect("deadman/threshold-is-4min", "OnUnitActiveSec=2min" in timer,
           "2 min cadence -> deadman alerts once the stamp passes 4 min")


DRILLS = {
    "schema": drill_schema,
    "deepseek": drill_deepseek,
    "stream": drill_stream,
    "deadman": drill_deadman,
}


def main():
    wanted = sys.argv[1:] or list(DRILLS)
    for name in wanted:
        print(f"\n== drill: {name} ==")
        DRILLS[name]()
    print(f"\n{len(FAILURES)} failing assertion(s): {FAILURES or 'none'}")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
