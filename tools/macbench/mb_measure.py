"""The measurement sequence for one warm row. One JSON record per measurement.

Prompts carry a fresh nonce so nothing is served from a prefix cache, except the
deliberate 4k repeat that measures reuse. Token counts and rates are the server's own.
"""
import threading
import time
import uuid

UNIT = "The dispatcher scans open tickets and decides which to spawn. "
USER = "Reply with the single word OK."
PREFILL_SIZES = (512, 4096, 8192)
CONC_TOKENS = 2048
DECODE_PROMPT = "Count from 1 to 200 separated by commas, no other text."
DECODE_TOKENS = 128
SCHEMA = "macbench/1"


def make_prompt(units):
    return uuid.uuid4().hex + " " + UNIT * units


class Measurer:
    def __init__(self, engine, node, row, sampler, sink, run_id, clock=time.time):
        self.e, self.node, self.row = engine, node, row
        self.sampler, self.sink, self.run_id, self.clock = sampler, sink, run_id, clock
        self.tokens_per_unit = None

    def _emit(self, phase, t0, t1, **fields):
        rec = {"schema": SCHEMA, "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t1)),
               "run": self.run_id, "node": self.node.name, "row": self.row.id,
               "row_digest": self.row.digest(), "model": self.row.model,
               "quant": self.row.quant, "engine": self.row.engine, "phase": phase}
        rec.update(fields)
        rec.update(self.sampler.window(t0, t1) if self.sampler else {})
        rss = self.e.resident_bytes()
        if rss:
            rec["resident_bytes"] = rss
        self.sink(rec)
        return rec

    def _call(self, system, user, max_tokens):
        t0 = self.clock()
        r = self.e.complete(system, user, max_tokens)
        return r, t0, self.clock()

    def calibrate(self):
        """Tokens per filler unit, from two tiny probes (server prompt counts)."""
        counts = []
        for units in (1, 9):
            r, _, _ = self._call(make_prompt(units), USER, 1)
            counts.append(r["prompt_total_n"] or r["prompt_new_n"])
        self.tokens_per_unit = max((counts[1] - counts[0]) / 8, 1.0)
        return self.tokens_per_unit

    def _units_for(self, tokens):
        return max(1, round(tokens / self.tokens_per_unit))

    def prefill(self, target, prompt=None, phase=None):
        prompt = prompt or make_prompt(self._units_for(target))
        r, t0, t1 = self._call(prompt, USER, 16)
        self._emit(phase or f"prefill_{target}", t0, t1, target_tokens=target, **r)
        return prompt, r

    def prefix_reuse(self, target, prompt, first):
        r, t0, t1 = self._call(prompt, USER, 16)
        total = r["prompt_total_n"] or first["prompt_total_n"] or first["prompt_new_n"]
        cached = r["cached_n"]
        if cached is None:
            cached = max(0, (first["prompt_total_n"] or first["prompt_new_n"]) - r["prompt_new_n"])
        self._emit(f"prefix_reuse_{target}", t0, t1, target_tokens=target,
                   cached_tokens=cached, reuse_frac=round(cached / total, 3) if total else None,
                   first_prompt_s=first["prompt_s"], **r)
        return r

    def decode(self):
        r, t0, t1 = self._call(DECODE_PROMPT, USER, DECODE_TOKENS)
        self._emit("decode", t0, t1, **r)

    def concurrent(self, tokens=CONC_TOKENS):
        """Solo baseline, then two simultaneous fresh prompts: does prefill time-slice?"""
        solo, t0, t1 = self._call(make_prompt(self._units_for(tokens)), USER, 16)
        solo_wall = t1 - t0
        self._emit("prefill_solo_conc", t0, t1, target_tokens=tokens, **solo)
        results = [None, None]

        def worker(i):
            results[i] = self._call(make_prompt(self._units_for(tokens)), USER, 16)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(2)]
        p0 = self.clock()
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        p1 = self.clock()
        pair_wall = p1 - p0
        self._emit("concurrent_x2", p0, p1, target_tokens=tokens, solo_wall_s=round(solo_wall, 2),
                   pair_wall_s=round(pair_wall, 2),
                   slowdown=round(pair_wall / solo_wall, 2) if solo_wall else None,
                   solo_prompt_tps=solo["prompt_tps"],
                   pair_prompt_tps=[r[0]["prompt_tps"] for r in results],
                   pair_prompt_s=[r[0]["prompt_s"] for r in results])

    def run(self):
        """Warm, then the full sequence. Raises on engine failure; caller records row_error."""
        t0 = self.clock()
        self.e.complete("warm", USER, 4)
        self._emit("warm", t0, self.clock())
        self.calibrate()
        self.prefill(512)
        prompt, first = self.prefill(4096)
        self.prefix_reuse(4096, prompt, first)
        self.prefill(8192)
        self.decode()
        self.concurrent()
