"""liveness.py — Liveness Gate, Slice 1 (research/LIVENESS_GATE.md §6).

Deterministic per-ticket liveness probes: each probe re-checks a frank-gated
ticket's premise against LIVE state and returns one of

    SATISFIED      the premise is already met -> the gate is stale
    STILL_BLOCKED  the premise is genuinely unmet -> Frank really is needed
    UNKNOWN        the probe could not decide -> fail-safe, leave the ticket alone

No LLM, no writes. Probes read files, GET HTTP endpoints, run `systemctl is-active`
and `tailscale status`. The one non-GET is balance_over(cerebras, ..) which sends a
max_tokens=1 completion because that is the surface that returns the 402 (a models
list is 200 regardless of billing).

FIREWALL (§4): nothing here is imported by needs.py's Needs object, policy_grants,
preflight, or fworker.resolve_exec_grant. A liveness verdict is never a grant.

Registry: PROBES maps the tag name used on a `Liveness:` line to a callable taking
string args. run(name, args) -> (verdict, detail) and never raises.

Rollback: FOREMAN_LIVENESS_DISABLE=1 makes run() return UNKNOWN for every probe.
"""
import json
import os
import subprocess
import urllib.error
import urllib.request

SATISFIED = "SATISFIED"
STILL_BLOCKED = "STILL_BLOCKED"
UNKNOWN = "UNKNOWN"
VERDICTS = (SATISFIED, STILL_BLOCKED, UNKNOWN)

HTTP_TIMEOUT_S = 15
SSH_TIMEOUT_S = 20
TS_API_KEY = os.environ.get("FOREMAN_TS_API_KEY", "/home/lumen/.secrets/tailscale-api.key")
GH_TOKEN = os.environ.get("FOREMAN_GH_TOKEN", "/home/lumen/.secrets/github-frank.token")
AGENT_ENV = os.environ.get("FOREMAN_AGENT_ENV", "/home/lumen/agent/.env")
# wq#1492 backlog probes: ticket_done reads queue.db read-only (same WQ_DB the sweep
# uses); pr_merged reads the forge API with the same token file lumen-pr uses.
WQ_DB = os.environ.get("WQ_DB", "/home/lumen/agents/work-queue/queue.db")
GITEA_TOKEN = os.environ.get("FOREMAN_GITEA_TOKEN", "/home/lumen/.secrets/gitea-lumen.token")
GITEA_URL = os.environ.get("FOREMAN_GITEA_URL", "http://100.121.17.5:3000")


def disabled():
    return os.environ.get("FOREMAN_LIVENESS_DISABLE") == "1"


def _read_secret(path):
    try:
        with open(path) as f:
            return f.read().strip()
    except OSError:
        return ""


def _env_value(key, path=AGENT_ENV):
    try:
        with open(path) as f:
            for line in f:
                if line.startswith(key + "="):
                    return line.split("=", 1)[1].strip().strip('"').strip("'")
    except OSError:
        pass
    return ""


def _http(url, headers=None, data=None, method=None):
    """(status, body_text). status 0 on transport error (timeout, DNS)."""
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={"User-Agent": "lumen-liveness/1", **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT_S) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        try:
            body = e.read().decode("utf-8", "replace")
        except Exception:
            body = ""
        return e.code, body
    except Exception as e:
        return 0, str(e)[:120]


def _sh(argv, timeout=SSH_TIMEOUT_S, cwd=None):
    """argv list, never a shell string. (rc, stdout, stderr); rc 99 on exception."""
    try:
        p = subprocess.run(argv, capture_output=True, text=True, timeout=timeout, cwd=cwd)
        return p.returncode, p.stdout.strip(), p.stderr.strip()
    except Exception as e:
        return 99, "", str(e)[:120]


# ---------------------------------------------------------------------------
# probes — each returns (verdict, detail); detail never contains a secret value
# ---------------------------------------------------------------------------

def cred_alive(path, verify_url, scheme="bearer"):
    """Credential file present AND verify_url answers 2xx with it. scheme is the
    Authorization prefix: bearer (CF, TS, GitHub PATs all accept it) or token."""
    if not os.path.exists(path):
        return STILL_BLOCKED, f"{path} missing"
    tok = _read_secret(path)
    if not tok:
        return STILL_BLOCKED, f"{path} empty"
    prefix = {"bearer": "Bearer", "token": "token"}.get(scheme.lower(), "Bearer")
    status, _ = _http(verify_url, {"Authorization": f"{prefix} {tok}"})
    if 200 <= status < 300:
        return SATISFIED, f"{os.path.basename(path)} present, verify {status}"
    if status in (401, 403):
        return STILL_BLOCKED, f"{os.path.basename(path)} rejected ({status})"
    return UNKNOWN, f"verify http {status}"


def ts_key_expiry_disabled():
    """Every tailnet device has keyExpiryDisabled=true."""
    tok = _read_secret(TS_API_KEY)
    if not tok:
        return UNKNOWN, "no TS API key"
    status, body = _http("https://api.tailscale.com/api/v2/tailnet/-/devices",
                         {"Authorization": f"Bearer {tok}"})
    if status != 200:
        return UNKNOWN, f"TS api http {status}"
    try:
        devices = json.loads(body).get("devices", [])
    except ValueError:
        return UNKNOWN, "TS api non-json"
    if not devices:
        return UNKNOWN, "TS api returned 0 devices"
    bad = [d.get("hostname") for d in devices if not d.get("keyExpiryDisabled")]
    if bad:
        return STILL_BLOCKED, f"{len(bad)}/{len(devices)} still expiring: {', '.join(bad[:5])}"
    return SATISFIED, f"all {len(devices)} devices keyExpiryDisabled"


def repo_is(want, repo):
    """want = private|public. Authenticated GET decides when it can see the repo;
    for want=private an auth 404 + unauth 404 is 'not publicly visible' which is the
    outcome the ticket asks for (an org repo the PAT cannot read still counts)."""
    want = want.lower()
    if want not in ("private", "public"):
        return UNKNOWN, f"bad want {want!r}"
    url = f"https://api.github.com/repos/{repo}"
    tok = _read_secret(GH_TOKEN)
    status, body = _http(url, {"Authorization": f"Bearer {tok}"} if tok else {})
    if status == 200:
        try:
            private = bool(json.loads(body).get("private"))
        except ValueError:
            return UNKNOWN, "gh non-json"
        actual = "private" if private else "public"
        return (SATISFIED if actual == want else STILL_BLOCKED), f"{repo} is {actual}"
    if status == 404:
        ustatus, _ = _http(url)
        if ustatus == 200:
            return (SATISFIED if want == "public" else STILL_BLOCKED), f"{repo} public (unauth 200)"
        if ustatus == 404:
            return (SATISFIED if want == "private" else STILL_BLOCKED), f"{repo} not publicly visible"
        return UNKNOWN, f"gh unauth http {ustatus}"
    return UNKNOWN, f"gh http {status}"


def balance_over(provider, minimum="0"):
    """Provider account can serve. cerebras: a max_tokens=1 completion (the surface
    that 402s). openrouter: /auth/key limit_remaining > minimum (None = unlimited)."""
    provider = provider.lower()
    try:
        minimum = float(minimum)
    except ValueError:
        return UNKNOWN, f"bad minimum {minimum!r}"
    if provider == "cerebras":
        key = _env_value("CEREBRAS_API_KEY")
        if not key:
            return UNKNOWN, "no CEREBRAS_API_KEY in agent .env"
        payload = json.dumps({"model": "gpt-oss-120b", "max_tokens": 1,
                              "messages": [{"role": "user", "content": "ping"}]}).encode()
        status, _ = _http("https://api.cerebras.ai/v1/chat/completions",
                          {"Authorization": f"Bearer {key}",
                           "Content-Type": "application/json"}, data=payload, method="POST")
        if status == 402:
            return STILL_BLOCKED, "cerebras 402 payment required"
        if 200 <= status < 300:
            return SATISFIED, "cerebras served a 1-token completion"
        return UNKNOWN, f"cerebras http {status}"
    if provider == "openrouter":
        key = _env_value("OPENROUTER_API_KEY")
        if not key:
            return UNKNOWN, "no OPENROUTER_API_KEY in agent .env"
        status, body = _http("https://openrouter.ai/api/v1/auth/key",
                             {"Authorization": f"Bearer {key}"})
        if status != 200:
            return UNKNOWN, f"openrouter http {status}"
        try:
            d = json.loads(body).get("data", {})
        except ValueError:
            return UNKNOWN, "openrouter non-json"
        limit, usage = d.get("limit"), d.get("usage", 0)
        if limit is None:
            return SATISFIED, f"openrouter unlimited key (usage {usage})"
        remaining = float(limit) - float(usage or 0)
        return (SATISFIED if remaining > minimum else STILL_BLOCKED), \
            f"openrouter remaining {remaining:.2f} vs min {minimum}"
    return UNKNOWN, f"unsupported provider {provider!r}"


def path_exists(path):
    return (SATISFIED, f"{path} exists") if os.path.exists(path) else (STILL_BLOCKED, f"{path} missing")


def _ssh_argv(node, remote_argv):
    return ["ssh", "-o", "BatchMode=yes", "-o", f"ConnectTimeout={SSH_TIMEOUT_S // 2}",
            node, "--", *remote_argv]


def service_active(target):
    """target = unit@node. Local systemctl on lumen, ssh alias otherwise."""
    unit, _, node = target.partition("@")
    if not unit or not node:
        return UNKNOWN, f"bad target {target!r} (want unit@node)"
    argv = ["systemctl", "is-active", unit]
    rc, out, err = _sh(argv if node == "lumen" else _ssh_argv(node, argv))
    if rc == 0 and out == "active":
        return SATISFIED, f"{unit}@{node} active"
    if out in ("inactive", "failed", "deactivating", "activating"):
        return STILL_BLOCKED, f"{unit}@{node} {out}"
    if rc == 255 or rc == 99:
        return UNKNOWN, f"ssh {node}: {err[:80]}"
    return UNKNOWN, f"{unit}@{node} rc={rc} out={out!r}"


def node_healthy(name):
    """tailscale reports the peer Online AND `ssh <name> -- true` returns 0."""
    rc, out, err = _sh(["tailscale", "status", "--json"])
    if rc != 0:
        return UNKNOWN, f"tailscale status rc={rc}"
    try:
        d = json.loads(out)
    except ValueError:
        return UNKNOWN, "tailscale status non-json"
    peers = {p.get("HostName", "").lower(): p for p in d.get("Peer", {}).values()}
    self_host = (d.get("Self") or {}).get("HostName", "").lower()
    if name.lower() == self_host or name.lower() == "lumen":
        online = True
    elif name.lower() in peers:
        online = bool(peers[name.lower()].get("Online"))
    else:
        return UNKNOWN, f"{name} not in tailscale status"
    if not online:
        return STILL_BLOCKED, f"{name} tailscale Online=false"
    if name.lower() == "lumen":
        return SATISFIED, "lumen is self"
    rc, _, err = _sh(_ssh_argv(name, ["true"]))
    if rc == 0:
        return SATISFIED, f"{name} online + ssh ok"
    return UNKNOWN, f"{name} online but ssh rc={rc}: {err[:60]}"


# ---------------------------------------------------------------------------
# backlog probes (wq#1492, LIVENESS_GATE.md §13) — deterministic, read-only
# ---------------------------------------------------------------------------

def ticket_done(tid):
    """wq#<tid> is status=done in queue.db. Only for tickets whose OUTCOME the cited
    ticket delivers (superseded-by); a prerequisite dep being done means unblock, not
    done, and must not be bound (§13.2)."""
    try:
        tid = int(str(tid).lstrip("wq#"))
    except ValueError:
        return UNKNOWN, f"bad ticket id {tid!r}"
    import sqlite3
    try:
        con = sqlite3.connect(f"file:{WQ_DB}?mode=ro", uri=True)
        try:
            row = con.execute("SELECT status FROM tasks WHERE id=?", (tid,)).fetchone()
        finally:
            con.close()
    except sqlite3.Error as e:
        return UNKNOWN, f"queue.db: {str(e)[:60]}"
    if row is None:
        return UNKNOWN, f"wq#{tid} not in queue.db"
    status = (row[0] or "").strip()
    if status == "done":
        return SATISFIED, f"wq#{tid} done"
    return STILL_BLOCKED, f"wq#{tid} {status or '?'}"


def pr_merged(repo, n, want="merged", forge="gitea"):
    """PR #n on owner/repo is merged (want=merged) or closed either way (want=closed).
    forge=gitea (default, the fleet forge) or github. A PR the token cannot see (404)
    is UNKNOWN, never a verdict."""
    want, forge = want.lower(), forge.lower()
    if want not in ("merged", "closed"):
        return UNKNOWN, f"bad want {want!r}"
    if forge == "gitea":
        url = f"{GITEA_URL.rstrip('/')}/api/v1/repos/{repo}/pulls/{n}"
        tok = _read_secret(GITEA_TOKEN)
        headers = {"Authorization": f"token {tok}"} if tok else {}
    elif forge == "github":
        url = f"https://api.github.com/repos/{repo}/pulls/{n}"
        tok = _read_secret(GH_TOKEN)
        headers = {"Authorization": f"Bearer {tok}"} if tok else {}
    else:
        return UNKNOWN, f"bad forge {forge!r}"
    status, body = _http(url, headers)
    if status != 200:
        return UNKNOWN, f"{forge} {repo}#{n} http {status}"
    try:
        d = json.loads(body)
    except ValueError:
        return UNKNOWN, f"{forge} {repo}#{n} non-json"
    merged, state = bool(d.get("merged")), (d.get("state") or "?")
    label = f"{repo}#{n} {'merged' if merged else state}"
    if want == "merged":
        return (SATISFIED if merged else STILL_BLOCKED), label
    return (SATISFIED if (merged or state == "closed") else STILL_BLOCKED), label


def file_contains(path, needle):
    """A readable file contains the literal needle. Detail echoes the needle, never
    the file content."""
    if not needle:
        return UNKNOWN, "empty needle"
    try:
        with open(path, errors="replace") as f:
            text = f.read()
    except OSError as e:
        return UNKNOWN, f"{path}: {e.strerror or 'unreadable'}"
    if needle in text:
        return SATISFIED, f"{os.path.basename(path)} contains {needle!r}"
    return STILL_BLOCKED, f"{os.path.basename(path)} lacks {needle!r}"


def json_gte(path, key, minimum):
    """Numeric value at dotted `key` in a JSON file is >= minimum."""
    try:
        minimum = float(minimum)
    except ValueError:
        return UNKNOWN, f"bad minimum {minimum!r}"
    try:
        with open(path) as f:
            d = json.load(f)
    except (OSError, ValueError) as e:
        return UNKNOWN, f"{path}: {str(e)[:60]}"
    for part in key.split("."):
        if not isinstance(d, dict) or part not in d:
            return UNKNOWN, f"{os.path.basename(path)}: no key {key!r}"
        d = d[part]
    if isinstance(d, bool) or not isinstance(d, (int, float)):
        return UNKNOWN, f"{key} is not a number"
    return (SATISFIED if d >= minimum else STILL_BLOCKED), f"{key}={d} vs min {minimum:g}"


# ---------------------------------------------------------------------------
# wq#1676 probes: covers #1356's class (dirty worktree) and decide-chains like #124/#940
# ---------------------------------------------------------------------------

def git_clean(repo):
    """`git status --porcelain` in <repo> is empty. SATISFIED = the tree has nothing
    uncommitted. UNKNOWN on a bad path or any git/subprocess error, never a guess."""
    if not os.path.isdir(repo):
        return UNKNOWN, f"{repo} not a directory"
    rc, out, err = _sh(["git", "status", "--porcelain"], cwd=repo)
    if rc != 0:
        return UNKNOWN, f"git status rc={rc}: {err[:80]}"
    if out.strip():
        n = len(out.strip().splitlines())
        return STILL_BLOCKED, f"{repo} dirty ({n} entr{'y' if n == 1 else 'ies'})"
    return SATISFIED, f"{repo} clean"


def wq_field(id, col, val):
    """Column <col> of wq#<id> in queue.db's `tasks` table equals <val> (string compare).
    col is untrusted (it comes from a Liveness: line an agent wrote) so it is checked
    against PRAGMA table_info(tasks) before being interpolated into the SELECT."""
    try:
        tid = int(str(id).lstrip("wq#"))
    except ValueError:
        return UNKNOWN, f"bad ticket id {id!r}"
    import sqlite3
    try:
        con = sqlite3.connect(f"file:{WQ_DB}?mode=ro", uri=True)
        try:
            cols = {r[1] for r in con.execute("PRAGMA table_info(tasks)")}
            if col not in cols:
                return UNKNOWN, f"tasks has no column {col!r}"
            row = con.execute(f"SELECT {col} FROM tasks WHERE id=?", (tid,)).fetchone()  # noqa: S608 col whitelisted above
        finally:
            con.close()
    except sqlite3.Error as e:
        return UNKNOWN, f"queue.db: {str(e)[:60]}"
    if row is None:
        return UNKNOWN, f"wq#{tid} not in queue.db"
    actual = "" if row[0] is None else str(row[0])
    if actual == str(val):
        return SATISFIED, f"wq#{tid}.{col} == {val!r}"
    return STILL_BLOCKED, f"wq#{tid}.{col} == {actual!r}, want {val!r}"


# ---------------------------------------------------------------------------
# registry — the closed vocabulary a `Liveness:` line may use (§5, §6, §13)
# ---------------------------------------------------------------------------

PROBES = {
    "cred_alive": cred_alive,
    "ts_key_expiry_disabled": ts_key_expiry_disabled,
    "repo_is": repo_is,
    "balance_over": balance_over,
    "path_exists": path_exists,
    "service_active": service_active,
    "node_healthy": node_healthy,
    "ticket_done": ticket_done,
    "pr_merged": pr_merged,
    "file_contains": file_contains,
    "json_gte": json_gte,
    "git_clean": git_clean,
    "wq_field": wq_field,
}
PROBE_NAMES = tuple(PROBES)


def split_args(arg_text):
    """'a, b' -> ['a', 'b']; '' -> []. Same comma split the needs vocabulary uses."""
    return [a.strip() for a in (arg_text or "").split(",") if a.strip()]


def run(name, arg_text):
    """(verdict, detail) for one probe tag. Never raises; anything unexpected is
    UNKNOWN, which Slice 3 must treat as leave-the-ticket-alone."""
    if disabled():
        return UNKNOWN, "FOREMAN_LIVENESS_DISABLE=1"
    fn = PROBES.get(name)
    if fn is None:
        return UNKNOWN, f"unknown probe {name!r} (vocabulary: {', '.join(PROBE_NAMES)})"
    try:
        verdict, detail = fn(*split_args(arg_text))
    except TypeError as e:
        return UNKNOWN, f"{name}: bad arity ({str(e)[:60]})"
    except Exception as e:
        return UNKNOWN, f"{name}: {type(e).__name__}: {str(e)[:60]}"
    if verdict not in VERDICTS:
        return UNKNOWN, f"{name}: probe returned non-verdict {verdict!r}"
    return verdict, detail


def aggregate(results):
    """Per-ticket verdict over [(verdict, detail), ...]: any STILL_BLOCKED wins,
    then any UNKNOWN, and only all-SATISFIED is SATISFIED. Empty -> UNKNOWN."""
    verdicts = [v for v, _ in results]
    if not verdicts:
        return UNKNOWN
    if STILL_BLOCKED in verdicts:
        return STILL_BLOCKED
    if UNKNOWN in verdicts:
        return UNKNOWN
    return SATISFIED
