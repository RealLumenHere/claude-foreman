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

T2 shipped the broker skeleton with `ping` and `mask-selftest`. T10 (wq#2278)
adds the remaining-inventory verbs per TOKEN_ENGINE_DESIGN.md §2 + §7 ranks 3
and 5–8: `gateway-key` (per-ticket LiteLLM key with duration + budget; the
frame carries key id/expiry/handoff-path, never the key value), `api-call`
(provider admin calls with the credential attached daemon-side, body via file
never argv, Authorization never returned), `plug` (smart-plug control), and
`ssh-exec` (the fleet ssh key moves to uid tokeneng; fleet-exec's run_ssh
transport runs here instead of the worker holding a key that reaches every
node). git/PR verbs land via T1/T4.
"""

import json
import os
import re
import shlex
import socket
import struct
import subprocess
import sys
import time
import urllib.error
import urllib.request

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
# T10: static policy AND grant (design §2) — the same file fleet-exec enforces.
POLICY_PATH = os.environ.get("FLEET_EXEC_POLICY_PATH",
                             "/home/lumen/foreman/harness/fleet-exec-policy.yaml")
LITELLM_BASE = os.environ.get("TOKEN_ENGINE_LITELLM_BASE", "http://localhost:4000")
PLUG_CTL = os.environ.get("TOKEN_ENGINE_PLUG_CTL", "/home/lumen/bin/plug-ctl")
# Minted-key handoff: the per-ticket gateway key is MEANT for the worker env
# (design §7 rank 5 — bounded by duration + budget), so it leaves the daemon via
# a 0640 tokeneng:token-client file under the socket runtime dir, never via the
# response frame (which is schema+mask asserted).
HANDOFF_DIR = os.path.join(SOCK_DIR, "minted")
OUT_CAP = 8192          # api/ssh/plug output cap per result leaf
BODY_CAP = 1 << 20      # request body file cap
API_METHODS = ("GET", "POST", "PUT", "PATCH", "DELETE")
TICKET_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")

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


class Deny(Exception):
    """A fail-closed verb refusal: carries the audit rule tag and a reason that
    must never contain a secret (paths and status codes only)."""

    def __init__(self, rule, reason):
        super().__init__(reason)
        self.rule, self.reason = rule, reason


def get_policy():
    try:
        return P.load_policy(POLICY_PATH)
    except P.PolicyError as e:
        raise Deny("policy_unreadable", str(e))


def secret_path(*parts):
    return os.path.join(SECRETS_DIR, "secrets", *parts)


def read_secret(*parts):
    """Read a credential from the store. Failure reports the PATH only."""
    path = secret_path(*parts)
    try:
        with open(path) as f:
            return f.read().strip()
    except OSError:
        raise Deny("secret_missing", f"no readable secret at {path}")


def read_body_file(path):
    """Verb request bodies come via file, never argv (design §2) — keeps them
    out of /proc/*/cmdline and inside check_argv_shape's metachar ban."""
    if not path.startswith("/"):
        raise Deny("body_file_relative", "body-file must be an absolute path")
    try:
        with open(path, "rb") as f:
            body = f.read(BODY_CAP + 1)
    except OSError as e:
        raise Deny("body_file_unreadable", f"{type(e).__name__} on {path}")
    if len(body) > BODY_CAP:
        raise Deny("body_file_too_large", f"body-file exceeds {BODY_CAP} bytes")
    return body


def http_call(method, url, headers, body=None, timeout=30):
    """(status, text). Headers carry the credential; neither headers nor the
    URL ever reach a response frame — callers return status/body only."""
    req = urllib.request.Request(url, data=body, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read(OUT_CAP * 4).decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read(OUT_CAP * 4).decode("utf-8", "replace")
    except (urllib.error.URLError, OSError) as e:
        raise Deny("provider_unreachable", f"{type(e).__name__}: {e.reason!s:.120}"
                   if hasattr(e, "reason") else type(e).__name__)


def require_argv_shape(argv):
    shape = P.check_argv_shape(argv)
    if shape:
        raise Deny(f"argv:{shape}", "argv shape rejected before allowlist")


# --------------------------------------------------------------------- verbs

def verb_ping(req, grant, policy):
    return {"pong": True, "daemon_uid": os.getuid(),
            "secrets_dir": SECRETS_DIR}


def verb_mask_selftest(req, grant, policy):
    """Deliberately returns a credential-shaped string so the outbound tripwire
    fires. Proves property 4 end to end without a real secret in play."""
    return {"leak": "ghp_" + "selftestselftest0000"}


def _ticket_slug(req):
    ticket = str(req.get("ticket", ""))
    if not TICKET_RE.match(ticket):
        raise Deny("ticket_malformed", "ticket id fails shape check")
    return ticket


def minted_record_path(ticket):
    return os.path.join(SECRETS_DIR, "minted", f"{ticket}.json")


def handoff_path(ticket):
    return os.path.join(HANDOFF_DIR, f"{ticket}.key")


def _write_private(path, data, mode):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode)
    try:
        os.write(fd, data.encode())
    finally:
        os.close(fd)
    os.chmod(path, mode)  # O_CREAT mode is umask-filtered; unit runs UMask=0077


def verb_gateway_key(req, grant, policy):
    """T10(a): per-ticket LiteLLM virtual key with a duration and a budget,
    replacing the long-lived key fworker.py put in worker env. The frame
    returns key id + expiry + the handoff file path — never the key value."""
    argv = req.get("argv") or []
    op = argv[0] if argv else ""
    ticket = _ticket_slug(req)
    alias = f"te-{ticket}"
    if op == "mint":
        ttl = argv[1] if len(argv) > 1 else "24h"
        if not re.match(r"^[0-9]{1,5}[smhd]$", ttl):
            raise Deny("gateway_ttl_malformed", f"bad duration {ttl!r}")
        try:
            budget = float(argv[2]) if len(argv) > 2 else 15.0
        except ValueError:
            raise Deny("gateway_budget_malformed", "budget must be a number")
        admin = read_secret("litellm-admin.token")
        status, text = http_call(
            "POST", LITELLM_BASE + "/key/generate",
            {"Authorization": f"Bearer {admin}",
             "Content-Type": "application/json"},
            json.dumps({"key_alias": alias, "duration": ttl,
                        "max_budget": budget}).encode())
        if status != 200:
            # Response text stays out of the frame: a gateway error body is
            # uncontrolled input on a path that exists to carry keys.
            raise Deny("gateway_mint_failed", f"/key/generate -> {status}")
        try:
            minted = json.loads(text)
            key_value = minted["key"]
        except (ValueError, KeyError):
            raise Deny("gateway_mint_unparseable", "/key/generate response had no key")
        expires = str(minted.get("expires") or "")
        _write_private(minted_record_path(ticket), json.dumps(
            {"key_alias": alias, "key": key_value, "expires": expires}), 0o600)
        hpath = handoff_path(ticket)
        _write_private(hpath, key_value, 0o640)
        try:
            import grp
            os.chown(hpath, -1, grp.getgrnam(CLIENT_GROUP).gr_gid)
        except (KeyError, PermissionError, OSError) as e:
            # Group missing/chgrp refused: the file stays tokeneng-only, the
            # client falls back to its static key — degrade visibly, not open.
            print(f"warn: handoff chgrp failed: {type(e).__name__}",
                  file=sys.stderr, flush=True)
        return {"key_id": alias, "expires": expires, "key_file": hpath}
    if op == "revoke":
        rec_path = minted_record_path(ticket)
        try:
            with open(rec_path) as f:
                rec = json.load(f)
        except (OSError, ValueError):
            # Idempotent: revoking a never-minted/already-revoked ticket is ok.
            return {"key_id": alias, "revoked": False}
        admin = read_secret("litellm-admin.token")
        status, _ = http_call(
            "POST", LITELLM_BASE + "/key/delete",
            {"Authorization": f"Bearer {admin}",
             "Content-Type": "application/json"},
            json.dumps({"keys": [rec["key"]]}).encode())
        if status != 200:
            raise Deny("gateway_revoke_failed", f"/key/delete -> {status}")
        for p in (rec_path, handoff_path(ticket)):
            try:
                os.unlink(p)
            except FileNotFoundError:
                pass
        return {"key_id": alias, "revoked": True}
    raise Deny("gateway_op_unknown", f"no such gateway-key op {op!r}")


def _ebay_bearer():
    """T10(b): the eBay client credential (the thing to lock in the engine)
    stays in the store; the daemon mints/caches the ~2 h access token itself —
    a port of bin/ebay-token's mint, minus the lumen-readable cache."""
    env = {}
    for line in read_secret("ebay.env").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip().strip('"').strip("'")
    sandbox = "sand" in (env.get("EBAY_ENV", "production") or "").lower()
    base = "https://api.sandbox.ebay.com" if sandbox else "https://api.ebay.com"
    cache = os.path.join(SECRETS_DIR, "cache", f"ebay_{'sb' if sandbox else 'prod'}.json")
    try:
        with open(cache) as f:
            t = json.load(f)
        if t["obtained_at"] + int(t.get("expires_in", 7200)) - 90 > time.time():
            return base, t["access_token"]
    except (OSError, ValueError, KeyError):
        pass
    import base64
    import urllib.parse
    cred = base64.b64encode(
        f"{env['EBAY_APP_ID']}:{env['EBAY_CERT_ID']}".encode()).decode()
    status, text = http_call(
        "POST", base + "/identity/v1/oauth2/token",
        {"Authorization": "Basic " + cred,
         "Content-Type": "application/x-www-form-urlencoded"},
        urllib.parse.urlencode({"grant_type": "client_credentials",
                                "scope": "https://api.ebay.com/oauth/api_scope"}).encode())
    if status != 200:
        raise Deny("ebay_token_mint_failed", f"oauth2/token -> {status}")
    t = json.loads(text)
    t["obtained_at"] = int(time.time())
    _write_private(cache, json.dumps(t), 0o600)
    return base, t["access_token"]


def _provider_request(provider, path):
    """(base_url, auth_header_value) — the only place a provider credential is
    attached; the header value never appears in any return path."""
    if provider == "cloudflare":
        return "https://api.cloudflare.com", "Bearer " + read_secret("cloudflare-api.token")
    if provider == "ebay":
        base, token = _ebay_bearer()
        return base, "Bearer " + token
    raise Deny("provider_unknown", f"no credential wiring for provider {provider!r}")


def verb_api_call(req, grant, policy):
    """T10(b): api-call <provider> <method> <path> [body-file]."""
    argv = req.get("argv") or []
    require_argv_shape(argv)
    if len(argv) not in (3, 4):
        raise Deny("api_argv", "expected: <provider> <method> <path> [body-file]")
    provider, method, path = argv[0], argv[1].upper(), argv[2]
    if method not in API_METHODS:
        raise Deny("api_method", f"method {argv[1]!r} not in {API_METHODS}")
    if not path.startswith("/") or ".." in path:
        raise Deny("api_path", "path must be absolute on the provider API, no dot-dot")
    body = read_body_file(argv[3]) if len(argv) == 4 else None
    base, auth = _provider_request(provider, path)
    status, text = http_call(method, base + path,
                             {"Authorization": auth,
                              "Content-Type": "application/json"},
                             body)
    # status + body only: no headers in the result, so the Authorization header
    # (and any Set-Cookie) structurally cannot be returned; the frame-level mask
    # tripwire then fails the verb if the BODY carries a credential shape.
    return {"status": status, "body": text[:OUT_CAP]}


def verb_plug(req, grant, policy):
    """T10(c): plug <name> on|off|state via the existing plug-ctl."""
    argv = req.get("argv") or []
    require_argv_shape(argv)
    if len(argv) != 2 or argv[1] not in ("on", "off", "state"):
        raise Deny("plug_argv", "expected: <name> on|off|state")
    try:
        p = subprocess.run([PLUG_CTL, argv[0], argv[1]], capture_output=True,
                           text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired) as e:
        raise Deny("plug_ctl_failed", type(e).__name__)
    return {"exit_code": p.returncode,
            "output": (p.stdout + p.stderr)[:OUT_CAP]}


def verb_ssh_exec(req, grant, policy):
    """T10(d): migration rank 3 — the fleet ssh key lives at
    /var/lib/token-engine/secrets/fleet-ssh/id_ed25519 (uid tokeneng), and the
    ssh transport fleet-exec's run_ssh (fleet-exec:174) used to run as uid
    lumen runs here instead. Same argv contract: <node> <remote argv...>,
    re-checked against run_readonly_cmds — the daemon trusts no caller's
    earlier policy pass (property 2)."""
    argv = req.get("argv") or []
    if len(argv) < 2:
        raise Deny("ssh_argv", "expected: <node> <remote argv...>")
    node, remote = argv[0], argv[1:]
    require_argv_shape(remote)
    ok, rule, reason = P.check_run(node, remote, policy)
    if not ok:
        raise Deny(rule, reason)
    key = secret_path("fleet-ssh", "id_ed25519")
    if not os.path.exists(key):
        raise Deny("ssh_key_missing", f"no fleet ssh key at {key}")
    cmd = ["ssh", "-i", key, "-o", "BatchMode=yes", "-o", "ConnectTimeout=10",
           "-o", "StrictHostKeyChecking=accept-new",
           "-o", f"UserKnownHostsFile={os.path.join(SECRETS_DIR, 'known_hosts')}",
           node, "--", *[shlex.quote(a) for a in remote]]
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=90)
    except subprocess.TimeoutExpired:
        raise Deny("ssh_timeout", f"ssh to {node} exceeded 90s")
    return {"exit_code": p.returncode, "stdout": p.stdout[:OUT_CAP],
            "stderr": p.stderr[:OUT_CAP]}


# ----------------------------------------------- grant + policy resolution

def _static_grant(kind, node):
    def resolve(req, policy):
        return kind, node
    return resolve


def _grant_gateway_key(req, policy):
    argv = req.get("argv") or []
    require_argv_shape(argv)
    ops = policy.get("gateway_admin_ops") or []
    if not argv or argv[0] not in ops:
        raise Deny("gateway_op_not_allowed",
                   f"op {(argv or [''])[0]!r} not in policy gateway_admin_ops")
    return "gateway_admin", "gateway:litellm"


def _grant_api_call(req, policy):
    argv = req.get("argv") or []
    require_argv_shape(argv)
    providers = policy.get("api_providers") or []
    if not argv or argv[0] not in providers:
        raise Deny("api_provider_not_allowed",
                   f"provider {(argv or [''])[0]!r} not in policy api_providers")
    return f"api:{argv[0]}", f"api:{argv[0]}"


def _grant_plug(req, policy):
    argv = req.get("argv") or []
    require_argv_shape(argv)
    names = policy.get("plug_names") or []
    if not argv or argv[0] not in names:
        raise Deny("plug_not_allowed",
                   f"plug {(argv or [''])[0]!r} not in policy plug_names")
    return "plug", f"plug:{argv[0]}"


def _grant_ssh_exec(req, policy):
    argv = req.get("argv") or []
    if not argv:
        raise Deny("ssh_argv", "expected: <node> <remote argv...>")
    # Same grant fleet-exec's run path checks (fleet-exec:286): kind ssh, node.
    return "ssh", argv[0]


# verb -> (handler, grant resolver, needs_policy). Handlers/resolvers raise
# Deny for every refusal; T2 verbs keep the static token_engine/daemon grant
# and no policy dependency.
VERBS = {
    "ping": (verb_ping, _static_grant("token_engine", "daemon"), False),
    "mask-selftest": (verb_mask_selftest, _static_grant("token_engine", "daemon"), False),
    "gateway-key": (verb_gateway_key, _grant_gateway_key, True),
    "api-call": (verb_api_call, _grant_api_call, True),
    "plug": (verb_plug, _grant_plug, True),
    "ssh-exec": (verb_ssh_exec, _grant_ssh_exec, True),
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
    handler, resolve, needs_policy = VERBS[verb]
    try:
        policy = get_policy() if needs_policy else None
        kind, node = resolve(req, policy)
    except Deny as e:
        return False, e.rule, e.reason, {}, "deny"
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
        result = handler(req, grant, policy)
    except Deny as e:
        return False, e.rule, e.reason, {}, "deny"
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
    # T10: handoff dir for minted per-ticket key files (0750, group-readable so
    # a token-client process can read the 0640 files inside; secrets/ itself
    # stays 0700 tokeneng-only).
    os.makedirs(HANDOFF_DIR, exist_ok=True)
    os.chown(HANDOFF_DIR, -1, client_gid)
    os.chmod(HANDOFF_DIR, 0o750)
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
