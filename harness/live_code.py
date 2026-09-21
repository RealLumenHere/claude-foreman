"""wq#2404: a module as its FILE defines it right now, not as the caller imported it.

An fworker process scores every line of a stream with the code it imported at spawn,
and it holds a whole TICKET — wq#2278's process spawned attempt 1 at 22:14:43Z and was
still filing alarms at 23:01:44Z, across an attempt boundary, 75 minutes after its
import. Every fix that lands on the alarm path in between is invisible to it. On
2026-09-20 that was four fixes in one evening (22:54, 22:57, 23:09, 23:31), and the
runs in flight kept filing pri-1 rotate-today rows under the rules those fixes retired.

wq#2327 solved that for the rule table by re-reading masking.py per call. This is that
mechanism, factored out, so the provenance screen (burn_filter) gets it too and the
next alarm-path module can have it for two lines. The contract every caller inherits:

  - the fingerprint is taken at IMPORT time, by the client module, and passed in.
    Taken on first call instead, a file already rewritten by then would be recorded as
    the baseline and never reload.
  - a fresh copy must EXECUTE and must pass the caller's `verify` self-test. Silencing
    a tripwire is worse than a false page, so a half-written deploy, an emptied table
    or a screen that stopped screening falls back to the imported module.
  - the worst case is therefore today's behaviour, which is what lets the call sites
    use the result one-directionally (they only ever DROP an alarm the deployed code
    no longer raises, never add one it does).
"""
import importlib.util
import os

# (path, mtime_ns, size) -> module. Keyed by path, so several modules share it.
_LOADED = {}


def fingerprint(path):
    """(abspath, mtime_ns, size), or None when the source is not on disk.

    Client modules call this at import (`_SELF = live_code.fingerprint(__file__)`) and
    hand the result back to deployed(): that comparison is what makes the common case —
    nothing deployed since spawn — free, returning the imported module by identity
    without a second exec.
    """
    try:
        st = os.stat(path)
    except OSError:      # pragma: no cover - zipimport, -c, deleted source
        return None
    return (os.path.abspath(path), st.st_mtime_ns, st.st_size)


def deployed(module, self_key, verify, path=None):
    """`module` as `path` (default: the module's own file) defines it right now.

    Returns the imported `module` itself — by identity, so callers can test for it —
    whenever the file is unchanged, unreadable, unloadable, or fails `verify(mod)`.
    """
    path = os.path.abspath(path or module.__file__)
    key = fingerprint(path)
    if key is None or key == self_key:
        return module
    hit = _LOADED.get(path)
    if hit and hit[0] == key:
        return hit[1]
    try:
        spec = importlib.util.spec_from_file_location(
            f"{module.__name__}_deployed", path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        if not verify(mod):
            raise ValueError(f"{path} failed its self-test")
    except Exception:  # noqa: BLE001 - any failure means keep the imported module
        return module
    _LOADED[path] = (key, mod)
    return mod
