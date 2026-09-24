#!/usr/bin/env python3
"""patch_dashboard.py — add the read-only drift-plan route to lumen-dashboard.

(wq#2434, proposal 9f2866bcda22 step 3.) dashboard_api.py is a live, untracked
1078-line FastAPI service, so this is a marker-guarded, idempotent, compile-
checked insert rather than a file replacement.

The route MUST land above the `if __name__ == "__main__":` block: the service
is started as `python3 dashboard_api.py`, so anything appended after that guard
is defined only after uvicorn.run() has already been handed the app, and the
route would never be registered.

  patch_dashboard.py --check   read-only; exit 0 if present, 1 if absent, 2 on error
  patch_dashboard.py           insert (no-op if already present)
  patch_dashboard.py --revert  remove the block again (rollback)

Only ever touches text between its own markers. Always leaves a timestamped
backup, and refuses to install a file that does not compile.
"""
import argparse, datetime, os, shutil, sys, tempfile


class PatchError(Exception):
    """The target is not in a shape this patcher may touch; nothing is written."""

TARGET = os.environ.get("DASHBOARD_API", "/home/lumen/dashboard/dashboard_api.py")
BEGIN = "# --- wq#2434 drift plans (read-only) ---"
END = "# --- end wq#2434 drift plans ---"
ANCHOR = 'if __name__ == "__main__":'

BLOCK = f'''{BEGIN}
DRIFT_PLANS_DIR = Path("/home/lumen/minerva/drift_plans")


@app.get("/api/v1/drift-plans")
def drift_plans():
    """Per-rung observation policy (wq#2434). Read-only; empty until generated."""
    try:
        index = json.loads((DRIFT_PLANS_DIR / "index.json").read_text())
    except (OSError, ValueError):
        return JSONResponse({{"counts": {{"total": 0}}, "plans": [],
                             "skipped_rungs": [], "available": False}})
    index["available"] = True
    return JSONResponse(index)


{END}


'''


def read(path):
    with open(path) as f:
        return f.read()


def strip_block(src):
    """Remove an existing marker block. Returns (text, was_present)."""
    start = src.find(BEGIN)
    if start < 0:
        return src, False
    end = src.find(END, start)
    if end < 0:
        # A half-written marker means a previous edit was interrupted. Reporting
        # that as "already present" would leave the route permanently missing.
        raise PatchError(f"{BEGIN} present without {END}; refusing to guess")
    end = src.index("\n", end) + 1
    while src[end:end + 1] == "\n":
        end += 1
    return src[:start] + src[end:], True


def insert(src):
    at = src.rfind(ANCHOR)
    if at < 0:
        raise PatchError(f"anchor {ANCHOR!r} not found; refusing")
    return src[:at] + BLOCK + src[at:]


def install(path, text):
    """Syntax-check, back up, then replace. A broken dashboard never lands."""
    try:
        compile(text, path, "exec")
    except SyntaxError as e:
        raise PatchError(f"patched file does not compile: {e}")
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path) or ".", suffix=".py")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(text)
        stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        shutil.copy2(path, f"{path}.bak.{stamp}_wq2434")
        shutil.copymode(path, tmp)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--target", default=TARGET)
    ap.add_argument("--check", action="store_true", help="read-only presence check")
    ap.add_argument("--revert", action="store_true", help="remove the block")
    args = ap.parse_args(argv)

    try:
        src = read(args.target)
        # Validated before any branch: a half-written marker must never be
        # mistaken for a healthy install.
        stripped, present = strip_block(src)

        if args.check:
            print(f"{'present' if present else 'absent'}: {args.target}")
            return 0 if present else 1

        if args.revert:
            if not present:
                print("patch_dashboard: nothing to revert")
                return 0
            install(args.target, stripped)
            print(f"patch_dashboard: reverted {args.target}")
            return 0

        if present:
            print(f"patch_dashboard: already present in {args.target}")
            return 0
        install(args.target, insert(stripped))
    except (OSError, PatchError) as e:
        print(f"patch_dashboard: {e}", file=sys.stderr)
        return 2
    print(f"patch_dashboard: added /api/v1/drift-plans to {args.target}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
