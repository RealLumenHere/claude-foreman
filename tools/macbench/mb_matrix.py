"""Matrix file: which models to bench on a node, in what order, with what flags.

Rows run top to bottom (cheapest information first). A `sweep` row is resolved at run
time: it clones the best already-measured candidate onto other quants.
"""
import hashlib
import json

import yaml

ENGINES = ("ollama", "llama-server")
FLAG_KEYS = {"threads", "ctx", "slots", "batch", "kv_type", "thinking", "extra_args"}
# the 8k prefill probe plus template and reply headroom must fit in one slot
MIN_CTX_PER_SLOT = 8704
KV_TYPES = ("f16", "q8_0", "q4_0")
FETCH_KINDS = ("ollama_pull", "url", "copy_from")


class MatrixError(ValueError):
    pass


class Row:
    def __init__(self, raw, flags):
        self.raw = raw
        self.id = raw["id"]
        self.model = raw["model"]
        self.quant = raw.get("quant")
        self.engine = raw["engine"]
        self.tag = raw.get("tag")
        self.gguf = raw.get("gguf")
        self.size_gb = raw.get("size_gb")
        self.fetch = raw.get("fetch")
        self.agentic = raw.get("agentic") or {}
        self.variants = raw.get("variants") or {}
        self.flags = flags

    def digest(self):
        """Identity of what was measured: a row edited after its run is re-run."""
        blob = json.dumps({"raw": self.raw, "flags": self.flags}, sort_keys=True, default=str)
        return hashlib.sha1(blob.encode()).hexdigest()[:12]

    def clone(self, new_id, quant, target):
        raw = dict(self.raw, id=new_id, quant=quant)
        raw["gguf" if self.engine == "llama-server" else "tag"] = target
        raw["variants"] = {}
        raw["fetch"] = None
        return Row(raw, dict(self.flags))


class Sweep:
    """`sweep: {of: [row ids], quants: [...], metric: decode_tps}`."""

    def __init__(self, raw):
        s = raw["sweep"]
        self.id = raw["id"]
        self.of = list(s["of"])
        self.quants = list(s["quants"])
        self.metric = s.get("metric", "decode_tps")
        self.raw = raw


class Matrix:
    def __init__(self, items, defaults):
        self.items = items
        self.defaults = defaults

    def rows(self):
        return [i for i in self.items if isinstance(i, Row)]


def _check_row(row):
    where = f"row {row.id}"
    if row.engine not in ENGINES:
        raise MatrixError(f"{where}: engine {row.engine!r} not in {ENGINES}")
    bad = set(row.flags) - FLAG_KEYS
    if bad:
        raise MatrixError(f"{where}: unknown flags {sorted(bad)}")
    if row.flags.get("kv_type", "f16") not in KV_TYPES:
        raise MatrixError(f"{where}: kv_type must be one of {KV_TYPES}")
    if row.engine == "ollama":
        if not row.tag:
            raise MatrixError(f"{where}: ollama rows need `tag`")
        # OLLAMA_KV_CACHE_TYPE / OLLAMA_NUM_PARALLEL are server-wide; a row cannot set them
        if row.flags.get("kv_type", "f16") != "f16" or row.flags.get("slots"):
            raise MatrixError(f"{where}: kv_type/slots are server-level on ollama; leave unset")
        per_slot = row.flags.get("ctx", 0)
    else:
        if not row.gguf:
            raise MatrixError(f"{where}: llama-server rows need `gguf`")
        slots = int(row.flags.get("slots") or 1)
        per_slot = int(row.flags.get("ctx", 0)) // slots
    if per_slot < MIN_CTX_PER_SLOT:
        raise MatrixError(f"{where}: {per_slot} ctx per slot < {MIN_CTX_PER_SLOT} needed for the 8k probe")
    f = row.fetch
    if f is not None and f.get("kind") not in FETCH_KINDS:
        raise MatrixError(f"{where}: fetch.kind must be one of {FETCH_KINDS}")


def load_matrix(path):
    with open(path) as fh:
        doc = yaml.safe_load(fh)
    defaults = doc.get("defaults") or {}
    items, seen = [], set()
    for raw in doc["rows"]:
        if raw["id"] in seen:
            raise MatrixError(f"duplicate row id {raw['id']}")
        seen.add(raw["id"])
        if "sweep" in raw:
            items.append(Sweep(raw))
            continue
        flags = dict(defaults.get(raw["engine"]) or {})
        flags.update(raw.get("flags") or {})
        row = Row(raw, flags)
        _check_row(row)
        items.append(row)
    ids = {i.id for i in items if isinstance(i, Row)}
    for i in items:
        if isinstance(i, Sweep) and not set(i.of) <= ids:
            raise MatrixError(f"sweep {i.id}: unknown candidates {sorted(set(i.of) - ids)}")
    return Matrix(items, defaults)


def resolve_sweep(sweep, matrix, best_id):
    """Rows a sweep expands to, given the id of the winning candidate.
    Returns (rows, skipped) - skipped are (id, reason) for quants the winner has no source for."""
    by_id = {r.id: r for r in matrix.rows()}
    winner = by_id[best_id]
    rows, skipped = [], []
    for q in sweep.quants:
        target = winner.variants.get(q)
        new_id = f"{sweep.id}-{q.lower()}"
        if not target:
            skipped.append((new_id, f"{winner.id} has no `variants.{q}`"))
            continue
        rows.append(winner.clone(new_id, q, target))
    return rows, skipped
