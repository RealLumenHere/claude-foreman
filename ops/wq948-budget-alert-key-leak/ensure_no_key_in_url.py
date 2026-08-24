#!/usr/bin/env python3
"""wq#948 — assert the live litellm-budget-alert never puts a key in a URL.

`/home/lumen/bin` is untracked and lanes rewrite scripts there in place from older
copies (see ops/wq841-rework-durability). If that happens here the leak comes back
silently: budgets still report fine while every service key is written to the uvicorn
access log again. This is the alarm.

    python3 ensure_no_key_in_url.py          # exit 0 clean, 1 leaking
    python3 ensure_no_key_in_url.py --json
    python3 ensure_no_key_in_url.py --file /some/other/copy
"""
import argparse, ast, json, pathlib, re, sys

TARGET = pathlib.Path("/home/lumen/bin/litellm-budget-alert")
# A key interpolated into a query string: ?key={var}, &key=" + var, or an sk- literal.
LEAK = re.compile(r"""[?&](key|api_key)=\s*(\{|["']?\s*\+|sk-)""")


def audit(target):
    if not target.exists():
        return {"ok": False, "reason": f"{target} is missing"}
    src = target.read_text()
    try:
        tree = ast.parse(src)
    except SyntaxError as e:
        return {"ok": False, "reason": f"{target} does not parse: {e}"}

    # The module docstring quotes the old leaking URL as the record of the bug; the
    # audit is about code, so start after it.
    first = tree.body[0] if tree.body else None
    start = (
        first.end_lineno
        if isinstance(first, ast.Expr) and isinstance(getattr(first, "value", None), ast.Constant) and isinstance(first.value.value, str)
        else 0
    )
    body = src.splitlines()[start:]

    hits = [f"{start + i}: {ln.strip()}" for i, ln in enumerate(body, 1) if LEAK.search(ln)]
    if hits:
        return {"ok": False, "reason": "key interpolated into a URL", "lines": hits}
    if not any("/key/list" in ln for ln in body):
        return {"ok": False, "reason": "no /key/list call — script replaced with an older copy?"}
    return {"ok": True, "reason": "budget lookups go through /key/list; no key in any URL"}


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", type=pathlib.Path, default=TARGET)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    result = audit(args.file)
    print(json.dumps(result) if args.json else f"{'OK' if result['ok'] else 'LEAKING'}: {result['reason']}")
    sys.exit(0 if result["ok"] else 1)
