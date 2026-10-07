"""CLASS 4 (disk), CLASS 5 (providers), CLASS 6 (nodes) with injected probes."""
import json
import os

import pytest
from selfheal_fakes import Proc

import sh_disk
import sh_nodes
import sh_providers

# ---- CLASS 4 ----------------------------------------------------------------------


def test_disk_over_threshold_runs_every_safe_pruner_then_tickets_if_still_over(make_ctx, tmp_path):
    ran = []
    runner = lambda argv, **kw: ran.append(argv) or Proc()  # noqa: E731
    cfg = {"disk": {"threshold_pct": 90, "mounts": [str(tmp_path)],
                    "pruners": ["npm_cache", "journal", "stale_worktrees"]}}
    ctx = make_ctx(cfg, {}, runner=runner)
    sh_disk.sweep(ctx, measure=lambda m: 95.0)
    joined = [" ".join(a) for a in ran]
    assert any("npm cache clean --force" in a for a in joined)
    assert any("journalctl --vacuum-time=7d" in a for a in joined)
    assert any("prune_worktrees.py --apply --grace-days 7" in a for a in joined)
    assert not any("pip" in a for a in joined)            # disabled in config
    [t] = ctx.out.tickets
    assert "still 95% full" in t["title"]
    # cooldown: the next sweep inside an hour prunes nothing
    ran.clear()
    sh_disk.sweep(make_ctx(cfg, ctx.state, now=ctx.now + 60, runner=runner),
                  measure=lambda m: 95.0)
    assert ran == []


def test_disk_prune_that_works_files_nothing_and_under_threshold_does_nothing(make_ctx, tmp_path):
    levels = iter([93.0, 70.0])
    cfg = {"disk": {"mounts": [str(tmp_path)], "pruners": ["npm_cache"]}}
    ctx = make_ctx(cfg, {})
    sh_disk.sweep(ctx, measure=lambda m: next(levels))
    assert ctx.out.tickets == [] and any("93.0% -> 70.0%" in a for a in ctx.actions)
    ctx = make_ctx(cfg, {}, now=ctx.now + 7200)
    sh_disk.sweep(ctx, measure=lambda m: 50.0)
    assert ctx.actions == []


def test_cargo_clear_refuses_paths_outside_the_cache(tmp_path, monkeypatch):
    cache = tmp_path / "cache"
    (cache / "index-x").mkdir(parents=True)
    (cache / "index-x" / "a.crate").write_text("x")
    keep = tmp_path / "cache-evil"
    keep.mkdir()
    monkeypatch.setattr(sh_disk, "CARGO_CACHE", str(cache))
    assert sh_disk._clear_dir_contents(str(cache)) and os.listdir(cache) == []
    assert cache.is_dir()
    with pytest.raises(ValueError):
        sh_disk._clear_dir_contents(str(keep))


def test_pruner_table_is_code_not_config(make_ctx, tmp_path):
    """A config naming an unknown pruner (or a path) cannot make the class run it."""
    ran = []
    cfg = {"disk": {"mounts": [str(tmp_path)], "pruners": ["rm -rf /data", "backups"]}}
    sh_disk.sweep(make_ctx(cfg, {}, runner=lambda a, **k: ran.append(a) or Proc()),
                  measure=lambda m: 99.0)
    assert ran == []

# ---- CLASS 5 ----------------------------------------------------------------------


HOSTS = {"api.deepseek.com": ["deepseek-v4-flash", "deepseek-v4"],
         "api.z.ai": ["glm-5.3"]}


def _pcfg(tmp_path):
    return {"providers": {"cooldown_path": str(tmp_path / "cooldown.json"),
                          "full_interval_s": 1800, "recheck_interval_s": 300,
                          "disable_after_s": 600, "cooldown_s": 3600}}


def _psweep(ctx, verdicts):
    def prober(url, key, model):
        host = next(h for h, ms in HOSTS.items() if model in ms)
        return verdicts[host]
    sh_providers.sweep(ctx, prober=prober, key_reader=lambda p: "k",
                       hosts_reader=lambda p: HOSTS)


PROVIDER_401 = (401, '{"error": {"message": "litellm.AuthenticationError: '
                     'DeepseekException - invalid api key"}}')
PROVIDER_402 = (402, '{"error": {"message": "litellm.BadRequestError: '
                     'DeepseekException - Insufficient Balance"}}')


def test_classify_tells_provider_auth_from_gateway_auth():
    assert sh_providers.classify(200, "") == "ok"
    assert sh_providers.classify(*PROVIDER_401) == "auth"
    assert sh_providers.classify(403, "Forbidden by upstream") == "auth"
    assert sh_providers.classify(
        401, '{"detail":"Authentication Error, Invalid proxy server token passed"}') \
        == "gateway-auth"
    assert sh_providers.classify(*PROVIDER_402) == "billing"
    assert sh_providers.classify(429, "rate limited") == "error"
    assert sh_providers.classify(None, "timed out") == "error"


def test_provider_401_for_ten_minutes_cools_its_rungs_then_recovery_reenables(make_ctx, tmp_path):
    cfg = _pcfg(tmp_path)
    cd = tmp_path / "cooldown.json"
    cd.write_text(json.dumps({"hathor-llama8b": 1e12}))          # another writer's row
    state, t0 = {}, 100_000
    bad = {"api.deepseek.com": PROVIDER_401, "api.z.ai": (200, "")}
    _psweep(make_ctx(cfg, state, now=t0), bad)
    assert not cd.read_text().count("deepseek")                 # 0 min: not yet
    ctx = make_ctx(cfg, state, now=t0 + 300)
    _psweep(ctx, bad)                                           # recheck, 5 min
    assert ctx.out.tickets == []
    ctx = make_ctx(cfg, state, now=t0 + 600)
    _psweep(ctx, bad)                                           # 10 min: disable
    data = json.loads(cd.read_text())
    assert data["deepseek-v4-flash"]["source"] == "fleet-selfheal"
    assert data["deepseek-v4"]["until"] == t0 + 600 + 3600
    assert "glm-5.3" not in data and data["hathor-llama8b"] == 1e12
    [t] = ctx.out.tickets
    assert t["title"].startswith("[selfheal] rotate key: api.deepseek.com")
    good = {"api.deepseek.com": (200, ""), "api.z.ai": (200, "")}
    ctx = make_ctx(cfg, state, now=t0 + 900)
    _psweep(ctx, good)
    assert json.loads(cd.read_text()) == {"hathor-llama8b": 1e12}
    assert "provider-auth:api.deepseek.com" not in state["tickets"]


def test_a_flap_shorter_than_ten_minutes_never_disables(make_ctx, tmp_path):
    cfg, state, t0 = _pcfg(tmp_path), {}, 100_000
    _psweep(make_ctx(cfg, state, now=t0), {"api.deepseek.com": PROVIDER_401,
                                            "api.z.ai": (200, "")})
    _psweep(make_ctx(cfg, state, now=t0 + 300), {"api.deepseek.com": (200, ""),
                                                  "api.z.ai": (200, "")})
    ctx = make_ctx(cfg, state, now=t0 + 1800)
    _psweep(ctx, {"api.deepseek.com": PROVIDER_401, "api.z.ai": (200, "")})
    assert ctx.out.tickets == [] and not (tmp_path / "cooldown.json").exists()


def test_provider_402_for_ten_minutes_cools_its_rungs_then_recovery_reenables(make_ctx, tmp_path):
    cfg = _pcfg(tmp_path)
    cd = tmp_path / "cooldown.json"
    cd.write_text(json.dumps({"hathor-llama8b": 1e12}))          # another writer's row
    state, t0 = {}, 100_000
    bad = {"api.deepseek.com": PROVIDER_402, "api.z.ai": (200, "")}
    _psweep(make_ctx(cfg, state, now=t0), bad)
    assert not cd.read_text().count("deepseek")                 # 0 min: not yet
    ctx = make_ctx(cfg, state, now=t0 + 300)
    _psweep(ctx, bad)                                           # recheck, 5 min
    assert ctx.out.tickets == []
    ctx = make_ctx(cfg, state, now=t0 + 600)
    _psweep(ctx, bad)                                           # 10 min: disable
    data = json.loads(cd.read_text())
    assert data["deepseek-v4-flash"]["source"] == "fleet-selfheal"
    assert data["deepseek-v4"]["until"] == t0 + 600 + 3600
    assert "glm-5.3" not in data and data["hathor-llama8b"] == 1e12
    [t] = ctx.out.tickets
    assert t["title"].startswith("[selfheal] top up api.deepseek.com")
    assert "rotate" not in t["title"].lower()
    good = {"api.deepseek.com": (200, ""), "api.z.ai": (200, "")}
    ctx = make_ctx(cfg, state, now=t0 + 900)
    _psweep(ctx, good)
    assert json.loads(cd.read_text()) == {"hathor-llama8b": 1e12}
    assert "provider-billing:api.deepseek.com" not in state["tickets"]


def test_every_host_402_files_per_host_tickets_not_a_gateway_key_page(make_ctx, tmp_path):
    cfg, state, t0 = _pcfg(tmp_path), {}, 100_000
    bad = {"api.deepseek.com": PROVIDER_402, "api.z.ai": PROVIDER_402}
    _psweep(make_ctx(cfg, state, now=t0), bad)
    ctx = make_ctx(cfg, state, now=t0 + 600)
    _psweep(ctx, bad)                                           # 10 min: disable both
    titles = sorted(t["title"] for t in ctx.out.tickets)
    assert titles == ["[selfheal] top up api.deepseek.com: provider balance exhausted "
                      "(10 min)",
                      "[selfheal] top up api.z.ai: provider balance exhausted (10 min)"]
    assert not any("master key" in t for t in titles)


def test_every_host_401_is_our_gateway_key_and_disables_nothing(make_ctx, tmp_path):
    ctx = make_ctx(_pcfg(tmp_path), {}, now=100_000)
    _psweep(ctx, {"api.deepseek.com": PROVIDER_401, "api.z.ai": PROVIDER_401})
    [t] = ctx.out.tickets
    assert "master key" in t["title"]
    assert not (tmp_path / "cooldown.json").exists()


def test_hosts_from_config_groups_chat_models_and_skips_embeddings(tmp_path):
    p = tmp_path / "litellm.yaml"
    p.write_text("model_list:\n"
                 "  - model_name: a\n    litellm_params: {model: openai/x, api_base: 'https://h1/v1'}\n"
                 "  - model_name: b\n    litellm_params: {model: openai/y, api_base: 'https://h1/v1'}\n"
                 "  - model_name: e\n    litellm_params: {model: openai/text-embed, api_base: 'https://h2'}\n"
                 "  - model_name: d\n    litellm_params: {model: deepseek/deepseek-chat}\n")
    assert dict(sh_providers.hosts_from_config(str(p))) == {"h1": ["a", "b"],
                                                           "deepseek": ["d"]}

# ---- CLASS 6 ----------------------------------------------------------------------


TS_JSON = json.dumps({"Peer": {
    "k1": {"HostName": "isis", "TailscaleIPs": ["100.100.129.24"], "Online": False,
           "LastSeen": "2026-10-01T10:00:00Z"},
    "k2": {"HostName": "lumen-1", "TailscaleIPs": ["100.117.66.102"], "Online": True},
    "k3": {"HostName": "franks-phone", "TailscaleIPs": ["100.64.0.9"], "Online": False,
           "LastSeen": "2026-10-01T09:00:00Z"}}})
STATE_OUT = """\
      isis.tailscale_ip   = 100.100.129.24  (since 2026-06-09)  [verified today]
      isis.power_plug     = p1 — (Tapo P115 @ 192.168.8.118, REMOTELY CONTROLLABLE)
      isis.power_policy   = WARM STANDBY - stays ON
     lumen.tailscale_ip   = 100.117.66.102
"""


def BASELINED():
    return {"nodes_meta": {"baselined": True}}


def _node_runner(calls):
    def runner(argv, **kw):
        calls.append(argv)
        if argv[:2] == ["tailscale", "status"]:
            return Proc(TS_JSON)
        if "lumen_state.py" in " ".join(argv):
            return Proc(STATE_OUT)
        if argv[0].endswith("plug-ctl"):
            return Proc("p1: off (0.0 W)")
        return Proc(returncode=1)
    return runner


def test_power_state_truncates_on_word_boundary_not_mid_path(make_ctx):
    # wq#3508: a hard v[:200] cut sliced the trailing kit path mid-word
    # ("/home/lumen/fleet/thor-power" -> "...thor-powe"), filing a ticket that cited a
    # path which doesn't exist.
    value = ("TLP 1.8 auto AC/BAT; ASPM backed off to default (was powersupersave on BAT); "
             "USB_AUTOSUSPEND=0; mem_sleep=s2idle (deep removed 2026-07-03 freeze fix); "
             "charge cap 85pct; kit /home/lumen/fleet/thor-power")
    assert len(value) == 201
    ctx = make_ctx({}, {})
    [line] = sh_nodes.power_state(ctx, {"power_mgmt": value})
    assert "thor-powe" not in line           # no truncated path fragment survives
    assert line.endswith("…")


def test_fleet_node_offline_30_min_is_ticketed_with_last_seen_and_power(make_ctx):
    calls = []
    last_seen = sh_nodes._ts("2026-10-01T10:00:00Z")
    ctx = make_ctx({"nodes": {"offline_after_s": 1800}}, BASELINED(), now=last_seen + 1799,
                   runner=_node_runner(calls))
    sh_nodes.sweep(ctx)
    assert ctx.out.tickets == []
    ctx = make_ctx({"nodes": {"offline_after_s": 1800}}, ctx.state, now=last_seen + 1800,
                   runner=_node_runner(calls))
    sh_nodes.sweep(ctx)
    [t] = ctx.out.tickets
    assert t["title"] == "[selfheal] fleet node isis offline on tailscale for 30 min"
    assert "LastSeen 2026-10-01T10:00:00Z" in t["detail"]
    assert "power_policy = WARM STANDBY" in t["detail"]
    assert "live plug p1 state: p1: off (0.0 W)" in t["detail"]
    assert "franks-phone" not in json.dumps(ctx.out.tickets)     # not a fleet node


def test_first_sweep_baselines_known_outages_into_one_ticket(make_ctx):
    last_seen = sh_nodes._ts("2026-10-01T10:00:00Z")
    ctx = make_ctx({"nodes": {}}, {}, now=last_seen + 90_000, runner=_node_runner([]))
    sh_nodes.sweep(ctx)
    [t] = ctx.out.tickets
    assert t["title"] == "[selfheal] 1 fleet nodes already offline at fleet-selfheal install"
    assert "isis" in t["detail"] and not ctx.out.pages
    ctx = make_ctx({"nodes": {}}, ctx.state, now=last_seen + 90_060, runner=_node_runner([]))
    sh_nodes.sweep(ctx)
    assert ctx.out.tickets == []                        # baselined: never re-files


def test_node_back_online_resolves_and_ignore_list_suppresses(make_ctx):
    last_seen = sh_nodes._ts("2026-10-01T10:00:00Z")
    ctx = make_ctx({"nodes": {}}, BASELINED(), now=last_seen + 4000, runner=_node_runner([]))
    sh_nodes.sweep(ctx)
    assert "node-offline:isis" in ctx.state["tickets"]
    online = lambda c: {"k1": {"host": "isis", "ips": ["100.100.129.24"],  # noqa: E731
                               "online": True, "last_seen": None}}
    sh_nodes.sweep(make_ctx({"nodes": {}}, ctx.state, now=last_seen + 4100), peers_fn=online)
    assert "node-offline:isis" not in ctx.state["tickets"]
    ctx = make_ctx({"nodes": {"ignore": ["isis"]}}, BASELINED(), now=last_seen + 4000,
                   runner=_node_runner([]))
    sh_nodes.sweep(ctx)
    assert ctx.out.tickets == []
