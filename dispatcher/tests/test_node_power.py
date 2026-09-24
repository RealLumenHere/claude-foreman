"""Design §8 T7 (cold wake) and T8 (drain, power-down, anti-flap) — wq#2254, C6 of wq#2241.

A scratch repo root carries the real registry with isis turned into a cold node (measured_ready_s 120,
idle 103.2 W, plug p1). FakePorts stands in for ssh, plug-ctl, fleet-inv, the health probe and lumen_state, keeps
one ordered call log, and runs on virtual time (`sleep` advances the clock), so the ordering the design
insists on (§6.3: plug-ctl off strictly last) is an assertion on that log.
"""
import json
import os
import shutil
import sys

import pytest
import yaml

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
DISPATCHER_DIR = os.path.dirname(TESTS_DIR)
REPO = os.path.dirname(DISPATCHER_DIR)
for p in (DISPATCHER_DIR, os.path.join(REPO, "harness")):
    sys.path.insert(0, p)
import inference_registry as ir  # noqa: E402
import node_power as np_  # noqa: E402
import node_render as nr  # noqa: E402

PASS_S = 1200
TARGETS = (nr.DISPATCHER_CONFIG, nr.MODELS_YAML) + nr.PREFIX_FILES
COLD = "isis"
COLD_ID = "qwen36-local"
HOT_IDS = ("hathor-llama8b", "nephthys-qwen14b")


class FakePorts:
    def __init__(self):
        self.calls, self.t = [], 1_800_000_000.0
        self.plugmap = {"hathor": "P3", "nephthys": "P2", COLD: "p1"}
        self.hostname = {COLD: COLD}
        self.watts = 103.2
        self.ssh_up = {COLD: False}
        self.boot_s, self.booted_at = 120, None
        self.poweroff_hangs, self.stop_rc = False, 0
        self.running = []
        self.on_ssh = None

    # ordered call log -------------------------------------------------------------------------
    def kinds(self):
        return list(self.calls)

    def plug_calls(self, verb):
        return [c for c in self.calls if c[0] == "plug" and c[2] == verb]

    # fleet-inv ---------------------------------------------------------------------------------
    def plugs(self):
        return dict(self.plugmap)

    # ssh ---------------------------------------------------------------------------------------
    def ssh(self, node, cmd, timeout=120, input=None):
        self.calls.append(("ssh", node, cmd))
        if self.on_ssh:
            self.on_ssh(cmd)
        if cmd == "hostname":
            return (0, self.hostname[node] + "\n") if self.ssh_up[node] else (255, "")
        if "systemctl stop" in cmd:
            return self.stop_rc, ""
        if "poweroff" in cmd:
            if not self.poweroff_hangs:
                self.ssh_up[node] = False
            return 0, ""
        return (0, "") if self.ssh_up[node] else (255, "")

    # plug-ctl ----------------------------------------------------------------------------------
    def plug(self, plug_id, verb):
        self.calls.append(("plug", plug_id, verb))
        if verb == "on":
            self.booted_at = self.t
        if verb == "watts":
            return 0, f"{plug_id} True {self.watts}\n"
        return 0, ""

    def probe(self, url):
        self.calls.append(("probe", url))
        up = self.booted_at is not None and self.t - self.booted_at >= self.boot_s
        if up:
            self.ssh_up[COLD] = True
        return up

    def running_local_models(self):
        return list(self.running)

    def state_write(self, subject, predicate, value):
        # power_action is JSON (json.dumps by _action); remediation_hold is deliberately a plain string
        # (wq#1981 — power-remediate's guard reads its leading word as text, never JSON). Store either as-is.
        try:
            parsed = json.loads(value)
        except ValueError:
            parsed = value
        self.calls.append(("state_write", subject, predicate, parsed))

    def now(self):
        return self.t

    def clock(self):
        return self.t

    def sleep(self, s):
        self.t += s


class Env:
    def __init__(self, tmp_path, monkeypatch):
        self.root = str(tmp_path / "repo")
        self.registry = os.path.join(self.root, "research", "inference_nodes.yaml")
        self.state_path = str(tmp_path / "runs" / "power_state.json")
        os.makedirs(os.path.dirname(self.registry))
        for rel in TARGETS:
            os.makedirs(os.path.dirname(os.path.join(self.root, rel)), exist_ok=True)
            shutil.copy(os.path.join(REPO, rel), os.path.join(self.root, rel))
        head, tail = open(os.path.join(REPO, "research", "inference_nodes.yaml")).read().split(f"  {COLD}:\n")
        assert "\n  hathor:" not in tail and "\n  nephthys:" not in tail   # isis is the last row
        for old, new in (("state: down", "state: cold"), ("power_policy: warm", "power_policy: cold"),
                         ("measured_ready_s: null", "measured_ready_s: 120")):
            assert old in tail
            tail = tail.replace(old, new, 1)
        with open(self.registry, "w") as f:
            f.write(head + f"  {COLD}:\n" + tail)
        nr.apply(ir.load(self.registry), self.root)
        self.ports, self.events = FakePorts(), []
        self.free = {i: 0 for i in HOT_IDS + (COLD_ID,)}
        monkeypatch.delenv("FOREMAN_NODE_POWER", raising=False)

    def emit(self, kind, **kw):
        self.events.append((kind, kw))

    def kinds(self, name):
        return [kw for k, kw in self.events if k == name]

    def state(self):
        with open(self.state_path) as f:
            return json.load(f)

    def seed(self, **fields):
        os.makedirs(os.path.dirname(self.state_path), exist_ok=True)
        cur = {}
        if os.path.exists(self.state_path):
            cur = self.state()
        cur.setdefault(COLD, {}).update(fields)
        with open(self.state_path, "w") as f:
            json.dump(cur, f)

    def reg_state(self, node=COLD):
        return ir.load(self.registry)["nodes"][node]["state"]

    def set_reg(self, old, new):
        text = open(self.registry).read()
        assert old in text
        with open(self.registry, "w") as f:
            f.write(text.replace(old, new, 1))

    def one_pass(self, slots_full=0, pending=0, running=None, dry_run=False):
        """One dispatcher pass 1200 s after the last, with the hot members full whenever the lane is starved."""
        self.ports.t += PASS_S
        models = self.ports.running if running is None else running
        sig = np_.Signals(slots_full, lambda b: self.free.get(b, 0), lambda: list(models), pending)
        return np_.run(sig, dry_run=dry_run, emit=self.emit, ports=self.ports, registry_path=self.registry,
                       root=self.root, state_path=self.state_path)

    def starved(self, n=2):
        return [self.one_pass(slots_full=3, pending=3) for _ in range(n)][-1]


@pytest.fixture
def env(tmp_path, monkeypatch):
    return Env(tmp_path, monkeypatch)


# --- T7: cold wake ------------------------------------------------------------------------------

def test_one_starved_pass_triggers_nothing_two_wake_exactly_once(env):
    assert env.one_pass(slots_full=3, pending=3) is None
    assert env.ports.plug_calls("on") == []
    assert env.state()[COLD]["starved_passes"] == 1
    act = env.one_pass(slots_full=3, pending=3)
    assert act == {"node": COLD, "verb": "wake", "ok": True}
    assert env.ports.plug_calls("on") == [("plug", "p1", "on")]
    assert env.reg_state() == "live"
    st = env.state()[COLD]
    assert st["wakes_today"] == 1 and st["starved_passes"] == 0 and st["last_wake_ts"] > 0
    env.one_pass(slots_full=3, pending=3)     # awake now: a third starved pass wakes nothing further
    assert len(env.ports.plug_calls("on")) == 1


def test_wake_polls_health_then_records_the_action_in_lumen_state(env):
    env.starved()
    calls = env.ports.kinds()
    assert calls.index(("plug", "p1", "on")) < calls.index(next(c for c in calls if c[0] == "probe"))
    (rec,) = [c for c in env.ports.calls if c[0] == "state_write" and c[2] == "power_action"]
    assert rec[1:3] == (COLD, "power_action")
    assert rec[3]["verb"] == "on" and rec[3]["plug"] == "p1" and rec[3]["trigger"] == "starvation"
    assert rec[3]["starved_passes"] == 2 and rec[3]["local_slots_full"] == 3
    assert env.kinds("power_action")[0]["verb"] == "on"


def test_wake_lifts_the_remediation_hold_before_ac_moves(env):
    """wq#1981: power-remediate must not fight a wake it should instead help rescue if it stalls."""
    act = env.starved()
    hold_calls = [c for c in env.ports.calls if c[0] == "state_write" and c[2] == "remediation_hold"]
    (hold,) = hold_calls
    assert hold[1] == COLD and hold[3].startswith("off - ")
    on_idx = env.ports.calls.index(("plug", "p1", "on"))
    assert env.ports.calls.index(hold) < on_idx
    assert act == {"node": COLD, "verb": "wake", "ok": True}


def test_no_wake_while_a_live_member_has_a_free_slot_or_slots_full_is_one(env):
    env.free["hathor-llama8b"] = 1
    for _ in range(3):
        env.one_pass(slots_full=3, pending=3)
    env.free["hathor-llama8b"] = 0
    for _ in range(3):
        env.one_pass(slots_full=1, pending=1)
    assert env.ports.plug_calls("on") == []


def test_measured_ready_null_is_never_picked(env):
    env.set_reg("measured_ready_s: 120", "measured_ready_s: null")
    env.starved(4)
    assert env.ports.plug_calls("on") == []
    assert any("measured_ready_s unset" in kw["reasons"] for kw in env.kinds("wake_blocked"))


def test_a_full_day_of_wakes_blocks(env):
    now = env.ports.t + PASS_S
    env.seed(wake_log=[now - 3600 * k for k in (1, 5, 9, 23)], starved_passes=1)
    env.one_pass(slots_full=3, pending=3)
    assert env.ports.plug_calls("on") == []
    assert any("wakes_today" in r for kw in env.kinds("wake_blocked") for r in kw["reasons"])
    now = env.ports.t + PASS_S
    env.seed(wake_log=[now - 3600 * k for k in (1, 5, 9, 25)], starved_passes=1)   # one has aged out
    env.one_pass(slots_full=3, pending=3)
    assert len(env.ports.plug_calls("on")) == 1


def test_recent_drain_blocks_until_cool_s_passes(env):
    env.seed(starved_passes=1, last_drain_ts=env.ports.t + PASS_S - 600)
    env.one_pass(slots_full=3, pending=3)
    assert env.ports.plug_calls("on") == []
    env.seed(starved_passes=1, last_drain_ts=env.ports.t + PASS_S - 901)
    env.one_pass(slots_full=3, pending=3)
    assert len(env.ports.plug_calls("on")) == 1


def test_kill_switch_blocks_the_act_but_logs_the_decision(env, monkeypatch):
    monkeypatch.setenv("FOREMAN_NODE_POWER", "off")
    assert env.starved(3) is None
    assert env.ports.plug_calls("on") == [] and env.reg_state() == "cold"
    dec = env.kinds("power_decision")
    assert dec and dec[0]["verb"] == "wake" and dec[0]["acted"] is False and "kill_switch" in dec[0]["blocked"]
    assert env.state()[COLD]["wakes_today"] == 0


def test_dry_run_decides_but_moves_nothing_and_writes_no_counters(env):
    env.one_pass(slots_full=3, pending=3, dry_run=True)
    env.one_pass(slots_full=3, pending=3, dry_run=True)
    assert env.ports.plug_calls("on") == [] and not os.path.exists(env.state_path)


def test_health_timeout_powers_the_node_back_off_and_excludes_it_a_day(env):
    env.ports.boot_s = 10 ** 6            # never answers its health URL
    env.ports.ssh_up[COLD] = True         # the OS booted; only the model unit did not come up
    env.starved()
    assert env.ports.plug_calls("on") and env.ports.plug_calls("off")
    assert env.ports.kinds().index(("plug", "p1", "off")) > env.ports.kinds().index(("plug", "p1", "on"))
    assert env.reg_state() == "down"
    st = env.state()[COLD]
    assert st["excluded_until"] - env.ports.t <= np_.EXCLUDE_S and st["excluded_until"] > env.ports.t
    n_on = len(env.ports.plug_calls("on"))
    env.ports.boot_s = 0
    env.starved(2)                        # still inside the 24 h exclusion
    assert len(env.ports.plug_calls("on")) == n_on
    env.ports.t += np_.EXCLUDE_S
    env.starved(2)
    assert len(env.ports.plug_calls("on")) == n_on + 1 and env.reg_state() == "live"


# --- T8: drain, power-down, anti-flap -----------------------------------------------------------

def awake(env, last_wake_ago=7200):
    env.ports.ssh_up[COLD], env.ports.booted_at = True, env.ports.t - 10 ** 6
    env.set_reg("state: cold", "state: live")
    env.seed(last_wake_ts=env.ports.t - last_wake_ago)
    nr.apply(ir.load(env.registry), env.root)


def idle_passes(env, n=2, **kw):
    return [env.one_pass(**kw) for _ in range(n)][-1]


def test_a_running_unit_blocks_power_off(env):
    awake(env)
    idle_passes(env, 4, running=[COLD_ID])
    assert env.ports.plug_calls("off") == [] and env.state()[COLD]["idle_passes"] == 0
    assert env.reg_state() == "live"


def test_ready_local_work_blocks_power_off(env):
    awake(env)
    idle_passes(env, 4, pending=1)
    assert env.ports.plug_calls("off") == []


def test_an_unattributable_unit_blocks_power_off(env):
    awake(env)
    idle_passes(env, 4, running=["local-worker"])
    assert env.ports.plug_calls("off") == []


def test_idle_inside_min_warm_s_does_not_power_off(env):
    awake(env, last_wake_ago=1000)
    idle_passes(env, 2)
    assert env.ports.plug_calls("off") == []
    assert any("min_warm_s" in kw["blocked"] for kw in env.kinds("power_decision"))


def test_one_idle_pass_is_not_enough(env):
    awake(env)
    env.one_pass()
    assert env.ports.plug_calls("off") == []


def test_six_steps_fire_in_order_with_plug_off_strictly_last(env):
    awake(env)
    seen = {}
    env.ports.on_ssh = lambda cmd: seen.setdefault(cmd.split()[-1] if "stop" in cmd else cmd, env.reg_state())
    act = idle_passes(env, 2)
    assert act == {"node": COLD, "verb": "off", "ok": True}
    calls = env.ports.kinds()
    stop = next(i for i, c in enumerate(calls) if c[0] == "ssh" and "systemctl stop llama-qwen36.service" in c[2])
    off = next(i for i, c in enumerate(calls) if c[0] == "ssh" and "systemctl poweroff" in c[2])
    gone = max(i for i, c in enumerate(calls) if c[0] == "ssh" and c[2] == "true")
    plug_off = calls.index(("plug", "p1", "off"))
    assert stop < off < gone < plug_off
    assert plug_off == max(i for i, c in enumerate(calls) if c[0] in ("plug", "ssh"))   # nothing follows AC
    assert seen["llama-qwen36.service"] == "draining"      # step 1 ran (and rendered) before step 3
    assert env.reg_state() == "cold"
    st = env.state()[COLD]
    assert st["last_drain_ts"] == env.ports.t and st["idle_passes"] == 0
    (rec,) = [c for c in env.ports.calls if c[0] == "state_write" and c[2] == "power_action"
              and c[3]["verb"] == "off"]
    assert rec[1:3] == (COLD, "power_action") and rec[3]["plug"] == "p1"
    (hold,) = [c for c in env.ports.calls if c[0] == "state_write" and c[2] == "remediation_hold"]
    assert hold[1] == COLD and hold[3].startswith("on - ")
    assert plug_off < calls.index(hold)  # wq#1981: held only after AC is actually off


def test_draining_render_removes_the_node_from_the_dispatcher_view(env):
    awake(env)
    seen = {}
    env.ports.on_ssh = lambda cmd: "stop" in cmd and seen.setdefault(
        "cfg", open(os.path.join(env.root, nr.DISPATCHER_CONFIG)).read())
    idle_passes(env, 2)
    assert COLD_ID not in json.loads(seen["cfg"])["local_backends"]
    assert COLD_ID in json.loads(open(os.path.join(env.root, nr.DISPATCHER_CONFIG)).read())["local_backends"]


def test_always_on_and_warm_nodes_are_never_candidates(env):
    awake(env)
    env.seed(last_wake_ts=env.ports.t + 10 ** 6)          # keep the cold node inside its dwell floor
    idle_passes(env, 6)
    assert set(json.loads(open(env.state_path).read())) == {COLD}
    assert not [c for c in env.ports.calls if c[0] == "plug" and c[1] in ("P3", "P2")]
    assert env.reg_state("hathor") == "live" and env.reg_state("nephthys") == "live"


@pytest.mark.parametrize("what", ["wrong_plug", "wrong_host", "zero_watts", "far_watts", "no_idle_watts",
                                  "ssh_down"])
def test_interlock_refuses_and_leaves_everything_as_it_was(env, what):
    awake(env)
    p = env.ports
    if what == "wrong_plug":
        p.plugmap[COLD] = "p9"
    elif what == "wrong_host":
        p.hostname[COLD] = "hathor"
    elif what == "zero_watts":
        p.watts = 0
    elif what == "far_watts":
        p.watts = 103.2 * 1.31
    elif what == "no_idle_watts":
        env.set_reg("idle_watts: 103.2", "idle_watts: null")
    else:
        p.ssh_up[COLD] = False
    idle_passes(env, 2)
    assert p.plug_calls("off") == []
    assert not [c for c in p.calls if c[0] == "ssh" and ("stop" in c[2] or "poweroff" in c[2])]
    assert env.reg_state() == "live"
    assert env.kinds("power_refused")
    refusals = [c for c in p.calls if c[0] == "state_write" and c[3]["verb"] == "off_refused"]
    assert refusals and refusals[0][3]["reasons"]


def test_interlock_passes_on_the_boundary_of_thirty_percent(env):
    awake(env)
    env.ports.watts = 103.2 * 1.29
    assert idle_passes(env, 2)["ok"] is True


def test_a_unit_that_started_between_signal_and_stop_aborts_and_rearms(env):
    awake(env)
    env.ports.running = [COLD_ID]
    sig_models = []           # the pass's signal saw an idle node; the fresh read at step 2 does not
    env.one_pass(running=sig_models)
    act = env.one_pass(running=sig_models)
    assert act == {"node": COLD, "verb": "off", "ok": False}
    assert env.ports.plug_calls("off") == [] and env.reg_state() == "live"
    assert env.state()[COLD]["idle_passes"] == 0
    assert not [c for c in env.ports.calls if c[0] == "ssh" and "stop" in c[2]]


def test_a_failed_unit_stop_aborts_before_the_os_is_told_to_halt(env):
    awake(env)
    env.ports.stop_rc = 1
    idle_passes(env, 2)
    assert env.ports.plug_calls("off") == [] and env.reg_state() == "live"
    assert not [c for c in env.ports.calls if c[0] == "ssh" and "poweroff" in c[2]]


def test_ssh_that_never_stops_answering_leaves_the_plug_on(env):
    awake(env)
    env.ports.poweroff_hangs = True
    idle_passes(env, 2)
    assert env.ports.plug_calls("off") == []
    assert env.reg_state() == "down" and "excluded_until" in env.state()[COLD]
    assert any(kw["reason"] == "power_down_stuck" for kw in env.kinds("power_error"))


def _plug_off_or_live_with_os_off(env):
    """The invariant behind every failure test: plug off never with the OS answering, and never `live`
    for a box whose OS was told to halt."""
    return env.ports.plug_calls("off"), env.reg_state()


def test_binding_flip_after_poweroff_is_stuck_not_restored_live(env):
    awake(env)

    def flip(cmd):
        if "poweroff" in cmd:
            env.ports.plugmap[COLD] = "p9"
    env.ports.on_ssh = flip
    assert idle_passes(env, 2) == {"node": COLD, "verb": "off", "ok": False}
    assert _plug_off_or_live_with_os_off(env) == ([], "down")
    assert "excluded_until" in env.state()[COLD]
    assert any(kw["reason"] == "power_down_stuck" for kw in env.kinds("power_error"))
    assert not env.kinds("power_error") or all(kw["reason"] != "power_down_aborted" for kw in env.kinds("power_error"))


def test_plug_inventory_unreadable_after_poweroff_is_stuck(env):
    awake(env)

    def break_inventory(cmd):
        if "poweroff" in cmd:
            def boom():
                raise OSError("fleet-inv gone")
            env.ports.plugs = boom
    env.ports.on_ssh = break_inventory
    idle_passes(env, 2)
    assert _plug_off_or_live_with_os_off(env) == ([], "down")
    assert any(kw["reason"] == "power_down_stuck" for kw in env.kinds("power_error"))


def test_a_pass_killed_mid_drain_is_recovered_live_when_ssh_answers(env):
    awake(env)

    def die(cmd):
        if "systemctl stop" in cmd:
            raise SystemExit(143)
    env.ports.on_ssh = die
    with pytest.raises(SystemExit):
        idle_passes(env, 2)
    assert env.reg_state() == "draining"
    env.ports.on_ssh = None
    act = env.one_pass()
    assert act == {"node": COLD, "verb": "recover", "ok": True, "to": "live"}
    assert env.reg_state() == "live" and env.ports.plug_calls("off") == []
    assert env.state()[COLD]["idle_passes"] == 0
    assert any(k["reason"] == "draining_orphan_recovered" for k in env.kinds("power_error"))
    assert COLD_ID in json.loads(open(os.path.join(env.root, nr.DISPATCHER_CONFIG)).read())["local_backends"]


def test_a_pass_killed_mid_drain_is_recovered_down_and_excluded_when_ssh_is_dead(env):
    awake(env)

    def die(cmd):
        if "poweroff" in cmd:
            raise SystemExit(143)
    env.ports.on_ssh = die
    with pytest.raises(SystemExit):
        idle_passes(env, 2)
    env.ports.on_ssh, env.ports.ssh_up[COLD] = None, False
    act = env.one_pass()
    assert act == {"node": COLD, "verb": "recover", "ok": True, "to": "down"}
    assert env.reg_state() == "down" and env.state()[COLD]["excluded_until"] > env.ports.t
    for _ in range(6):                                   # excluded: no wake, and no plug off ever
        env.one_pass(slots_full=3, pending=3)
    assert env.ports.plug_calls("off") == [] and env.ports.plug_calls("on") == []


def test_recovery_is_read_only_under_dry_run(env):
    awake(env)
    import registry_edit as ed
    with open(env.registry) as f:
        text = f.read()
    with open(env.registry, "w") as f:
        f.write(ed.set_field(text, COLD, "state", "draining"))
    before = env.state()
    assert env.one_pass(dry_run=True) is None
    assert env.reg_state() == "draining" and env.state() == before and env.ports.plug_calls("off") == []


def test_wake_timeout_on_a_box_whose_os_never_booted_leaves_the_plug_on(env):
    """Design §6.2 amended: with ssh dead the interlock cannot tell a dead OS from a dead network, so the
    plug stays on; the node is down, excluded and the refusal is on the audit trail."""
    env.ports.boot_s = 10 ** 6
    env.ports.ssh_up[COLD] = False
    env.starved()
    assert env.ports.plug_calls("on") and env.ports.plug_calls("off") == []
    assert env.reg_state() == "down" and env.state()[COLD]["excluded_until"] > env.ports.t
    (refusal,) = env.kinds("power_refused")
    assert refusal["trigger"] == "wake_failed" and any("ssh reports hostname None" in r for r in refusal["reasons"])
    assert any(c[3]["verb"] == "off_refused" for c in env.ports.calls
               if c[0] == "state_write" and c[2] == "power_action" and len(c) > 3)
    # wq#1981: the hold was lifted for this wake attempt and, since the plug stayed on with the node
    # unresponsive, is deliberately left lifted — power-remediate is now free to try to rescue it.
    (hold,) = [c for c in env.ports.calls if c[0] == "state_write" and c[2] == "remediation_hold"]
    assert hold[3].startswith("off - ")


def test_wake_deadline_is_capped_to_what_a_pass_can_watch(env):
    env.set_reg("measured_ready_s: 120", "measured_ready_s: 200")     # 2x = 400 > the cap
    env.ports.boot_s = 10 ** 6
    env.ports.ssh_up[COLD] = True
    seen = []
    probe = env.ports.probe
    env.ports.probe = lambda u: seen.append(env.ports.t) or probe(u)
    env.starved()
    span = seen[-1] - seen[0]
    assert np_.WAKE_DEADLINE_CAP_S - np_.HEALTH_POLL_S <= span <= np_.WAKE_DEADLINE_CAP_S
    assert (np_.WAKE_DEADLINE_CAP_S + np_.HEALTH_POLL_S + np_.SSH_DOWN_DEADLINE_S + np_.SETTLE_S
            + np_.PASS_RESERVE_S <= np_.PASS_TIMEOUT_S)


def test_a_node_too_slow_to_watch_inside_a_pass_is_not_woken(env):
    env.set_reg("measured_ready_s: 120", f"measured_ready_s: {np_.WAKE_DEADLINE_CAP_S}")
    env.starved(4)
    assert env.ports.plug_calls("on") == [] and env.reg_state() == "cold"
    assert any("longest wake one pass can watch" in r for kw in env.kinds("wake_blocked") for r in kw["reasons"])


def test_a_24_hour_oscillating_trace_makes_at_most_max_wakes_power_cycles(env):
    starved_for, idle_for, wakes, offs = 3, 6, 0, 0
    for i in range(72):                                   # 72 passes x 20 min
        busy = (i % (starved_for + idle_for)) < starved_for
        env.free[COLD_ID] = 0 if env.reg_state() == "cold" else 2
        env.one_pass(slots_full=3 if busy else 0, pending=3 if busy else 0)
    wakes, offs = len(env.ports.plug_calls("on")), len(env.ports.plug_calls("off"))
    assert 1 <= wakes <= np_.MAX_WAKES_PER_DAY
    assert offs <= wakes


def test_thor_has_no_registry_row_and_a_missing_node_cannot_be_powered():
    reg = ir.load()
    assert "thor" not in reg["nodes"]
    with pytest.raises(KeyError):
        np_.NodePower(ports=FakePorts()).power_down("thor", "test")


def test_system_ports_speak_plug_ctl_in_lower_case_and_refusal_is_a_port_error(monkeypatch):
    import subprocess
    import types
    import node_ports
    argv = []
    monkeypatch.setattr(subprocess, "run", lambda a, **kw: argv.append(a) or types.SimpleNamespace(
        returncode=0, stdout="p3 True 86.6\n"))
    assert node_ports.SystemPorts.plug(object(), "P3", "watts") == (0, "p3 True 86.6\n")
    assert argv == [[node_ports.PLUG_CTL, "p3", "watts"]]
    ports = node_ports.SystemPorts.__new__(node_ports.SystemPorts)
    monkeypatch.setattr(np_.NodePower, "power_down", lambda self, node, trigger, manage_state: {"ok": False})
    with pytest.raises(node_ports.PortError):
        ports.power_off("isis")


# --- dispatcher wiring --------------------------------------------------------------------------

def test_dispatcher_step_feeds_the_pass_signals_and_survives_a_power_failure(monkeypatch):
    import types
    import foreman_dispatcher as fd
    events, seen = [], {}

    class Log:
        def emit(self, event, **kw):
            events.append((event, kw))

    cap = types.SimpleNamespace(free_slots=lambda b: 0)
    ctx = types.SimpleNamespace(
        capacity=lambda: cap, local_slots_full=3, spawned={"local": 1, "cloud": 0}, dry_run=False, log=Log(),
        refresh_units=lambda: seen.setdefault("refreshed", True), local_unit_models=lambda: ["x"])

    def fake_run(sig, dry_run, emit):
        seen.update(full=sig.local_slots_full, pending=sig.ready_pending, models=sig.running_models(),
                    free=sig.free_slots("any"), dry=dry_run)
        emit("power_action", node="n")
    monkeypatch.setattr(np_, "run", fake_run)
    fd._node_power_step(ctx, {"local": [1, 2, 3, 4], "cloud": []})
    assert seen == {"full": 3, "pending": 3, "models": ["x"], "free": 0, "dry": False, "refreshed": True}
    assert events == [("info", {"stage": "node_power", "what": "power_action", "node": "n"})]

    def boom(sig, dry_run, emit):
        raise RuntimeError("plug unreachable")
    monkeypatch.setattr(np_, "run", boom)
    fd._node_power_step(ctx, {"local": [], "cloud": []})
    assert events[-1][0] == "error" and events[-1][1]["reason"] == "step_failed"

    ctx.capacity = lambda: None            # FOREMAN_LOCAL_CAPACITY=static: no health view, no power decisions
    n = len(events)
    fd._node_power_step(ctx, {"local": [], "cloud": []})
    assert len(events) == n
