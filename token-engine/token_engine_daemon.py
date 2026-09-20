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
import urllib.error
import urllib.parse
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fleet_exec_policy as P  # root-owned copy installed next to this file
import token_ledger as L      # minted-token ledger + revocation (wq#2277 T9)

SOCK_DIR = os.environ.get("TOKEN_ENGINE_RUNTIME_DIR", "/run/token-engine")
SOCK_PATH = os.path.join(SOCK_DIR, "broker.sock")
SECRETS_DIR = os.environ.get("TOKEN_ENGINE_STATE_DIR", "/var/lib/token-engine")
# Overridable so a gate replay can run an env-scoped daemon as an ordinary
# uid; the systemd unit sets neither env, so production keeps token-client.
CLIENT_GROUP = os.environ.get("TOKEN_ENGINE_CLIENT_GROUP", "token-client")
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

class Deny(Exception):
    """A clean verb refusal: (rule, reason). handle() turns it into a deny
    frame rather than the verb_crashed error path."""
    def __init__(self, rule, reason):
        super().__init__(reason)
        self.rule = rule
        self.reason = reason


def verb_ping(req, grant):
    return {"pong": True, "daemon_uid": os.getuid(),
            "secrets_dir": SECRETS_DIR}


def verb_mask_selftest(req, grant):
    """Deliberately returns a credential-shaped string so the outbound tripwire
    fires. Proves property 4 end to end without a real secret in play."""
    return {"leak": "ghp_" + "selftestselftest0000"}


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

FORGES_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "forges.json")
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
    if node is None:
        # PR verbs: the resource is the repo slug in argv[0] and the grant is
        # per-resource ({"kind": pr_*, "node": "repo:owner/name"}, design §2).
        argv = req.get("argv") or []
        if not argv or not isinstance(argv[0], str) or not REPO_RE.match(argv[0]):
            return False, "repo_malformed", "argv[0] must be owner/name", {}, "deny"
        node = "repo:" + argv[0]
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
