"""Shared fixture helpers for energize_confirm (wq#5216).

energize_confirm reads no environment variable (an env-chosen verifier path would
let a caller choose the passphrase), so tests redirect it by patching module
attributes in-process, and by running subprocesses through a wrapper script that
patches the same attributes before handing over. A wrapper only redirects the
process it launches itself, so it is no bypass: the production hook and MCP
servers never run under it.
"""
from __future__ import annotations

import os

HARNESS = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
PASSPHRASE = "test-only passphrase " + "q" * 4
WRONG = "not the passphrase " + "z" * 4


def install(ec, tmp_path, monkeypatch, *, with_verifier=True):
    """Point `ec` at tmp dirs, cheapen scrypt, and (by default) set PASSPHRASE.
    Returns (token_dir, verifier_path)."""
    token_dir = str(tmp_path / "re" / ".energize-confirm")
    verifier = str(tmp_path / "config" / "energize-confirm" / "verifier.json")
    monkeypatch.setattr(ec, "TOKEN_DIR", token_dir)
    monkeypatch.setattr(ec, "VERIFIER_DIR", os.path.dirname(verifier))
    monkeypatch.setattr(ec, "VERIFIER", verifier)
    monkeypatch.setattr(ec, "SCRYPT_N", 2 ** 10)  # params are stored in the verifier
    monkeypatch.setattr(ec, "FAIL_DELAY", 0)
    if with_verifier:
        ec._write_verifier(PASSPHRASE)
    return token_dir, verifier


def mint(ec, capability, passphrase=PASSPHRASE, **kw):
    return ec.issue_token(capability, _getpass=lambda _prompt: passphrase, **kw)


def wrapper(tmp_path, token_dir, verifier, target):
    """Write a python script that patches energize_confirm onto the tmp dirs, then
    runs `target`: "ec" for energize_confirm's CLI, or a path run as __main__
    (e.g. action_gate.py). Returns its path."""
    body = (
        "import runpy, sys\n"
        f"sys.path.insert(0, {HARNESS!r})\n"
        "import energize_confirm as ec\n"
        f"ec.TOKEN_DIR = {token_dir!r}\n"
        f"ec.VERIFIER = {verifier!r}\n"
        f"ec.VERIFIER_DIR = {os.path.dirname(verifier)!r}\n"
        "ec.FAIL_DELAY = 0\n"
    )
    if target == "ec":
        body += "sys.exit(ec._main(sys.argv[1:]))\n"
    else:
        body += (f"sys.argv = [{target!r}] + sys.argv[1:]\n"
                 f"runpy.run_path({target!r}, run_name='__main__')\n")
    path = tmp_path / f"ec_wrapper_{'ec' if target == 'ec' else os.path.basename(target)}.py"
    path.write_text(body)
    return str(path)
