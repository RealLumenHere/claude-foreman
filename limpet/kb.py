"""`limpet-kb` -- the offline knowledge-base front end for the Limpet appliance.

Design: ``scratch/research/re-knowledge-bases.md`` (wq#3930) §8; source synthesis
``scratch/research/research-ingestion-summary-2026-10-16.md`` §4. The design doc writes
the front end as ``limpet kb ...``; the appliance ships it as the ``limpet-kb`` binary,
one subsystem per binary (``limpet`` = plugin registry, ``limpet-workspace`` = reporting).

One KB namespace, one updater. Knowledge sources live under a single root
(``/usr/share/limpet/kb/<category>/``) and each is described by a TOML drop-in in
``kb-sources.d/`` carrying its integration **tier**:

  SHIP  -- flat files that ride on the ISO (CSV/JSON/markdown). ``update`` loads every
           present SHIP source into one read-only SQLite FTS5 index (``limpet-kb.db``)
           so ``search`` is a single offline full-text query, no DB server.
  FETCH -- downloaded on demand by ``pull <name>`` (getsploit needs a Vulners key, the
           WikiDevi/TechInfoDepot mirror is hundreds of MB). A fetch stamps a dated
           marker so ``status`` can show staleness.
  LINK  -- an online helper only (FCC-ID, iFixit); never indexed, ``list`` shows the URL.

The loader is catalog-driven, not hardcoded: which sources exist is a question of which
drop-ins ship, not of code (the one-schema-many-files posture of hardware.d/
target-devices.d, wq#3940). Mirrors ``limpet/cli.py`` conventions: ``--json`` on every
subcommand, ``--kb-root``/``--db``/``--sources-dir`` overrides of the ``/usr`` and ``/var``
defaults so a test can point them at a tmp dir (a falsifiable boundary, outside the real
install paths). Exit 0 on success, 1 on usage/not-found, 2 on a catalog/index error.
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import sqlite3
import sys
import time
import tomllib
from dataclasses import dataclass, field

DEFAULT_KB_ROOT = "/usr/share/limpet/kb"
DEFAULT_DB = "/var/lib/limpet/limpet-kb.db"
# Shipped catalog + an operator site-override dir, same split as report-templates (wq#3934).
DEFAULT_SOURCES_DIRS = ("/usr/share/limpet/kb-sources.d", "/etc/limpet/kb-sources.d")

TIERS = ("ship", "fetch", "link")
# Subdirs of the KB root, per design §8. A source's category must be one of these so the
# namespace stays flat and predictable; the Target-Device Wizard reads them by name.
CATEGORIES = ("exploits", "creds", "devices", "hw", "cwe", "attack", "mobile")
FORMATS = ("csv", "json", "jsonl", "markdown", "lines")


class KBError(Exception):
    """A catalog, index, or fetch error. CLI maps it to exit 2."""


@dataclass
class Source:
    name: str
    tier: str
    category: str
    summary: str = ""
    license: str = ""
    size: str = ""
    version: str = ""          # upstream version string for SHIP static taxonomies
    # SHIP: how to load the on-disk files into the index.
    fmt: str = ""              # one of FORMATS
    path: str = ""             # glob, relative to the KB root
    title_fields: list = field(default_factory=list)
    body_fields: list = field(default_factory=list)
    # SHIP: optional refresh of the on-disk files (needs egress; never auto-run).
    update_cmd: str = ""
    # FETCH: how to download on demand.
    fetch_cmd: str = ""
    fetch_requires: str = ""
    fetch_dest: str = ""
    # LINK: the online helper.
    link_url: str = ""
    link_helper: str = ""

    @property
    def indexable(self) -> bool:
        return self.tier == "ship" and self.fmt in FORMATS and self.fmt != "" and bool(self.path)


# --------------------------------------------------------------------------- catalog

def _require(cond, msg):
    if not cond:
        raise KBError(msg)


def parse_source(text, *, filename="<memory>"):
    """Parse one kb-source TOML into a validated Source. Raises KBError on a bad catalog."""
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise KBError(f"{filename}: not valid TOML: {exc}") from exc
    src = data.get("source")
    _require(isinstance(src, dict), f"{filename}: missing [source] table")
    name = src.get("name", "")
    _require(isinstance(name, str) and name, f"{filename}: [source].name is required")
    tier = src.get("tier", "")
    _require(tier in TIERS, f"{name}: tier must be one of {TIERS}, got {tier!r}")
    category = src.get("category", "")
    _require(category in CATEGORIES, f"{name}: category must be one of {CATEGORIES}, got {category!r}")

    s = Source(name=name, tier=tier, category=category,
               summary=src.get("summary", ""), license=src.get("license", ""),
               size=src.get("size", ""), version=src.get("version", ""))

    if tier == "ship":
        ship = data.get("ship", {})
        _require(isinstance(ship, dict) and ship, f"{name}: SHIP source needs a [ship] table")
        s.fmt = ship.get("format", "")
        _require(s.fmt in FORMATS, f"{name}: [ship].format must be one of {FORMATS}, got {s.fmt!r}")
        s.path = ship.get("path", "")
        _require(isinstance(s.path, str) and s.path, f"{name}: [ship].path is required")
        s.title_fields = list(ship.get("title_fields", []))
        s.body_fields = list(ship.get("body_fields", []))
        if s.fmt in ("csv", "json", "jsonl"):
            _require(s.title_fields, f"{name}: [ship].title_fields required for format {s.fmt!r}")
        s.update_cmd = data.get("update", {}).get("command", "")
    elif tier == "fetch":
        fetch = data.get("fetch", {})
        _require(isinstance(fetch, dict) and fetch, f"{name}: FETCH source needs a [fetch] table")
        s.fetch_cmd = fetch.get("command", "")
        _require(s.fetch_cmd, f"{name}: [fetch].command is required")
        s.fetch_requires = fetch.get("requires", "")
        s.fetch_dest = fetch.get("dest", "")
    else:  # link
        link = data.get("link", {})
        _require(isinstance(link, dict) and link, f"{name}: LINK source needs a [link] table")
        s.link_url = link.get("url", "")
        s.link_helper = link.get("helper", "")
        _require(s.link_url, f"{name}: [link].url is required")
    return s


def load_sources(dirs):
    """Load every ``*.toml`` drop-in across ``dirs`` into {name: Source}, newest dir wins.

    A duplicate name across dirs lets an /etc override shadow the shipped catalog; a
    duplicate within a single dir is a packaging error and raises.
    """
    sources = {}
    for d in dirs:
        if not os.path.isdir(d):
            continue
        seen_here = set()
        for path in sorted(glob.glob(os.path.join(d, "*.toml"))):
            with open(path, encoding="utf-8") as fh:
                src = parse_source(fh.read(), filename=path)
            _require(src.name not in seen_here,
                     f"{path}: duplicate source name {src.name!r} in {d}")
            seen_here.add(src.name)
            sources[src.name] = src
    return sources


# --------------------------------------------------------------------------- ingest

def _ingest_csv(fh, src):
    import csv
    reader = csv.DictReader(fh)
    for row in reader:
        yield _row_to_entry(row, src)


def _ingest_json(fh, src):
    data = json.load(fh)
    rows = data if isinstance(data, list) else data.get("devices") or data.get("data") or []
    for row in rows:
        if isinstance(row, dict):
            yield _row_to_entry(row, src)


def _ingest_jsonl(fh, src):
    for line in fh:
        line = line.strip()
        if line:
            row = json.loads(line)
            if isinstance(row, dict):
                yield _row_to_entry(row, src)


def _ingest_markdown(fh, src):
    """One entry per markdown file: the first heading is the title, the file is the body."""
    text = fh.read()
    title = ""
    for line in text.splitlines():
        if line.lstrip().startswith("#"):
            title = line.lstrip("#").strip()
            break
    yield (title or os.path.basename(getattr(fh, "name", "doc")), text)


def _ingest_lines(fh, src):
    for line in fh:
        line = line.rstrip("\n")
        if line.strip():
            yield (line, line)


_INGESTERS = {"csv": _ingest_csv, "json": _ingest_json, "jsonl": _ingest_jsonl,
              "markdown": _ingest_markdown, "lines": _ingest_lines}


def _row_to_entry(row, src):
    def join(fields):
        return " ".join(str(row[f]) for f in fields if row.get(f))
    title = join(src.title_fields) or next((str(v) for v in row.values() if v), "")
    body = join(src.body_fields) if src.body_fields else " ".join(str(v) for v in row.values() if v)
    return (title, body)


def _source_files(src, kb_root):
    """Resolve a source's glob against the KB root; returns sorted absolute paths present."""
    return sorted(p for p in glob.glob(os.path.join(kb_root, src.path)) if os.path.isfile(p))


# --------------------------------------------------------------------------- index

_SCHEMA = [
    "CREATE VIRTUAL TABLE IF NOT EXISTS kb_entries "
    "USING fts5(source, category, title, body, ref)",
    "CREATE TABLE IF NOT EXISTS kb_source_meta "
    "(name TEXT PRIMARY KEY, category TEXT, tier TEXT, version TEXT, "
    " row_count INTEGER, indexed_at REAL)",
]


def _connect(db_path):
    os.makedirs(os.path.dirname(os.path.abspath(db_path)), exist_ok=True)
    conn = sqlite3.connect(db_path)
    for stmt in _SCHEMA:
        conn.execute(stmt)
    return conn


def build_index(sources, kb_root, db_path, *, only=None, now=None):
    """(Re)build the FTS5 index from every present, indexable SHIP source.

    Idempotent: each source's rows are deleted and re-inserted, so an update never
    double-counts. Returns a per-source stat dict. ``only`` restricts to named sources.
    """
    now = time.time() if now is None else now
    conn = _connect(db_path)
    stats = {}
    try:
        targets = [s for s in sources.values() if s.indexable
                   and (only is None or s.name in only)]
        for src in targets:
            files = _source_files(src, kb_root)
            conn.execute("DELETE FROM kb_entries WHERE source = ?", (src.name,))
            rows = 0
            ingester = _INGESTERS[src.fmt]
            for fpath in files:
                ref = os.path.relpath(fpath, kb_root)
                with open(fpath, encoding="utf-8", errors="replace", newline="") as fh:
                    for title, body in ingester(fh, src):
                        conn.execute(
                            "INSERT INTO kb_entries(source, category, title, body, ref) "
                            "VALUES (?,?,?,?,?)",
                            (src.name, src.category, title, body, ref))
                        rows += 1
            conn.execute(
                "INSERT INTO kb_source_meta(name, category, tier, version, row_count, indexed_at) "
                "VALUES (?,?,?,?,?,?) ON CONFLICT(name) DO UPDATE SET "
                "category=excluded.category, tier=excluded.tier, version=excluded.version, "
                "row_count=excluded.row_count, indexed_at=excluded.indexed_at",
                (src.name, src.category, src.tier, src.version, rows, now))
            stats[src.name] = {"files": len(files), "rows": rows, "indexed_at": now}
        conn.commit()
    finally:
        conn.close()
    return stats


def _fts_query(raw):
    """Build a safe FTS5 MATCH string: quote each whitespace token so punctuation in user
    input can never be read as FTS5 syntax (bare ``AND``/``*``/``"`` would error or surprise)."""
    tokens = [t for t in raw.split() if t]
    if not tokens:
        raise KBError("empty search query")
    return " ".join('"' + t.replace('"', '""') + '"' for t in tokens)


def search(db_path, query, *, limit=20, category=None):
    if not os.path.exists(db_path):
        raise KBError(f"no index at {db_path}; run `limpet-kb update` first")
    match = _fts_query(query)
    conn = sqlite3.connect(db_path)
    try:
        sql = ("SELECT source, category, title, ref FROM kb_entries "
               "WHERE kb_entries MATCH ?")
        params = [match]
        if category:
            sql += " AND category = ?"
            params.append(category)
        sql += " ORDER BY rank LIMIT ?"
        params.append(int(limit))
        return [{"source": r[0], "category": r[1], "title": r[2], "ref": r[3]}
                for r in conn.execute(sql, params)]
    except sqlite3.OperationalError as exc:
        raise KBError(f"search failed: {exc}") from exc
    finally:
        conn.close()


def _index_meta(db_path):
    if not os.path.exists(db_path):
        return {}
    conn = sqlite3.connect(db_path)
    try:
        return {r[0]: {"row_count": r[1], "indexed_at": r[2]}
                for r in conn.execute("SELECT name, row_count, indexed_at FROM kb_source_meta")}
    except sqlite3.OperationalError:
        return {}
    finally:
        conn.close()


# --------------------------------------------------------------------------- fetch

def _fetch_marker(src, kb_root):
    return os.path.join(kb_root, src.category, f".{src.name}.fetch.json")


def pull(src, kb_root, *, run=False, now=None):
    """FETCH tier: fetch a source on demand.

    Downloading needs network egress the build sandbox lacks, so the default is a dry plan:
    report the command, its requirement, and the destination. With ``run=True`` the fetch
    command is executed and a dated marker is written so ``status`` shows staleness. SHIP/LINK
    sources are not pullable.
    """
    now = time.time() if now is None else now
    if src.tier != "fetch":
        raise KBError(f"{src.name} is a {src.tier.upper()} source, not FETCH; nothing to pull")
    plan = {"name": src.name, "command": src.fetch_cmd, "requires": src.fetch_requires,
            "dest": src.fetch_dest, "ran": False}
    if not run:
        plan["note"] = "dry run; pass --run to execute (needs network egress)"
        return plan
    import subprocess
    marker = _fetch_marker(src, kb_root)
    os.makedirs(os.path.dirname(marker), exist_ok=True)
    proc = subprocess.run(src.fetch_cmd, shell=True, capture_output=True, text=True)
    plan["ran"] = True
    plan["exit_code"] = proc.returncode
    if proc.returncode == 0:
        with open(marker, "w", encoding="utf-8") as fh:
            json.dump({"name": src.name, "fetched_at": now, "command": src.fetch_cmd}, fh)
        plan["fetched_at"] = now
    else:
        plan["stderr"] = proc.stderr[-500:]
    return plan


def source_status(src, kb_root, index_meta, *, now=None):
    now = time.time() if now is None else now
    row = {"name": src.name, "tier": src.tier, "category": src.category,
           "summary": src.summary, "license": src.license, "size": src.size}
    if src.tier == "ship":
        files = _source_files(src, kb_root) if src.indexable else []
        meta = index_meta.get(src.name)
        row["present"] = bool(files)
        row["indexed"] = meta is not None
        row["rows"] = meta["row_count"] if meta else 0
        if meta and meta["indexed_at"]:
            row["indexed_at"] = meta["indexed_at"]
            row["age_days"] = round((now - meta["indexed_at"]) / 86400, 1)
        if src.version:
            row["version"] = src.version
    elif src.tier == "fetch":
        marker = _fetch_marker(src, kb_root)
        row["requires"] = src.fetch_requires
        row["fetched"] = os.path.exists(marker)
        if os.path.exists(marker):
            try:
                with open(marker, encoding="utf-8") as fh:
                    fetched_at = json.load(fh).get("fetched_at")
                if fetched_at:
                    row["fetched_at"] = fetched_at
                    row["age_days"] = round((now - fetched_at) / 86400, 1)
            except (OSError, ValueError, json.JSONDecodeError):
                pass
    else:
        row["url"] = src.link_url
        row["helper"] = src.link_helper
    return row


# --------------------------------------------------------------------------- CLI

def _emit(obj, as_json):
    if as_json:
        print(json.dumps(obj, indent=2, sort_keys=True))


def _load(args):
    dirs = [args.sources_dir] if args.sources_dir else list(DEFAULT_SOURCES_DIRS)
    return load_sources(dirs)


def _cmd_list(args):
    sources = _load(args)
    rows = sorted(sources.values(), key=lambda s: (TIERS.index(s.tier), s.category, s.name))
    if args.tier:
        rows = [s for s in rows if s.tier == args.tier]
    out = [{"name": s.name, "tier": s.tier, "category": s.category, "summary": s.summary}
           for s in rows]
    _emit(out, args.json)
    if not args.json:
        if not rows:
            print("no KB sources found")
        for s in rows:
            print(f"{s.tier.upper():<5} {s.category:<9} {s.name:<16} {s.summary}")
    return 0


def _cmd_status(args):
    sources = _load(args)
    meta = _index_meta(args.db)
    out = [source_status(s, args.kb_root, meta)
           for s in sorted(sources.values(), key=lambda s: (TIERS.index(s.tier), s.name))]
    _emit(out, args.json)
    if not args.json:
        for r in out:
            if r["tier"] == "ship":
                state = f"indexed({r['rows']})" if r.get("indexed") else (
                    "present" if r.get("present") else "absent")
            elif r["tier"] == "fetch":
                state = "fetched" if r.get("fetched") else "not-fetched"
            else:
                state = "link"
            age = f"  {r['age_days']}d" if "age_days" in r else ""
            print(f"{r['tier'].upper():<5} {r['name']:<16} {state}{age}")
    return 0


def _cmd_update(args):
    sources = _load(args)
    only = set(args.names) if args.names else None
    if only:
        missing = only - set(sources)
        if missing:
            print(f"error: unknown source(s): {', '.join(sorted(missing))}", file=sys.stderr)
            return 1
    try:
        stats = build_index(sources, args.kb_root, args.db, only=only)
    except KBError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    total = sum(s["rows"] for s in stats.values())
    _emit({"db": args.db, "indexed": stats, "total_rows": total}, args.json)
    if not args.json:
        for name, s in sorted(stats.items()):
            print(f"indexed {name}: {s['rows']} rows from {s['files']} file(s)")
        print(f"{total} rows in {args.db}")
    return 0


def _cmd_pull(args):
    sources = _load(args)
    src = sources.get(args.name)
    if src is None:
        print(f"error: unknown source {args.name!r}", file=sys.stderr)
        return 1
    try:
        plan = pull(src, args.kb_root, run=args.run)
    except KBError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    _emit(plan, args.json)
    if not args.json:
        if plan.get("ran"):
            ok = plan.get("exit_code") == 0
            print(f"{'fetched' if ok else 'FAILED'} {src.name}"
                  + ("" if ok else f" (exit {plan['exit_code']})"))
        else:
            print(f"would run: {plan['command']}")
            if plan.get("requires"):
                print(f"requires: {plan['requires']}")
            print(plan["note"])
    return 0


def _cmd_search(args):
    try:
        results = search(args.db, args.query, limit=args.limit, category=args.category)
    except KBError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    _emit(results, args.json)
    if not args.json:
        if not results:
            print("no matches")
        for r in results:
            print(f"[{r['category']}/{r['source']}] {r['title']}  ({r['ref']})")
    return 0


def build_parser():
    p = argparse.ArgumentParser(prog="limpet-kb",
                                description="LIMPET offline knowledge-base front end")
    p.add_argument("--json", action="store_true", help="machine-readable output")
    p.add_argument("--kb-root", default=os.environ.get("LIMPET_KB_ROOT", DEFAULT_KB_ROOT),
                   help=f"KB data root (default {DEFAULT_KB_ROOT})")
    p.add_argument("--db", default=os.environ.get("LIMPET_KB_DB", DEFAULT_DB),
                   help=f"SQLite FTS5 index path (default {DEFAULT_DB})")
    p.add_argument("--sources-dir",
                   help="override the kb-sources.d catalog dir (default shipped + /etc site)")
    sub = p.add_subparsers(dest="command", required=True)

    sl = sub.add_parser("list", help="list KB sources and their tiers")
    sl.add_argument("--tier", choices=TIERS, help="show only this tier")
    sl.set_defaults(func=_cmd_list)

    ss = sub.add_parser("status", help="per-source presence, index rows, and staleness")
    ss.set_defaults(func=_cmd_status)

    su = sub.add_parser("update", help="(re)build the FTS5 index from present SHIP sources")
    su.add_argument("names", nargs="*", help="limit to these sources (default all)")
    su.set_defaults(func=_cmd_update)

    sp = sub.add_parser("pull", help="fetch a FETCH-tier source on demand")
    sp.add_argument("name")
    sp.add_argument("--run", action="store_true",
                    help="actually run the fetch command (needs egress); default is a dry plan")
    sp.set_defaults(func=_cmd_pull)

    sr = sub.add_parser("search", help="full-text search the index")
    sr.add_argument("query")
    sr.add_argument("--category", choices=CATEGORIES, help="restrict to one category")
    sr.add_argument("--limit", type=int, default=20)
    sr.set_defaults(func=_cmd_search)
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
