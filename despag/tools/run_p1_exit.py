"""wq#1882: reproduce the P1-exit full-conformance run against a single despag
process from a clean worktree, isolated from any production ~/despag-data or
~/.secrets — everything lives under a --data-dir the caller owns.

    python3 -m despag.tools.run_p1_exit serve --port 8795 --data-dir DIR
    python3 -m despag.tools.run_p1_exit conformance --port 8795 --data-dir DIR \
        --class core --harness-id chrn_litellm-mechanical --json OUT.json

`serve` never returns (run it with the caller's own backgrounding); `conformance`
runs uhp-conformance in-process against a running `serve` instance. Both avoid the
two despag/kb#372 traps: no `VAR=value cmd` env prefix (defeats the Bash per-
command allowlist) and no /tmp path (outside a BUILD worker's bounded filesystem).
"""
from __future__ import annotations

import argparse
import os
import sys

CONFORMANCE_SRC = "/home/lumen/research/harnessrouter-ref/src/protocol/conformance"


def _configure_env(data_dir: str, port: int) -> None:
    os.environ["DESPAG_PORT"] = str(port)
    os.environ["DESPAG_DATA_DIR"] = data_dir
    os.environ["DESPAG_SESSIONS_ROOT"] = os.path.join(data_dir, "sessions")
    os.environ["DESPAG_WORKTREES_ROOT"] = os.path.join(data_dir, "worktrees")
    os.environ.setdefault("DESPAG_API_TOKEN_FILE", os.path.join(data_dir, "despag-api.token"))
    # wq#1882 (ISSUE-LOG #374): a BUILD worker's PATH lacks /home/lumen/.npm-global/bin,
    # so despag/app.py's bare "claude" default 404s every real task. Production despag
    # is expected to run with that dir on PATH; this override is test-environment only.
    os.environ.setdefault("DESPAG_CLAUDE_BIN", "/home/lumen/.npm-global/bin/claude")
    for sub in ("sessions", "worktrees"):
        os.makedirs(os.path.join(data_dir, sub), exist_ok=True)


def cmd_serve(args: argparse.Namespace) -> int:
    _configure_env(args.data_dir, args.port)
    import uvicorn
    uvicorn.run("despag.app:app", host="127.0.0.1", port=args.port, log_level="info")
    return 0


def cmd_conformance(args: argparse.Namespace) -> int:
    if os.environ.get("DESPAG_API_TOKEN_FILE") != os.path.join(args.data_dir, "despag-api.token"):
        os.environ.setdefault("DESPAG_API_TOKEN_FILE", os.path.join(args.data_dir, "despag-api.token"))
    token = open(os.path.join(args.data_dir, "despag-api.token")).read().strip()
    sys.path.insert(0, CONFORMANCE_SRC)
    from uhp_conformance.cli import main as conformance_main

    return conformance_main([
        "--base-url", f"http://127.0.0.1:{args.port}",
        "--api-key", token,
        "--class", args.cls,
        "--harness-id", args.harness_id,
        "--json", args.json,
        "--plain",
    ])


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="run_p1_exit")
    sub = p.add_subparsers(dest="cmd", required=True)

    p_serve = sub.add_parser("serve")
    p_serve.add_argument("--port", type=int, default=8795)
    p_serve.add_argument("--data-dir", required=True)
    p_serve.set_defaults(func=cmd_serve)

    p_conf = sub.add_parser("conformance")
    p_conf.add_argument("--port", type=int, default=8795)
    p_conf.add_argument("--data-dir", required=True)
    p_conf.add_argument("--class", dest="cls", default="core")
    p_conf.add_argument("--harness-id", default="chrn_litellm-mechanical")
    p_conf.add_argument("--json", required=True)
    p_conf.set_defaults(func=cmd_conformance)

    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
