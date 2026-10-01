"""CLASS 2 — service crash-loop (wq#3354).

systemd's own start limiter (5 starts / 10 s by default) never trips on a unit with
RestartSec=15, so `Restart=always` units crash-loop forever and nobody is told. Every
sweep samples NRestarts for every loaded service. More than `max_restarts` automatic
restarts inside `window_s` is a crash-loop:

  * one ticket + aq row + page per episode;
  * the unit is stopped and a wq#967 holddown flag is written (restore_by =
    hold_hours out), so lumen-self-healer does not restart what we just stopped and
    restores it on its own once the hold lapses. A pool that routes on health
    (LiteLLM, the dispatcher's backend probes) routes around a stopped backend;
  * units on the `never_stop` list (the paths Frank reaches Lumen through, the
    healers themselves) are alerted and ticketed but left running.

NRestarts only counts systemd's automatic restarts, so a manual restart or a
timer-fired oneshot never looks like a loop. A counter that went backwards (the unit
was stopped/started by hand) resets that unit's history.
"""
import datetime
import os
import pwd

from selfheal_common import TICKET_OWNER, matches_any

CLS = "crashloop"


def _hold(unit, reason, hours, now):
    import holddown_lib
    restore_by = (datetime.datetime.fromtimestamp(now, datetime.timezone.utc)
                  + datetime.timedelta(hours=hours)).strftime("%Y-%m-%d %H:%M:%S")
    holddown_lib.write_flag(unit, reason, restore_by, owner_agent="fleet-selfheal",
                            was_enabled=True)
    try:  # written as root; hand it to lumen so `holddown drop` works without sudo
        pw = pwd.getpwnam(TICKET_OWNER)
        os.chown(holddown_lib.flag_path(unit), pw.pw_uid, pw.pw_gid)
    except (KeyError, OSError):
        pass


def sweep(ctx, hold=_hold):
    c = ctx.cfg.get("crashloop", {})
    window = int(c.get("window_s", 300))
    limit = int(c.get("max_restarts", 3))
    never_stop = c.get("never_stop", [])
    ignore = c.get("ignore", [])
    samples = ctx.section("crashloop_samples")
    looping = ctx.section("crashloop_active")

    units = [u for u in ctx.systemd.list_units("service") if not matches_any(u, ignore)]
    info = ctx.systemd.show_many(units, ["NRestarts", "ActiveState"])
    for unit in units:
        try:
            n = int(info.get(unit, {}).get("NRestarts", ""))
        except ValueError:
            continue
        hist = [s for s in samples.get(unit, []) if ctx.now - s[0] <= window]
        if hist and n < hist[-1][1]:
            hist = []
        hist.append([int(ctx.now), n])
        samples[unit] = hist[-12:]
        delta = n - hist[0][1]

        if delta > limit and unit not in looping:
            looping[unit] = {"since": int(ctx.now), "nrestarts": n}
            protected = matches_any(unit, never_stop)
            how = ("left RUNNING: it is on the never_stop list (a path Frank reaches "
                   "Lumen through, or a healer), so a human decides" if protected else
                   f"STOPPED by fleet-selfheal with a {c.get('hold_hours', 12)}h holddown "
                   f"flag; lumen-self-healer restores it when the hold lapses. Once "
                   f"fixed, bring it back sooner with `systemctl start {unit}` then "
                   f"`holddown drop {unit}`")
            tid = ctx.file_once(
                CLS, f"crashloop:{unit}",
                f"[selfheal] {unit} crash-looping: {delta} restarts in {window // 60} min",
                f"{unit} restarted {delta} times in the last {window}s "
                f"(NRestarts={n}, ActiveState={info[unit].get('ActiveState')}). It was "
                f"{how}. Root-cause from `journalctl -u {unit} -n 200`.",
                page="urgent")
            if not protected:
                ctx.act(CLS, f"stop {unit} + holddown", _stop_and_hold, ctx, unit,
                        c, tid, hold)
        elif unit in looping and delta == 0 and len(hist) > 1 \
                and info[unit].get("ActiveState") == "active" \
                and ctx.now - looping[unit]["since"] >= window:
            looping.pop(unit)
            ctx.resolve(CLS, f"crashloop:{unit}",
                        f"{unit}: no automatic restarts in the last {window}s.")

    for unit in list(samples):
        if unit not in info:
            samples.pop(unit)


def _stop_and_hold(ctx, unit, c, tid, hold):
    """Flag first, then stop: the healer must never see the unit down without the flag."""
    hold(unit, f"crash-loop (wq#3354 fleet-selfheal, ticket wq#{tid})",
         float(c.get("hold_hours", 12)), ctx.now)
    return ctx.systemd.stop(unit)
