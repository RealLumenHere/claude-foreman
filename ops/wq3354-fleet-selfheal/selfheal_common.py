"""fleet-selfheal shared plumbing (wq#3354).

One systemctl surface, one state file, and the three escalation outlets every class
uses: a wq ticket (a build-lane ticket is what the orchestrator dispatches an fworker
on, so filing it IS the auto-dispatch), an aq row, and an ntfy page. Class modules take
a Ctx, so tests swap the outside world for fakes and --dry-run swaps every mutation
for a logged "would".

Ticket dedup is per episode: `ctx.file_once(key, ...)` files at most one ticket per key
until the class calls `ctx.resolve(key)` because the condition cleared. A failure that
persists for a week produces one ticket, not 10,080.
"""
import fnmatch
import json
import os
import re
import subprocess
import time

HERE = os.path.dirname(os.path.abspath(__file__))
WQ = os.environ.get("FLEET_SELFHEAL_WQ", "/home/lumen/bin/wq")
AQ = os.environ.get("FLEET_SELFHEAL_AQ", "/home/lumen/bin/aq")
NTFY = os.environ.get("FLEET_SELFHEAL_NTFY", "/home/lumen/bin/ntfy-send")
NTFY_TOPIC = os.environ.get("FLEET_SELFHEAL_NTFY_TOPIC", "lumen-alerts")
STATE_PATH = os.environ.get("FLEET_SELFHEAL_STATE",
                            "/home/lumen/agents/fleet-selfheal/state.json")
CONFIG_PATH = os.environ.get("FLEET_SELFHEAL_CONFIG", os.path.join(HERE, "selfheal.yaml"))
AQ_SOURCE = "fleet-selfheal"
TICKET_OWNER = "lumen"   # wq's sqlite must never pick up root-owned WAL/journal files


def run(argv, timeout=30, input=None):
    """CompletedProcess, or None when the command could not run at all."""
    try:
        return subprocess.run(argv, capture_output=True, text=True, timeout=timeout,
                              input=input)
    except (OSError, subprocess.SubprocessError):
        return None


def as_lumen(argv):
    """Run `argv` as the lumen user when we are root (the sweep is), unchanged otherwise."""
    if os.geteuid() == 0:
        return ["runuser", "-u", TICKET_OWNER, "--"] + list(argv)
    return list(argv)


def load_config(path=None):
    import yaml
    with open(path or CONFIG_PATH) as f:
        return yaml.safe_load(f) or {}


def load_state(path=None):
    try:
        with open(path or STATE_PATH) as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save_state(state, path=None):
    path = path or STATE_PATH
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(state, f, indent=1, sort_keys=True)
    os.replace(tmp, path)


def matches_any(name, patterns):
    return any(fnmatch.fnmatch(name, p) for p in patterns or ())


class Systemd:
    """The only object that shells out to systemctl. Tests substitute a fake."""

    def show(self, unit, props):
        return self.show_many([unit], props).get(unit, {})

    def show_many(self, units, props):
        """{unit: {prop: value}} for every unit, in one systemctl call. `Id` is always
        requested so each blank-line-separated block can be attributed."""
        units = list(units)
        if not units:
            return {}
        props = ["Id"] + [p for p in props if p != "Id"]
        p = run(["systemctl", "show", "--no-pager", "-p", ",".join(props)] + units,
                timeout=60)
        out = {}
        if p is None or p.returncode != 0:
            return out
        for block in (p.stdout or "").split("\n\n"):
            vals = dict(line.split("=", 1) for line in block.splitlines() if "=" in line)
            if vals.get("Id"):
                out[vals["Id"]] = vals
        return out

    def list_units(self, unit_type, state=None):
        argv = ["systemctl", "list-units", f"--type={unit_type}", "--all",
                "--no-legend", "--plain"]
        if state:
            argv.append(f"--state={state}")
        p = run(argv)
        if p is None or p.returncode != 0:
            return []
        return sorted({ln.split()[0] for ln in (p.stdout or "").splitlines()
                       if ln.split() and ln.split()[0].endswith("." + unit_type)})

    def _act(self, verb, unit, timeout=120):
        p = run(["systemctl", verb, unit], timeout=timeout)
        return bool(p and p.returncode == 0)

    def start(self, unit):
        return self._act("start", unit)

    def stop(self, unit):
        return self._act("stop", unit)

    def reset_failed(self, unit):
        return self._act("reset-failed", unit, timeout=30)


class Outlets:
    """wq / aq / ntfy. Never raises: an outlet fault must not stop the sweep."""

    def __init__(self, wq=WQ, aq=AQ, ntfy=NTFY, runner=run):
        self.wq, self.aq, self.ntfy, self.runner = wq, aq, ntfy, runner

    def file_ticket(self, title, detail, lane, pri):
        p = self.runner(as_lumen([self.wq, "add", "--lane", lane, "--pri", pri,
                                  "--title", title[:180], "--detail", detail]), timeout=60)
        if p is None or p.returncode != 0:
            return None
        m = re.search(r"added task (\d+)", p.stdout or "")
        return m.group(1) if m else "?"

    def alert(self, key, title, msg):
        self.runner([self.aq, "add", AQ_SOURCE, key, title, msg], timeout=15)

    def page(self, priority, title, msg):
        self.runner([self.ntfy, "-t", NTFY_TOPIC, "-p", priority, "-T", title,
                     "--quiet", msg], timeout=15)

    def find_open_ticket(self, contains):
        """id of the first non-done/released ticket whose title contains every string
        in `contains` (case-insensitive), or None. One `wq list` call; other detectors
        (e.g. litellm-completion-probe, wq#3169) file the same root cause under a
        different episode key, so file_once's own dedup never sees it (wq#3577 duplicated
        wq#3557 this way)."""
        p = self.runner(as_lumen([self.wq, "list"]), timeout=30)
        if p is None or p.returncode != 0:
            return None
        for line in (p.stdout or "").splitlines():
            m = re.match(r"^\[(\d+)\]\s+\S+\s+\S+\s+(\S+)\s+(.*)$", line)
            if not m:
                continue
            tid, status, title = m.group(1), m.group(2), m.group(3)
            if status in ("done", "released"):
                continue
            low = title.lower()
            if all(c.lower() in low for c in contains):
                return tid
        return None


class Ctx:
    """Everything a class needs. `actions` is the human-readable record of what this
    sweep did (or, under dry_run, would have done)."""

    def __init__(self, cfg, state, systemd=None, outlets=None, now=None, dry_run=False,
                 runner=run):
        self.cfg, self.state = cfg, state
        self.systemd = systemd or Systemd()
        self.out = outlets or Outlets(runner=runner)
        self.now = time.time() if now is None else now
        self.dry_run = dry_run
        self.runner = runner
        self.actions = []

    def section(self, name):
        return self.state.setdefault(name, {})

    def note(self, cls, msg):
        self.actions.append(f"[{cls}] " + ("would " if self.dry_run else "") + msg)

    def act(self, cls, msg, fn, *args):
        """Run a mutating call unless dry_run. Returns its result (True under dry_run)."""
        self.note(cls, msg)
        return True if self.dry_run else fn(*args)

    def file_once(self, cls, key, title, detail, lane=None, pri=None, page=None):
        """One ticket + aq row (+ optional page) per open episode of `key`."""
        tickets = self.section("tickets")
        if key in tickets:
            return tickets[key].get("id")
        lane = lane or self.cfg.get("ticket_lane", "build")
        pri = pri or self.cfg.get("ticket_pri", "p1")
        detail = detail + f"\n\nFiled automatically by fleet-selfheal (wq#3354) class {cls}."
        self.note(cls, f"file {lane}/{pri} ticket: {title}")
        if self.dry_run:
            return None
        tid = self.out.file_ticket(title, detail, lane, pri)
        self.out.alert(key, title, f"wq#{tid}: " + detail[:400] if tid else detail[:400])
        if page:
            self.out.page(page, title, detail[:400])
        if tid:
            tickets[key] = {"id": tid, "at": int(self.now), "title": title}
        return tid

    def resolve(self, cls, key, msg=None):
        """The episode is over: forget the ticket so the next one can file."""
        rec = self.section("tickets").pop(key, None)
        if rec is not None:
            self.note(cls, f"resolved {key} (was wq#{rec.get('id')})")
            if msg and not self.dry_run:
                self.out.page("low", f"fleet-selfheal: {key} cleared", msg)
