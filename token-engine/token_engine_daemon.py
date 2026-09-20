#!/usr/bin/env python3
"""token-engine daemon — the one process that may hold a credential (wq#2270).

Design: research/TOKEN_ENGINE_DESIGN.md §1 (architecture) + §5 (worker isolation).
Runs as uid `tokeneng` from a root-owned install dir (/usr/local/lib/token-engine)
so the worker uid can neither read the secret store nor edit the code that can.

Wire protocol: one JSON object per connection, newline-terminated, over
AF_UNIX SOCK_STREAM at /run/token-engine/broker.sock (0660 tokeneng:token-client).
Request:  {"verb", "argv", "ticket", "attempt", "worker",
           "grant_file", "grant_sha"}
Response: {"ok", "verb", "ticket", "rule", "reason", "result", "elapsed_s"}
          — exactly these keys, asserted before send (§1.2).

Four properties, each enforced here, none delegated to the client:
  1. identity from SO_PEERCRED, never from the request body;
  2. the grant is re-read and re-hashed by this process with
     fleet_exec_policy.load_grant semantics — every branch a DENY, no exception
     path that could fail open;
  3. every call (allow or deny) appends one audit line carrying the ticket id to
     the global fleet-exec.jsonl AND the per-worker events/<worker>.jsonl, the
     same dual sink as fleet-exec:112-114; an unappendable audit fails the verb;
  4. every outbound frame passes a response-schema assertion plus the
     mask-secrets patterns as a tripwire: a hit FAILS the verb and raises ntfy —
     a masked token is a burned token, not a save (instinct #4).

T2 ships the broker skeleton with `ping` and `mask-selftest`; provider verbs
(git-fetch, pr-open, gateway-key, …) land in T1/T4/T10 via VERBS below.
"""

import json
import os
import re
import socket
import struct
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fleet_exec_policy as P  # root-owned copy installed next to this file

SOCK_DIR = os.environ.get("TOKEN_ENGINE_RUNTIME_DIR", "/run/token-engine")
SOCK_PATH = os.path.join(SOCK_DIR, "broker.sock")
SECRETS_DIR = os.environ.get("TOKEN_ENGINE_STATE_DIR", "/var/lib/token-engine")
CLIENT_GROUP = "token-client"
# Same dual audit sink as fleet-exec:112-114.
AUDIT_PATH = os.environ.get("FLEET_EXEC_AUDIT_PATH",
                            "/home/lumen/foreman/events/fleet-exec.jsonl")
EVENTS_DIR = os.path.dirname(AUDIT_PATH)
NTFY_BIN = "/home/lumen/bin/ntfy-send"
ALERT_TOPIC = "lumen-alerts"

MAX_FRAME = 65536
CONN_TIMEOUT_S = 10
# The worker id names the per-worker sink file; keep it a bare filename.
WORKER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")

# Mirror of /home/lumen/bin/mask-secrets, used as a tripwire (never a rewrite).
MASK_PATTERNS = [
    ("github_token", re.compile(r"(?:ghp_|gho_|ghu_|ghs_|ghr_|github_pat_)[A-Za-z0-9_]{6,}")),
    ("cloudflare_token", re.compile(r"cfut_?[A-Za-z0-9_.-]{6,}")),
    ("sk_key", re.compile(r"sk-[a-zA-Z-]*[A-Za-z0-9_-]{16,}")),
    ("slack_token", re.compile(r"xox[bpasr]-[A-Za-z0-9-]{8,}")),
    ("aws_key_id", re.compile(r"AKIA[0-9A-Z]{16}")),
    ("kv_secret", re.compile(
        r'(?:TOKEN|KEY|SECRET|PASSWORD|PASSWD|PAT)["\s]*[:=]["\s]*[A-Za-z0-9._-]{12,}',
        re.IGNORECASE)),
]

RESPONSE_KEYS = {"ok", "verb", "ticket", "rule", "reason", "result", "elapsed_s"}


def mask_hits(text):
    return [name for name, pat in MASK_PATTERNS if pat.search(text)]


def assert_response_schema(frame):
    """Raise ValueError unless the frame is exactly the documented shape with
    JSON-scalar leaves. Runs on every outbound frame before the mask tripwire."""
    if set(frame) != RESPONSE_KEYS:
        raise ValueError(f"response_keys: {sorted(set(frame) ^ RESPONSE_KEYS)}")
    if not isinstance(frame["ok"], bool):
        raise ValueError("response_ok_not_bool")
    for key in ("verb", "ticket", "rule", "reason"):
        if not isinstance(frame[key], str):
            raise ValueError(f"response_{key}_not_str")
    if not isinstance(frame["result"], dict):
        raise ValueError("response_result_not_dict")
    for k, v in frame["result"].items():
        if not isinstance(k, str) or not isinstance(v, (str, int, float, bool, type(None))):
            raise ValueError(f"response_result_leaf: {k!r}")


def notify(priority, title, message):
    """Best effort: an alert that fails to publish must never change the
    broker's verdict (same contract as fleet-exec.notify)."""
    try:
        subprocess.run([NTFY_BIN, "-t", ALERT_TOPIC, "-p", priority, "-T", title,
                        message], capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.TimeoutExpired):
        pass


def audit(req, peer, decision, rule, **extra):
    """One line per call to both sinks, ticket id always present. Raises on an
    unwritable sink — the caller turns that into a failed verb, because an
    unaudited action must not proceed."""
    rec = P.audit_record(
        ticket=str(req.get("ticket", "")), attempt=req.get("attempt"),
        worker=str(req.get("worker", "")), node="token-engine",
        verb=str(req.get("verb", "")), argv=req.get("argv") or [],
        decision=decision, rule=rule, grant_sha=req.get("grant_sha"), **extra)
    rec["peer"] = {"uid": peer[1], "pid": peer[0]}
    P.append_jsonl(AUDIT_PATH, rec)
    worker = str(req.get("worker", ""))
    if WORKER_RE.match(worker):
        P.append_jsonl(os.path.join(EVENTS_DIR, f"{worker}.jsonl"), rec)


# ---------------------------------------------------------------- verbs (T2)

def verb_ping(req, grant):
    return {"pong": True, "daemon_uid": os.getuid(),
            "secrets_dir": SECRETS_DIR}


def verb_mask_selftest(req, grant):
    """Deliberately returns a credential-shaped string so the outbound tripwire
    fires. Proves property 4 end to end without a real secret in play."""
    return {"leak": "ghp_" + "selftestselftest0000"}


# verb -> (handler, grant kind, grant node). Every T2 verb needs a
# {"kind": "token_engine", "node": "daemon"} grant entry; provider verbs added
# by later children bring their own kinds per TOKEN_ENGINE_DESIGN.md §2.
VERBS = {
    "ping": (verb_ping, "token_engine", "daemon"),
    "mask-selftest": (verb_mask_selftest, "token_engine", "daemon"),
}


# ------------------------------------------------------------------- serving

def handle(req, peer):
    """Returns (ok, rule, reason, result, decision). Deny-by-default: the only
    path to ok=True walks every check."""
    verb = req.get("verb")
    if verb not in VERBS:
        return False, "verb_unknown", f"no such verb {verb!r}", {}, "deny"
    worker = str(req.get("worker", ""))
    if not WORKER_RE.match(worker):
        return False, "worker_malformed", "worker id fails shape check", {}, "deny"
    handler, kind, node = VERBS[verb]
    try:
        grant, why = P.load_grant(req.get("grant_file"), req.get("grant_sha"),
                                  req.get("ticket"), worker)
    except Exception as e:  # load_grant promises DENY not raise; belt anyway
        return False, "grant_check_crashed", f"{type(e).__name__}: {e}", {}, "deny"
    if grant is None:
        return False, f"grant:{why}", "grant validation failed", {}, "deny"
    if not P.find_grants(grant, kind, node):
        return False, "grant_no_match", f"no grant for kind={kind} node={node}", {}, "deny"
    try:
        result = handler(req, grant)
    except Exception as e:
        return False, "verb_crashed", f"{type(e).__name__}: {e}", {}, "error"
    return True, f"grant:{kind}", "ok", result, "allow"


def respond(conn, req, peer, ok, rule, reason, result, decision, t0):
    frame = {"ok": ok, "verb": str(req.get("verb", "")),
             "ticket": str(req.get("ticket", "")), "rule": rule,
             "reason": reason, "result": result,
             "elapsed_s": round(time.monotonic() - t0, 3)}
    try:
        assert_response_schema(frame)
        hits = mask_hits(json.dumps(frame, sort_keys=True))
    except ValueError as e:
        frame.update(ok=False, rule="response_schema", reason=str(e), result={})
        decision, hits = "error", []
    if hits:
        # Tripwire, not sanitiser: the verb FAILS, the alert fires, nothing of
        # the original result leaves the daemon (instinct #4 / design §1.2).
        frame.update(ok=False, rule="mask_tripwire",
                     reason=f"outbound frame matched mask patterns: {hits}",
                     result={})
        decision = "error"
        notify("high", "token-engine mask tripwire",
               f"verb={frame['verb']} ticket={frame['ticket']} patterns={hits} "
               "— treat as a burned credential, rotate today")
    try:
        audit(req, peer, decision, frame["rule"],
              exit_code=0 if frame["ok"] else 1,
              elapsed_s=frame["elapsed_s"])
    except OSError as e:
        frame.update(ok=False, rule="audit_unwritable", reason=str(e), result={})
    conn.sendall((json.dumps(frame, sort_keys=True) + "\n").encode())


def serve_conn(conn):
    conn.settimeout(CONN_TIMEOUT_S)
    pid, uid, gid = struct.unpack("3i", conn.getsockopt(
        socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i")))
    peer = (pid, uid, gid)
    t0 = time.monotonic()
    buf = b""
    while b"\n" not in buf and len(buf) < MAX_FRAME:
        chunk = conn.recv(4096)
        if not chunk:
            break
        buf += chunk
    try:
        req = json.loads(buf.split(b"\n", 1)[0].decode())
        if not isinstance(req, dict):
            raise ValueError("request not an object")
    except (ValueError, UnicodeDecodeError) as e:
        respond(conn, {}, peer, False, "request_malformed", str(e), {}, "deny", t0)
        return
    ok, rule, reason, result, decision = handle(req, peer)
    respond(conn, req, peer, ok, rule, reason, result, decision, t0)


def main():
    if os.getuid() == 0:
        sys.exit("refusing to run as root: unit must set User=tokeneng")
    import grp
    client_gid = grp.getgrnam(CLIENT_GROUP).gr_gid
    try:
        os.unlink(SOCK_PATH)
    except FileNotFoundError:
        pass
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    srv.bind(SOCK_PATH)
    os.chown(SOCK_PATH, -1, client_gid)
    os.chmod(SOCK_PATH, 0o660)
    srv.listen(16)
    print(f"token-engine listening on {SOCK_PATH} uid={os.getuid()}", flush=True)
    while True:
        conn, _ = srv.accept()
        try:
            serve_conn(conn)
        except Exception as e:
            # One bad connection must not take the broker down; the failure is
            # still visible in the journal.
            print(f"conn error: {type(e).__name__}: {e}", file=sys.stderr, flush=True)
        finally:
            conn.close()


if __name__ == "__main__":
    main()
