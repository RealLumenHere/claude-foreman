#!/usr/bin/env python3
"""cli_canary — can the installed claude CLI drive each model on the router ladder? (wq#2527)

wq#2525 incident: the router started assigning claude-opus-5-5 while Lumen's CLI was 2.1.276;
the API answered claude_code_version_too_old and every opus-5-5 launch died for ~5.5h. Nothing
checked that a rung's model is drivable by the installed CLI, and nothing tracked CLI drift.

  held(model)   the router/dispatcher gate. A rung is held when the installed CLI is below the
                model's models.yaml `min_claude_code`, or when the cached canary for
                (installed CLI version, model) failed. No cached entry = no evidence = not held.
  run           probe every live claude-* ladder model whose (CLI version, model) entry is
                missing or older than 24h: `claude -p 'reply OK' --max-turns 1`. A ladder edit
                (new model) or a CLI upgrade (new version) is a cache miss, so both re-probe.
                Also records the installed version as the lumen.claude_code_cli fact on change.
  drift         installed vs npm latest; files ONE aq row when behind and a live rung needs newer.
  status        cached verdicts for the ladder, no probes.

A failure stays held until a re-probe on the same CLI passes. Rate limits, timeouts and auth
errors are inconclusive: never cached, so they never hold a rung.
Rollback: FOREMAN_CLI_CANARY_MODE=off turns held() into a no-op.
"""
import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
import time

DISPATCHER_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(DISPATCHER_DIR)
sys.path.insert(0, os.path.join(REPO_ROOT, "harness"))
sys.path.insert(0, os.path.join(REPO_ROOT, "orchestrator"))

RUNTIME_DIR = os.environ.get("FOREMAN_RUNTIME_DIR", os.path.join(DISPATCHER_DIR, "runtime"))
PACKAGE = "@anthropic-ai/claude-code"
TTL_S = 24 * 3600
PROBE_TIMEOUT_S = 120
PROMPT = "reply OK"
TOO_OLD = "claude_code_version_too_old"
HOLD_TAG = "cli-canary hold:"   # every held() reason starts with it; the dispatcher keys on it
_NEEDS_RE = re.compile(r"requires Claude Code version (\d+\.\d+\.\d+)")
_VERSION_RE = re.compile(r"(\d+\.\d+\.\d+)")
# lowercase substrings of CLI output that say nothing about whether this CLI can drive the model
INCONCLUSIVE = ("rate_limit", "rate limit", "overloaded", "usage limit", "529", "timed out",
                "etimedout", "econnreset", "enotfound", "401", "authentication", "/login")


def cache_path():
    return os.environ.get("FOREMAN_CLI_CANARY", os.path.join(RUNTIME_DIR, "cli_canary.json"))


def claude_bin():
    return os.environ.get("FOREMAN_CLAUDE_BIN", "/home/lumen/.npm-global/bin/claude")


def vtuple(v):
    return tuple(int(x) for x in v.split(".")) if v and _VERSION_RE.fullmatch(v) else None


def installed_version(bin_path=None):
    """The claude-code version behind `bin_path`: its package.json (no process spawn, so the
    router can ask on every select), else `claude --version`. None when neither answers."""
    d = os.path.dirname(os.path.realpath(bin_path or claude_bin()))
    for _ in range(4):
        try:
            with open(os.path.join(d, "package.json")) as f:
                pkg = json.load(f)
            if pkg.get("name") == PACKAGE and vtuple(pkg.get("version")):
                return pkg["version"]
        except (OSError, ValueError, AttributeError):
            pass
        d = os.path.dirname(d)
    try:
        p = subprocess.run([bin_path or claude_bin(), "--version"], capture_output=True,
                           text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return None
    m = _VERSION_RE.search(p.stdout or "")
    return m.group(1) if p.returncode == 0 and m else None


def load(path=None):
    try:
        with open(path or cache_path()) as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save(data, path=None):
    path = path or cache_path()
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path) or ".", prefix=".cli_canary.")
    with os.fdopen(fd, "w") as f:
        json.dump(data, f, indent=1, sort_keys=True)
    os.replace(tmp, path)


def _key(version, model):
    return f"{version}|{model}"


def gate(model_rows=None, version=None, path=None):
    """-> f(model) -> None when the installed CLI can drive `model`, else why the rung is held.
    Reads the version and cache once, so a router select() pays for them once."""
    if os.environ.get("FOREMAN_CLI_CANARY_MODE", "").strip() == "off":
        return lambda model: None
    version = version or installed_version()
    entries = load(path).get("entries") or {}
    rows = model_rows or {}

    def check(model):
        if not version or not str(model).startswith("claude-"):
            return None
        need = str((rows.get(model) or {}).get("min_claude_code") or "")
        if vtuple(need) and vtuple(need) > vtuple(version):
            return f"{HOLD_TAG} {model} needs claude-code >= {need}, installed {version}"
        e = entries.get(_key(version, model)) or {}
        if e.get("ok") is False:
            return f"{HOLD_TAG} {model} failed on claude-code {version}: {e.get('reason')}"
        return None
    return check


def held(model, model_rows=None, **kw):
    return gate(model_rows, **kw)(model)


def _mask(text):
    try:
        import masking
        return masking.mask_text(text)[0]
    except Exception:  # masking is best-effort here; the text is CLI output, not a secret file
        return text


def classify(rc, stdout, stderr):
    """-> (ok, reason, needs). ok None = inconclusive (not cached)."""
    text = f"{stdout or ''}\n{stderr or ''}"
    frame = {}
    for line in reversed((stdout or "").strip().splitlines()):
        try:
            frame = json.loads(line)
            break
        except ValueError:
            continue
    frame = frame if isinstance(frame, dict) else {}
    # markers are matched against the message only: the json frame's ids and counters could
    # contain '401' or '529' by accident
    msg = f"{frame.get('result') or ('' if frame else stdout) or ''}\n{stderr or ''}"
    needs = _NEEDS_RE.search(msg)
    if TOO_OLD in text or needs:
        return False, TOO_OLD + (f" (needs >= {needs.group(1)})" if needs else ""), \
            needs.group(1) if needs else None
    if rc == 0 and not frame.get("is_error"):
        return True, "ok", None
    if frame.get("subtype") == "error_max_turns":   # a model turn ran: the CLI drives it
        return True, "ok (max turns)", None
    tail = _mask(msg.strip())[-300:]
    if any(m in msg.lower() for m in INCONCLUSIVE):
        return None, tail, None
    return False, tail or f"exit {rc}", None


def _probe_env():
    """The auth a worker launch uses (fworker.build_env): the static worker bearer, never an
    API key or gateway, so the canary answers for exactly the path a dispatch takes."""
    env = dict(os.environ)
    for k in ("ANTHROPIC_API_KEY", "ANTHROPIC_BASE_URL", "ANTHROPIC_AUTH_TOKEN",
              "CLAUDE_CODE_OAUTH_TOKEN", "CLAUDE_CODE_ENABLE_GATEWAY_MODEL_DISCOVERY"):
        env.pop(k, None)
    token_file = os.environ.get("FOREMAN_OAUTH_TOKEN_FILE",
                                "/home/lumen/.secrets/claude-oat-worker.token")
    try:
        with open(token_file) as f:
            token = f.read().strip()
        if token:
            env["CLAUDE_CODE_OAUTH_TOKEN"] = token
    except OSError:
        pass
    return env


def probe(model, bin_path=None):
    cmd = [bin_path or claude_bin(), "-p", PROMPT, "--model", model, "--max-turns", "1",
           "--output-format", "json"]
    # an empty dir outside /home/lumen: no project CLAUDE.md for the model to act on, so it
    # answers instead of reaching for a tool (a worktree cwd hit error_max_turns)
    with tempfile.TemporaryDirectory(dir="/tmp", prefix="cli-canary.") as cwd:
        try:
            p = subprocess.run(cmd, capture_output=True, text=True, timeout=PROBE_TIMEOUT_S,
                               env=_probe_env(), cwd=cwd)
        except subprocess.TimeoutExpired:
            return None, f"timed out after {PROBE_TIMEOUT_S}s", None
        except OSError as e:
            return None, f"cannot exec {cmd[0]}: {e}", None
    return classify(p.returncode, p.stdout, p.stderr)


def refresh(models, version, *, force=False, now=None, path=None, probe_fn=None, bin_path=None):
    """Probe each model whose (version, model) entry is missing or stale. -> {model: result}."""
    now = now or time.time()
    probe_fn = probe_fn or (lambda m: probe(m, bin_path))
    data = load(path)
    entries = data.setdefault("entries", {})
    out = {}
    for model in models:
        e = entries.get(_key(version, model))
        if e and not force and now - float(e.get("ts") or 0) < TTL_S:
            out[model] = dict(e, cached=True)
            continue
        ok, reason, needs = probe_fn(model)
        row = {"cli": version, "model": model, "ok": ok, "reason": reason, "ts": now}
        if needs:
            row["needs"] = needs
        if ok is not None:
            entries[_key(version, model)] = row
        out[model] = row
    save(data, path)
    return out


def ladder_models(manifest=None):
    """Every claude-* model on a routing chain whose models.yaml status is LIVE* (or absent)."""
    import router
    manifest = manifest or router.load_manifest()
    out = set()
    for chain in manifest.routing.values():
        for r in chain:
            st = (manifest.models.get(r.model) or {}).get("status")
            if r.model.startswith("claude-") and (st is None or str(st).startswith("LIVE")):
                out.add(r.model)
    return sorted(out)


def lumen_state_bin():
    return os.environ.get("FOREMAN_LUMEN_STATE", "/home/lumen/bin/lumen_state.py")


def record_version_fact(version, path=None):
    """lumen.claude_code_cli, ingested only when the installed version changed since the last
    recorded one (cache meta), so a 15-minute timer doesn't restate the fact 96 times a day."""
    data = load(path)
    meta = data.setdefault("meta", {})
    if not version or meta.get("recorded_cli_version") == version:
        return False
    p = subprocess.run([lumen_state_bin(), "ingest", "lumen", "claude_code_cli", version,
                        "--source", "foreman/dispatcher/cli_canary.py", "--source-kind",
                        "observed", "--note",
                        f"installed {PACKAGE} at {claude_bin()} (wq#2527 canary)"],
                       capture_output=True, text=True, timeout=60)
    if p.returncode != 0:
        print(f"cli_canary: fact ingest failed rc={p.returncode}: {p.stderr.strip()[:200]}",
              file=sys.stderr)
        return False
    meta["recorded_cli_version"] = version
    save(data, path)
    return True


def needed_versions(models, installed, model_rows=None, path=None):
    """{model: version} for live ladder models the installed CLI is too old for."""
    rows = model_rows or {}
    entries = load(path).get("entries") or {}
    out = {}
    for model in models:
        need = str((rows.get(model) or {}).get("min_claude_code") or "")
        cached = (entries.get(_key(installed, model)) or {}).get("needs") or ""
        for v in (need, cached):
            if vtuple(v) and vtuple(v) > vtuple(installed) and \
                    (model not in out or vtuple(v) > vtuple(out[model])):
                out[model] = v
    return out


def npm_latest():
    try:
        p = subprocess.run([os.environ.get("FOREMAN_NPM", "npm"), "view", PACKAGE, "version"],
                           capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.SubprocessError):
        return None
    v = (p.stdout or "").strip()
    return v if p.returncode == 0 and vtuple(v) else None


def drift(models, installed, latest, model_rows=None, path=None):
    """-> the aq row (source, key, title, message) to file, or None. One row: aq itself bumps
    an open row with the same (source, key) instead of duplicating it."""
    needs = needed_versions(models, installed, model_rows, path)
    if not (installed and latest and needs) or vtuple(latest) <= vtuple(installed):
        return None
    want = max(needs.values(), key=vtuple)
    return ("cli_canary", "claude-code-behind",
            f"claude-code {installed} is behind {latest}; live rung(s) need >= {want}",
            "Rungs held by the router until the CLI is upgraded: "
            + ", ".join(f"{m} needs {v}" for m, v in sorted(needs.items()))
            + f". Upgrade: npm i -g {PACKAGE}@{latest} (keep a rollback tarball), then "
            "`python3 dispatcher/cli_canary.py run --force`.")


def file_aq(row):
    source, key, title, message = row
    p = subprocess.run([os.environ.get("FOREMAN_AQ_BIN", "/home/lumen/bin/aq"), "add",
                        source, key, title, message], capture_output=True, text=True, timeout=30)
    return p.returncode == 0


def main(argv=None):
    ap = argparse.ArgumentParser(prog="cli_canary.py", description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("run", help="probe missing/stale (cli version, model) pairs")
    s.add_argument("--force", action="store_true", help="re-probe even fresh entries")
    s.add_argument("--model", action="append", help="probe only these (default: the ladder)")
    sub.add_parser("status", help="cached verdicts for the ladder, no probes")
    s = sub.add_parser("drift", help="installed vs npm latest; aq row when a live rung needs it")
    s.add_argument("--dry-run", action="store_true", help="print the row, file nothing")
    args = ap.parse_args(argv)

    import router
    manifest = router.load_manifest()
    version = installed_version()
    if not version:
        print(json.dumps({"ok": False, "error": f"cannot read claude-code version at {claude_bin()}"}))
        return 2
    models = ladder_models(manifest)
    if args.cmd == "run":
        results = refresh(args.model or models, version, force=args.force)
        fact = record_version_fact(version)
        print(json.dumps({"cli": version, "fact_recorded": fact, "results": results}, indent=1))
        return 0 if all(r.get("ok") is not False for r in results.values()) else 1
    if args.cmd == "status":
        check = gate(manifest.models, version)
        print(json.dumps({"cli": version, "held": {m: check(m) for m in models if check(m)},
                          "clear": [m for m in models if not check(m)]}, indent=1))
        return 0
    latest = npm_latest()
    row = drift(models, version, latest, manifest.models)
    filed = bool(row) and not args.dry_run and file_aq(row)
    print(json.dumps({"installed": version, "latest": latest, "aq_row": row, "filed": filed}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
