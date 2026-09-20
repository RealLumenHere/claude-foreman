"""Walk a matrix on ONE node: sequential, resumable, restores what was resident."""
import json
import os
import signal
import time
import uuid

from mb_engines import OllamaEngine, check_can_start, make_engine
from mb_matrix import Row, Sweep, resolve_sweep
from mb_measure import Measurer
from mb_node import MIN_FREE_GB, Refused, Sampler


class Results:
    """Append-only JSONL. `done` maps row id -> digest of the last completed measurement."""

    def __init__(self, path):
        self.path = path
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)

    def write(self, rec):
        with open(self.path, "a") as fh:
            fh.write(json.dumps(rec, sort_keys=True) + "\n")

    def records(self):
        if not os.path.exists(self.path):
            return []
        out = []
        with open(self.path) as fh:
            for ln in fh:
                try:
                    out.append(json.loads(ln))
                except ValueError:
                    pass          # a torn last line from a killed run is not a result
        return out

    def done(self):
        return {r["row"]: r["row_digest"] for r in self.records() if r.get("phase") == "row_done"}

    def metric(self, row_id, metric):
        vals = [r.get(metric) for r in self.records()
                if r.get("row") == row_id and r.get("phase") in ("decode", "agentic_summary")
                and r.get(metric) is not None]
        return vals[-1] if vals else None


def best_candidate(sweep, results):
    scored = [(results.metric(c, sweep.metric), c) for c in sweep.of]
    scored = [(v, c) for v, c in scored if v is not None]
    return max(scored)[1] if scored else None


def plan_rows(matrix, results):
    """Flat run order with sweeps expanded from what is measured so far.
    Returns [(row_or_None, note)] - a sweep with no winner yet stays a placeholder."""
    out = []
    for item in matrix.items:
        if isinstance(item, Row):
            out.append((item, None))
            continue
        winner = best_candidate(item, results)
        if winner is None:
            out.append((item, f"pending: best of {item.of} by {item.metric}, then {item.quants}"))
            continue
        rows, skipped = resolve_sweep(item, matrix, winner)
        out.extend((r, f"sweep of {winner}") for r in rows)
        out.extend((_Skipped(i, why), why) for i, why in skipped)
    return out


class _Skipped:
    def __init__(self, id_, why):
        self.id, self.why = id_, why


def describe(node, row):
    eng = make_engine(node, row)
    cmd = " ".join(eng.argv()) if row.engine == "llama-server" else \
        f"ollama {row.tag} options={eng._options()}"
    return cmd


class Runner:
    def __init__(self, node, matrix, results, allow_serving_group=False, only=None,
                 http=None, sampler_interval=10.0, log=print):
        self.node, self.matrix, self.results = node, matrix, results
        self.allow, self.only, self.http = allow_serving_group, only, http
        self.sampler_interval, self.log = sampler_interval, log
        self.run_id = time.strftime("%Y%m%dT%H%M%S") + "-" + uuid.uuid4().hex[:6]

    def _rec(self, phase, row, **kw):
        self.results.write({"schema": "macbench/1", "run": self.run_id, "node": self.node.name,
                            "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                            "row": row.id, "row_digest": getattr(row, "digest", lambda: None)(),
                            "phase": phase, **kw})

    def guards(self):
        self.node.guard_identity()
        member = self.node.guard_serving_group(self.allow)
        self.node.guard_disk()
        return member

    def _admin(self):
        stub = type("Stub", (), {"tag": None, "flags": {}, "id": "admin", "model": None})()
        return OllamaEngine(self.node, stub, self.http)

    def run_row(self, row):
        eng = make_engine(self.node, row, self.http)
        check_can_start(eng)
        try:
            present = eng.has_model()
        except Refused as e:            # row misconfigured for this node: skip it, keep walking
            return self._rec("row_skipped", row, reason=str(e))
        if not present:
            if not row.fetch and row.engine == "llama-server":
                return self._rec("row_skipped", row, reason="model missing and row has no fetch source")
            self.node.guard_disk(row.size_gb)
            self._rec("fetch", row)
            eng.fetch()
            if not eng.has_model():
                return self._rec("row_error", row, error="model still missing after fetch")
        sampler = Sampler(self.node, self.sampler_interval).start()
        self._rec("row_start", row, flags=row.flags)
        try:
            eng.start()
            Measurer(eng, self.node, row, sampler, self.results.write, self.run_id).run()
            self._rec("row_done", row)
        finally:
            sampler.stop()
            try:
                eng.stop()
            except Exception as e:      # an unload failure must not mask the row's own outcome
                self.log(f"WARN unload {row.id}: {e!r}")

    def run(self):
        member = self.guards()
        admin = self._admin()
        residents = admin.residents()
        self._rec("run_start", _Skipped("-", None), residents=residents, serving_group=member)
        prev = signal.signal(signal.SIGTERM, lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()))
        try:
            for r in residents:
                admin.unload(r["name"])
            while True:
                item = self._next()
                if item is None:
                    break
                self.log(f"row {item.id}")
                try:
                    self.run_row(item)
                except Refused:
                    raise
                except Exception as e:
                    self._rec("row_error", item, error=repr(e))
                    self.log(f"row {item.id} failed: {e!r}")
        finally:
            signal.signal(signal.SIGTERM, prev)
            try:
                admin.restore(residents)
                self._rec("run_end", _Skipped("-", None), restored=[r["name"] for r in residents])
            except Exception as e:
                self.log(f"WARN restore failed: {e!r}")

    def _next(self):
        """The next runnable row, re-planned each time so sweeps see fresh results."""
        done = self.results.done()
        attempted = {r["row"] for r in self.results.records()
                     if r.get("run") == self.run_id and r.get("phase") in
                     ("row_error", "row_skipped", "row_done")}
        for item, _note in plan_rows(self.matrix, self.results):
            if isinstance(item, (Sweep, _Skipped)):
                continue
            if self.only and item.id not in self.only:
                continue
            if item.id in attempted or done.get(item.id) == item.digest():
                continue
            return item
        return None
