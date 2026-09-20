"""Design §8 T9, the routing and swap-refusal parts — wq#2255, C7 of wq#2241 (§7.1, §7.3).

The fixture registry has three live boxes: `boxa` serves two llama-server (swap: unit) families, one
resident and one not; `boxb` serves an ollama (swap: implicit) family with a second one lazy-loadable;
`boxc` serves the same family as boxa's resident one, for ranking. FakePorts stands in for ssh and the
health probe and keeps one ordered call log on virtual time. The capability-refresh third of T9 is
test_capability_profiles.py.
"""
import os
import sys

import pytest

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
DISPATCHER_DIR = os.path.dirname(TESTS_DIR)
REPO = os.path.dirname(DISPATCHER_DIR)
for p in (DISPATCHER_DIR, os.path.join(REPO, "harness")):
    sys.path.insert(0, p)
import inference_registry as ir  # noqa: E402
import model_swap as ms  # noqa: E402

ARCH = "standard-engineering"
T0 = 1_800_000_000.0

REGISTRY = """\
version: 1
governor_prefixes: [deepseek-v4-flash, qwen36-local, nephthys-qwen14b, hathor-llama8b]
families:
  qwen36-local:
    blob: q.gguf
    serving: llama-server
    port: 8095
    health_path: /health
    swap: unit
  hathor-llama8b:
    blob: h.gguf
    serving: llama-server
    port: 8096
    health_path: /health
    swap: unit
  nephthys-qwen14b:
    blob: "qwen3:14b"
    serving: ollama
    port: 11434
    health_path: /api/ps
    swap: implicit
  llama-implicit:
    blob: "llama3.1:8b"
    serving: ollama
    port: 11434
    health_path: /api/ps
    swap: implicit
nodes:
  boxa:
    state: live
    tailscale_ip: 10.0.0.1
    plug_id: p1
    power_policy: always-on
    models:
      - family: qwen36-local
        resident: true
        slots: 2
        served_ctx: 32768
        first_frame_s: 900
        prefill_tps: 60
        load_s: 300
        unit_stop_s: 10
        capabilities:
          standard-engineering: {trusted: true, source: declared}
          mechanical-edit: {trusted: true, source: declared}
      - family: hathor-llama8b
        resident: false
        slots: 2
        served_ctx: 32768
        first_frame_s: 900
        prefill_tps: 14
        load_s: 240
        capabilities:
          standard-engineering: {trusted: true, source: declared}
          swap-probe: {trusted: true, source: declared}
  boxb:
    state: live
    tailscale_ip: 10.0.0.2
    plug_id: p2
    power_policy: warm
    models:
      - family: nephthys-qwen14b
        resident: true
        slots: 2
        served_ctx: 32768
        first_frame_s: 2400
        prefill_tps: 7
        load_s: null
        capabilities:
          standard-engineering: {trusted: true, source: declared}
      - family: llama-implicit
        resident: false
        slots: 2
        served_ctx: 32768
        first_frame_s: 1500
        prefill_tps: 13
        load_s: null
        capabilities:
          standard-engineering: {trusted: true, source: declared}
  boxc:
    state: live
    tailscale_ip: 10.0.0.3
    plug_id: p3
    power_policy: warm
    models:
      - family: qwen36-local
        id: qwen36-local.boxc
        resident: true
        slots: 2
        served_ctx: 8192
        first_frame_s: 900
        prefill_tps: 55
        load_s: 300
        capabilities:
          standard-engineering: {trusted: true, source: declared}
"""


class FakePorts:
    def __init__(self):
        self.calls, self.t = [], T0
        self.healthy_after = 0        # probes that fail before the target answers
        self.probes = 0
        self.stop_rc = 0

    def now(self):
        return self.t

    def clock(self):
        return self.t

    def sleep(self, s):
        self.t += s

    def ssh(self, node, cmd, timeout=120, input=None):
        self.calls.append(("ssh", node, cmd))
        return (self.stop_rc if " stop " in cmd else 0), ""

    def probe(self, url):
        self.calls.append(("probe", url))
        self.probes += 1
        return self.probes > self.healthy_after

    def ssh_verbs(self):
        return [c[2].split()[3] + " " + c[2].split()[4] for c in self.calls if c[0] == "ssh"]


class Env:
    def __init__(self, tmp_path, monkeypatch):
        monkeypatch.delenv("FOREMAN_MODEL_SWAP", raising=False)
        self.reg_path = str(tmp_path / "inference_nodes.yaml")
        self.state_path = str(tmp_path / "swap_state.json")
        with open(self.reg_path, "w") as f:
            f.write(REGISTRY)
        self.ports, self.events = FakePorts(), []

    @property
    def reg(self):
        return ir.load(self.reg_path)

    def edit(self, old, new):
        with open(self.reg_path) as f:
            text = f.read()
        assert old in text
        with open(self.reg_path, "w") as f:
            f.write(text.replace(old, new, 1))

    def state(self):
        return ms.load_state(self.state_path)

    def seed(self, node="boxa", **fields):
        st = self.state()
        st.setdefault(node, {}).update(fields)
        ms.save_state(st, self.state_path)

    def idle(self):
        self.seed(idle_since=T0 - 600)

    def step(self, want=3, family="hathor-llama8b", in_flight=0, extra=(), dry_run=False):
        """One pass with `want` ready tickets whose best candidate is `family` on boxa."""
        ready = [{"archetype": "swap-probe"}] * want + list(extra)
        sig = ms.Signals(lambda node: in_flight if node == "boxa" else 0, ready)
        return ms.ModelSwap(ports=self.ports, registry_path=self.reg_path, state_path=self.state_path,
                            emit=lambda kind, **kw: self.events.append((kind, kw))).step(sig, dry_run=dry_run)

    def of(self, kind):
        return [kw for k, kw in self.events if k == kind]


@pytest.fixture
def env(tmp_path, monkeypatch):
    return Env(tmp_path, monkeypatch)


# --- routing (§7.1) --------------------------------------------------------------------------------

def names(reg, **kw):
    return [c["model"] for c in ms.candidates(reg, ARCH, **kw)]


def test_router_names_family_dot_node_ranked_by_prefill_when_nothing_is_measured(env):
    # boxa/qwen36 60, boxc/qwen36 55, boxa/hathor 14, boxb/llama-implicit 13, boxb/nephthys 7
    assert names(env.reg) == ["qwen36-local.boxa", "qwen36-local.boxc", "hathor-llama8b.boxa",
                              "llama-implicit.boxb", "nephthys-qwen14b.boxb"]


def test_measured_success_outranks_prefill_and_unmeasured_sorts_last(env):
    env.edit("""      - family: nephthys-qwen14b
        resident: true
        slots: 2
        served_ctx: 32768
        first_frame_s: 2400
        prefill_tps: 7
        load_s: null
        capabilities:
          standard-engineering: {trusted: true, source: declared}""",
             """      - family: nephthys-qwen14b
        resident: true
        slots: 2
        served_ctx: 32768
        first_frame_s: 2400
        prefill_tps: 7
        load_s: null
        capabilities:
          standard-engineering: {trusted: true, source: declared, n: 12, success: 0.9}""")
    env.edit("""        id: qwen36-local.boxc
        resident: true
        slots: 2
        served_ctx: 8192
        first_frame_s: 900
        prefill_tps: 55
        load_s: 300
        capabilities:
          standard-engineering: {trusted: true, source: declared}""",
             """        id: qwen36-local.boxc
        resident: true
        slots: 2
        served_ctx: 8192
        first_frame_s: 900
        prefill_tps: 55
        load_s: 300
        capabilities:
          standard-engineering: {trusted: true, source: measured, n: 20, success: 0.6}""")
    got = names(env.reg)
    assert got[:2] == ["nephthys-qwen14b.boxb", "qwen36-local.boxc"]      # 0.9 then 0.6
    assert got[2:] == ["qwen36-local.boxa", "hathor-llama8b.boxa", "llama-implicit.boxb"]


def test_served_ctx_below_the_prompt_estimate_is_skipped(env):
    assert "qwen36-local.boxc" in names(env.reg, prompt_tokens_est=8192)
    assert "qwen36-local.boxc" not in names(env.reg, prompt_tokens_est=8193)
    assert names(env.reg, prompt_tokens_est=40000) == []


def test_a_row_with_no_measured_served_ctx_cannot_hold_a_known_prompt(env):
    env.edit("served_ctx: 8192", "served_ctx: null")
    assert "qwen36-local.boxc" in names(env.reg)
    assert "qwen36-local.boxc" not in names(env.reg, prompt_tokens_est=100)


def test_untrusted_and_out_of_service_rows_are_not_candidates(env):
    env.edit("standard-engineering: {trusted: true, source: declared}\n          mechanical-edit",
             "standard-engineering: {trusted: false, source: declared}\n          mechanical-edit")
    assert "qwen36-local.boxa" not in names(env.reg)
    for state in ("down", "draining", "cold", "retired"):
        env.edit("state: live\n    tailscale_ip: 10.0.0.3", f"state: {state}\n    tailscale_ip: 10.0.0.3")
        assert "qwen36-local.boxc" not in names(env.reg)
        env.edit(f"state: {state}\n    tailscale_ip: 10.0.0.3", "state: live\n    tailscale_ip: 10.0.0.3")


def test_route_takes_the_best_candidate_that_can_serve_now(env):
    pick = ms.route(env.reg, {"archetype": ARCH})
    assert pick["model"] == "qwen36-local.boxa" and pick["backend"] == "qwen36-local"
    # the backend id is what local_capacity accounts slots under; an explicit `id` wins over the family
    full = ms.route(env.reg, {"archetype": ARCH}, usable=lambda c: c["node"] != "boxa")
    assert full["model"] == "qwen36-local.boxc" and full["backend"] == "qwen36-local.boxc"


def test_route_skips_a_non_resident_unit_family_and_says_so(env):
    assert [c["model"] for c in ms.candidates(env.reg, "swap-probe")] == ["hathor-llama8b.boxa"]
    assert ms.route(env.reg, {"archetype": "swap-probe"}) is None


def test_route_returns_the_skipped_unit_candidates_ahead_of_the_winner(env):
    env.edit("prefill_tps: 14", "prefill_tps: 99")           # hathor-llama8b.boxa now outranks all
    pick = ms.route(env.reg, {"archetype": ARCH})
    assert pick["model"] == "qwen36-local.boxa" and pick["skipped"] == ["hathor-llama8b.boxa"]


def test_an_implicit_family_is_routed_without_any_unit_swap(env):
    env.edit("prefill_tps: 13", "prefill_tps: 99")           # llama-implicit.boxb, non-resident, best
    env.edit("prefill_tps: 60", "prefill_tps: 5")
    pick = ms.route(env.reg, {"archetype": ARCH})
    assert pick["model"] == "llama-implicit.boxb" and pick["swap"] == "implicit" and not pick["resident"]
    # and the swap step never touches a unit on its behalf, however many tickets want it
    env.ports.calls.clear()
    env.step(want=0, extra=[{"archetype": ARCH}] * 5)
    assert not [c for c in env.ports.calls if c[0] == "ssh"]
    assert not env.of("swap_admitted") and not env.of("swap_refused")


# --- swap refusal (§7.3) ---------------------------------------------------------------------------

def refused(env):
    out = env.of("swap_refused")
    assert len(out) == 1, out
    return out[0]


def test_admitted_when_every_condition_holds_and_the_unit_is_swapped_in_order(env):
    env.idle()
    res = env.step()
    assert res["ok"] is True and res["to"] == "hathor-llama8b.boxa" and res["swap_cost_s"] == 250
    assert env.ports.ssh_verbs() == ["stop llama-qwen36.service", "start llama-hathor-llama8b.service"]
    assert not env.of("swap_refused")
    assert ir.load(env.reg_path)["nodes"]["boxa"]["models"][1]["resident"] is True
    assert ir.load(env.reg_path)["nodes"]["boxa"]["models"][0]["resident"] is False
    st = env.state()["boxa"]
    assert st["last_swap_ts"] == T0 and st["swap_log"] == [T0]


def test_refused_while_a_slot_is_in_flight_and_the_window_resets(env):
    env.idle()
    env.step(in_flight=1)
    assert "in_flight" in refused(env)["failed"]
    assert not env.ports.calls
    assert env.state()["boxa"]["idle_since"] is None


def test_refused_until_the_node_has_been_idle_for_swap_settle_s(env):
    env.step()                                            # first pass sees it idle: window opens
    assert refused(env)["failed"] == ["settle"]
    assert env.state()["boxa"]["idle_since"] == T0
    env.events.clear()
    env.ports.t = T0 + ms.SWAP_SETTLE_S - 1
    env.step()
    assert refused(env)["failed"] == ["settle"]
    env.events.clear()
    env.ports.t = T0 + ms.SWAP_SETTLE_S
    assert env.step()["ok"] is True


def test_refused_when_fewer_than_swap_min_queue_tickets_want_the_target(env):
    env.idle()
    env.step(want=ms.SWAP_MIN_QUEUE - 1)
    f = refused(env)
    assert f["failed"] == ["queue"] and "2 ready" in f["detail"] and not env.ports.calls
    env.events.clear()
    assert env.step(want=ms.SWAP_MIN_QUEUE)["ok"] is True


def test_refused_while_a_ready_ticket_still_wants_the_resident_family(env):
    env.idle()
    env.step(extra=[{"archetype": "mechanical-edit"}])
    f = refused(env)
    assert f["failed"] == ["resident"] and "qwen36-local" in f["detail"] and not env.ports.calls


def test_refused_inside_swap_cool_s_then_admitted_after_it(env):
    env.seed(idle_since=T0 - 600, last_swap_ts=T0 - ms.SWAP_COOL_S + 1)
    env.step()
    f = refused(env)
    assert f["failed"] == ["cool"] and not env.ports.calls
    env.events.clear()
    env.seed(last_swap_ts=T0 - ms.SWAP_COOL_S)
    assert env.step()["ok"] is True


def test_refused_at_max_swaps_per_day_counting_a_rolling_24h(env):
    log = [T0 - 86000 + i for i in range(ms.MAX_SWAPS_PER_DAY)]
    env.seed(idle_since=T0 - 600, last_swap_ts=T0 - 7200, swap_log=log)
    env.step()
    assert refused(env)["failed"] == ["cap"]
    env.events.clear()
    env.seed(swap_log=[T0 - 86401] + log[1:])              # one aged out of the window
    assert env.step()["ok"] is True


def test_a_null_load_s_means_no_swap_is_ever_attempted(env):
    env.idle()
    env.edit("load_s: 240", "load_s: null")
    env.step()
    f = refused(env)
    assert f["failed"] == ["load_s"] and "no measured load_s" in f["detail"] and not env.ports.calls
    assert f["swap_cost_s"] is None


def test_every_failing_condition_is_named_on_one_line(env):
    env.seed(last_swap_ts=T0 - 60)
    env.step(want=1, in_flight=2, extra=[{"archetype": "mechanical-edit"}])
    assert refused(env)["failed"] == ["in_flight", "queue", "resident", "cool"]


def test_kill_switch_logs_the_admission_and_moves_no_unit(env, monkeypatch):
    env.idle()
    monkeypatch.setenv("FOREMAN_MODEL_SWAP", "off")
    res = env.step()
    assert res["ok"] is None and env.of("swap_admitted")[0]["mode"] == "off"
    assert not env.ports.calls and not env.state()["boxa"].get("last_swap_ts")


def test_dry_run_moves_no_unit_and_writes_no_state(env):
    env.idle()
    before = env.state()
    assert env.step(dry_run=True)["ok"] is None
    assert not env.ports.calls and env.state() == before


def test_a_failed_swap_restarts_the_resident_unit_and_still_counts(env):
    env.idle()
    env.ports.healthy_after = 10 ** 6
    res = env.step()
    assert res["ok"] is False
    assert env.ports.ssh_verbs() == ["stop llama-qwen36.service", "start llama-hathor-llama8b.service",
                                     "stop llama-hathor-llama8b.service", "start llama-qwen36.service"]
    assert env.of("swap_failed")[0]["to"] == "hathor-llama8b.boxa"
    assert ir.load(env.reg_path)["nodes"]["boxa"]["models"][0]["resident"] is True
    assert env.state()["boxa"]["swap_log"] == [T0]
    env.events.clear()
    env.step()
    assert refused(env)["failed"] == ["settle", "cool"]


def test_in_flight_by_node_places_units_and_counts_the_unplaceable_everywhere(env):
    counts = ms.in_flight_by_node(env.reg, ["qwen36-local@low", "qwen36-local.boxc", "local-worker"])
    assert counts == {"boxa": 2, "boxb": 1, "boxc": 2}


# --- the dispatcher hook ---------------------------------------------------------------------------

class Log:
    def __init__(self):
        self.lines = []

    def emit(self, kind, **kw):
        self.lines.append((kind, kw))


def _hook(env, monkeypatch, running=(), archetype=ARCH):
    import foreman_dispatcher as fd
    import local_capacity
    monkeypatch.setattr(ir, "REGISTRY_YAML", env.reg_path)
    names_ = ("qwen36-local", "qwen36-local.boxc", "nephthys-qwen14b")
    backends = {name: {"health_url": f"http://{name}/h", "slots": 2} for name in names_}
    backends["local-worker"] = {"group": list(names_)}
    cap = local_capacity.LocalCapacity(backends, lambda: list(running), probe=lambda url: True)
    ctx = type("Ctx", (), {"log": Log()})()
    r = {"t": {"archetype": archetype, "title": "t", "detail": "d"}, "cand": {"id": 7}}
    return fd._routed_member(ctx, r, cap, "local-worker"), ctx


def test_dispatcher_resolves_the_group_to_the_registry_ranked_member(env, monkeypatch):
    member, ctx = _hook(env, monkeypatch)
    assert member == "qwen36-local"
    kw = ctx.log.lines[0][1]
    assert kw["model"] == "qwen36-local.boxa" and kw["member"] == "qwen36-local" and kw["ticket"] == 7


def test_dispatcher_falls_to_the_next_ranked_member_with_a_free_slot(env, monkeypatch):
    member, _ = _hook(env, monkeypatch, running=["qwen36-local"] * 2)
    assert member == "qwen36-local.boxc"


def test_dispatcher_hands_back_to_pick_member_when_the_registry_has_no_opinion(env, monkeypatch):
    assert _hook(env, monkeypatch, archetype="unlisted")[0] is None


def test_dispatcher_hook_fails_open_and_logs_when_the_registry_is_unreadable(env, monkeypatch):
    os.remove(env.reg_path)
    member, ctx = _hook(env, monkeypatch)
    assert member is None and ctx.log.lines[0][1]["reason"] == "route_failed"
