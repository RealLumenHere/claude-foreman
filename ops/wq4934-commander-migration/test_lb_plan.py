"""Tests for lb_plan.py against an in-memory Cloudflare API. No network."""

import copy
import importlib.util
import json
import os
from pathlib import Path

import pytest

_spec = importlib.util.spec_from_file_location("lb_plan", Path(__file__).with_name("lb_plan.py"))
lb = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(lb)

ACCOUNT = "a" * 32
ZONE = "b" * 32
TUNNEL_LUMEN = "11111111-1111-4111-8111-111111111111"
TUNNEL_SENECA = "22222222-2222-4222-8222-222222222222"


def config(**over):
    cfg = {
        "account_id": ACCOUNT,
        "zone_id": ZONE,
        "lb_hostname": "mcp-backend.example.com",
        "pools": [
            {"name": "commander-lumen", "endpoint_name": "lumen", "tunnel_id": TUNNEL_LUMEN},
            {"name": "commander-seneca", "endpoint_name": "seneca", "tunnel_id": TUNNEL_SENECA},
        ],
    }
    cfg.update(over)
    return cfg


class FakeCF:
    """Just enough of the load balancing API: list, create, replace, patch, pool health."""

    def __init__(self):
        self.store = {"monitors": {}, "pools": {}, "lbs": {}}
        self.health = {}
        self.calls = []
        self._n = 0

    def _kind(self, path):
        if path.startswith(f"/zones/{ZONE}/load_balancers"):
            return "lbs", path[len(f"/zones/{ZONE}/load_balancers"):]
        base = f"/accounts/{ACCOUNT}/load_balancers/"
        assert path.startswith(base), path
        kind, _, rest = path[len(base):].partition("/")
        return kind, "/" + rest if rest else ""

    def call(self, method, path, body=None):
        self.calls.append((method, path))
        kind, rest = self._kind(path)
        items = self.store[kind]
        if method == "GET" and not rest:
            return copy.deepcopy(list(items.values()))
        if method == "GET" and rest.endswith("/health"):
            pool_id = rest.split("/")[1]
            flag = self.health.get(pool_id)
            if flag is None:
                return {"pool_id": pool_id, "pop_health": {}}
            reason = "No failures" if flag else "HTTP status 403 not in expected codes"
            return {"pool_id": pool_id, "pop_health": {"healthy": flag, "origins": [
                {"198.51.100.7": {"healthy": flag, "failure_reason": reason}}]}}
        if method == "POST" and not rest:
            self._n += 1
            obj = {**copy.deepcopy(body), "id": f"{kind}-{self._n}"}
            items[obj["id"]] = obj
            return copy.deepcopy(obj)
        obj_id = rest.lstrip("/")
        if method == "PUT":
            items[obj_id] = {**copy.deepcopy(body), "id": obj_id}
        elif method == "PATCH":
            items[obj_id].update(copy.deepcopy(body))
        else:
            raise AssertionError(f"unexpected {method} {path}")
        return copy.deepcopy(items[obj_id])

    def writes(self):
        return [c for c in self.calls if c[0] != "GET"]

    def the_lb(self):
        (only,) = self.store["lbs"].values()
        return only

    def set_health(self, *flags):
        for pool_id, flag in zip(self.store["pools"], flags):
            self.health[pool_id] = flag


@pytest.fixture
def cfg():
    return lb.validate_config(config())


@pytest.fixture
def applied(cfg):
    cf = FakeCF()
    lb.apply(cf.call, cfg)
    cf.set_health(True, True)
    return cf


# ---- config ----

def test_valid_config_defaults_host_header_and_health_path(cfg):
    assert cfg["origin_host_header"] == "mcp-backend.example.com"
    assert cfg["health_path"] == "/health"
    assert cfg["interval_s"] == 60


@pytest.mark.parametrize("over, needle", [
    ({"account_id": "REPLACE_ME"}, "account_id"),
    ({"zone_id": "short"}, "zone_id"),
    ({"lb_hostname": "not a host"}, "lb_hostname"),
    ({"health_path": "health"}, "health_path"),
    ({"expected_codes": "ok"}, "expected_codes"),
    ({"interval_s": 0}, "interval_s"),
    ({"interval_s": True}, "interval_s"),
    ({"pools": config()["pools"][:1]}, "exactly two"),
    ({"pools": config()["pools"] + config()["pools"][:1]}, "exactly two"),
])
def test_bad_config_is_rejected(over, needle):
    with pytest.raises(lb.ConfigError, match=needle):
        lb.validate_config(config(**over))


def test_same_tunnel_in_both_pools_is_rejected():
    pools = config()["pools"]
    pools[1]["tunnel_id"] = TUNNEL_LUMEN
    with pytest.raises(lb.ConfigError, match="same tunnel_id"):
        lb.validate_config(config(pools=pools))


def test_example_config_is_rejected_until_filled_in():
    example = json.loads(Path(__file__).with_name("lb_config.example.json").read_text())
    with pytest.raises(lb.ConfigError) as e:
        lb.validate_config(example)
    for field in ("account_id", "zone_id", "pools[0].tunnel_id", "pools[1].tunnel_id"):
        assert field in str(e.value)
    assert "lb_hostname" not in str(e.value)


# ---- plan ----

def test_plan_is_active_passive_with_one_tunnel_per_pool(cfg):
    plan = lb.build_plan(cfg)
    balancer = plan["load_balancer"]
    assert balancer["steering_policy"] == "off"
    assert balancer["enabled"] is False
    assert balancer["proxied"] is True
    assert balancer["default_pools"] == ["<id of pool commander-lumen>",
                                         "<id of pool commander-seneca>"]
    assert balancer["fallback_pool"] == "<id of pool commander-seneca>"
    addresses = [p["origins"][0]["address"] for p in plan["pools"]]
    assert addresses == [f"{TUNNEL_LUMEN}.cfargotunnel.com", f"{TUNNEL_SENECA}.cfargotunnel.com"]
    assert all(len(p["origins"]) == 1 for p in plan["pools"])


def test_monitor_is_https_443_with_host_header(cfg):
    monitor = lb.monitor_body(cfg)
    assert (monitor["type"], monitor["port"], monitor["method"]) == ("https", 443, "GET")
    assert monitor["path"] == "/health"
    assert monitor["expected_codes"] == "200"
    assert monitor["header"] == {"Host": ["mcp-backend.example.com"]}


def test_non_default_host_header_path_and_interval_reach_the_bodies():
    cfg = lb.validate_config(config(origin_host_header="gw.example.net", health_path="/ready",
                                    expected_codes="406", interval_s=15))
    plan = lb.build_plan(cfg)
    assert plan["monitor"]["header"] == {"Host": ["gw.example.net"]}
    assert plan["monitor"]["path"] == "/ready"
    assert plan["monitor"]["expected_codes"] == "406"
    assert plan["monitor"]["interval"] == 15
    assert [p["origins"][0]["header"] for p in plan["pools"]] == [{"Host": ["gw.example.net"]}] * 2
    assert plan["load_balancer"]["name"] == "mcp-backend.example.com"


def test_plan_command_needs_no_token_and_makes_no_call(tmp_path, capsys):
    path = tmp_path / "cfg.json"
    path.write_text(json.dumps(config()))

    def no_caller(token):
        raise AssertionError("plan must not build an API caller")

    assert lb.main(["plan", "--config", str(path)], caller=no_caller) == 0
    assert json.loads(capsys.readouterr().out)["load_balancer"]["steering_policy"] == "off"


# ---- apply ----

def test_apply_creates_everything_with_the_balancer_disabled(cfg):
    cf = FakeCF()
    actions = lb.apply(cf.call, cfg)
    assert [a for _, a in actions] == ["created"] * 4
    balancer = cf.the_lb()
    assert balancer["enabled"] is False
    pool_ids = list(cf.store["pools"])
    assert balancer["default_pools"] == pool_ids
    assert balancer["fallback_pool"] == pool_ids[1]
    (monitor_id,) = cf.store["monitors"]
    assert {p["monitor"] for p in cf.store["pools"].values()} == {monitor_id}


def test_second_apply_changes_nothing(applied, cfg):
    before = len(applied.writes())
    assert [a for _, a in lb.apply(applied.call, cfg)] == ["unchanged"] * 4
    assert len(applied.writes()) == before


def test_apply_repairs_drift(applied, cfg):
    applied.the_lb()["steering_policy"] = "random"
    actions = dict(lb.apply(applied.call, cfg))
    assert actions["load balancer mcp-backend.example.com"] == "updated"
    assert actions["monitor"] == "unchanged"
    assert applied.the_lb()["steering_policy"] == "off"


def test_apply_never_disables_a_live_balancer(applied, cfg):
    applied.the_lb()["enabled"] = True
    applied.the_lb()["steering_policy"] = "random"
    lb.apply(applied.call, cfg)
    assert applied.the_lb()["enabled"] is True
    assert applied.the_lb()["steering_policy"] == "off"


# ---- check ----

def test_check_passes_when_applied_and_healthy(applied, cfg):
    assert lb.check(applied.call, cfg, expect_enabled=False) == []


def test_check_is_read_only(applied, cfg):
    before = len(applied.writes())
    lb.check(applied.call, cfg)
    assert len(applied.writes()) == before


def test_check_on_empty_account_reports_the_monitor(cfg):
    assert lb.check(FakeCF().call, cfg) == ["monitor 'wq4934 commander gateway health' not found"]


def test_check_fails_when_standby_pool_is_unhealthy(applied, cfg):
    applied.set_health(True, False)
    problems = lb.check(applied.call, cfg)
    assert problems == ["pool commander-seneca not healthy: 0/2 health reports healthy "
                        "(HTTP status 403 not in expected codes)"]


def test_check_treats_missing_health_data_as_unhealthy(applied, cfg):
    applied.set_health(True, None)
    assert lb.check(applied.call, cfg) == ["pool commander-seneca not healthy: no health data yet"]
    assert lb.check(applied.call, cfg, health_gate=False) == []


def test_check_catches_steering_and_pool_order_drift(applied, cfg):
    balancer = applied.the_lb()
    balancer["steering_policy"] = "random"
    balancer["default_pools"] = balancer["default_pools"][::-1]
    problems = lb.check(applied.call, cfg)
    assert any("steering_policy" in p for p in problems)
    assert any("default_pools[0]" in p for p in problems)


def test_check_catches_a_pool_pointing_at_the_wrong_tunnel(applied, cfg):
    first = next(iter(applied.store["pools"].values()))
    first["origins"][0]["address"] = f"{TUNNEL_SENECA}.cfargotunnel.com"
    (problem,) = lb.check(applied.call, cfg)
    assert problem.startswith("pool commander-lumen origins[0].address")


def test_check_ignores_tuning_drift(applied, cfg):
    next(iter(applied.store["monitors"].values()))["timeout"] = 9
    assert lb.check(applied.call, cfg) == []


def test_check_expect_flag(applied, cfg):
    (problem,) = lb.check(applied.call, cfg, expect_enabled=True)
    assert problem == "load balancer enabled: want True, live False"


def test_check_reports_missing_balancer(applied, cfg):
    applied.store["lbs"].clear()
    assert lb.check(applied.call, cfg) == ["load balancer mcp-backend.example.com not found"]


# ---- enable / disable ----

def test_enable_then_disable(applied, cfg):
    lb.set_enabled(applied.call, cfg, True)
    assert applied.the_lb()["enabled"] is True
    assert lb.check(applied.call, cfg, expect_enabled=True) == []
    lb.set_enabled(applied.call, cfg, False)
    assert applied.the_lb()["enabled"] is False


def test_enable_refuses_when_a_pool_is_unhealthy(applied, cfg):
    applied.set_health(False, True)
    with pytest.raises(lb.CfError, match="refusing to enable: pool commander-lumen not healthy"):
        lb.set_enabled(applied.call, cfg, True)
    assert applied.the_lb()["enabled"] is False


def test_enable_refuses_on_plan_drift_even_without_health_gate(applied, cfg):
    applied.the_lb()["steering_policy"] = "random"
    with pytest.raises(lb.CfError, match="steering_policy"):
        lb.set_enabled(applied.call, cfg, True, health_gate=False)
    assert applied.the_lb()["enabled"] is False


def test_disable_works_while_unhealthy(applied, cfg):
    applied.the_lb()["enabled"] = True
    applied.set_health(False, False)
    lb.set_enabled(applied.call, cfg, False)
    assert applied.the_lb()["enabled"] is False


# ---- command line ----

@pytest.fixture
def cli(tmp_path, applied):
    cfg_path = tmp_path / "cfg.json"
    cfg_path.write_text(json.dumps(config()))
    token_path = tmp_path / "token"
    token_path.write_text("t" * 24 + "\n")
    os.chmod(token_path, 0o600)
    seen = []

    def caller(token):
        seen.append(token)
        return applied.call

    def run(cmd, *extra):
        return lb.main([cmd, "--config", str(cfg_path), "--token-file", str(token_path), *extra],
                       caller=caller)

    run.token_path, run.seen, run.cf = token_path, seen, applied
    return run


@pytest.mark.parametrize("cmd", ["apply", "enable", "disable"])
def test_writes_refuse_without_attended(cli, cmd, capsys):
    before = len(cli.cf.calls)
    assert cli(cmd) == 2
    assert "--attended" in capsys.readouterr().err
    assert cli.seen == [] and len(cli.cf.calls) == before


def test_cli_check_enable_disable_round_trip(cli, capsys):
    assert cli("check", "--expect", "disabled") == 0
    assert cli("check", "--expect", "enabled") == 1
    assert cli("enable", "--attended") == 0
    assert cli("check", "--expect", "enabled") == 0
    assert cli("disable", "--attended") == 0
    assert cli.cf.the_lb()["enabled"] is False
    out = capsys.readouterr()
    assert "t" * 24 not in out.out + out.err
    assert set(cli.seen) == {"t" * 24}


def test_cli_enable_fails_closed_when_unhealthy(cli, capsys):
    cli.cf.set_health(True, False)
    assert cli("enable", "--attended") == 1
    assert "refusing to enable" in capsys.readouterr().err
    assert cli.cf.the_lb()["enabled"] is False
    assert cli("enable", "--attended", "--no-health-gate") == 0
    assert cli.cf.the_lb()["enabled"] is True


def test_token_file_readable_by_others_is_refused(cli):
    os.chmod(cli.token_path, 0o644)
    with pytest.raises(SystemExit, match="mode 0600"):
        cli("check")
    assert cli.seen == []


def test_missing_or_empty_token_file_is_refused(cli, tmp_path):
    cli.token_path.write_text("")
    with pytest.raises(SystemExit, match="empty"):
        cli("check")
    cli.token_path.unlink()
    with pytest.raises(SystemExit, match="No such file"):
        cli("check")


def test_bad_config_exits_2_before_any_call(tmp_path, capsys):
    path = tmp_path / "cfg.json"
    path.write_text(json.dumps(config(zone_id="nope")))
    assert lb.main(["plan", "--config", str(path)]) == 2
    assert "zone_id" in capsys.readouterr().err


def test_api_error_is_reported_not_raised(cli, capsys):
    def broken(token):
        def call(method, path, body=None):
            raise lb.CfError(f"{method} {path}: [{{'code': 10000}}]")
        return call

    cfg_path = cli.token_path.with_name("cfg.json")
    rc = lb.main(["check", "--config", str(cfg_path), "--token-file", str(cli.token_path)],
                 caller=broken)
    assert rc == 1
    assert "10000" in capsys.readouterr().err
