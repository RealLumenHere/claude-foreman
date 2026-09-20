"""Tests for lumen-public-guardian (wq#1252).

Everything here runs offline: no D1, no systemd, no network. The checks are
built so the parts that decide RED vs OK are pure functions, and the parts that
touch the world are thin enough to fake.
"""
import json
import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.dirname(HERE)
sys.path.insert(0, SRC)

import guardian  # noqa: E402
import schema_parse  # noqa: E402
import checks_email  # noqa: E402
import checks_stream  # noqa: E402


# --------------------------------------------------------------------------
# schema parsing — the 2026-09-02 root cause


def test_parse_create_table_columns_not_constraints():
    tables = schema_parse.parse_sql("""
      CREATE TABLE IF NOT EXISTS messages (
        id TEXT PRIMARY KEY,
        direction TEXT NOT NULL CHECK (direction IN ('in','out')),
        ts TEXT NOT NULL,
        PRIMARY KEY (id, ts)
      );
      CREATE INDEX IF NOT EXISTS idx_x ON messages(ts);
    """)
    assert tables == {"messages": {"id", "direction", "ts"}}


def test_parse_alter_add_column():
    tables = schema_parse.parse_sql(
        "ALTER TABLE messages ADD COLUMN dodge_style TEXT;")
    assert tables == {"messages": {"dodge_style"}}


def test_parse_ignores_comments():
    tables = schema_parse.parse_sql("""
      -- CREATE TABLE ghost (a TEXT);
      /* CREATE TABLE phantom (b TEXT); */
      CREATE TABLE real_t (c TEXT);
    """)
    assert set(tables) == {"real_t"}


def test_expected_schema_from_real_mailbot_migrations():
    """Guards against a parser that silently stops understanding the real
    files — the whole check is worthless if `expected` comes back empty."""
    mig = "/home/lumen/mailbot/migrations"
    if not os.path.isdir(mig):
        pytest.skip("mailbot migrations not present")
    expected = schema_parse.expected_schema(mig)
    assert {"threads", "messages", "suppressed"} <= set(expected)
    assert "dodge_style" in expected["messages"]     # 0002
    assert "reason" in expected["suppressed"]        # 0003


def test_diff_reports_missing_and_ignores_extra():
    expected = {"messages": {"id", "dodge_style"}, "suppressed": {"id"}}
    actual = {"messages": {"id", "leftover"}}
    missing_tables, missing_columns = schema_parse.diff_schema(expected, actual)
    assert missing_tables == ["suppressed"]
    assert missing_columns == ["messages.dodge_style"]


def test_pending_migrations_picks_only_the_relevant_files(tmp_path):
    (tmp_path / "0001_init.sql").write_text("CREATE TABLE messages (id TEXT);")
    (tmp_path / "0002_dodge.sql").write_text(
        "ALTER TABLE messages ADD COLUMN dodge_style TEXT;")
    (tmp_path / "0003_sup.sql").write_text("CREATE TABLE suppressed (id INTEGER);")
    pending = schema_parse.pending_migrations(
        str(tmp_path), ["suppressed"], ["messages.dodge_style"])
    assert pending == ["0002_dodge.sql", "0003_sup.sql"]


# --------------------------------------------------------------------------
# E2 — probe token + round-trip matching


def test_probe_token_is_stable_inside_a_window_and_changes_across_them():
    a = checks_email.probe_token("s3cret", 1_000_000, window_s=1800)
    b = checks_email.probe_token("s3cret", 1_000_100, window_s=1800)
    c = checks_email.probe_token("s3cret", 1_000_000 + 1800, window_s=1800)
    assert a == b and a != c
    assert checks_email.probe_token("other", 1_000_000) != a


def test_valid_tokens_cover_mail_in_flight_across_a_boundary():
    now = 1_000_000
    tokens = checks_email.valid_probe_tokens("s", now, window_s=1800, back=2)
    assert checks_email.probe_token("s", now - 1800, 1800) in tokens
    assert checks_email.probe_token("s", now - 3600, 1800) in tokens
    assert checks_email.probe_token("s", now - 5400, 1800) not in tokens


def _row(direction, ts, body=""):
    return {"direction": direction, "ts": ts, "body": body,
            "from_addr": "probe@lumennode.uk", "to_addr": "lumen@lumennode.uk"}


def test_roundtrip_matches_reply_within_window():
    rows = [_row("in", "2026-09-02T10:00:00Z", "token abc123 here"),
            _row("out", "2026-09-02T10:01:00Z")]
    inbound, outbound = checks_email.match_roundtrip(rows, {"abc123"}, 300)
    assert inbound and outbound


def test_roundtrip_reply_too_late_is_not_a_match():
    rows = [_row("in", "2026-09-02T10:00:00Z", "abc123"),
            _row("out", "2026-09-02T10:30:00Z")]
    inbound, outbound = checks_email.match_roundtrip(rows, {"abc123"}, 300)
    assert inbound and outbound is None


def test_roundtrip_ignores_inbound_without_a_valid_token():
    rows = [_row("in", "2026-09-02T10:00:00Z", "no token at all"),
            _row("out", "2026-09-02T10:01:00Z")]
    assert checks_email.match_roundtrip(rows, {"abc123"}, 300) == (None, None)


# --------------------------------------------------------------------------
# E6 — suppression bursts


def test_burst_thresholds():
    assert checks_email.burst_verdict({"auth_fail": 4}) == []
    assert checks_email.burst_verdict({"auth_fail": 5})
    assert checks_email.burst_verdict({"canary_hit": 2, "canary_reply": 1})
    assert checks_email.burst_verdict({"daily_cap": 99}) == []


def test_read_env_key_finds_value_and_missing_file_is_none():
    tmp = os.path.join(os.path.dirname(__file__), "_env_fixture")
    with open(tmp, "w") as f:
        f.write("OTHER=1\nDEEPSEEK_API_KEY=\"sk-xyz\"\n")
    try:
        assert checks_email.read_env_key(tmp, "DEEPSEEK_API_KEY") == "sk-xyz"
        assert checks_email.read_env_key(tmp, "NOPE") is None
        assert checks_email.read_env_key("/nonexistent", "X") is None
    finally:
        os.unlink(tmp)


# --------------------------------------------------------------------------
# S1/S4 — freshness, and the BRB-splash trap


def test_event_feed_freshness():
    body = json.dumps([{"t": "2026-09-02T10:00:00Z"},
                       {"t": "2026-09-02T10:05:00Z"}]).encode()
    import calendar
    import time as _t
    now = calendar.timegm(_t.strptime("2026-09-02T10:06:00", "%Y-%m-%dT%H:%M:%S"))
    age = checks_stream.newest_event_age(body, now=now)
    assert 55 < age < 65


def test_brb_splash_is_not_a_live_feed():
    assert checks_stream.newest_event_age(b"<html>be right back</html>") is None
    assert checks_stream.newest_event_age(b"[]") is None
    assert checks_stream.newest_event_age(b'[{"kind":"x"}]') is None


# --------------------------------------------------------------------------
# heal allowlist


def test_heal_allowlist_refuses_unlisted_commands():
    with pytest.raises(guardian.HealNotAllowed):
        guardian.run_heal(["sudo", "reboot"])
    with pytest.raises(guardian.HealNotAllowed):
        guardian.run_heal(["sudo", "/usr/bin/systemctl", "stop",
                           "cloudflared.service"])
    # A different unit on an allowed verb must also be refused — the entries
    # are complete argv, not open-ended prefixes.
    with pytest.raises(guardian.HealNotAllowed):
        guardian.run_heal(["sudo", "/usr/bin/systemctl", "restart", "vaultwarden"])


def test_heal_allowlist_permits_the_listed_stream_heals():
    ok, out = guardian.run_heal(
        ["sudo", "/usr/bin/systemctl", "restart", "lumen-pokemon-live.service"],
        dry_run=True)
    assert ok and "dry-run" in out


def test_every_heal_the_checks_can_run_is_allowlisted():
    """A heal that is not in HEAL_ALLOWLIST raises at the worst possible moment
    — mid-incident. Assert the two shapes the check modules actually build."""
    cfg = json.load(open(os.path.join(SRC, "config.json")))
    for fn in (checks_stream.heal_restart_live,
               checks_stream.heal_restart_cloudflared,
               checks_email.heal_sync):
        ok, out = fn(cfg, dry_run=True)
        assert ok, out


def test_cloudflared_heal_goes_through_detached_systemd_run():
    """Instinct 4: cloudflared is only ever touched via detached systemd-run."""
    ok, out = checks_stream.heal_restart_cloudflared(
        {"stream": {"cloudflared_unit": "cloudflared.service"}}, dry_run=True)
    assert ok
    assert "systemd-run" in out and "--collect" in out


# --------------------------------------------------------------------------
# state machine: alert -> heal ladder -> fire


class Recorder:
    def __init__(self):
        self.alerts = []
        self.fires = []
        self.heals = []


@pytest.fixture
def rec(monkeypatch):
    r = Recorder()
    monkeypatch.setattr(guardian, "ntfy",
                        lambda cfg, p, t, m, d=False: r.alerts.append((p, t)) or True)
    monkeypatch.setattr(guardian, "open_fire",
                        lambda cfg, res, log, d=False: r.fires.append(res.id) or "9999")
    return r


def _spec(rec):
    def heal(name):
        def fn(cfg, dry_run=False):
            rec.heals.append(name)
            return True, name
        return fn
    return {"heals": [("first", heal("first")), ("second", heal("second"))]}


def _cfg():
    return {"runbook": "/tmp/rb.md", "state_path": "/tmp/s.json",
            "realert_cycles": 15}


def test_red_alerts_then_walks_the_ladder_then_fires(rec):
    cfg, state = _cfg(), {"checks": {}}
    cs = guardian.check_state(state, "S1-public")
    spec = _spec(rec)
    for cycle in range(4):
        res = guardian.Result("S1-public", guardian.RED, "stream frozen")
        guardian._apply(cfg, state, cs, spec, res, dry_run=True, now=100 + cycle)

    assert rec.heals == ["first", "second"]      # ladder in order, once each
    assert rec.fires == ["S1-public"]            # fire only after it is exhausted
    assert cs["consecutive_red"] == 4
    assert cs["fire_ticket"] == "9999"


def test_fire_is_opened_once_per_episode(rec):
    cfg, state = _cfg(), {"checks": {}}
    cs = guardian.check_state(state, "S1-public")
    spec = _spec(rec)
    for cycle in range(8):
        guardian._apply(cfg, state, cs, spec,
                        guardian.Result("S1-public", guardian.RED, "still down"),
                        dry_run=True, now=100 + cycle)
    assert rec.fires == ["S1-public"]


def test_recovery_clears_state_and_alerts(rec):
    cfg, state = _cfg(), {"checks": {}}
    cs = guardian.check_state(state, "S2-unit")
    spec = _spec(rec)
    guardian._apply(cfg, state, cs, spec,
                    guardian.Result("S2-unit", guardian.RED, "down"),
                    dry_run=True, now=100)
    guardian._apply(cfg, state, cs, spec,
                    guardian.Result("S2-unit", guardian.OK, "up"),
                    dry_run=True, now=200)
    assert cs["status"] == guardian.OK
    assert cs["consecutive_red"] == 0 and cs["heal_step"] == 0
    assert ("high", "RECOVERED S2-unit") in rec.alerts


def test_repeated_red_does_not_realert_every_cycle(rec):
    cfg, state = _cfg(), {"checks": {}}
    cs = guardian.check_state(state, "E1-schema")
    for cycle in range(10):
        guardian._apply(cfg, state, cs, {"heals": []},
                        guardian.Result("E1-schema", guardian.RED, "cols missing"),
                        dry_run=True, now=100 + cycle)
    assert len([a for a in rec.alerts if a[1] == "RED E1-schema"]) == 1


def test_unconfigured_is_not_green_and_nags_at_most_daily(rec):
    cfg, state = _cfg(), {"checks": {}}
    cs = guardian.check_state(state, "E2-roundtrip")
    res = guardian.Result("E2-roundtrip", guardian.UNCONFIGURED, "no secret")
    base = 1_756_000_000
    guardian._apply(cfg, state, cs, {"heals": []}, res, dry_run=True, now=base)
    guardian._apply(cfg, state, cs, {"heals": []}, res, dry_run=True,
                    now=base + 3600)          # same day — stays quiet
    guardian._apply(cfg, state, cs, {"heals": []}, res, dry_run=True,
                    now=base + 200_000)       # >24h later — nags again
    assert cs["status"] == guardian.UNCONFIGURED
    assert len([a for a in rec.alerts if a[1].startswith("UNCONFIGURED")]) == 2


def test_a_check_that_raises_becomes_red_not_a_crash(monkeypatch, tmp_path):
    cfg = json.load(open(os.path.join(SRC, "config.json")))
    cfg["state_path"] = str(tmp_path / "state.json")
    cfg["status_path"] = str(tmp_path / "status.json")
    cfg["status_push_map"] = str(tmp_path / "nope.json")

    def boom(_cfg):
        raise RuntimeError("D1 exploded")

    monkeypatch.setattr(guardian, "build_registry",
                        lambda c: {"E1-schema": {"fn": boom, "interval_s": 0,
                                                 "heals": []}})
    monkeypatch.setattr(guardian, "ntfy", lambda *a, **k: True)
    results, state = guardian.run_once(cfg, dry_run=False, force=True)
    assert results[0].status == guardian.RED
    assert "D1 exploded" in results[0].detail
    published = json.load(open(cfg["status_path"]))
    assert published["overall"] == guardian.RED


def test_registry_covers_every_check_the_ticket_names(monkeypatch):
    monkeypatch.setattr(checks_stream, "unit_load_state", lambda unit: "loaded")
    cfg = json.load(open(os.path.join(SRC, "config.json")))
    reg = guardian.build_registry(cfg)
    assert set(reg) == {
        "E1-version", "E1-schema", "E2-roundtrip", "E3-deepseek", "E4-sync",
        "E5-recall", "E6-suppressed",
        "S1-public", "S2-unit", "S3-tunnel", "S4-local",
    }


@pytest.mark.parametrize("load", ["masked", "not-found"])
def test_retired_live_unit_drops_its_checks_but_keeps_the_tunnel(monkeypatch, load):
    monkeypatch.setattr(checks_stream, "unit_load_state", lambda unit: load)
    cfg = json.load(open(os.path.join(SRC, "config.json")))
    reg = guardian.build_registry(cfg)
    assert not {"S1-public", "S2-unit", "S4-local"} & set(reg)
    assert "S3-tunnel" in reg and "E1-version" in reg


def test_uninspectable_unit_is_not_treated_as_retired(monkeypatch):
    monkeypatch.setattr(checks_stream, "systemctl", lambda args, timeout=20: (1, "boom"))
    assert checks_stream.unit_load_state("x.service") is None
    monkeypatch.setattr(checks_stream, "unit_load_state", lambda unit: None)
    cfg = json.load(open(os.path.join(SRC, "config.json")))
    assert "S1-public" in guardian.build_registry(cfg)


def test_a_retired_check_stops_publishing_its_stale_red(monkeypatch, tmp_path):
    cfg = json.load(open(os.path.join(SRC, "config.json")))
    cfg["state_path"] = str(tmp_path / "state.json")
    cfg["status_path"] = str(tmp_path / "status.json")
    cfg["status_push_map"] = str(tmp_path / "nope.json")
    guardian.save_state(cfg["state_path"], {"checks": {
        "S2-unit": {"status": guardian.RED, "consecutive_red": 9, "heal_step": 1,
                    "last_run": 0, "last_alert": 0, "fire_ticket": "wq#1"}}})
    monkeypatch.setattr(guardian, "build_registry", lambda c: {})
    monkeypatch.setattr(guardian, "ntfy", lambda *a, **k: True)
    guardian.run_once(cfg, force=True)
    published = json.load(open(cfg["status_path"]))
    assert "S2-unit" not in published["checks"]
    assert published["overall"] == guardian.OK


def _e1_cfg(tmp_path):
    cfg = json.load(open(os.path.join(SRC, "config.json")))
    e = cfg["email"]
    for key in ("cf_account_id_file", "d1_id_file", "d1_token_file",
                "cf_deploy_token_file"):
        p = tmp_path / key
        p.write_text("x")
        e[key] = str(p)
    return cfg


@pytest.mark.parametrize("code", [401, 403])
def test_rejected_deploy_token_says_so_and_is_unconfigured_not_red(
        monkeypatch, tmp_path, code):
    import urllib.error

    def deny(req, timeout=None):
        raise urllib.error.HTTPError(req.full_url, code, "Unauthorized", {}, None)

    monkeypatch.setattr(checks_email.urllib.request, "urlopen", deny)
    r = checks_email.check_e1_version(_e1_cfg(tmp_path))
    assert r.status == guardian.UNCONFIGURED
    assert f"HTTP {code}" in r.detail and "mint a new one" in r.detail


def test_other_workers_api_errors_stay_red_via_the_raise_path(monkeypatch, tmp_path):
    import urllib.error

    def boom(req, timeout=None):
        raise urllib.error.HTTPError(req.full_url, 500, "oops", {}, None)

    monkeypatch.setattr(checks_email.urllib.request, "urlopen", boom)
    with pytest.raises(urllib.error.HTTPError):
        checks_email.check_e1_version(_e1_cfg(tmp_path))


def test_config_paths_are_absolute():
    cfg = json.load(open(os.path.join(SRC, "config.json")))
    for key in ("state_path", "status_path", "wq_bin", "runbook"):
        assert cfg[key].startswith("/"), key
