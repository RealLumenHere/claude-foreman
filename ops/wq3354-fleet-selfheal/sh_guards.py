"""CLASS 1 — guard timer down (wq#3354).

A guard is only as good as the timer that fires it. wq#1705's guard-meta-watchdog
restarts a guard SERVICE that crashed; nothing restarted a guard TIMER that stopped
(a `systemctl stop`, a failed daemon-reload, an install that never enabled it), and a
stopped timer leaves no failed unit for anyone to see. This class:

  timer inactive          -> `systemctl start` it, up to max_attempts sweeps in a row
  still inactive after    -> build-lane ticket (orchestrator dispatches an fworker) + page
  timer missing (not-found) for a named guard -> ticket, nothing to restart
  guard service failed for longer than service_failed_after_s
                          -> ticket. wq#1705 owns the restart; this is the escalation it
                             lacks (it only raises aq rows).

A unit under an active holddown flag (wq#967) is a deliberate pause and is left alone.
"""
from selfheal_common import matches_any

CLS = "guards"


def _holddown_shielded():
    try:
        import holddown_lib
        return holddown_lib.shielded_units()
    except Exception:  # noqa: BLE001 — no flag dir / lib means nothing is held
        return set()


def guard_timers(ctx):
    """Named guard timers plus every loaded timer matching a guard name pattern."""
    gcfg = ctx.cfg.get("guards", {})
    named = list(gcfg.get("timers", []))
    found = [t for t in ctx.systemd.list_units("timer")
             if t not in named and _is_guard_name(t, gcfg)]
    return named, named + found


def _is_guard_name(unit, gcfg):
    return (matches_any(unit, gcfg.get("patterns", []))
            and not matches_any(unit, gcfg.get("exclude", [])))


def sweep(ctx, shielded=None):
    gcfg = ctx.cfg.get("guards", {})
    max_attempts = int(gcfg.get("max_attempts", 3))
    failed_after = int(gcfg.get("service_failed_after_s", 1800))
    shielded = _holddown_shielded() if shielded is None else shielded
    named, timers = guard_timers(ctx)
    info = ctx.systemd.show_many(timers, ["LoadState", "ActiveState", "UnitFileState", "Unit"])
    services = {t: info.get(t, {}).get("Unit") or t[:-len(".timer")] + ".service"
                for t in timers}
    svc_info = ctx.systemd.show_many(sorted(set(services.values())),
                                     ["ActiveState", "Result"])
    attempts = ctx.section("guard_attempts")
    failed_since = ctx.section("guard_service_failed_since")

    for timer in timers:
        base = timer[:-len(".timer")]
        if base in shielded or timer in shielded:
            continue
        t = info.get(timer, {})
        if t.get("LoadState") != "loaded":
            if timer in named:
                ctx.file_once(CLS, f"guard-missing:{timer}",
                              f"[selfheal] guard timer {timer} is not installed "
                              f"(LoadState={t.get('LoadState') or '?'})",
                              f"{timer} is on the fleet-selfheal guard list but systemd "
                              f"cannot load it, so it has nothing to restart. Reinstall it "
                              f"from its ops bundle or drop it from selfheal.yaml guards.timers.")
            continue
        ctx.resolve(CLS, f"guard-missing:{timer}")

        if t.get("ActiveState") == "active":
            if attempts.pop(timer, None) is not None:
                ctx.note(CLS, f"{timer} active again")
            ctx.resolve(CLS, f"guard-timer-down:{timer}", f"{timer} is active again.")
        else:
            n = attempts.get(timer, 0)
            if n < max_attempts:
                attempts[timer] = n + 1
                ok = ctx.act(CLS, f"start {timer} (attempt {n + 1}/{max_attempts}, "
                                  f"was {t.get('ActiveState')})",
                             ctx.systemd.start, timer)
                if ok and not ctx.dry_run:
                    ctx.out.alert(f"guard-timer-restarted:{timer}",
                                  f"guard timer {timer} was stopped; restarted",
                                  f"ActiveState was {t.get('ActiveState')}, "
                                  f"UnitFileState={t.get('UnitFileState')}")
            else:
                ctx.file_once(CLS, f"guard-timer-down:{timer}",
                              f"[selfheal] guard timer {timer} will not stay up after "
                              f"{max_attempts} restarts",
                              f"fleet-selfheal started {timer} {max_attempts} sweeps in a row "
                              f"and it is still {t.get('ActiveState')} "
                              f"(UnitFileState={t.get('UnitFileState')}). Find why: "
                              f"`systemctl status {timer}`, `journalctl -u {timer}`.",
                              page="high")

        svc = services[timer]
        s = svc_info.get(svc, {})
        if s.get("ActiveState") == "failed":
            since = failed_since.setdefault(svc, int(ctx.now))
            if ctx.now - since >= failed_after:
                ctx.file_once(CLS, f"guard-service-failed:{svc}",
                              f"[selfheal] guard {svc} has been failed for "
                              f"{int((ctx.now - since) // 60)} min",
                              f"{svc} (fired by {timer}) is in failed state "
                              f"(Result={s.get('Result')}) and has stayed there past the "
                              f"{failed_after // 60} min wq#1705 restart window. Either "
                              f"the guard crashes every run or it keeps exiting nonzero on "
                              f"a standing finding nobody fixed; `journalctl -u {svc}` "
                              f"says which. Fix the cause, not the unit state.",
                              page="high")
        elif failed_since.pop(svc, None) is not None:
            ctx.resolve(CLS, f"guard-service-failed:{svc}", f"{svc} recovered.")
