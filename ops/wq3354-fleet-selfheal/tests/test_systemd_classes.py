"""CLASS 1 (guard timers) and CLASS 2 (crash-loops) against a fake systemd."""
from selfheal_fakes import FakeSystemd

import sh_crashloop
import sh_guards

GCFG = {"guards": {"timers": ["lumen-pat-scan.timer", "gone-guard.timer"],
                   "patterns": ["*drift*.timer"], "max_attempts": 2,
                   "service_failed_after_s": 1800}}


def _guard_sd(pat_state="inactive", svc_state="inactive"):
    return FakeSystemd(
        units={"lumen-pat-scan.timer": {"LoadState": "loaded", "ActiveState": pat_state,
                                        "UnitFileState": "enabled",
                                        "Unit": "lumen-pat-scan.service"},
               "gone-guard.timer": {"LoadState": "not-found", "ActiveState": "inactive"},
               "wq-drift-guard.timer": {"LoadState": "loaded", "ActiveState": "active",
                                        "Unit": "wq-drift-guard.service"},
               "lumen-pat-scan.service": {"ActiveState": svc_state, "Result": "exit-code"},
               "wq-drift-guard.service": {"ActiveState": "inactive"}},
        timers=["lumen-pat-scan.timer", "wq-drift-guard.timer", "unrelated.timer"])


def test_stopped_guard_timer_is_started_then_ticketed_when_it_will_not_stay_up(make_ctx):
    state = {}
    sd = _guard_sd()
    for i in range(2):
        ctx = make_ctx(GCFG, state, sd, now=1000 + 60 * i)
        sh_guards.sweep(ctx, shielded=set())
        assert ("start", "lumen-pat-scan.timer") in sd.calls
        assert not [t for t in ctx.out.tickets if "pat-scan" in t["title"]]
    ctx = make_ctx(GCFG, state, sd, now=1200)
    sh_guards.sweep(ctx, shielded=set())
    assert sd.calls.count(("start", "lumen-pat-scan.timer")) == 2   # capped at max_attempts
    [t] = [t for t in ctx.out.tickets if "pat-scan" in t["title"]]
    assert t["lane"] == "build" and ctx.out.pages
    # the episode files once
    ctx = make_ctx(GCFG, state, sd, now=1260)
    sh_guards.sweep(ctx, shielded=set())
    assert not [t for t in ctx.out.tickets if "pat-scan" in t["title"]]


def test_named_guard_missing_is_a_ticket_and_unrelated_timers_are_left_alone(make_ctx):
    sd = _guard_sd(pat_state="active")
    ctx = make_ctx(GCFG, {}, sd)
    sh_guards.sweep(ctx, shielded=set())
    assert [t["title"] for t in ctx.out.tickets] == [
        "[selfheal] guard timer gone-guard.timer is not installed (LoadState=not-found)"]
    assert not any(u == "unrelated.timer" for _, u in sd.calls)


def test_holddown_shield_is_respected(make_ctx):
    sd = _guard_sd()
    sh_guards.sweep(make_ctx(GCFG, {}, sd), shielded={"lumen-pat-scan"})
    assert ("start", "lumen-pat-scan.timer") not in sd.calls


def test_guard_service_failed_past_window_is_ticketed_and_clears(make_ctx):
    state = {}
    sd = _guard_sd(pat_state="active", svc_state="failed")
    sh_guards.sweep(make_ctx(GCFG, state, sd, now=0), shielded=set())
    ctx = make_ctx(GCFG, state, sd, now=1799)
    sh_guards.sweep(ctx, shielded=set())
    assert not [t for t in ctx.out.tickets if "pat-scan.service" in t["title"]]
    ctx = make_ctx(GCFG, state, sd, now=1800)
    sh_guards.sweep(ctx, shielded=set())
    assert [t for t in ctx.out.tickets if "lumen-pat-scan.service" in t["title"]]
    sd.props["lumen-pat-scan.service"]["ActiveState"] = "inactive"
    sh_guards.sweep(make_ctx(GCFG, state, sd, now=1900), shielded=set())
    assert "guard-service-failed:lumen-pat-scan.service" not in state["tickets"]


def test_dry_run_starts_nothing_files_nothing(make_ctx):
    sd = _guard_sd()
    ctx = make_ctx(GCFG, {}, sd, dry_run=True)
    sh_guards.sweep(ctx, shielded=set())
    assert sd.calls == [] and ctx.out.tickets == []
    assert any(a.startswith("[guards] would start lumen-pat-scan.timer") for a in ctx.actions)


# ---- CLASS 2 ----------------------------------------------------------------------

CCFG = {"crashloop": {"window_s": 300, "max_restarts": 3, "hold_hours": 12,
                      "never_stop": ["cloudflared*.service"], "ignore": []}}


def _loop(make_ctx, unit, counts, state=None, active="activating"):
    sd = FakeSystemd(units={unit: {"NRestarts": "0", "ActiveState": active}},
                     services=[unit])
    state = {} if state is None else state
    holds, ctxs = [], []
    for i, n in enumerate(counts):
        sd.props[unit]["NRestarts"] = str(n)
        ctx = make_ctx(CCFG, state, sd, now=10_000 + 60 * i)
        sh_crashloop.sweep(ctx, hold=lambda *a: holds.append(a))
        ctxs.append(ctx)
    return sd, state, holds, ctxs


def test_more_than_three_restarts_in_five_minutes_stops_holds_and_tickets(make_ctx):
    sd, state, holds, ctxs = _loop(make_ctx, "flaky.service", [0, 1, 2, 3, 4])
    [t] = [t for c in ctxs for t in c.out.tickets]
    assert "flaky.service crash-looping: 4 restarts" in t["title"]
    assert "STOPPED" in t["detail"]
    assert sd.calls == [("stop", "flaky.service")]
    assert holds and holds[0][0] == "flaky.service"
    assert ctxs[-1].out.pages[0][0] == "urgent"


def test_three_restarts_is_not_a_loop_and_slow_restarts_never_are(make_ctx):
    _, _, _, ctxs = _loop(make_ctx, "ok.service", [0, 1, 2, 3])
    assert not [t for c in ctxs for t in c.out.tickets]
    # one restart every 2 minutes for 20 minutes: never >3 inside any 5-min window
    _, _, _, ctxs = _loop(make_ctx, "slow.service", [i // 2 for i in range(20)])
    assert not [t for c in ctxs for t in c.out.tickets]


def test_never_stop_units_are_ticketed_but_left_running(make_ctx):
    sd, _, holds, ctxs = _loop(make_ctx, "cloudflared.service", [0, 2, 5])
    assert [t for c in ctxs for t in c.out.tickets]
    assert sd.calls == [] and holds == []


def test_counter_reset_by_manual_restart_is_not_a_loop(make_ctx):
    _, _, _, ctxs = _loop(make_ctx, "svc.service", [3, 0, 1, 2])
    assert not [t for c in ctxs for t in c.out.tickets]


def test_stopped_unit_keeps_its_episode_and_a_stable_one_resolves(make_ctx):
    unit = "flaky.service"
    sd, state, _, _ = _loop(make_ctx, unit, [0, 2, 5], active="inactive")
    sd.props[unit]["ActiveState"] = "inactive"      # we stopped it: not resolved
    for i in range(3, 12):
        sh_crashloop.sweep(make_ctx(CCFG, state, sd, now=10_000 + 60 * i), hold=lambda *a: 0)
    assert f"crashloop:{unit}" in state["tickets"]
    sd.props[unit]["ActiveState"] = "active"        # healer restored it, stays up
    for i in range(12, 20):
        sh_crashloop.sweep(make_ctx(CCFG, state, sd, now=10_000 + 60 * i), hold=lambda *a: 0)
    assert f"crashloop:{unit}" not in state["tickets"]
