#!/usr/bin/env python3
"""Minted-token ledger + revocation for the token-engine daemon (wq#2277, T9).

Design: research/TOKEN_ENGINE_DESIGN.md §3.4. Every token the daemon mints is
recorded here BEFORE the mint response leaves the daemon: one JSON file per
ticket under <state>/minted/, 0600, readable by uid tokeneng only. The file —
not the daemon's memory — is the authority, because the two revocation paths
have different lifetimes:

  1. `ticket-end <id>` (broker verb, fired by guard.sh --lifecycle end at
     SessionEnd) revokes the ticket's tokens while the daemon is up;
  2. `reap_all()` on daemon start revokes EVERY ledgered token, because a
     kill -9'd worker never fires SessionEnd and a restarted daemon has no
     memory. Sweeping live tickets too is safe by design: workers never hold
     token values (§1), so the next broker verb just re-mints.

Both paths are idempotent: a missing ledger file, a missing entry, or a
provider answering "that token is already dead" (401/404, unlinked file) all
count as revoked. An entry whose provider call FAILS (network, 5xx) stays in
the ledger so the next sweep retries it — revocation is never silently
dropped.

Secrets discipline: revoke payloads (which must carry the token — GitHub App
revocation authenticates WITH the token being revoked) live only in the 0600
ledger file inside the tokeneng-only state dir. No function here returns,
logs, or raises a token value.
"""

import json
import os
import re
import urllib.error
import urllib.request

TICKET_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
HTTP_TIMEOUT_S = 15

# In-memory map, ticket -> [token_id, ...] (§3.4 "drops the ticket from its
# in-memory map"). A cache over the ledger files, never the authority: it is
# empty after a daemon restart, which is exactly why reap_all() reads disk.
MINTED = {}


def state_dir():
    # Read per call, not at import: tests and gate replays re-point it by env.
    return os.environ.get("TOKEN_ENGINE_STATE_DIR", "/var/lib/token-engine")


def ledger_dir():
    return os.path.join(state_dir(), "minted")


def _ticket_path(ticket):
    ticket = str(ticket)
    if not TICKET_RE.match(ticket):
        raise ValueError("ticket id fails shape check")
    return os.path.join(ledger_dir(), f"{ticket}.json")


def _load(path):
    try:
        with open(path, "rb") as f:
            doc = json.load(f)
    except FileNotFoundError:
        return []
    entries = doc.get("entries") if isinstance(doc, dict) else None
    return entries if isinstance(entries, list) else []


def _store(path, ticket, entries):
    if not entries:
        try:
            os.unlink(path)
        except FileNotFoundError:
            pass
        return
    tmp = path + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump({"ticket": str(ticket), "entries": entries}, f, sort_keys=True)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def record_mint(ticket, provider, token_id, worker, revoke, minted_at):
    """Append one minted token to the ticket's ledger file. Must be called
    before the mint result is sent to the client: an unledgered token is an
    unrevocable token."""
    if provider not in REVOKERS:
        raise ValueError(f"no revoker for provider {provider!r}")
    path = _ticket_path(ticket)
    os.makedirs(ledger_dir(), mode=0o700, exist_ok=True)
    entries = _load(path)
    entries.append({"provider": provider, "token_id": str(token_id),
                    "worker": str(worker), "minted_at": minted_at,
                    "revoke": revoke})
    _store(path, ticket, entries)
    MINTED.setdefault(str(ticket), []).append(str(token_id))


# ------------------------------------------------------------- providers

def _revoke_github_app(revoke):
    """DELETE /installation/token, authenticated with the token itself
    (design §3.4 / §4). 204 = revoked now; 401 = the token is already dead
    (revoked earlier or expired) — idempotent success either way."""
    token = revoke.get("token", "")
    req = urllib.request.Request(
        revoke.get("api_url", "https://api.github.com") + "/installation/token",
        method="DELETE",
        headers={"Authorization": f"token {token}",
                 "Accept": "application/vnd.github+json"})
    try:
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT_S) as resp:
            status = resp.status
    except urllib.error.HTTPError as e:
        status = e.code
    except (urllib.error.URLError, OSError) as e:
        return False, f"unreachable: {type(e).__name__}"
    if status in (204, 401, 404):
        return True, f"http_{status}"
    return False, f"http_{status}"


def _revoke_test_file(revoke):
    """Stub provider for gates/tests: a token is live iff its marker file
    exists under the state dir. Revoke = unlink; already gone = success."""
    path = os.path.realpath(str(revoke.get("live_path", "")))
    root = os.path.realpath(state_dir())
    if not path.startswith(root + os.sep):
        return False, "live_path outside state dir"
    try:
        os.unlink(path)
        return True, "unlinked"
    except FileNotFoundError:
        return True, "already_gone"
    except OSError as e:
        return False, f"unlink: {type(e).__name__}"


def _revoke_litellm_key(revoke):
    """T10 (wq#2278, §7 rank 5): POST /key/delete with the engine-held admin
    key. 200 = deleted; 400/404 = the gateway no longer knows the key (already
    deleted, or expired past its `duration`) — idempotent success. The handoff
    file is unlinked first so a failed provider call still never leaves the
    value readable outside the ledger."""
    key_file = revoke.get("key_file")
    if key_file:
        try:
            os.unlink(key_file)
        except OSError:
            pass
    admin_file = os.path.join(state_dir(), "secrets",
                              os.path.basename(str(revoke.get("admin_key_file") or
                                                   "litellm-admin.token")))
    try:
        with open(admin_file) as f:
            admin = f.read().strip()
    except OSError:
        return False, "admin key unreadable"
    body = json.dumps({"keys": [revoke.get("key", "")]}).encode()
    req = urllib.request.Request(
        str(revoke.get("base_url", "http://localhost:4000")).rstrip("/") + "/key/delete",
        data=body, method="POST",
        headers={"Authorization": f"Bearer {admin}",
                 "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT_S) as resp:
            status = resp.status
    except urllib.error.HTTPError as e:
        status = e.code
    except (urllib.error.URLError, OSError) as e:
        return False, f"unreachable: {type(e).__name__}"
    if status in (200, 400, 404):
        return True, f"http_{status}"
    return False, f"http_{status}"


REVOKERS = {
    "github-app": _revoke_github_app,
    "test-file": _revoke_test_file,
    "litellm-key": _revoke_litellm_key,
}


# ------------------------------------------------------------ revocation

def revoke_ticket(ticket):
    """Revoke every ledgered token for one ticket. Returns flat counters
    (JSON-scalar leaves only — the broker response schema requires it).
    Entries that fail stay ledgered for the next sweep."""
    path = _ticket_path(ticket)
    entries = _load(path)
    had = len(entries)
    kept = []
    for e in entries:
        revoker = REVOKERS.get(e.get("provider"))
        if revoker is None:
            kept.append(e)  # unknown provider: keep, never drop silently
            continue
        ok, _reason = revoker(e.get("revoke") or {})
        if not ok:
            kept.append(e)
    _store(path, ticket, kept)
    if kept:
        MINTED[str(ticket)] = [e.get("token_id") for e in kept]
    else:
        MINTED.pop(str(ticket), None)
    return {"had_entries": had, "revoked": had - len(kept), "failed": len(kept)}


def revoke_one(ticket, token_id):
    """Revoke a single ledgered token by id, scoped to one ticket (the
    gateway-key revoke verb). Same idempotency contract as revoke_ticket:
    an id the ledger no longer holds reports found=0, not an error."""
    path = _ticket_path(ticket)
    entries = _load(path)
    kept, found, failed = [], 0, 0
    for e in entries:
        if e.get("token_id") != str(token_id):
            kept.append(e)
            continue
        found += 1
        revoker = REVOKERS.get(e.get("provider"))
        ok = False
        if revoker is not None:
            ok, _reason = revoker(e.get("revoke") or {})
        if not ok:
            failed += 1
            kept.append(e)
    _store(path, ticket, kept)
    if kept:
        MINTED[str(ticket)] = [e.get("token_id") for e in kept]
    else:
        MINTED.pop(str(ticket), None)
    return {"found": found, "revoked": found - failed, "failed": failed}


def reap_all():
    """Daemon-start sweep (§3.4): revoke everything in the ledger, live
    tickets included — a killed worker never fires SessionEnd, and workers
    re-mint transparently because they never hold token values."""
    totals = {"tickets": 0, "revoked": 0, "failed": 0}
    try:
        names = sorted(os.listdir(ledger_dir()))
    except FileNotFoundError:
        return totals
    for name in names:
        if not name.endswith(".json"):
            continue
        ticket = name[:-5]
        if not TICKET_RE.match(ticket):
            continue
        r = revoke_ticket(ticket)
        totals["tickets"] += 1
        totals["revoked"] += r["revoked"]
        totals["failed"] += r["failed"]
    return totals
