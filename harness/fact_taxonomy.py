#!/usr/bin/env python3
"""wq#5008 — QWED dual-axis verification taxonomy for lumen_state facts.

Pattern source: scratch/research/extract_qwed_verification.md (wq#4968). The taxonomy and
the proof_ref idea are adopted; the QWED server is not. Design: design/WQ5008_FACT_TAXONOMY.md.

Two INDEPENDENT axes per open fact (state_facts WHERE valid_to IS NULL):

  status     VERIFIED      a deterministic check reached a verdict — carries a proof_ref
             BLOCKED       a probe/engine exists for this fact but could not answer
             UNVERIFIABLE  nothing deterministic exists for this fact
  admission  ADMIT | DENY  may this fact drive a decision

A fact can be VERIFIED and DENY ("32GB = 2x8GB": proven, and proven wrong). VERIFIED says
a verdict exists, not that the value is good; admission says what to do with it.

VERIFIED has a `scope`, because a formal proof about a value is not evidence about the
world (QWED #320: it "verifies the formal statement it is given"):
  world   a registered probe observed the wire and matched (fact_verifications: confirmed)
  formal  an engine proved a claim the VALUE itself makes (arithmetic, address range, …)
A formal verdict never touches lumen_state's evidence clock — this module never writes to
minerva.db at all (opened read-only).

Engines (all deterministic, each optional — a missing library is BLOCKED, never a pass):
  math        SymPy    arithmetic identities stated in a value   ("64GB = 4x16GB")
  constraint  Z3       range/membership over tokens parsed by fixed regexes — the formula
                       is built by code from a parsed IPv4/port/enum token, never from prose
  sql         SQLGlot  SQL-bearing values: destructive / tautology / stacked statements
  code        ast      Python-bearing values: forbidden calls; literal parsing of numbers
  probe       —        the existing lumen_verify probes, read from fact_verifications

NOT WIRED, on purpose (ROUTES_REFUSED): `logic` (natural language -> constraints) and
`consensus` (model agreement). Both are QWED's known fail-open routes (#320, #581): the
translation step is unverified, so a VERIFIED from them proves nothing about the fact.
There is no LLM, no network call and no prose-to-formula step anywhere in this file.
"""
import argparse
import ast
import hashlib
import ipaddress
import json
import os
import re
import sqlite3
import sys
import time

VERIFIED, UNVERIFIABLE, BLOCKED = "VERIFIED", "UNVERIFIABLE", "BLOCKED"
ADMIT, DENY = "ADMIT", "DENY"
WORLD, FORMAL = "world", "formal"
PROVEN_TRUE, PROVEN_FALSE, UNAVAILABLE = "proven_true", "proven_false", "unavailable"

ROUTES = ("math", "constraint", "sql", "code", "probe")
ROUTES_REFUSED = {
    "logic": "natural-language -> constraint translation is unverified (QWED #320/#581)",
    "consensus": "model agreement is evidence, not proof (QWED #320)",
    "natural_language": "translation verification unsolved (QWED #585)",
}

MINERVA_DIR = os.environ.get("FOREMAN_MINERVA_DIR", "/home/lumen/minerva")
STATE_DB = os.environ.get("FOREMAN_STATE_DB", os.path.join(MINERVA_DIR, "minerva.db"))
FOREMAN_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LEDGER_DB = os.environ.get("FOREMAN_FACT_LEDGER",
                           os.path.join(FOREMAN_ROOT, "fact_taxonomy.db"))
# z3-solver / sqlglot live in their own venv so the system python is never changed.
ENGINE_SITE = os.environ.get(
    "FOREMAN_QWED_SITE",
    f"/home/lumen/.local/share/qwed-engines/venv/lib/python{sys.version_info[0]}."
    f"{sys.version_info[1]}/site-packages")
PROOF_VERSION = 1
# world evidence older than this many verify-intervals no longer admits (lumen_state STALE)
STALE_RATIO = 3.0
CLASS_INTERVAL_DAYS = {"status": 3.0, "config": 14.0, "identity": 90.0}
DEFAULT_INTERVAL_DAYS = 14.0
LIFECYCLE_ENUM = ("provisioning", "active", "degraded", "offline", "decommissioned",
                  "retired")
LIFECYCLE_CLOSING = ("decommissioned", "retired")


class RouteRefused(ValueError):
    """Asked for a route this module deliberately does not have."""


# --------------------------------------------------------------------------
# engines — loaded lazily; None means "not installed here" -> BLOCKED, never a pass
# --------------------------------------------------------------------------

_ENGINE_CACHE = {}


def _load(name):
    if name in _ENGINE_CACHE:
        return _ENGINE_CACHE[name]
    mod = None
    for attempt in (0, 1):
        try:
            mod = __import__(name)
            break
        except Exception:
            if attempt == 0 and os.path.isdir(ENGINE_SITE) and ENGINE_SITE not in sys.path:
                sys.path.append(ENGINE_SITE)      # appended: never shadows the stdlib
            else:
                break
    _ENGINE_CACHE[name] = mod
    return mod


def engine_versions():
    out = {"ast": f"py{sys.version_info[0]}.{sys.version_info[1]}"}
    for name, attr in (("sympy", "__version__"), ("sqlglot", "__version__")):
        m = _load(name)
        out[name] = getattr(m, attr, "?") if m else None
    z3 = _load("z3")
    out["z3"] = z3.get_version_string() if z3 else None
    return out


def _check(route, engine, claim, verdict, detail="", issues=()):
    return {"route": route, "engine": engine, "claim": claim, "verdict": verdict,
            "detail": detail, "issues": list(issues)}


# ---- math (SymPy) ---------------------------------------------------------

_UNIT = r"(GiB|GB|TB|MB|G|T)"
_ARITH_RES = (
    # "64GB = 4x16GB"
    re.compile(rf"(?<![\w.])(\d+(?:\.\d+)?)\s*{_UNIT}\s*=\s*(\d+)\s*[x×]\s*(\d+(?:\.\d+)?)\s*{_UNIT}\b"),
    # "32GB DDR5-4800 (2x16GB)"
    re.compile(rf"(?<![\w.])(\d+(?:\.\d+)?)\s*{_UNIT}\b[^()=;,]{{0,40}}\(\s*(\d+)\s*[x×]\s*(\d+(?:\.\d+)?)\s*{_UNIT}\s*\)"),
)


_MEMORY_PRED = re.compile(r"(^|_)(ram|memory)($|_)")


def math_claims(value, predicate=None):
    """(text, total, count, each) for every `TOTAL unit = N x EACH unit` the value states
    with the SAME unit on both sides. Mixed units are not a claim this engine reads. The
    parenthetical form `32GB … (2x16GB)` is only read on a memory predicate: elsewhere
    the bracket may describe something other than the leading figure (a disk and its
    RAM), and an explicit `=` is the only identity the value itself asserts."""
    out = []
    memory = bool(predicate is None or _MEMORY_PRED.search(predicate.lower()))
    for rx in (_ARITH_RES if memory else _ARITH_RES[:1]):
        for m in rx.finditer(value or ""):
            total, unit_a, count, each, unit_b = m.groups()
            if unit_a.upper().rstrip("B") != unit_b.upper().rstrip("B"):
                continue
            out.append((m.group(0).strip(), total, count, each))
    return out


def check_math(value, predicate=None):
    claims = math_claims(value, predicate)
    if not claims:
        return []
    sympy = _load("sympy")
    out = []
    for text, total, count, each in claims:
        claim = f"{total} = {count} * {each}"
        if sympy is None:
            out.append(_check("math", "sympy", claim, UNAVAILABLE, "sympy not installed"))
            continue
        lhs = sympy.Rational(total)
        rhs = sympy.Integer(int(count)) * sympy.Rational(each)
        ok = sympy.simplify(lhs - rhs) == 0
        out.append(_check("math", "sympy", claim, PROVEN_TRUE if ok else PROVEN_FALSE,
                          f"value states {text!r}; {count}*{each} = {rhs}",
                          () if ok else ("arithmetic_mismatch",)))
    return out


# ---- constraint (Z3) ------------------------------------------------------

_IPV4_LEAD = re.compile(r"^\s*(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})(?![\d.])")
_IP_PRED = re.compile(r"(^|_)ip$")
_NETS = {
    "tailscale_ip": (("100.64.0.0/10",), "the Tailscale CGNAT range"),
    None: (("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16"), "RFC1918 private space"),
}


def _z3_prove(z3, claim_expr):
    """Valid iff the negation is unsatisfiable. Returns True / False / None (unknown)."""
    s = z3.Solver()
    s.set("timeout", 2000)
    s.add(z3.Not(claim_expr))
    r = s.check()
    return True if r == z3.unsat else (False if r == z3.sat else None)


def check_constraint(predicate, value, lifecycle_enum=LIFECYCLE_ENUM):
    pred = (predicate or "").lower()
    val = value or ""
    todo = []           # (claim text, builder(z3) -> expr, issue)
    if _IP_PRED.search(pred):
        m = _IPV4_LEAD.match(val)
        if not m:
            return []   # prose, not an address token: nothing formal to check
        octets = [int(g) for g in m.groups()]
        addr = ".".join(map(str, octets))
        todo.append((f"{addr} is a well-formed IPv4 address",
                     lambda z3: z3.And(*[z3.And(z3.IntVal(o) >= 0, z3.IntVal(o) <= 255)
                                         for o in octets]), "malformed_address"))
        if all(o <= 255 for o in octets):
            ipint = int(ipaddress.IPv4Address(addr))
            nets, label = _NETS.get(pred, _NETS[None])

            def member(z3, nets=nets, ipint=ipint):
                x = z3.BitVecVal(ipint, 32)
                alts = []
                for n in nets:
                    net = ipaddress.ip_network(n)
                    alts.append((x & z3.BitVecVal(int(net.netmask), 32))
                                == z3.BitVecVal(int(net.network_address), 32))
                return z3.Or(*alts)
            todo.append((f"{addr} is inside {label} ({', '.join(nets)})", member,
                         "address_out_of_range"))
    elif pred == "port" or pred.endswith("_port"):
        m = re.match(r"^\s*(\d{1,7})(?!\d)", val)
        if not m:
            return []
        port = int(m.group(1))
        todo.append((f"{port} is a valid TCP/UDP port (1..65535)",
                     lambda z3: z3.And(z3.IntVal(port) >= 1, z3.IntVal(port) <= 65535),
                     "port_out_of_range"))
    elif pred == "lifecycle":
        token = val.strip().split()[0].lower() if val.strip() else ""
        idx = {name: i for i, name in enumerate(lifecycle_enum)}
        code = idx.get(token, -1)
        todo.append((f"lifecycle {token!r} is in the registered enum",
                     lambda z3: z3.Or(*[z3.IntVal(code) == z3.IntVal(i)
                                        for i in idx.values()]), "lifecycle_outside_enum"))
    if not todo:
        return []
    z3 = _load("z3")
    out = []
    for claim, build, issue in todo:
        if z3 is None:
            out.append(_check("constraint", "z3", claim, UNAVAILABLE, "z3-solver not installed"))
            continue
        proved = _z3_prove(z3, build(z3))
        if proved is None:
            out.append(_check("constraint", "z3", claim, UNAVAILABLE, "solver returned unknown"))
        else:
            out.append(_check("constraint", "z3", claim,
                              PROVEN_TRUE if proved else PROVEN_FALSE,
                              "negation unsat" if proved else "negation sat (counterexample)",
                              () if proved else (issue,)))
    return out


# ---- sql (SQLGlot) --------------------------------------------------------

_SQL_PRED = re.compile(r"(^|_)(sql|query)($|_)")
_SQL_LEAD = re.compile(r"^\s*(SELECT|INSERT|UPDATE|DELETE|DROP|TRUNCATE|ALTER|CREATE|WITH)\b")


def sql_candidate(predicate, value):
    return bool(_SQL_PRED.search((predicate or "").lower()) or _SQL_LEAD.match(value or ""))


def check_sql(value):
    """One check for a SQL-bearing value. VERIFIED either way once it parses: a
    destructive statement is proven destructive (verdict proven_false on the claim "safe
    to run"), which is what makes it VERIFIED + DENY."""
    claim = "statement is a single non-destructive, non-tautological query"
    sqlglot = _load("sqlglot")
    if sqlglot is None:
        return [_check("sql", "sqlglot", claim, UNAVAILABLE, "sqlglot not installed")]
    from sqlglot import exp
    try:
        stmts = [s for s in sqlglot.parse(value) if s is not None]
    except Exception:
        return []                       # does not parse as SQL: not this engine's fact
    if not stmts:
        return []
    issues = []
    if len(stmts) > 1:
        issues.append("stacked_queries")
    for st in stmts:
        if isinstance(st, (exp.Drop, exp.TruncateTable)):
            issues.append("destructive_command")
        elif isinstance(st, exp.Command):
            issues.append("unparsed_command")
        elif isinstance(st, (exp.Delete, exp.Update)) and not st.args.get("where"):
            issues.append("destructive_command")
        for eq in st.find_all(exp.EQ):
            left, right = eq.left, eq.right
            if (isinstance(left, exp.Literal) and isinstance(right, exp.Literal)
                    and left.this == right.this):
                issues.append("injection_tautology")
    issues = sorted(set(issues))
    kinds = ",".join(type(s).__name__ for s in stmts)
    return [_check("sql", "sqlglot", claim, PROVEN_FALSE if issues else PROVEN_TRUE,
                   f"parsed {len(stmts)} statement(s): {kinds}", issues)]


# ---- code (Python AST) ----------------------------------------------------

_CODE_PRED = re.compile(r"(^|_)(python|py|code|snippet)($|_)")
_PY_C = re.compile(r"^\s*python3?\s+-c\s+(['\"])(.*)\1\s*$", re.S)
_FORBIDDEN_NAMES = {"eval", "exec", "compile", "__import__"}
_FORBIDDEN_ATTRS = {("os", "system"), ("os", "popen"), ("os", "execv"), ("os", "execvp"),
                    ("os", "remove"), ("os", "unlink"), ("os", "rmdir"),
                    ("shutil", "rmtree"), ("pty", "spawn")}


def code_candidate(predicate, value):
    return bool(_CODE_PRED.search((predicate or "").lower()) or _PY_C.match(value or ""))


def check_code(value):
    claim = "python source makes no forbidden call"
    m = _PY_C.match(value or "")
    src = m.group(2) if m else (value or "")
    try:
        tree = ast.parse(src)
    except (SyntaxError, ValueError):
        return []                       # not Python: not this engine's fact
    issues = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        f = node.func
        if isinstance(f, ast.Name) and f.id in _FORBIDDEN_NAMES:
            issues.append(f"forbidden_call:{f.id}")
        elif isinstance(f, ast.Attribute) and isinstance(f.value, ast.Name):
            if (f.value.id, f.attr) in _FORBIDDEN_ATTRS:
                issues.append(f"forbidden_call:{f.value.id}.{f.attr}")
            elif f.value.id == "subprocess" and any(
                    kw.arg == "shell" and isinstance(kw.value, ast.Constant)
                    and kw.value.value is True for kw in node.keywords):
                issues.append("shell_injection:subprocess(shell=True)")
    issues = sorted(set(issues))
    return [_check("code", "ast", claim, PROVEN_FALSE if issues else PROVEN_TRUE,
                   f"{sum(isinstance(n, ast.Call) for n in ast.walk(tree))} call(s) walked",
                   issues)]


def check_number(predicate, value):
    """ast.literal_eval as the safe scalar parser: a `price` must be a non-negative
    number. Only the leading token is read; prose after it is not this engine's concern."""
    if (predicate or "").lower() != "price":
        return []
    m = re.match(r"^\s*\$?\s*([-+]?\d[\d.]*)", value or "")
    if not m:
        return []
    token = m.group(1)
    claim = f"{token} is a non-negative number"
    try:
        n = ast.literal_eval(token)
    except (ValueError, SyntaxError):
        return [_check("code", "ast", claim, PROVEN_FALSE, "not a numeric literal",
                       ("not_a_number",))]
    ok = isinstance(n, (int, float)) and not isinstance(n, bool) and n >= 0
    return [_check("code", "ast", claim, PROVEN_TRUE if ok else PROVEN_FALSE,
                   f"literal_eval -> {n!r}", () if ok else ("negative_price",))]


def verify_claim(route, text, predicate=None):
    """Public single-claim entry point (for callers outside the fact sweep). Refuses the
    fail-open routes by name instead of quietly returning nothing."""
    if route in ROUTES_REFUSED:
        raise RouteRefused(f"route {route!r} is not wired: {ROUTES_REFUSED[route]}")
    if route == "math":
        return check_math(text, predicate)
    if route == "constraint":
        return check_constraint(predicate, text)
    if route == "sql":
        return check_sql(text)
    if route == "code":
        return check_code(text)
    raise RouteRefused(f"unknown route {route!r}; wired routes: {', '.join(ROUTES)}")


# --------------------------------------------------------------------------
# reading lumen_state (READ-ONLY — minerva.db is never written from here)
# --------------------------------------------------------------------------

def _ro_conn(path):
    c = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=5)
    c.row_factory = sqlite3.Row
    return c


def load_facts(db_path=None, subject=None):
    """(facts, verifications_by_fact_id). verifications are newest-first per fact."""
    c = _ro_conn(db_path or STATE_DB)
    try:
        cols = {r[1] for r in c.execute("PRAGMA table_info(state_facts)")}
        want = ["id", "subject", "predicate", "value", "valid_from"]
        opt = ["last_confirmed_at", "fact_class", "source_kind", "verify_recipe",
               "verify_interval_days", "contradicted_at"]
        sel = want + [o for o in opt if o in cols]
        q = f"SELECT {', '.join(sel)} FROM state_facts WHERE valid_to IS NULL"
        args = ()
        if subject:
            q += " AND subject = ?"
            args = (subject.strip().lower(),)
        facts = [dict(r) for r in c.execute(q + " ORDER BY subject, predicate", args)]
        ver = {}
        has_fv = c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND "
                           "name='fact_verifications'").fetchone()
        if has_fv:
            for r in c.execute("SELECT fact_id, probe, observed, outcome, ts FROM "
                               "fact_verifications ORDER BY ts DESC"):
                ver.setdefault(r["fact_id"], []).append(dict(r))
        return facts, ver
    finally:
        c.close()


def _epoch(ts):
    if not ts:
        return None
    try:
        from datetime import datetime, timezone
        d = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
        if d.tzinfo is None:
            d = d.replace(tzinfo=timezone.utc)
        return d.timestamp()
    except (ValueError, TypeError):
        return None


def _interval_days(fact):
    if fact.get("verify_interval_days"):
        return float(fact["verify_interval_days"])
    return CLASS_INTERVAL_DAYS.get(fact.get("fact_class") or "", DEFAULT_INTERVAL_DAYS)


def _operational_fn():
    """lumen_state.is_operational when minerva is importable; else None (the closed-
    lifecycle admission rule is then simply not applied — never guessed)."""
    try:
        if MINERVA_DIR not in sys.path:
            sys.path.append(MINERVA_DIR)
        import lumen_state
        reg = lumen_state.registry()
        return lambda pred: lumen_state.is_operational(pred, reg)
    except Exception:
        return None


def probe_evidence(fact, verifications, now):
    """World-evidence reading for one fact -> dict or None (no probe owns it).
    state: confirmed | stale | blocked."""
    rows = verifications or []
    assigned = bool(fact.get("verify_recipe")) or bool(rows)
    if not assigned:
        return None
    confirmed = next((r for r in rows if r.get("outcome") == "confirmed"), None)
    ev = None
    if confirmed:
        ev = {"probe": confirmed["probe"], "observed": confirmed.get("observed"),
              "ts": confirmed["ts"], "outcome": "confirmed"}
    elif fact.get("source_kind") == "observed" and fact.get("verify_recipe"):
        # the open row was itself written by a probe observation (supersession path)
        ev = {"probe": fact["verify_recipe"], "observed": fact["value"],
              "ts": fact.get("last_confirmed_at") or fact["valid_from"],
              "outcome": "observed"}
    latest = rows[0] if rows else None
    if ev is None:
        why = (f"probe {latest['probe']} last returned {latest['outcome']!r} at {latest['ts']}"
               if latest else f"probe {fact.get('verify_recipe')} assigned, no observation yet")
        return {"state": "blocked", "detail": why,
                "probe": (latest or {}).get("probe") or fact.get("verify_recipe")}
    t = _epoch(ev["ts"])
    age_days = None if t is None else max(0.0, (now - t) / 86400.0)
    interval = _interval_days(fact)
    stale = age_days is None or age_days > STALE_RATIO * interval
    out = dict(ev, state="stale" if stale else "confirmed",
               age_days=None if age_days is None else round(age_days, 2),
               interval_days=interval)
    if latest and latest is not confirmed and latest.get("outcome") == "unknown":
        out["latest_run"] = f"{latest['probe']} returned unknown at {latest['ts']}"
    return out


# --------------------------------------------------------------------------
# classification
# --------------------------------------------------------------------------

def _canonical(obj):
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def sha256_hex(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def proof_ref(fact, scope, evidence):
    """SHA-256 commitment to exactly what made the fact VERIFIED: the fact identity, the
    value it had, and the evidence. Recomputable — `verify-proof` re-derives and compares.
    Engine versions and the check time are deliberately outside the commitment so the
    same fact with the same evidence always commits to the same ref."""
    return sha256_hex(_canonical({
        "v": PROOF_VERSION, "fact_id": fact["id"], "subject": fact["subject"],
        "predicate": fact["predicate"], "value_sha256": sha256_hex(fact["value"] or ""),
        "scope": scope, "evidence": evidence}))


def run_engines(fact):
    pred, val = fact["predicate"], fact["value"] or ""
    checks = []
    checks += check_math(val, pred)
    checks += check_constraint(pred, val)
    checks += check_number(pred, val)
    if sql_candidate(pred, val):
        checks += check_sql(val)
    if code_candidate(pred, val):
        checks += check_code(val)
    return checks


def classify_fact(fact, verifications=None, now=None, closed_subjects=(),
                  is_operational=None):
    """One fact -> its taxonomy record. Pure given its inputs."""
    now = time.time() if now is None else now
    checks = run_engines(fact)
    probe = probe_evidence(fact, verifications, now)
    decided = [c for c in checks if c["verdict"] in (PROVEN_TRUE, PROVEN_FALSE)]
    unavailable = [c for c in checks if c["verdict"] == UNAVAILABLE]

    issues = []
    if fact.get("contradicted_at"):
        issues.append("contradicted")
    for c in decided:
        if c["verdict"] == PROVEN_FALSE:
            issues += c["issues"] or ["proven_false"]
    if probe and probe["state"] == "stale":
        issues.append("stale_evidence")
    if (is_operational and fact["subject"] in closed_subjects
            and fact["predicate"] != "lifecycle" and is_operational(fact["predicate"])):
        issues.append("closed_lifecycle")

    scope = ref = None
    blocked_by = None
    if probe and probe["state"] in ("confirmed", "stale"):
        status, scope = VERIFIED, WORLD
        ref = proof_ref(fact, WORLD, {k: probe.get(k) for k in
                                      ("probe", "observed", "ts", "outcome")})
    elif probe and probe["state"] == "blocked":
        status, blocked_by = BLOCKED, f"probe: {probe['detail']}"
    elif decided and not unavailable:
        status, scope = VERIFIED, FORMAL
        ref = proof_ref(fact, FORMAL, sorted(
            ({k: c[k] for k in ("route", "engine", "claim", "verdict", "issues")}
             for c in decided), key=_canonical))
    elif unavailable:
        status = BLOCKED
        blocked_by = "engine: " + "; ".join(sorted({f"{c['engine']} — {c['detail']}"
                                                    for c in unavailable}))
    else:
        status = UNVERIFIABLE

    return {
        "fact_id": fact["id"], "subject": fact["subject"], "predicate": fact["predicate"],
        "value_sha256": sha256_hex(fact["value"] or ""),
        "status": status, "scope": scope,
        "admission": DENY if issues else ADMIT,
        "issues": sorted(set(issues)), "blocked_by": blocked_by,
        "proof_ref": ref, "checks": checks, "probe": probe,
    }


def scan(db_path=None, subject=None, now=None):
    facts, ver = load_facts(db_path, subject)
    all_facts = facts if not subject else load_facts(db_path)[0]
    closed = {f["subject"] for f in all_facts
              if f["predicate"] == "lifecycle"
              and (f["value"] or "").strip().split(" ")[0].lower() in LIFECYCLE_CLOSING}
    op = _operational_fn()
    now = time.time() if now is None else now
    return [classify_fact(f, ver.get(f["id"]), now, closed, op) for f in facts]


def summarize(records):
    s = {"facts": len(records), "status": {}, "verified_scope": {}, "admission": {},
         "deny_issues": {}, "blocked_by": {}, "routes_decided": {}}
    for r in records:
        s["status"][r["status"]] = s["status"].get(r["status"], 0) + 1
        s["admission"][r["admission"]] = s["admission"].get(r["admission"], 0) + 1
        if r["scope"]:
            s["verified_scope"][r["scope"]] = s["verified_scope"].get(r["scope"], 0) + 1
        for i in r["issues"]:
            s["deny_issues"][i] = s["deny_issues"].get(i, 0) + 1
        if r["blocked_by"]:
            key = r["blocked_by"].split(" last returned")[0].split(" assigned")[0][:60]
            s["blocked_by"][key] = s["blocked_by"].get(key, 0) + 1
        for c in r["checks"]:
            if c["verdict"] in (PROVEN_TRUE, PROVEN_FALSE):
                k = f"{c['route']}:{c['verdict']}"
                s["routes_decided"][k] = s["routes_decided"].get(k, 0) + 1
    # the invariant the whole taxonomy rests on
    s["proof_invariant_ok"] = all((r["status"] == VERIFIED) == bool(r["proof_ref"])
                                  for r in records)
    return s


# --------------------------------------------------------------------------
# ledger (foreman-side; lumen_state is not modified)
# --------------------------------------------------------------------------

def _ledger(path=None):
    c = sqlite3.connect(path or LEDGER_DB, timeout=10)
    c.execute("""CREATE TABLE IF NOT EXISTS fact_taxonomy (
        fact_id TEXT PRIMARY KEY, subject TEXT, predicate TEXT, value_sha256 TEXT,
        status TEXT NOT NULL, scope TEXT, admission TEXT NOT NULL, issues TEXT,
        blocked_by TEXT, proof_ref TEXT, checks TEXT, probe TEXT, checked_at REAL,
        CHECK ((status = 'VERIFIED') = (proof_ref IS NOT NULL)))""")
    c.execute("""CREATE TABLE IF NOT EXISTS taxonomy_runs (
        id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, summary TEXT, engines TEXT)""")
    c.execute("CREATE INDEX IF NOT EXISTS idx_ft_subject ON fact_taxonomy(subject, predicate)")
    return c


def write_ledger(records, path=None, now=None, full=True):
    """Upsert every record; on a full (unfiltered) scan, rows for facts that are no
    longer open are dropped so the ledger always mirrors the current slice."""
    now = time.time() if now is None else now
    c = _ledger(path)
    try:
        with c:
            for r in records:
                c.execute(
                    "INSERT OR REPLACE INTO fact_taxonomy VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (r["fact_id"], r["subject"], r["predicate"], r["value_sha256"],
                     r["status"], r["scope"], r["admission"], json.dumps(r["issues"]),
                     r["blocked_by"], r["proof_ref"], json.dumps(r["checks"]),
                     json.dumps(r["probe"]), now))
            if full:
                live = {r["fact_id"] for r in records}
                stale = [row[0] for row in c.execute("SELECT fact_id FROM fact_taxonomy")
                         if row[0] not in live]
                c.executemany("DELETE FROM fact_taxonomy WHERE fact_id = ?",
                              [(s,) for s in stale])
            c.execute("INSERT INTO taxonomy_runs (ts, summary, engines) VALUES (?,?,?)",
                      (now, json.dumps(summarize(records)), json.dumps(engine_versions())))
    finally:
        c.close()


def verify_proof(fact_id, db_path=None, ledger_path=None):
    """Re-derive the proof for a fact from live state and compare with the ledger.
    -> (ok, detail). A changed value, changed evidence or an edited ledger row all fail."""
    c = _ledger(ledger_path)
    try:
        row = c.execute("SELECT proof_ref, status FROM fact_taxonomy WHERE fact_id = ?",
                        (fact_id,)).fetchone()
    finally:
        c.close()
    if not row:
        return False, "no ledger row"
    live = [r for r in scan(db_path) if r["fact_id"] == fact_id]
    if not live:
        return False, "fact is no longer open in lumen_state"
    if live[0]["proof_ref"] != row[0]:
        return False, f"mismatch: ledger {row[0]} vs live {live[0]['proof_ref']}"
    return True, f"{row[1]} proof_ref {row[0]}"


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def _line(r):
    tag = r["status"] + (f"/{r['scope']}" if r["scope"] else "")
    extra = ""
    if r["issues"]:
        extra = "  issues=" + ",".join(r["issues"])
    elif r["blocked_by"]:
        extra = "  " + r["blocked_by"][:90]
    ref = f"  proof={r['proof_ref'][:16]}" if r["proof_ref"] else ""
    return (f"{r['subject']:>18}.{r['predicate']:<22} {tag:<16} {r['admission']:<5}"
            f"{ref}{extra}")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    ps = sub.add_parser("scan", help="classify every open fact")
    ps.add_argument("--subject")
    ps.add_argument("--write", action="store_true", help="persist to the foreman ledger")
    ps.add_argument("--json", action="store_true")
    ps.add_argument("--only", choices=["VERIFIED", "BLOCKED", "UNVERIFIABLE", "DENY"])
    sub.add_parser("summary", help="counts from a fresh scan (nothing written)")
    pp = sub.add_parser("verify-proof", help="re-derive one fact's proof_ref")
    pp.add_argument("fact_id")
    sub.add_parser("engines", help="which engines are available here")
    a = ap.parse_args(argv)

    if a.cmd == "engines":
        print(json.dumps({"engines": engine_versions(), "routes": ROUTES,
                          "refused": ROUTES_REFUSED}, indent=1))
        return 0
    if a.cmd == "verify-proof":
        ok, detail = verify_proof(a.fact_id)
        print(("OK " if ok else "FAIL ") + detail)
        return 0 if ok else 1
    records = scan(subject=getattr(a, "subject", None))
    summary = summarize(records)
    if a.cmd == "summary":
        print(json.dumps(summary, indent=1, sort_keys=True))
        return 0 if summary["proof_invariant_ok"] else 1
    if a.write:
        write_ledger(records, full=not a.subject)
    shown = [r for r in records if not a.only
             or (r["admission"] == DENY if a.only == "DENY" else r["status"] == a.only)]
    if a.json:
        print(json.dumps({"summary": summary, "records": shown}, indent=1))
    else:
        for r in shown:
            print(_line(r))
        print(f"\n{summary['facts']} facts · status {summary['status']} · "
              f"verified scope {summary['verified_scope']} · admission {summary['admission']}"
              + (f" · ledger {LEDGER_DB}" if a.write else ""))
    return 0 if summary["proof_invariant_ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
