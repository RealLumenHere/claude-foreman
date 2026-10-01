"""Fakes for the fleet-selfheal tests: systemd, the wq/aq/ntfy outlets, a process."""


class FakeSystemd:
    def __init__(self, units=None, timers=None, services=None):
        self.props = dict(units or {})        # unit -> {prop: value}
        self.timers = list(timers or [])
        self.services = list(services or [])
        self.calls = []

    def show_many(self, units, props):
        return {u: dict(self.props[u], Id=u) for u in units if u in self.props}

    def show(self, unit, props):
        return self.show_many([unit], props).get(unit, {})

    def list_units(self, unit_type, state=None):
        return self.timers if unit_type == "timer" else self.services

    def start(self, unit):
        self.calls.append(("start", unit))
        return True

    def stop(self, unit):
        self.calls.append(("stop", unit))
        return True

    def reset_failed(self, unit):
        self.calls.append(("reset-failed", unit))
        return True


class FakeOutlets:
    def __init__(self):
        self.tickets, self.alerts, self.pages = [], [], []

    def file_ticket(self, title, detail, lane, pri):
        self.tickets.append({"title": title, "detail": detail, "lane": lane, "pri": pri})
        return str(9000 + len(self.tickets))

    def alert(self, key, title, msg):
        self.alerts.append((key, title))

    def page(self, priority, title, msg):
        self.pages.append((priority, title))


class Proc:
    def __init__(self, stdout="", returncode=0, stderr=""):
        self.stdout, self.returncode, self.stderr = stdout, returncode, stderr
