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

T2 shipped the broker skeleton with `ping` and `mask-selftest`; T4 the PR
family, T9 revocation, T10 gateway-key/api-call/plug/ssh-run, and T1
(wq#2271) the git family: `git-credential` behind the worktree credential
helper, plus zero-crossing `git-fetch`/`git-push`.
"""

import base64
import hashlib
import json
import os
import re
import shlex
import shutil
import socket
import struct
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fleet_exec_policy as P  # root-owned copy installed next to this file
import token_ledger as L      # minted-token ledger + revocation (wq#2277 T9)

# wq#2567: fingerprint of the source main() actually loaded, so deployed-vs-
# merged drift on this file (ISSUE-LOG rows 595/695/697/698) is visible from a
# running daemon instead of needing systemctl-timestamp + journalctl archaeology.
def _self_sha256():
    try:
        with open(os.path.abspath(__file__), "rb") as f:
            return hashlib.sha256(f.read()).hexdigest()
    except OSError:
        return None


SELF_SHA256 = _self_sha256()

SOCK_DIR = os.environ.get("TOKEN_ENGINE_RUNTIME_DIR", "/run/token-engine")
SOCK_PATH = os.path.join(SOCK_DIR, "broker.sock")
SECRETS_DIR = os.environ.get("TOKEN_ENGINE_STATE_DIR", "/var/lib/token-engine")
# Overridable so a gate replay can run an env-scoped daemon as an ordinary
# uid; the systemd unit sets neither env, so production keeps token-client.
CLIENT_GROUP = os.environ.get("TOKEN_ENGINE_CLIENT_GROUP", "token-client")
# wq#2561: belt-and-suspenders alongside CLIENT_GROUP — see the setfacl call
# in main() for why group-only access isn't enough.
CLIENT_USER = os.environ.get("TOKEN_ENGINE_CLIENT_USER", "lumen")
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
# wq#2292: anchored on a word boundary, and the github bodies carry their real
# lengths. Here the cost of a false positive is not just a wasted page — line 182
# FAILS the verb and discards the result — so an unanchored `ghs_` matching inside
# the repo name `highs_solver` is an availability bug, not just noise. Rationale
# and the kv_secret carve-out: harness/masking.py. wq#2400 added `_` to the class for
# the same reason the alnums are there — a prefix after `_` is an identifier tail.
_B = r"(?<![A-Za-z0-9_])"
MASK = "MASKED"   # masking.MASK — the literal every replacement there ends in


def _body(charset, quant):
    """masking._body (wq#2638): a body run that cannot cross the mask literal.

    Detect-only here, but the bug is worse in this copy than in masking.py's: a body
    class that swallows an already-masked value behind a live one made the whole thing
    one match ENDING in the literal, and the `endswith` screen in mask_hits() below
    then read it as this fleet's own output. The tripwire went silent on a live token
    (cloudflare, sk-key, github_pat_ and slack all reproduced) exactly when the text it
    was screening also carried masked residue — which outbound PR bodies and quoted log
    tails routinely do. Case-sensitive literal for the reason masking._body gives.
    """
    return r"(?:(?!(?-i:" + MASK + r"))" + charset + r")" + quant
# wq#2297 kv_secret label vocabulary — the copy of harness/masking.py:_KV_* that the
# daemon needs because it runs off that import path. See there for the reasoning.
_KV_NOUN = r"(?:secret|token|key|credentials?|passphrase|password|passwd)"
_KV_WORD = r"[A-Za-z][A-Za-z0-9]{1,20}(?:[ _-]{1,2}|(?-i:(?=[A-Z])))"
_KV_NOT_PUBLIC = r"(?!pub(?:lic)?[ _-]{0,2}key\b)"
_KV_BARE = r"(?:secret|token|credentials?|passphrase|password|passwd|bearer)"
_KV_LABEL = (r"(?:" + _KV_WORD + r")?" + _KV_NOT_PUBLIC + _KV_WORD + _KV_NOUN
             + r"|" + _KV_BARE)
_KV_POINTER = (r"(?!os\.environ)(?!os\.getenv)(?!(?:process\.)?env\.)"
               r"(?!\$\{?[A-Z_])(?![~.]{0,2}/)(?!<)")
# wq#2632: the call test reads the VALUE's shape, not the character after it. The
# trailing `(?![A-Za-z0-9._-]|\()` this replaces dropped the whole match for any value
# glued to a `(`, so an opaque credential in that shape passed the tripwire silently —
# here that means it reaches the broker result instead of failing the verb. Reasoning
# and the 485-file probe: harness/masking.py.
_KV_SEGMENTED = r"[A-Za-z_][A-Za-z0-9_.]*[_.][A-Za-z0-9_.]*"
_KV_HUMPED = r"(?-i:[A-Za-z][A-Za-z0-9]*[a-z][A-Z][A-Za-z0-9]*)"
_KV_NOT_CALLED = r"(?!(?:" + _KV_SEGMENTED + r"|" + _KV_HUMPED + r")\()"
_KV_BODY = r"[A-Za-z0-9._-]"
# wq#2677: masking._kv_rule and its probes, mirrored. The daemon DOES get a
# suppressed-but-seen channel, and it is the audit line rather than a mask-hits file:
# this copy is detect-only and fails closed, so it has no run dir to write forensics
# into, but it already writes one audit record per call to two sinks. A suppression
# there costs one key on a line that is being written anyway, and without it the
# daemon has the same blind spot the harness just closed — a carve-out that goes
# overbroad here does not merely stop paging, it stops FAILING the verb, and the
# unmasked result rides the broker response out (wq#2632's reasoning for this copy).
_KV_LABEL_ANY = (r"(?:" + _KV_WORD + r")?" + _KV_WORD + _KV_NOUN + r"|" + _KV_BARE)


def _kv_rule(label, guards):
    return (r'\b((?:' + label + r')\b["\s]*[:=]["\s]*)' + guards
            + _body(_KV_BODY, '{12,}'))


_KV_SECRET = re.compile(_kv_rule(_KV_LABEL, _KV_POINTER + _KV_NOT_CALLED),
                        re.IGNORECASE)
_KV_CANDIDATE = re.compile(_kv_rule(_KV_LABEL_ANY, ""), re.IGNORECASE)
_KV_SUPPRESSIONS = (
    ("pointer", re.compile(_kv_rule(_KV_LABEL_ANY, _KV_POINTER), re.IGNORECASE)),
    ("call", re.compile(_kv_rule(_KV_LABEL_ANY, _KV_NOT_CALLED), re.IGNORECASE)),
    ("public-key", re.compile(_kv_rule(_KV_LABEL, ""), re.IGNORECASE)),
)
MASK_PATTERNS = [
    ("github_token", re.compile(_B + r"(?:ghp_|gho_|ghu_|ghs_|ghr_)"
                                + _body(r"[A-Za-z0-9]", "{30,}"))),
    ("github_token", re.compile(_B + r"(?:github_pat_)"
                                + _body(r"[A-Za-z0-9_]", "{20,}"))),
    ("cloudflare_token", re.compile(_B + r"cfut_?"
                                    + _body(r"[A-Za-z0-9_.-]", "{6,}"))),
    # wq#2300: same prefix shape as masking.py, where a greedy `[a-zA-Z-]*` kept
    # part of the body in the replacement. Detect-only here, so this copy leaked
    # nothing — it moves to keep the two readable as one rule.
    ("sk_key", re.compile(_B + r"sk-(?:[a-zA-Z]+-)*"
                          + _body(r"[A-Za-z0-9_-]", "{16,}"))),
    ("slack_token", re.compile(_B + r"xox[bpasr]-"
                               + _body(r"[A-Za-z0-9-]", "{8,}"))),
    ("aws_key_id", re.compile(_B + r"AKIA" + _body(r"[0-9A-Z]", "{16}"))),
    # wq#2288/wq#2297: keep in lockstep with harness/masking.py — a called identifier
    # (`key = apply_key_allowlist(reg)`) is not a credential, and neither is a noun
    # buried in a longer word (`monkey:`) or a bare `key=`/`PAT =`. Here a false hit
    # does not just page: it fails the broker verb and drops the result. The daemon
    # runs as `tokeneng`, off the harness import path, so the vocabulary is copied
    # rather than imported; test_masking.test_token_engine_mirror_agrees pins them.
    ("kv_secret", _KV_SECRET),
]

RESPONSE_KEYS = {"ok", "verb", "ticket", "rule", "reason", "result", "elapsed_s"}


def mask_rewrite(text):
    """T10 (wq#2278): the api-call/plug/ssh-run verbs return provider output,
    which can legitimately contain credential-shaped strings (a Cloudflare
    verify response echoes token metadata; remote stdout is arbitrary). For
    THOSE verbs masking is a rewrite applied to the body before framing —
    design §2 row api-call, 'response body, masked' — and the outbound
    tripwire in respond() stays the backstop: a rewritten body can no longer
    match, so a tripwire hit still means a daemon bug, never business as
    usual."""
    for _name, pat in MASK_PATTERNS:
        text = pat.sub(MASK, text)
    return text


def mask_hits(text):
    # wq#2298 kept masking.py's own output (`cfut_MASKED`, `PAT: github_pat_MASKED`)
    # from failing the verb here — a hit does not just page, it fails the broker verb
    # and drops the result, so a masked artefact quoted in a PR body would break every
    # PR verb. It did that by ignoring any match ENDING in the literal, which wq#2638
    # showed also ignores a live token with masked text glued behind it. The screen is
    # gone because _body() made it structural: a bounded body cannot reach the literal,
    # so masked residue no longer matches at ALL, and everything that still matches is
    # a hit. test_masking.test_token_engine_mirror_agrees pins both halves.
    names = []
    for name, pat in MASK_PATTERNS:
        if name in names:
            continue
        if pat.search(text):
            names.append(name)
    return names


def mask_suppressions(text):
    """masking.suppressions, mirrored: what kv_secret DECLINED to read (wq#2677).

    Detect-only here too, so this changes no verdict — a suppressed candidate stays
    suppressed and the verb still succeeds. What it buys is that the suppression is
    written down: respond() hangs these on the audit record, so a carve-out that has
    gone overbroad shows up as a pile of audit rows instead of as a broker result that
    quietly stopped being screened. No value, no context — reason, length and 12 hex of
    sha256, the same fields the harness record carries, and for the same reason: a
    suppressed value is by definition unmasked in the text it was read from.
    """
    live = [(m.end(1), m.end()) for m in _KV_SECRET.finditer(text)]
    out, seen = [], set()
    for m in _KV_CANDIDATE.finditer(text):
        lo, hi = m.end(1), m.end()
        if any(start < hi and lo < end for start, end in live):
            continue
        reasons = [n for n, rx in _KV_SUPPRESSIONS if not rx.match(text, m.start())]
        if not reasons:
            continue
        value, reason = text[lo:hi], "+".join(reasons)
        sha12 = hashlib.sha256(value.encode("utf-8", "replace")).hexdigest()[:12]
        if (reason, sha12) in seen:
            continue
        seen.add((reason, sha12))
        out.append({"rule": "kv_secret", "suppressed": reason,
                    "value_len": len(value), "value_sha256_12": sha12})
    return out


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

class Deny(Exception):
    """A clean verb refusal: (rule, reason). handle() turns it into a deny
    frame rather than the verb_crashed error path."""
    def __init__(self, rule, reason):
        super().__init__(reason)
        self.rule = rule
        self.reason = reason


def verb_ping(req, grant):
    return {"pong": True, "daemon_uid": os.getuid(),
            "secrets_dir": SECRETS_DIR, "self_sha256": SELF_SHA256}


def verb_mask_selftest(req, grant):
    """Deliberately returns a credential-shaped string so the outbound tripwire
    fires. Proves property 4 end to end without a real secret in play."""
    # wq#2292: 36-char body, the real ghp_ width. The old 20-char canary proved
    # the tripwire caught a shape no live token actually has.
    return {"leak": "ghp_" + "selftest" * 4 + "0000"}


def verb_ticket_end(req, grant):
    """§3.4: revoke every token minted for this ticket and drop it from the
    in-memory map. Scope: load_grant already forced grant.ticket == req.ticket,
    so a worker can only end its own ticket. Idempotent: a second call finds
    no ledger and reports had_entries=0. Failed revocations stay ledgered for
    the daemon-start reaper; they alert but do not fail the verb, because
    SessionEnd must still complete."""
    result = L.revoke_ticket(str(req.get("ticket", "")))
    if result["failed"]:
        notify("high", "token-engine revocation incomplete",
               f"ticket={req.get('ticket')} failed={result['failed']} — "
               "entries kept for the daemon-start reaper")
    return result


def verb_mint_test(req, grant):
    """Test-provider mint so T9's revocation gate can run before T1's real
    provider verbs land: a token is 'live' iff its marker file exists under
    the state dir. Refused in handle() unless TOKEN_ENGINE_ALLOW_TEST_MINT=1
    (the systemd unit never sets it). Returns the token id only — no token
    value ever leaves the daemon (§1.2)."""
    import hashlib
    import uuid
    token_value = uuid.uuid4().hex + uuid.uuid4().hex  # stays in this frame
    token_id = "tetest-" + uuid.uuid4().hex[:12]
    live_dir = os.path.join(SECRETS_DIR, "test-provider")
    os.makedirs(live_dir, mode=0o700, exist_ok=True)
    live_path = os.path.join(live_dir, token_id + ".live")
    with open(live_path, "w") as f:
        f.write(hashlib.sha256(token_value.encode()).hexdigest() + "\n")
    L.record_mint(str(req.get("ticket", "")), "test-file", token_id,
                  str(req.get("worker", "")), {"live_path": live_path},
                  time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
    return {"token_id": token_id, "provider": "test-file"}


# ------------------------------------------------------------- PR verbs (T4)
# TOKEN_ENGINE_DESIGN.md §2 + §5.3 (wq#2273). The daemon — not the client —
# picks the forge, the base URL and the token file for a repo slug: a client
# that could name either would have the daemon post the credential to an
# attacker's server. Titles and bodies arrive as FILE PATHS (never argv), so
# nothing lands in /proc/*/cmdline and the metachar ban stays strict.

# Env-overridable for the T1 gate replay (a fake forge on a loopback port),
# same contract as PROVIDERS_PATH; the systemd unit sets nothing.
FORGES_PATH = os.environ.get(
    "TOKEN_ENGINE_FORGES_PATH",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "forges.json"))
REPO_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*/[A-Za-z0-9][A-Za-z0-9._-]*$")
NUMBER_RE = re.compile(r"^[0-9]{1,9}$")
SHA_RE = re.compile(r"^[0-9a-f]{7,64}$")
BODY_MAX_BYTES = 65536
HTTP_TIMEOUT_S = 30
# Body/title files must resolve under this root (worker worktrees and the
# foreman tree live here) and never inside the engine's own state dir — a
# body path into the secret store would post the credential as a PR body.
BODY_ROOT = os.environ.get("TOKEN_ENGINE_BODY_ROOT", "/home/lumen")
FOOTER_ASSIST = "Assisted-by: Claude (Anthropic) via Lumen Industries automation"
FOOTER_RE = re.compile(
    r"^(Refs|Assisted-by|Generated-by|BREAKING CHANGE|Co-authored-by):")


def load_forges():
    try:
        with open(FORGES_PATH) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def identity_map_lookup(slug, cfg):
    """Port of lumen-github-identity.sh `_lgi_map_lookup` (wq#525): exact-slug
    `owner/repo = /path/to/token-file` lines. Only the BASENAME of the mapped
    path is honoured — the engine serves tokens from its own store only."""
    path = cfg.get("identity_map", "/home/lumen/infra/github/identity-map.conf")
    try:
        with open(path) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                if k.strip() == slug:
                    return os.path.basename(v.strip())
    except OSError:
        pass
    return None


def resolve_repo(slug):
    """(api_base, auth_header_value_prefix, token, forge) for a repo slug, or
    Deny. Mirrors the ladder lumen-pr used in-process before T4 — identity map
    first, then the default per forge — with every file read from the engine
    store, never /home/lumen/.secrets."""
    cfg = load_forges()
    owner = slug.split("/")[0]
    if owner in cfg.get("gitea_owners", ["lumen"]):
        base = cfg.get("gitea_url", "http://100.121.17.5:3000").rstrip("/") + "/api/v1"
        token_file = cfg.get("gitea_token_file", "gitea-lumen.token")
        scheme, forge = "token", "gitea"
    else:
        base = "https://api.github.com"
        token_file = (identity_map_lookup(slug, cfg)
                      or cfg.get("github_default_token_file", "github-frank.token"))
        scheme, forge = "Bearer", "github"
    path = os.path.join(SECRETS_DIR, "secrets", os.path.basename(token_file))
    try:
        with open(path) as f:
            token = f.read().strip()
    except OSError:
        raise Deny("token_unreadable",
                   f"engine store has no readable {os.path.basename(token_file)} "
                   f"for {slug} — run token-engine/install.sh to migrate it")
    if not token:
        raise Deny("token_empty", f"{os.path.basename(token_file)} is empty")
    return base, scheme, token, forge


def http_json(method, base, path, scheme, token, body=None):
    """(status_code, parsed_json_or_None). One place builds the Authorization
    header; the token never appears in any error string. Content-Type json is
    NOT optional (wq#557: Gitea binds a form-encoded body as empty fields and
    answers a misleading 422)."""
    req = urllib.request.Request(base + path, method=method)
    req.add_header("Authorization", f"{scheme} {token}")
    req.add_header("Accept", "application/json")
    req.add_header("X-GitHub-Api-Version", "2022-11-28")
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, data=data, timeout=HTTP_TIMEOUT_S) as resp:
            raw = resp.read()
            code = resp.status
    except urllib.error.HTTPError as e:
        raw = e.read()
        code = e.code
    except (urllib.error.URLError, OSError, TimeoutError) as e:
        raise Deny("forge_unreachable", f"{method} {path}: {type(e).__name__}")
    try:
        return code, json.loads(raw.decode(errors="replace"))
    except ValueError:
        return code, None


def api_message(obj):
    if isinstance(obj, dict):
        return str(obj.get("message") or "no message")[:200]
    return "no message"


def read_worktree_file(path, what):
    """Body/title content from a client-named path, with the reads a hostile
    client would try refused: outside BODY_ROOT, inside the engine state dir,
    oversized, or carrying a credential shape (posting one as a PR body is an
    exfil, and the mask patterns are the same tripwire respond() uses)."""
    real = os.path.realpath(path)
    state_real = os.path.realpath(SECRETS_DIR)
    if not real.startswith(os.path.realpath(BODY_ROOT) + os.sep):
        raise Deny("body_outside_root", f"{what} path is outside {BODY_ROOT}")
    if real == state_real or real.startswith(state_real + os.sep):
        raise Deny("body_in_state_dir", f"{what} path reaches the engine state dir")
    try:
        if os.path.getsize(real) > BODY_MAX_BYTES:
            raise Deny("body_too_large", f"{what} exceeds {BODY_MAX_BYTES} bytes")
        with open(real, errors="replace") as f:
            text = f.read()
    except OSError as e:
        raise Deny("body_unreadable", f"{what}: {type(e).__name__}")
    hits = mask_hits(text)
    if hits:
        raise Deny("body_mask_tripwire",
                   f"{what} content matches mask patterns {hits} — refusing to "
                   "post a credential-shaped string to a forge")
    return text


def pr_argv_shape(argv, spec):
    """Shape gate for PR verbs, run before the grant check. `spec` is a list of
    (name, regex_or_None) for required tokens; extra tokens are refused. The
    metachar ban matches fleet_exec_policy.check_argv_shape; argv[0] here is a
    repo slug, not a program name, so the basename rule does not apply."""
    for tok in argv:
        if P._METACHAR_RE.search(tok or ""):
            raise Deny("argv_metachar", "argv token contains a shell metacharacter")
    required = [s for s in spec if not s[0].startswith("[")]
    if len(argv) < 1 + len(required) or len(argv) > 1 + len(spec):
        names = " ".join(name for name, _ in spec)
        raise Deny("argv_arity", f"expected: <repo> {names}"[:200])
    for (name, rx), tok in zip(spec, argv[1:]):
        if rx is not None and not rx.match(tok):
            raise Deny("argv_malformed", f"{name} fails shape check")


def _find_open_pr_for_head(base, scheme, token, slug, head):
    """wq#557: Gitea IGNORES the head= filter (verified live — a non-matching
    head still returns the full open list), so trusting it would dedup against
    ANY open PR. Filter client-side on head.ref; harmless on GitHub."""
    owner = slug.split("/")[0]
    code, obj = http_json("GET", base,
                          f"/repos/{slug}/pulls?state=open&head={owner}:{head}",
                          scheme, token)
    if code // 100 != 2 or not isinstance(obj, list):
        raise Deny("forge_error", f"open-PR list failed (HTTP {code}): {api_message(obj)}")
    for pr in obj:
        if isinstance(pr, dict) and (pr.get("head") or {}).get("ref") == head:
            return pr
    return None


def verb_pr_open(req, grant):
    argv = req.get("argv") or []
    pr_argv_shape(argv, [("head", None), ("base", None), ("title-file", None),
                         ("body-file", None), ("[draft]", re.compile("^(draft|nodraft)$"))])
    slug, head, base_branch, title_file, body_file = argv[:5]
    draft = len(argv) > 5 and argv[5] == "draft"
    title = read_worktree_file(title_file, "title file").strip()
    body = read_worktree_file(body_file, "body file")
    base, scheme, token, _forge = resolve_repo(slug)
    existing = _find_open_pr_for_head(base, scheme, token, slug, head)
    if existing is not None:
        number = existing.get("number")
        code, obj = http_json("PATCH", base, f"/repos/{slug}/pulls/{number}",
                              scheme, token, {"title": title, "body": body})
        if code // 100 != 2:
            raise Deny("forge_error", f"PR update failed (HTTP {code}): {api_message(obj)}")
        return {"number": int(number), "url": str(obj.get("html_url") or ""),
                "action": "updated"}
    payload = {"title": title, "body": body, "head": head,
               "base": base_branch, "draft": draft}
    code, obj = http_json("POST", base, f"/repos/{slug}/pulls", scheme, token, payload)
    if code // 100 != 2 or not isinstance(obj, dict):
        raise Deny("forge_error", f"PR create failed (HTTP {code}): {api_message(obj)}")
    return {"number": int(obj.get("number")), "url": str(obj.get("html_url") or ""),
            "action": "created"}


def verb_pr_update(req, grant):
    argv = req.get("argv") or []
    pr_argv_shape(argv, [("number", NUMBER_RE), ("body-file", None),
                         ("[title-file]", None)])
    slug, number, body_file = argv[:3]
    payload = {"body": read_worktree_file(body_file, "body file")}
    if len(argv) > 3:
        payload["title"] = read_worktree_file(argv[3], "title file").strip()
    base, scheme, token, _forge = resolve_repo(slug)
    code, obj = http_json("PATCH", base, f"/repos/{slug}/pulls/{number}",
                          scheme, token, payload)
    if code // 100 != 2 or not isinstance(obj, dict):
        raise Deny("forge_error", f"PR update failed (HTTP {code}): {api_message(obj)}")
    return {"number": int(number), "url": str(obj.get("html_url") or "")}


def verb_pr_comment(req, grant):
    argv = req.get("argv") or []
    pr_argv_shape(argv, [("number", NUMBER_RE), ("body-file", None)])
    slug, number, body_file = argv[:3]
    body = read_worktree_file(body_file, "body file")
    base, scheme, token, _forge = resolve_repo(slug)
    code, obj = http_json("POST", base, f"/repos/{slug}/issues/{number}/comments",
                          scheme, token, {"body": body})
    if code // 100 != 2 or not isinstance(obj, dict):
        raise Deny("forge_error", f"PR comment failed (HTTP {code}): {api_message(obj)}")
    return {"id": int(obj.get("id") or 0), "url": str(obj.get("html_url") or "")}


def verb_pr_view(req, grant):
    argv = req.get("argv") or []
    pr_argv_shape(argv, [("number", NUMBER_RE)])
    slug, number = argv[:2]
    base, scheme, token, forge = resolve_repo(slug)
    code, obj = http_json("GET", base, f"/repos/{slug}/pulls/{number}", scheme, token)
    if code // 100 != 2 or not isinstance(obj, dict):
        raise Deny("forge_error", f"PR fetch failed (HTTP {code}): {api_message(obj)}")
    head = obj.get("head") or {}
    return {"number": int(number), "state": str(obj.get("state") or ""),
            "merged": bool(obj.get("merged")), "draft": bool(obj.get("draft")),
            "head_sha": str(head.get("sha") or ""),
            "head_ref": str(head.get("ref") or ""),
            "title": str(obj.get("title") or "")[:200],
            "url": str(obj.get("html_url") or ""),
            # GitHub: mergeable bool|null + mergeable_state enum. Gitea:
            # mergeable bool only (wq#563 — the two are different contracts;
            # the client's guard switches on forge).
            "mergeable": obj.get("mergeable"),
            "mergeable_state": obj.get("mergeable_state"),
            "forge": forge}


def verb_pr_checks(req, grant):
    """Normalised CI state for a head sha: total/pending/failed/cancelled plus
    pre-formatted detail lines. Ports lumen-pr's wq#557 forge split (Gitea has
    NO check-runs endpoint — commit statuses only) and wq#986 (a Gitea queue
    cancellation reports as status=failure with a 'cancelled' description; an
    infra gap, not a failing check)."""
    argv = req.get("argv") or []
    pr_argv_shape(argv, [("head-sha", SHA_RE)])
    slug, sha = argv[:2]
    base, scheme, token, forge = resolve_repo(slug)
    if forge == "gitea":
        code, obj = http_json("GET", base, f"/repos/{slug}/commits/{sha}/statuses",
                              scheme, token)
        if code // 100 != 2 or not isinstance(obj, list):
            raise Deny("forge_error", f"statuses fetch failed (HTTP {code}): {api_message(obj)}")
        bad = [s for s in obj if s.get("status") in ("error", "failure")]
        cancelled = [s for s in bad if "cancel" in str(s.get("description") or "").lower()]
        failed = [s for s in bad if s not in cancelled]
        pending = [s for s in obj if s.get("status") == "pending"]
        total = len(obj)
        failed_detail = "\n".join(
            f"  FAILED: {s.get('context')} -> {s.get('status')}  {s.get('target_url') or ''}"
            for s in failed)
        cancelled_detail = "\n".join(
            f"  CANCELLED (not a failure, wq#986): {s.get('context')} -> "
            f"{s.get('description')}  {s.get('target_url') or ''}" for s in cancelled)
    else:
        code, obj = http_json("GET", base, f"/repos/{slug}/commits/{sha}/check-runs",
                              scheme, token)
        if code // 100 != 2 or not isinstance(obj, dict):
            raise Deny("forge_error", f"check-runs fetch failed (HTTP {code}): {api_message(obj)}")
        runs = obj.get("check_runs") or []
        total = int(obj.get("total_count") or 0)
        pending = [r for r in runs if r.get("status") != "completed"]
        failed = [r for r in runs if r.get("conclusion")
                  not in (None, "success", "neutral", "skipped")]
        cancelled = []
        failed_detail = "\n".join(
            f"  FAILED: {r.get('name')} -> {r.get('conclusion')}  {r.get('html_url') or ''}"
            for r in failed)
        cancelled_detail = ""
    return {"total": total, "pending": len(pending), "failed": len(failed),
            "cancelled": len(cancelled), "failed_detail": failed_detail,
            "cancelled_detail": cancelled_detail}


def verb_pr_files(req, grant):
    """Filenames a PR touches, previous_filename included (a rename lands a
    DELETE of the old path — wq#864). Newline-joined string because response
    result leaves must be scalars; complete=False past 5 pages (500 files)."""
    argv = req.get("argv") or []
    pr_argv_shape(argv, [("number", NUMBER_RE)])
    slug, number = argv[:2]
    base, scheme, token, _forge = resolve_repo(slug)
    names, complete = [], True
    for page in range(1, 6):
        code, obj = http_json(
            "GET", base, f"/repos/{slug}/pulls/{number}/files?per_page=100&page={page}",
            scheme, token)
        if code // 100 != 2 or not isinstance(obj, list):
            raise Deny("forge_error", f"PR files fetch failed (HTTP {code}): {api_message(obj)}")
        for f in obj:
            if f.get("filename"):
                names.append(str(f["filename"]))
            if f.get("previous_filename"):
                names.append(str(f["previous_filename"]))
        if len(obj) < 100:
            break
    else:
        complete = False
    return {"files": "\n".join(names), "complete": complete}


def verb_pr_list(req, grant):
    argv = req.get("argv") or []
    pr_argv_shape(argv, [])
    slug = argv[0]
    base, scheme, token, _forge = resolve_repo(slug)
    code, obj = http_json("GET", base, f"/repos/{slug}/pulls?state=open", scheme, token)
    if code // 100 != 2 or not isinstance(obj, list):
        raise Deny("forge_error", f"PR list failed (HTTP {code}): {api_message(obj)}")
    lines = [f"#{p.get('number')} {str(p.get('title') or '')[:120]} "
             f"[{(p.get('head') or {}).get('ref')}]" for p in obj]
    return {"prs": "\n".join(lines), "count": len(lines)}


def _pr_footers(base, scheme, token, slug, number):
    """§3 footers from the branch commits, deduped in order; GitHub's default
    squash body would drop Refs:/Assisted-by: off main entirely."""
    code, obj = http_json("GET", base, f"/repos/{slug}/pulls/{number}/commits",
                          scheme, token)
    footers, seen = [], set()
    if code // 100 == 2 and isinstance(obj, list):
        for c in obj:
            msg = ((c.get("commit") or {}).get("message") or "")
            for line in msg.splitlines():
                if (FOOTER_RE.match(line) and line not in seen
                        and not re.match(r"^Co-authored-by:.*[Cc]laude", line)):
                    seen.add(line)
                    footers.append(line)
    return "\n".join(footers) or FOOTER_ASSIST


def verb_pr_merge(req, grant):
    """Merge an open PR. wq#557: the merge call diverges per forge — GitHub PUT
    {merge_method, commit_title, commit_message, sha} then DELETE the head ref;
    Gitea POST {do, merge_title_field, merge_message_field, head_commit_id}
    with delete_branch_after_merge (its /git/refs has no DELETE)."""
    argv = req.get("argv") or []
    pr_argv_shape(argv, [("number", NUMBER_RE),
                         ("method", re.compile("^(squash|merge)$"))])
    slug, number, method = argv[:3]
    base, scheme, token, forge = resolve_repo(slug)
    code, obj = http_json("GET", base, f"/repos/{slug}/pulls/{number}", scheme, token)
    if code // 100 != 2 or not isinstance(obj, dict):
        raise Deny("forge_error", f"PR fetch failed (HTTP {code}): {api_message(obj)}")
    if obj.get("merged"):
        return {"merged": True, "sha": str((obj.get("merge_commit_sha") or ""))[:40],
                "head_ref": str((obj.get("head") or {}).get("ref") or ""),
                "branch_deleted": False, "already": True}
    if obj.get("state") != "open":
        raise Deny("pr_not_open", f"PR #{number} is {obj.get('state')!r}, not open")
    title = str(obj.get("title") or "")
    head = obj.get("head") or {}
    head_sha, head_ref = str(head.get("sha") or ""), str(head.get("ref") or "")
    footers = _pr_footers(base, scheme, token, slug, number)
    if forge == "gitea":
        payload = {"do": method, "merge_title_field": title,
                   "merge_message_field": footers, "head_commit_id": head_sha,
                   "delete_branch_after_merge": True}
        code, obj = http_json("POST", base, f"/repos/{slug}/pulls/{number}/merge",
                              scheme, token, payload)
        if code // 100 != 2:
            raise Deny("forge_error", f"{method} merge failed (HTTP {code}): {api_message(obj)}")
        return {"merged": True, "sha": head_sha, "head_ref": head_ref,
                "branch_deleted": True, "already": False}
    payload = {"merge_method": method, "commit_title": title,
               "commit_message": footers, "sha": head_sha}
    code, obj = http_json("PUT", base, f"/repos/{slug}/pulls/{number}/merge",
                          scheme, token, payload)
    if code // 100 != 2 or not isinstance(obj, dict):
        raise Deny("forge_error", f"{method} merge failed (HTTP {code}): {api_message(obj)}")
    merge_sha = str(obj.get("sha") or "")
    dcode, _dobj = http_json("DELETE", base, f"/repos/{slug}/git/refs/heads/{head_ref}",
                             scheme, token)
    return {"merged": True, "sha": merge_sha, "head_ref": head_ref,
            "branch_deleted": dcode // 100 == 2, "already": False}


# -------------------------------------------------------- git verbs (T1)
# TOKEN_ENGINE_DESIGN.md §3.1-3.2 (wq#2271) — the recurring leak (wq#2243 /
# ISSUE-LOG 537). Two strengths, per the design's "build both" call:
#   git-credential backs /usr/local/bin/git-credential-token-engine: the
#     token crosses into the worker's git for the seconds of one fetch/push
#     (§3.1's residual risk, bounded by TTL/scope/ticket-end revocation) but
#     never touches a URL, config file, ~/.git-credentials, env, or history.
#     The value travels by one-shot handoff FILE (the gateway-key pattern),
#     never in the frame, so the outbound mask tripwire stays intact.
#   git-fetch / git-push are the zero-crossing path: git runs HERE, as uid
#     tokeneng. Fetch lands in a group-readable bare mirror under the runtime
#     dir that the worker pulls from by PATH (file transport, no auth); push
#     reads the worker's branch through a scratch bare repo and pushes with
#     auth held in this process's env only.

GIT_BIN = os.environ.get("TOKEN_ENGINE_GIT_BIN", "git")
GIT_TIMEOUT_S = 300
GIT_PROTOCOLS = ("https", "http")
BRANCH_RE = re.compile(r"^(?!.*\.\.)[A-Za-z0-9][A-Za-z0-9._/-]{0,200}$")
GIT_HOST_RE = re.compile(r"^[A-Za-z0-9.-]{1,253}(:[0-9]{1,5})?$")
# git-push's default source: the calling ticket's worktree (fworker
# make_worktree's naming). An explicit argv[2] must still live under this
# root and outside the engine's own state.
WORKTREES_ROOT = os.environ.get("FOREMAN_WORKTREES_ROOT", "/home/lumen/worktrees")


def _b64url(raw):
    return base64.urlsafe_b64encode(raw).rstrip(b"=")


def mint_github_app_token(app, slug, ticket, worker):
    """§4 GitHub row: POST /app/installations/{id}/access_tokens — 1 h TTL,
    scoped to exactly this repo with contents:write, ledgered as provider
    github-app so ticket-end / the daemon-start reaper can DELETE
    /installation/token. Dormant until forges.json carries a github_app
    block (T0 prereq: no App installed yet — Frank's physical task)."""
    try:
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import padding
    except ImportError:
        raise Deny("github_app_unavailable",
                   "forges.json names a github_app but the daemon's python "
                   "has no cryptography module for the RS256 app JWT")
    pem = read_store(app.get("private_key_file", "github-app.pem"),
                     "github-app mint").encode()
    key = serialization.load_pem_private_key(pem, password=None)
    now = int(time.time())
    header = _b64url(json.dumps({"alg": "RS256", "typ": "JWT"}).encode())
    payload = _b64url(json.dumps({"iat": now - 60, "exp": now + 540,
                                  "iss": str(app.get("app_id"))}).encode())
    signing = header + b"." + payload
    sig = _b64url(key.sign(signing, padding.PKCS1v15(), hashes.SHA256()))
    jwt = (signing + b"." + sig).decode()
    api = str(app.get("api_url", "https://api.github.com")).rstrip("/")
    code, obj = http_json(
        "POST", api, f"/app/installations/{app['installation_id']}/access_tokens",
        "Bearer", jwt, {"repositories": [slug.split("/", 1)[1]],
                        "permissions": {"contents": "write"}})
    if code // 100 != 2 or not isinstance(obj, dict) or not obj.get("token"):
        raise Deny("forge_error",
                   f"app token mint failed (HTTP {code}): {api_message(obj)}")
    token = str(obj["token"])
    L.record_mint(str(ticket), "github-app", "ghapp-" + uuid.uuid4().hex[:12],
                  str(worker), {"token": token, "api_url": api},
                  time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
    return token


def resolve_git_remote(slug, ticket, worker):
    """(clone_base_url, username, token, source) for git-over-HTTP on a repo
    slug — the resolve_repo ladder (identity map first) aimed at the GIT
    host rather than the API host. GitHub prefers a minted App installation
    token when configured; until then the engine-held PAT — the same
    in-memory crossing the fleet has today, minus every on-disk surface."""
    cfg = load_forges()
    owner = slug.split("/")[0]
    if owner in cfg.get("gitea_owners", ["lumen"]):
        base = cfg.get("gitea_url", "http://100.121.17.5:3000").rstrip("/")
        token = read_store(cfg.get("gitea_token_file", "gitea-lumen.token"),
                           f"git credential for {slug}")
        return base, cfg.get("gitea_git_username", "lumen"), token, "engine-store"
    base = cfg.get("github_git_url", "https://github.com").rstrip("/")
    app = cfg.get("github_app")
    if isinstance(app, dict) and app.get("installation_id"):
        return base, "x-access-token", \
            mint_github_app_token(app, slug, ticket, worker), "github-app"
    token_file = (identity_map_lookup(slug, cfg)
                  or cfg.get("github_default_token_file", "github-frank.token"))
    return base, "x-access-token", \
        read_store(token_file, f"git credential for {slug}"), "engine-store"


def _run_git(args, cwd, auth=None):
    """Run git as this uid with the credential in process env only — never
    argv (/proc/*/cmdline is world-readable; /proc/<pid>/environ is 0400
    tokeneng). HOME points into the engine state so uid lumen's global
    config (credential.helper=store — the exact leak T1 retires) can never
    be read; auth rides an http.extraheader Basic line via GIT_CONFIG_*."""
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"),
           "HOME": SECRETS_DIR, "GIT_TERMINAL_PROMPT": "0",
           "GIT_CONFIG_NOSYSTEM": "1"}
    pairs = [("safe.directory", "*")]
    if auth:
        cred = base64.b64encode(("%s:%s" % auth).encode()).decode()
        pairs.append(("http.extraheader", "Authorization: Basic " + cred))
    env["GIT_CONFIG_COUNT"] = str(len(pairs))
    for i, (k, v) in enumerate(pairs):
        env["GIT_CONFIG_KEY_%d" % i] = k
        env["GIT_CONFIG_VALUE_%d" % i] = v
    try:
        return subprocess.run([GIT_BIN, *args], cwd=cwd, env=env,
                              capture_output=True, text=True,
                              timeout=GIT_TIMEOUT_S)
    except (OSError, subprocess.TimeoutExpired) as e:
        raise Deny("git_exec_failed", f"git: {type(e).__name__}")


def _git_result(p, **extra):
    return dict({"exit_code": p.returncode,
                 "stderr": mask_rewrite((p.stderr or "")[-OUT_TAIL_BYTES:])},
                **extra)


def _group_share(root):
    """chgrp token-client + setgid dirs / g+r files so uid lumen can fetch
    from the mirror by path. core.sharedRepository=group keeps what git
    creates later group-readable; the setgid bit keeps the group."""
    gid = _client_gid()
    for dirpath, dirnames, filenames in os.walk(root):
        if gid is not None:
            try:
                os.chown(dirpath, -1, gid)
            except PermissionError:
                pass
        os.chmod(dirpath, 0o2750)
        for name in filenames:
            path = os.path.join(dirpath, name)
            if gid is not None:
                try:
                    os.chown(path, -1, gid)
                except PermissionError:
                    pass
            os.chmod(path, os.stat(path).st_mode & 0o777 | 0o040)


def _git_argv_common(req, n_min, n_max, usage):
    argv = req.get("argv") or []
    if not (n_min <= len(argv) <= n_max):
        raise Deny("argv_arity", usage)
    return argv


def verb_git_credential(req, grant):
    """§3.1: answer git's credential `get` for the helper. argv = [slug,
    protocol, host]; the helper derived the slug from the request path
    (credential.useHttpPath=true) and the daemon refuses to serve a token
    toward any host or scheme other than the forge it maps the slug to — a
    git_read grant for repo:x/y cannot aim x/y's credential at a lookalike
    host. The value leaves via a one-shot handoff file the helper reads and
    unlinks; the frame carries the path and username only."""
    argv = _git_argv_common(req, 3, 3, "expected: <owner/name> <protocol> <host>")
    slug, protocol, host = argv
    if protocol not in GIT_PROTOCOLS:
        raise Deny("protocol_malformed", "protocol must be https or http")
    if not GIT_HOST_RE.match(host):
        raise Deny("host_malformed", "host fails shape check")
    base, username, token, source = resolve_git_remote(
        slug, str(req.get("ticket", "")), str(req.get("worker", "")))
    want = urllib.parse.urlsplit(base)
    if host != want.netloc:
        raise Deny("host_mismatch",
                   f"{slug} lives on {want.netloc} — refusing to serve its "
                   f"credential toward {host}")
    if protocol != want.scheme:
        raise Deny("protocol_mismatch", f"{slug} is served over {want.scheme}")
    path = _handoff_write("gitcred-" + uuid.uuid4().hex[:12], token)
    return {"username": username, "credential_file": path, "source": source}


def verb_git_fetch(req, grant):
    """§3.1 strong path, read half — zero crossing. Fetch runs here; the
    refs land in a bare mirror under the runtime dir, group-readable by
    token-client, and the worker fetches from that PATH: file transport,
    no credential, no network."""
    argv = _git_argv_common(req, 1, 2, "expected: <owner/name> [branch]")
    slug, branch = argv[0], (argv[1] if len(argv) == 2 else None)
    if branch is not None and not BRANCH_RE.match(branch):
        raise Deny("branch_malformed", "branch fails shape check")
    base, username, token, source = resolve_git_remote(
        slug, str(req.get("ticket", "")), str(req.get("worker", "")))
    mdir = os.path.join(SOCK_DIR, "git")
    os.makedirs(mdir, exist_ok=True)
    mirror = os.path.join(mdir, slug.replace("/", "__") + ".git")
    if not os.path.isdir(mirror):
        p = _run_git(["init", "--bare", "--quiet", "--shared=group", mirror],
                     cwd=mdir)
        if p.returncode != 0:
            raise Deny("git_exec_failed", "mirror init failed")
    refspec = (f"+refs/heads/{branch}:refs/heads/{branch}" if branch
               else "+refs/heads/*:refs/heads/*")
    p = _run_git(["fetch", "--prune", f"{base}/{slug}.git", refspec],
                 cwd=mirror, auth=(username, token))
    _group_share(mirror)
    return _git_result(p, mirror=mirror, source=source)


def verb_git_push(req, grant):
    """§3.1 strong path, write half — zero crossing. argv = [slug, branch,
    src?]: the branch is read out of the source repo (default: the calling
    ticket's worktree) through a scratch bare repo, then pushed with auth
    that exists only in this process's env. The worker never sees the
    token; the URL never carries userinfo; there is no --force spelling."""
    argv = _git_argv_common(req, 2, 3, "expected: <owner/name> <branch> [src-repo]")
    slug, branch = argv[0], argv[1]
    if not BRANCH_RE.match(branch):
        raise Deny("branch_malformed", "branch fails shape check")
    if len(argv) == 3:
        src = os.path.realpath(str(argv[2]))
    else:
        ticket = str(req.get("ticket", ""))
        name = ticket if ticket.startswith("wq") else "wq" + ticket
        src = os.path.realpath(os.path.join(
            WORKTREES_ROOT, f"{name}-a{req.get('attempt') or 1}"))
    root, state = os.path.realpath(BODY_ROOT), os.path.realpath(SECRETS_DIR)
    if not (src + os.sep).startswith(root + os.sep) \
            or (src + os.sep).startswith(state + os.sep):
        raise Deny("src_outside_root", f"push source must live under {BODY_ROOT}")
    if not os.path.isdir(src):
        raise Deny("src_missing", f"no source repo at the {os.path.basename(src)} "
                                  "worktree — name one as argv[2]")
    base, username, token, source = resolve_git_remote(
        slug, str(req.get("ticket", "")), str(req.get("worker", "")))
    scratch = os.path.join(SOCK_DIR, "scratch-" + uuid.uuid4().hex[:8])
    try:
        p = _run_git(["init", "--bare", "--quiet", scratch], cwd=SOCK_DIR)
        if p.returncode != 0:
            raise Deny("git_exec_failed", "scratch init failed")
        p = _run_git(["fetch", "--quiet", src,
                      f"+refs/heads/{branch}:refs/heads/{branch}"], cwd=scratch)
        if p.returncode != 0:
            raise Deny("branch_unreadable",
                       f"cannot read refs/heads/{branch} from the source: "
                       + mask_rewrite((p.stderr or "")[-300:]))
        p = _run_git(["push", f"{base}/{slug}.git",
                      f"refs/heads/{branch}:refs/heads/{branch}"],
                     cwd=scratch, auth=(username, token))
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
    return _git_result(p, branch=branch, source=source)


# ------------------------------------------------------------ T10 (wq#2278)
# Remaining verbs + migration ranks 5-8 (design §2 rows api-call/gateway-key/
# plug, §7 rank 3 ssh). providers.json is the static half, same contract as
# forges.json: the client never names a base URL, credential file, or plug IP.

# Env-overridable for the gate replay (a fake gateway on a loopback port);
# the systemd unit sets nothing, so production reads the root-installed copy.
PROVIDERS_PATH = os.environ.get(
    "TOKEN_ENGINE_PROVIDERS_PATH",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "providers.json"))
POLICY_PATH = os.environ.get("FLEET_EXEC_POLICY_PATH",
                             "/home/lumen/foreman/harness/fleet-exec-policy.yaml")
KASA_BIN = os.environ.get("TOKEN_ENGINE_KASA_BIN", "/home/lumen/.local/bin/kasa")
SSH_BIN = os.environ.get("TOKEN_ENGINE_SSH_BIN", "ssh")
API_METHODS = ("GET", "POST", "PUT", "PATCH", "DELETE")
PROVIDER_RE = re.compile(r"^[a-z][a-z0-9-]{0,31}$")
API_PATH_RE = re.compile(r"^/[A-Za-z0-9._~/%?=&+:,@!*-]*$")
PLUG_RE = re.compile(r"^p[0-9]{1,2}$")
PLUG_ACTIONS = ("on", "off", "state")
NODE_RE = re.compile(r"^[a-z][a-z0-9-]{0,31}$")
KEYNAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
TTL_RE = re.compile(r"^([0-9]{1,4})([smhd])$")
BUDGET_RE = re.compile(r"^[0-9]{1,4}(\.[0-9]{1,4})?$")
_TTL_UNIT_S = {"s": 1, "m": 60, "h": 3600, "d": 86400}
SSH_TIMEOUT_S = 90
OUT_TAIL_BYTES = 1800

# VERBS node sentinels: how handle() derives the grant (kind, node) from argv
# when the resource is client-named. None keeps the T4 meaning (repo slug).
NODE_FROM_ARGV = "<node-from-argv>"   # ssh-run: node name, kind stays "ssh"
API_FROM_ARGV = "<api-from-argv>"     # api-call: kind AND node = api:<provider>
PLUG_FROM_ARGV = "<plug-from-argv>"   # plug: node = plug:<name>


def load_providers():
    try:
        with open(PROVIDERS_PATH) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def read_store(basename, what):
    """One credential file from the engine store, by basename only. Deny, not
    crash, when it is missing — the migration message names install.sh."""
    path = os.path.join(SECRETS_DIR, "secrets", os.path.basename(basename))
    try:
        with open(path) as f:
            value = f.read().strip()
    except OSError:
        raise Deny("token_unreadable",
                   f"engine store has no readable {os.path.basename(basename)} "
                   f"for {what} — run token-engine/install.sh to migrate it")
    if not value:
        raise Deny("token_empty", f"{os.path.basename(basename)} is empty")
    return value


def read_env_file(basename, what):
    """KEY=VALUE lines from an engine-store env file (ports bin/ebay-token
    load_env / the tapo.env plug-ctl sources)."""
    out = {}
    for line in read_store(basename, what).splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        out[k.strip()] = v.strip().strip('"').strip("'")
    return out


def _client_gid():
    import grp
    try:
        return grp.getgrnam(CLIENT_GROUP).gr_gid
    except KeyError:
        return None


def _handoff_write(key_id, value):
    """The ONE sanctioned way a minted key value reaches the client side:
    a 0640 tokeneng:token-client file under the runtime dir, path returned in
    the frame, value never in it. fworker reads it into the worker env and
    unlinks it; the ledger entry unlinks it again at revocation as a backstop.
    O_EXCL: key_id is daemon-generated and unique, a collision is an attack."""
    hdir = os.path.join(SOCK_DIR, "minted")
    os.makedirs(hdir, exist_ok=True)
    os.chmod(hdir, 0o750)  # explicit: the unit's UMask=0077 would strip group
    gid = _client_gid()
    if gid is not None:
        try:
            os.chown(hdir, -1, gid)
        except PermissionError:
            pass
    path = os.path.join(hdir, key_id + ".key")
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o640)
    try:
        if gid is not None:
            try:
                os.fchown(fd, -1, gid)
            except PermissionError:
                pass
        os.fchmod(fd, 0o640)
        os.write(fd, (value + "\n").encode())
    finally:
        os.close(fd)
    return path


def verb_gateway_key(req, grant):
    """§7 rank 5: per-ticket LiteLLM virtual key with a TTL and a budget,
    minted with the engine-held admin key (the long-lived 'foreman' virtual
    key leaves worker env; fworker mints one of these per attempt instead).
    Frame carries key id, expiry, and the handoff PATH — never the value; the
    ledger revokes at ticket-end/reap like any other minted token."""
    argv = req.get("argv") or []
    if not argv or argv[0] not in ("mint", "revoke"):
        raise Deny("argv_arity", "expected: mint <name> <ttl> <budget-usd> | revoke <key-id>")
    cfg = load_providers().get("gateway", {})
    if argv[0] == "revoke":
        if len(argv) != 2 or not KEYNAME_RE.match(argv[1]):
            raise Deny("argv_malformed", "revoke takes one key id")
        r = L.revoke_one(str(req.get("ticket", "")), argv[1])
        return r
    if len(argv) != 4:
        raise Deny("argv_arity", "expected: mint <name> <ttl> <budget-usd>")
    name, ttl, budget = argv[1:4]
    if not KEYNAME_RE.match(name):
        raise Deny("argv_malformed", "key name fails shape check")
    m = TTL_RE.match(ttl)
    if not m:
        raise Deny("argv_malformed", "ttl must be like 30m / 24h")
    ttl_s = int(m.group(1)) * _TTL_UNIT_S[m.group(2)]
    if ttl_s <= 0 or ttl_s > int(cfg.get("max_ttl_s", 259200)):
        raise Deny("gateway_ttl_cap", f"ttl exceeds cap {cfg.get('max_ttl_s', 259200)}s")
    if not BUDGET_RE.match(budget):
        raise Deny("argv_malformed", "budget must be a decimal USD amount")
    budget_usd = float(budget)
    if budget_usd <= 0 or budget_usd > float(cfg.get("max_budget_usd", 25.0)):
        raise Deny("gateway_budget_cap",
                   f"budget exceeds cap {cfg.get('max_budget_usd', 25.0)} USD")
    admin_file = cfg.get("admin_key_file", "litellm-admin.token")
    admin = read_store(admin_file, "gateway-key mint")
    base = str(cfg.get("base_url", "http://localhost:4000")).rstrip("/")
    ticket = str(req.get("ticket", ""))
    code, obj = http_json("POST", base, "/key/generate", "Bearer", admin,
                          {"key_alias": f"{ticket}-{name}-{uuid.uuid4().hex[:6]}",
                           "duration": ttl, "max_budget": budget_usd,
                           "metadata": {"ticket": ticket,
                                        "worker": str(req.get("worker", "")),
                                        "minted_by": "token-engine"}})
    if code // 100 != 2 or not isinstance(obj, dict) or not obj.get("key"):
        raise Deny("gateway_error", f"key mint failed (HTTP {code}): {api_message(obj)}")
    value = str(obj["key"])
    key_id = "gwk-" + uuid.uuid4().hex[:12]
    path = _handoff_write(key_id, value)
    # Ledgered BEFORE the response leaves (an unledgered token is an
    # unrevocable token); the revoke payload keeps the value in the 0600
    # ledger only, per token_ledger's secrets discipline.
    L.record_mint(ticket, "litellm-key", key_id, str(req.get("worker", "")),
                  {"base_url": base, "key": value,
                   "admin_key_file": admin_file, "key_file": path},
                  time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
    return {"key_id": key_id, "expires": str(obj.get("expires") or ""),
            "budget_usd": budget_usd, "ttl": ttl, "key_file": path}


def _ebay_access_token(pcfg):
    """§7 rank 8: the eBay app credentials (the long-lived thing) live in the
    engine store; the ~2h access token minted from them is cached under the
    state dir, 0600 tokeneng. Ports bin/ebay-token mint()/get()."""
    env = read_env_file(pcfg.get("env_file", "ebay.env"), "api-call ebay")
    sandbox = "sand" in (env.get("EBAY_ENV", "production") or "").lower()
    base = pcfg.get("sandbox_base_url") if sandbox else pcfg.get("base_url")
    base = str(base or "https://api.ebay.com").rstrip("/")
    cache = os.path.join(SECRETS_DIR, "secrets",
                         ".ebay-access-%s.json" % ("sb" if sandbox else "prod"))
    try:
        with open(cache) as f:
            t = json.load(f)
        if t["obtained_at"] + int(t.get("expires_in", 7200)) - 90 > time.time():
            return base, t["access_token"]
    except (OSError, ValueError, KeyError):
        pass
    app_id, cert_id = env.get("EBAY_APP_ID"), env.get("EBAY_CERT_ID")
    if not app_id or not cert_id:
        raise Deny("token_unreadable", "ebay.env lacks EBAY_APP_ID/EBAY_CERT_ID")
    cred = base64.b64encode(f"{app_id}:{cert_id}".encode()).decode()
    data = urllib.parse.urlencode({
        "grant_type": "client_credentials",
        "scope": pcfg.get("scope", "https://api.ebay.com/oauth/api_scope")}).encode()
    hreq = urllib.request.Request(
        base + str(pcfg.get("token_path", "/identity/v1/oauth2/token")),
        data=data, method="POST",
        headers={"Authorization": "Basic " + cred,
                 "Content-Type": "application/x-www-form-urlencoded"})
    try:
        with urllib.request.urlopen(hreq, timeout=HTTP_TIMEOUT_S) as resp:
            t = json.load(resp)
    except urllib.error.HTTPError as e:
        raise Deny("gateway_error", f"ebay token mint failed (HTTP {e.code})")
    except (urllib.error.URLError, OSError, ValueError) as e:
        raise Deny("forge_unreachable", f"ebay token mint: {type(e).__name__}")
    if not t.get("access_token"):
        raise Deny("gateway_error", "ebay token response carried no access_token")
    t["obtained_at"] = int(time.time())
    fd = os.open(cache, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(t, f)
    return base, t["access_token"]


def verb_api_call(req, grant):
    """Design §2 row api-call: <provider> <method> <path> [body-file]. Grant
    kind api:<provider> (derived in handle()); body via file path, never argv;
    response body mask-REWRITTEN before framing; the Authorization header is
    built here and is structurally absent from the frame."""
    argv = req.get("argv") or []
    for tok in argv:
        if P._METACHAR_RE.search(tok or ""):
            raise Deny("argv_metachar", "argv token contains a shell metacharacter")
    if len(argv) < 3 or len(argv) > 4:
        raise Deny("argv_arity", "expected: <provider> <method> <path> [body-file]")
    provider, method, path = argv[0], argv[1].upper(), argv[2]
    pcfg = load_providers().get("providers", {}).get(provider)
    if not isinstance(pcfg, dict):
        raise Deny("provider_unknown", f"{provider!r} is not in providers.json")
    if method not in API_METHODS:
        raise Deny("argv_malformed", f"method must be one of {'/'.join(API_METHODS)}")
    if not API_PATH_RE.match(path) or ".." in path:
        raise Deny("argv_malformed", "path fails shape check")
    body_obj = None
    if len(argv) == 4:
        text = read_worktree_file(argv[3], "body file")
        try:
            body_obj = json.loads(text)
        except ValueError:
            raise Deny("body_not_json", "body file must contain valid JSON")
    if pcfg.get("auth") == "ebay-client-credentials":
        base, token = _ebay_access_token(pcfg)
    else:
        base = str(pcfg.get("base_url", "")).rstrip("/")
        if not base.startswith("https://"):
            raise Deny("provider_unknown", f"{provider!r} has no https base_url")
        token = read_store(pcfg.get("token_file", provider + ".token"),
                           f"api-call {provider}")
    code, obj = http_json(method, base, path, "Bearer", token, body_obj)
    body_text = "" if obj is None else json.dumps(obj, sort_keys=True)
    truncated = len(body_text) > BODY_MAX_BYTES
    return {"provider": provider, "status": code,
            "body": mask_rewrite(body_text[:BODY_MAX_BYTES]),
            "truncated": truncated}


def verb_plug(req, grant):
    """Design §2 row plug: <name> on|off|state, node plug:<name>. Tapo creds
    come from the engine store and reach kasa via KASA_USERNAME/KASA_PASSWORD
    env (python-kasa's documented envvars) — never argv, so nothing lands in
    /proc/*/cmdline."""
    argv = req.get("argv") or []
    if len(argv) != 2 or argv[1] not in PLUG_ACTIONS:
        raise Deny("argv_arity", "expected: <plug> on|off|state")
    name, action = argv
    cfg = load_providers()
    ip = cfg.get("plugs", {}).get(name)
    if not PLUG_RE.match(name) or not isinstance(ip, str):
        raise Deny("plug_unknown", f"{name!r} is not a mapped plug")
    creds = read_env_file(cfg.get("plug_creds_file", "tapo.env"), "plug")
    user, pw = creds.get("TAPO_USER"), creds.get("TAPO_PASS")
    if not user or not pw:
        raise Deny("token_unreadable", "tapo.env lacks TAPO_USER/TAPO_PASS")
    env = {"PATH": "/usr/bin:/bin", "HOME": SECRETS_DIR,
           "KASA_USERNAME": user, "KASA_PASSWORD": pw}
    try:
        p = subprocess.run([KASA_BIN, "--host", ip, action], env=env,
                           capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired) as e:
        raise Deny("plug_exec_failed", f"kasa: {type(e).__name__}")
    out = ((p.stdout or "") + (p.stderr or ""))[-OUT_TAIL_BYTES:]
    m = re.search(r"Device state:\s*(True|False)", out)
    state = {"True": "on", "False": "off"}.get(m.group(1)) if m else ""
    return {"plug": name, "action": action, "exit_code": p.returncode,
            "state": state, "out": mask_rewrite(out)}


def verb_ssh_run(req, grant):
    """§7 rank 3: the fleet ssh key moves into the engine store, and the ssh
    runs HERE, as uid tokeneng — the worker uid no longer holds a key that
    reaches every node. Policy is deliberately identical to fleet-exec `run`
    (same grant kind/verbs, same check_run readonly allowlist), so the move
    changes who holds the key, never what a worker may execute."""
    argv = req.get("argv") or []
    if len(argv) < 2:
        raise Deny("argv_arity", "expected: <node> <cmd> [args...]")
    node, remote_argv = argv[0], argv[1:]
    if not any("run" in (g.get("verbs") or [])
               for g in P.find_grants(grant, "ssh", node)):
        raise Deny("grant:no_ssh_run", f"no ssh grant for {node} with verb run")
    try:
        policy = P.load_policy(POLICY_PATH)
    except P.PolicyError as e:
        raise Deny("policy_unreadable", str(e))
    ok, rule, reason = P.check_run(node, remote_argv, policy)
    if not ok:
        raise Deny(rule, reason)
    key = os.path.join(SECRETS_DIR, "secrets", "fleet-ssh.key")
    if not os.path.isfile(key):
        raise Deny("token_unreadable",
                   "engine store has no fleet-ssh.key — run token-engine/"
                   "install.sh to migrate the worker ssh key")
    cmd = [SSH_BIN, "-i", key, "-o", "BatchMode=yes", "-o", "ConnectTimeout=10",
           "-o", "StrictHostKeyChecking=accept-new", node, "--",
           *[shlex.quote(a) for a in remote_argv]]
    try:
        p = subprocess.run(cmd, capture_output=True, text=True,
                           timeout=SSH_TIMEOUT_S)
    except (OSError, subprocess.TimeoutExpired) as e:
        raise Deny("ssh_exec_failed", f"ssh: {type(e).__name__}")
    out = ((p.stdout or "") + (p.stderr or ""))[-OUT_TAIL_BYTES:]
    return {"node": node, "exit_code": p.returncode, "out": mask_rewrite(out)}


# verb -> (handler, grant kind, grant node). Every T2 verb needs a
# {"kind": "token_engine", "node": "daemon"} grant entry; provider verbs added
# by later children bring their own kinds per TOKEN_ENGINE_DESIGN.md §2.
# node=None means "derive from argv[0]": the PR verbs are granted per resource
# as {"kind": pr_*, "node": "repo:owner/name"} (design §2), so handle() checks
# the slug's shape and matches the grant against the repo actually named.
VERBS = {
    "ping": (verb_ping, "token_engine", "daemon"),
    "mask-selftest": (verb_mask_selftest, "token_engine", "daemon"),
    "ticket-end": (verb_ticket_end, "token_engine", "daemon"),
    "mint-test": (verb_mint_test, "token_engine", "daemon"),
    "pr-open": (verb_pr_open, "pr_write", None),
    "pr-update": (verb_pr_update, "pr_write", None),
    "pr-comment": (verb_pr_comment, "pr_comment", None),
    "pr-view": (verb_pr_view, "pr_read", None),
    "pr-checks": (verb_pr_checks, "pr_read", None),
    "pr-files": (verb_pr_files, "pr_read", None),
    "pr-list": (verb_pr_list, "pr_read", None),
    "pr-merge": (verb_pr_merge, "pr_write", None),
    "git-credential": (verb_git_credential, "git_read", None),
    "git-fetch": (verb_git_fetch, "git_read", None),
    "git-push": (verb_git_push, "git_write", None),
    "gateway-key": (verb_gateway_key, "gateway_admin", "gateway:litellm"),
    "api-call": (verb_api_call, "api", API_FROM_ARGV),
    "plug": (verb_plug, "plug", PLUG_FROM_ARGV),
    "ssh-run": (verb_ssh_run, "ssh", NODE_FROM_ARGV),
}


# ------------------------------------------------------------------- serving

def handle(req, peer):
    """Returns (ok, rule, reason, result, decision). Deny-by-default: the only
    path to ok=True walks every check."""
    verb = req.get("verb")
    if verb not in VERBS:
        return False, "verb_unknown", f"no such verb {verb!r}", {}, "deny"
    if verb == "mint-test" and os.environ.get("TOKEN_ENGINE_ALLOW_TEST_MINT") != "1":
        return False, "verb_disabled", \
            "mint-test requires TOKEN_ENGINE_ALLOW_TEST_MINT=1 in the daemon env", {}, "deny"
    worker = str(req.get("worker", ""))
    if not WORKER_RE.match(worker):
        return False, "worker_malformed", "worker id fails shape check", {}, "deny"
    handler, kind, node = VERBS[verb]
    argv = req.get("argv") or []
    arg0 = argv[0] if argv and isinstance(argv[0], str) else ""
    if node is None:
        # PR/git verbs: the resource is the repo slug in argv[0] and the grant
        # is per-resource ({kind: pr_*|git_*, node: "repo:owner/name"}, §2).
        if not REPO_RE.match(arg0):
            return False, "repo_malformed", "argv[0] must be owner/name", {}, "deny"
        node = "repo:" + arg0
    elif node == API_FROM_ARGV:
        # api-call: BOTH the kind and the node are per-provider — a grant for
        # api:cloudflare must not reach api:ebay (design §2, kind api:<provider>).
        if not PROVIDER_RE.match(arg0):
            return False, "provider_malformed", "argv[0] must name a provider", {}, "deny"
        kind = node = "api:" + arg0
    elif node == PLUG_FROM_ARGV:
        if not PLUG_RE.match(arg0):
            return False, "plug_malformed", "argv[0] must be a plug name (pN)", {}, "deny"
        node = "plug:" + arg0
    elif node == NODE_FROM_ARGV:
        # ssh-run: same grant shape fleet-exec checks ({"kind": "ssh",
        # "node": <name>, "verbs": [...]}); the verbs list is re-checked in
        # the handler because find_grants matches kind+node only.
        if not NODE_RE.match(arg0):
            return False, "node_malformed", "argv[0] must be a fleet node name", {}, "deny"
        node = arg0
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
    except Deny as d:
        return False, d.rule, d.reason, {}, "deny"
    except Exception as e:
        return False, "verb_crashed", f"{type(e).__name__}: {e}", {}, "error"
    return True, f"grant:{kind}", "ok", result, "allow"


def respond(conn, req, peer, ok, rule, reason, result, decision, t0):
    frame = {"ok": ok, "verb": str(req.get("verb", "")),
             "ticket": str(req.get("ticket", "")), "rule": rule,
             "reason": reason, "result": result,
             "elapsed_s": round(time.monotonic() - t0, 3)}
    suppressed = []
    try:
        assert_response_schema(frame)
        body = json.dumps(frame, sort_keys=True)
        hits = mask_hits(body)
        # wq#2677: read the outbound frame for what the rule declined to alarm on, on
        # the same text the tripwire just screened. Its own try: an audit row is not a
        # verdict, so it may never be the reason a verb fails or a frame goes unsent.
        try:
            suppressed = mask_suppressions(body)
        except Exception:  # noqa: BLE001
            suppressed = []
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
        # The suppression key is omitted entirely when nothing was suppressed, so the
        # audit line only grows on the calls that have something to say.
        extra = {"mask_suppressed": suppressed} if suppressed else {}
        audit(req, peer, decision, frame["rule"],
              exit_code=0 if frame["ok"] else 1,
              elapsed_s=frame["elapsed_s"], **extra)
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


def _grant_client_acls():
    """wq#2561: a `usermod -aG token-client lumen` grant never reaches a lumen
    process that was already running (forked before or fork()'d off a parent
    from before the grant) — Linux fixes a process's supplementary groups at
    fork/exec time and never re-reads /etc/group for it, so it keeps EACCES
    on connect() until that process (or its ancestor) restarts. A named-user
    ACL entry is matched by the kernel's ordinary connect()-time permission
    check against the caller's fsuid — not via SO_PEERCRED, which this daemon
    reads only after accept() to identify an already-admitted peer. fsuid is
    not cached the way supplementary groups are, so the u: entry grants
    access immediately on every daemon (re)start regardless of how old the
    client process's group cache is.

    DEPLOY: this file runs from /usr/local/lib/token-engine (service unit
    ExecStart), NOT from the repo — editing it here changes nothing until
    install.sh is re-run as root AND token-engine is restarted. Landing
    without that step is what made wq#2561 closable-while-false (wq#2562,
    ISSUE-LOG rows 595/695/697 are all this same drift on this same file).
    """
    try:
        subprocess.run(["setfacl", "-m", f"u:{CLIENT_USER}:rw", SOCK_PATH], check=True)
    except (OSError, subprocess.CalledProcessError) as e:
        print(f"warning: setfacl u:{CLIENT_USER}:rw {SOCK_PATH} failed: {e}",
              file=sys.stderr, flush=True)
    # wq#2566: the socket ACL above is useless without traversal on the
    # directory that holds it — the kernel path walk needs x on SOCK_DIR
    # before it ever looks at the socket inode, and SOCK_DIR is 0750
    # tokeneng:token-client with no uid ACL, so it hits the exact same
    # stale-supplementary-group problem the socket ACL fixed. `x` only,
    # never `r` — a uid must not be able to list SOCK_DIR/minted.
    try:
        subprocess.run(["setfacl", "-m", f"u:{CLIENT_USER}:x", SOCK_DIR], check=True)
    except (OSError, subprocess.CalledProcessError) as e:
        print(f"warning: setfacl u:{CLIENT_USER}:x {SOCK_DIR} failed: {e}",
              file=sys.stderr, flush=True)


def main():
    if os.getuid() == 0:
        sys.exit("refusing to run as root: unit must set User=tokeneng")
    import grp
    client_gid = grp.getgrnam(CLIENT_GROUP).gr_gid
    # §3.4 reaper: BEFORE accepting any connection, revoke every ledgered
    # token. A kill -9'd worker never fires SessionEnd; its tokens die here.
    # A reaper crash must not stop the broker (the ledger survives for the
    # next start), but it must be loud.
    try:
        reaped = L.reap_all()
        print(f"reaper: {reaped}", flush=True)
        if reaped["failed"]:
            notify("high", "token-engine reaper incomplete",
                   f"failed={reaped['failed']} entries still ledgered — "
                   "provider unreachable? will retry next daemon start")
    except Exception as e:
        print(f"reaper crashed: {type(e).__name__}: {e}", file=sys.stderr, flush=True)
        notify("high", "token-engine reaper crashed", f"{type(e).__name__}: {e}")
    os.makedirs(SOCK_DIR, mode=0o750, exist_ok=True)
    try:
        os.unlink(SOCK_PATH)
    except FileNotFoundError:
        pass
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    srv.bind(SOCK_PATH)
    os.chown(SOCK_PATH, -1, client_gid)
    os.chmod(SOCK_PATH, 0o660)
    _grant_client_acls()
    srv.listen(16)
    print(f"token-engine listening on {SOCK_PATH} uid={os.getuid()} "
          f"self_sha256={SELF_SHA256}", flush=True)
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
