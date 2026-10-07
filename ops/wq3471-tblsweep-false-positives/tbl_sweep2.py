#!/usr/bin/env python3
import re, os, sqlite3, glob, ast, sys
# wq#3471: argv overrides the scan roots so tests can point the sweep at a fixture tree.
SCAN_DIRS = sys.argv[1:] or ["/home/lumen/agent","/home/lumen/minerva","/home/lumen/messenger","/home/lumen/dashboard"]
pyfiles = []
for d in SCAN_DIRS:
    pyfiles += [f for f in glob.glob(d+"/**/*.py", recursive=True)
                if ".bak" not in f and "__pycache__" not in f and ".OLD" not in f and ".deleted" not in f]
str_pat = re.compile(r'("""(?:.|\n)*?"""|\'\'\'(?:.|\n)*?\'\'\'|"[^"\n]*"|\'[^\'\n]*\')')
sql_kw = re.compile(r'\b(SELECT|INSERT|UPDATE|DELETE|CREATE|REPLACE)\b', re.I)
tbl_pat = re.compile(r'\b(?:FROM|INTO|UPDATE|JOIN|TABLE(?:\s+IF\s+NOT\s+EXISTS)?)\s+([a-zA-Z_][a-zA-Z0-9_]*)')  # case-sensitive (wq126): real SQL is UPPERCASE; prose "into Lumen's" must not match
# wq#651/aq#474: tolerate the sqlite3 read-only URI idiom
# ("file:/path/x.db?mode=ro", uri=True) -- without this the path never resolves,
# tbl_sweep2 silently falls back to unrelated .db candidates found elsewhere in
# the same file, and flags a real table as a false MISSING (daily_briefing.py's
# queue.db 'tasks').
# wq#1880/wq#3471: a path may also start right after an f-string substitution
# (f'{MINERVA}/minerva.db'); anchoring only on a quote hid minerva.db from
# lumen_dream_digest.py and checked its minerva tables against queue.db.
db_pat = re.compile(r'(?:["\']|\})(?:file:)?((?:/[^"\'?]*)?[a-zA-Z0-9_./-]*\.db)(?:\?[^"\']*)?["\']')
# "if": SQL keyword captured from prose "TABLE IF ..." (wq126).
# "of"/"on": trigger grammar, `BEFORE UPDATE ON t` / `AFTER UPDATE OF col ON t` (wq#3471).
SKIP = {"sqlite_master","where","select","values","set","if","of","on"}
def docstring_lines(src):
    """Start lines of module/class/function docstrings: prose about SQL, never executed (wq#3471)."""
    try:
        tree = ast.parse(src)
    except (SyntaxError, ValueError):
        return set()
    out = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)) and node.body:
            b = node.body[0]
            if isinstance(b, ast.Expr) and isinstance(b.value, ast.Constant) and isinstance(b.value.value, str):
                out.add(b.lineno)
    return out
def db_tables(path):
    try:
        c = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        t = {r[0].lower() for r in c.execute("SELECT name FROM sqlite_master WHERE type IN ('table','view')")}
        c.close(); return t
    except Exception: return None
for f in pyfiles:
    src = open(f, errors="replace").read()
    dbs = set(db_pat.findall(src)); resolved = []
    for d in dbs:
        for cand in (d, os.path.join(os.path.dirname(f), os.path.basename(d)), "/home/lumen/agent/"+os.path.basename(d), "/home/lumen/minerva/"+os.path.basename(d), "/home/lumen/messenger/"+os.path.basename(d)):
            if os.path.exists(cand): resolved.append(cand); break
    if not resolved: continue
    all_t = set()
    for r in set(resolved):
        t = db_tables(r)
        if t: all_t |= t
    if not all_t: continue
    refs = set()
    docs = docstring_lines(src)
    for sm in str_pat.finditer(src):
        s = sm.group(0)
        if not sql_kw.search(s): continue
        line = src[:sm.start()].count("\n")+1
        if line in docs: continue
        for m in tbl_pat.finditer(s):
            n = m.group(1)
            if n.lower() in SKIP: continue
            refs.add((n, line))
    for n, line in sorted(refs, key=lambda x:x[1]):
        if n.lower() not in all_t:
            print(f"{f}:{line} MISSING table '{n}' (dbs: {','.join(sorted(set(os.path.basename(x) for x in resolved)))})")
