#!/usr/bin/env python3
"""wq#1968 — rotate LiteLLM virtual keys leaked to Bes logs (wq#1747 finding).

Orchestrator-run script, not a self-service tool: this worker's sandbox cannot
read /home/lumen/.secrets (workers cannot read secrets — see CLAUDE.md), so
this only ever runs by hand from a session with real filesystem/proxy access.

For each alias this does, in order:
  1. Look up the alias's current key record on the LiteLLM proxy (/key/list)
     to get its sha256 token fingerprint, allowed models, and budget.
  2. Find the raw OLD value on disk: scan the search roots for `sk-...`-shaped
     substrings and keep the one whose sha256 matches the fingerprint from
     step 1. This is the same identify-by-hash trick as
     ops/wq1647-lpp-cerebras-retire/retire_lpp_cerebras.py, run in reverse
     (alias -> hash -> raw value instead of raw value -> hash -> record).
  3. POST /key/{old_value}/regenerate (master-key auth) to get a new raw
     value. LiteLLM's regenerate keeps alias/models/budget unless overridden,
     so nothing else needs to change proxy-side.
  4. grep every search root for literal occurrences of the OLD value
     (excluding noise dirs) and rewrite each file in place, preserving mode.
  5. Map each rewritten file to the systemd unit(s) that source it via
     EnvironmentFile= and restart them (detached systemd-run), skipping
     anything on the never-restart list.
  6. Verify the new value with one cheap /chat/completions call.
  7. Record a completion marker (alias -> new token sha256) so a re-run with
     nothing changed is a no-op (idempotent).

Nothing secret ever touches argv, an f-string headed for print(), or a log
line — only masked 6-char prefixes and file paths are ever printed.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

DEFAULT_ALIASES = [
    "langfuse-evaluator-2", "brain-healthcheck-2", "lpp-cerebras",
    "lumen-agent-2", "duet-2", "ops-emergency-3", "sentinel-2",
    "minerva-pipeline-2", "lumen-reflector-2", "lumen-messenger-2",
    "minerva-bench-2", "lumen-research-2", "sia-news-2",
    "lumen-dispatch-2", "lpp-2",
]

DEFAULT_BASE_URL = "http://localhost:4000"
DEFAULT_EXCLUDE_NAMES = {"_runs", ".git", "node_modules", ".venv"}
DEFAULT_VERIFY_MODEL = "gpt-4o-mini"
KEY_PATTERN = re.compile(r"sk-[A-Za-z0-9_\-]{8,}")
MAX_SCAN_BYTES = 5 * 1024 * 1024


def default_search_roots() -> list[Path]:
    home = Path.home()
    return [home, Path("/etc/systemd/system"), home / ".config" / "systemd"]


def mask(value: str) -> str:
    return f"{value[:6]}…" if value else "(empty)"


# --------------------------------------------------------------------------
# LiteLLM proxy HTTP calls (urllib only — never argv, never a URL for POST
# bodies, so a `ps` snapshot never sees a raw key. The regenerate endpoint's
# own contract puts the OLD value in the URL path; that part is unavoidable
# and is why this stays in-process urllib rather than a curl subprocess).
# --------------------------------------------------------------------------

def _request(url: str, master_key: str, method: str = "GET", body: dict | None = None) -> dict:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={
            "Authorization": f"Bearer {master_key}",
            "Content-Type": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read() or b"{}")


def key_list_lookup(base_url: str, master_key: str, alias: str) -> dict | None:
    page, pages = 1, 1
    while page <= pages:
        qs = urllib.parse.urlencode({
            "key_alias": alias,
            "return_full_object": "true",
            "size": 100,
            "page": page,
        })
        data = _request(f"{base_url}/key/list?{qs}", master_key)
        for rec in data.get("keys") or []:
            if isinstance(rec, dict) and rec.get("key_alias") == alias:
                return rec
        pages = int(data.get("total_pages") or 1)
        page += 1
    return None


def regenerate_key(base_url: str, master_key: str, old_value: str) -> dict:
    path = urllib.parse.quote(old_value, safe="")
    data = _request(f"{base_url}/key/{path}/regenerate", master_key, method="POST", body={})
    if not data.get("key"):
        raise RuntimeError("regenerate response had no 'key' field")
    return data


def verify_key(base_url: str, raw_key: str, model: str) -> tuple[bool, str]:
    body = json.dumps({
        "model": model,
        "messages": [{"role": "user", "content": "ping"}],
        "max_tokens": 1,
    }).encode()
    req = urllib.request.Request(
        f"{base_url}/chat/completions",
        data=body,
        headers={"Authorization": f"Bearer {raw_key}", "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status == 200, f"http {resp.status}"
    except urllib.error.HTTPError as e:
        detail = e.read()[:200].decode(errors="replace")
        return False, f"http {e.code} {detail}"
    except urllib.error.URLError as e:
        return False, f"unreachable: {e.reason}"


# --------------------------------------------------------------------------
# Filesystem scanning
# --------------------------------------------------------------------------

def _walk_files(roots: list[Path], excludes: set[str]):
    for root in roots:
        if not root.exists():
            continue
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if d not in excludes]
            for name in filenames:
                p = Path(dirpath) / name
                if p.is_symlink():
                    continue
                yield p


def _read_bounded(path: Path) -> bytes | None:
    try:
        if path.stat().st_size > MAX_SCAN_BYTES:
            return None
        return path.read_bytes()
    except (OSError, PermissionError):
        return None


def find_value_by_hash(target_sha256: str, roots: list[Path], excludes: set[str]) -> str | None:
    seen: set[str] = set()
    for path in _walk_files(roots, excludes):
        raw = _read_bounded(path)
        if raw is None:
            continue
        text = raw.decode("utf-8", errors="ignore")
        for candidate in KEY_PATTERN.findall(text):
            if candidate in seen:
                continue
            seen.add(candidate)
            if hashlib.sha256(candidate.encode()).hexdigest() == target_sha256:
                return candidate
    return None


def find_files_with_value(value: str, roots: list[Path], excludes: set[str]) -> list[Path]:
    hits = []
    needle = value.encode()
    for path in _walk_files(roots, excludes):
        raw = _read_bounded(path)
        if raw is None:
            continue
        if needle in raw:
            hits.append(path)
    return sorted(hits)


def rewrite_file_inplace(path: Path, old: str, new: str) -> None:
    mode = stat.S_IMODE(path.stat().st_mode)
    raw = path.read_bytes()
    raw = raw.replace(old.encode(), new.encode())
    path.write_bytes(raw)
    os.chmod(path, mode)


# --------------------------------------------------------------------------
# systemd unit mapping / restart
# --------------------------------------------------------------------------

ENV_FILE_RE = re.compile(r"^EnvironmentFile=-?(?P<path>\S+)\s*$", re.MULTILINE)


def map_file_to_units(target: Path, unit_dirs: list[Path]) -> list[str]:
    units: set[str] = set()
    target_resolved = str(target.resolve())
    for unit_dir in unit_dirs:
        if not unit_dir.exists():
            continue
        for path in unit_dir.rglob("*"):
            if path.is_dir() or path.suffix not in (".service", ".conf"):
                continue
            try:
                text = path.read_text(errors="ignore")
            except (OSError, PermissionError):
                continue
            for m in ENV_FILE_RE.finditer(text):
                ref = Path(os.path.expandvars(m.group("path")))
                if str(ref) == str(target) or str(ref.resolve() if ref.exists() else ref) == target_resolved:
                    unit_name = path.name
                    if path.suffix == ".conf":
                        # drop-in: <unit>.service.d/override.conf -> <unit>.service
                        unit_name = path.parent.parent.name if path.parent.name.endswith(".d") else path.parent.name
                    units.add(unit_name)
    return sorted(units)


def load_never_restart(path: Path | None) -> set[str]:
    if path is None or not path.exists():
        return set()
    out = set()
    for line in path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            out.add(line)
    return out


def restart_unit(unit: str, user_unit: bool, run_id: str, dry_run: bool) -> None:
    if dry_run:
        return
    label = f"rotate-{run_id}-{re.sub(r'[^A-Za-z0-9]', '-', unit)}"[:63]
    if user_unit:
        cmd = ["systemd-run", "--user", "--collect", f"--unit={label}", "systemctl", "--user", "restart", unit]
    else:
        cmd = ["systemd-run", "--collect", f"--unit={label}", "systemctl", "restart", unit]
    subprocess.run(cmd, check=True, capture_output=True)


# --------------------------------------------------------------------------
# State (idempotency)
# --------------------------------------------------------------------------

def state_path(state_dir: Path, alias: str) -> Path:
    return state_dir / f"{alias}.json"


def already_rotated(state_dir: Path, alias: str, current_token_sha: str) -> bool:
    p = state_path(state_dir, alias)
    if not p.exists():
        return False
    try:
        recorded = json.loads(p.read_text()).get("active_token_sha")
    except (OSError, json.JSONDecodeError):
        return False
    return recorded == current_token_sha


def record_state(state_dir: Path, alias: str, new_token_sha: str) -> None:
    state_dir.mkdir(parents=True, exist_ok=True)
    p = state_path(state_dir, alias)
    p.write_text(json.dumps({
        "alias": alias,
        "active_token_sha": new_token_sha,
        "rotated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }))
    os.chmod(p, 0o600)


# --------------------------------------------------------------------------
# Per-alias driver
# --------------------------------------------------------------------------

def rotate_one(alias: str, args, master_key: str) -> dict:
    result = {"alias": alias}
    record = key_list_lookup(args.base_url, master_key, alias)
    if record is None:
        result["status"] = "alias_not_found"
        return result

    token_sha = record.get("token")
    models = record.get("models") or []

    if already_rotated(args.state_dir, alias, token_sha):
        result["status"] = "already_rotated"
        return result

    old_value = find_value_by_hash(token_sha, args.search_roots, args.exclude)
    if old_value is None:
        result["status"] = "old_value_not_found_on_disk"
        return result

    files = find_files_with_value(old_value, args.search_roots, args.exclude)

    if args.dry_run:
        result["status"] = "dry_run"
        result["files_would_rewrite"] = [str(f) for f in files]
        units: set[str] = set()
        for f in files:
            units.update(map_file_to_units(f, args.unit_dirs))
        result["units_would_restart"] = sorted(units)
        return result

    new_data = regenerate_key(args.base_url, master_key, old_value)
    new_value = new_data["key"]
    new_token_sha = hashlib.sha256(new_value.encode()).hexdigest()

    for f in files:
        rewrite_file_inplace(f, old_value, new_value)

    never_restart = load_never_restart(args.never_restart_file)
    units: set[str] = set()
    for f in files:
        units.update(map_file_to_units(f, args.unit_dirs))

    restarted, skipped = [], []
    run_id = time.strftime("%H%M%S")
    for unit in sorted(units):
        if unit in never_restart:
            skipped.append(unit)
            continue
        is_user = any(str(f).startswith(str(Path.home() / ".config" / "systemd")) for f in files)
        restart_unit(unit, is_user, run_id, args.dry_run)
        restarted.append(unit)

    model = args.verify_model or (models[0] if models else DEFAULT_VERIFY_MODEL)
    verify_ok, verify_note = verify_key(args.base_url, new_value, model)

    record_state(args.state_dir, alias, new_token_sha)

    result.update({
        "status": "rotated",
        "prefix6": mask(new_value),
        "files_rewritten": [str(f) for f in files],
        "units_restarted": restarted,
        "units_skipped_never_restart": skipped,
        "verify_ok": verify_ok,
        "verify_note": verify_note,
    })
    return result


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--alias", action="append", dest="aliases", help="rotate only this alias (repeatable)")
    p.add_argument("--aliases-file", type=Path, help="file with one alias per line")
    p.add_argument("--base-url", default=os.environ.get("LITELLM_BASE_URL", DEFAULT_BASE_URL))
    p.add_argument("--search-root", action="append", dest="search_roots", type=Path,
                    help="root to scan for old/new values (repeatable; default: home, /etc/systemd/system, ~/.config/systemd)")
    p.add_argument("--exclude", action="append", dest="exclude",
                    help="directory name to prune from scans (repeatable)")
    p.add_argument("--unit-dir", action="append", dest="unit_dirs", type=Path,
                    help="systemd unit dir to search for EnvironmentFile= mappings (repeatable)")
    p.add_argument("--state-dir", type=Path,
                    default=Path.home() / ".secrets" / "litellm-service-keys" / ".rotation-state",
                    help="where idempotency markers are written")
    p.add_argument("--never-restart-file", type=Path,
                    default=Path(__file__).parent / "never-restart-units.txt")
    p.add_argument("--verify-model", help="model to use for the post-rotation verify call")
    p.add_argument("--dry-run", action="store_true")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    args.search_roots = args.search_roots or default_search_roots()
    args.exclude = set(args.exclude) if args.exclude else set(DEFAULT_EXCLUDE_NAMES)
    args.unit_dirs = args.unit_dirs or [Path("/etc/systemd/system"), Path.home() / ".config" / "systemd" / "user"]

    master_key = os.environ.get("LITELLM_MASTER_KEY")
    if not master_key:
        print("LITELLM_MASTER_KEY not set in env — refusing to run", file=sys.stderr)
        return 2

    if args.aliases:
        aliases = args.aliases
    elif args.aliases_file:
        aliases = [l.strip() for l in args.aliases_file.read_text().splitlines() if l.strip() and not l.startswith("#")]
    else:
        aliases = DEFAULT_ALIASES

    exit_code = 0
    for alias in aliases:
        try:
            result = rotate_one(alias, args, master_key)
        except (urllib.error.HTTPError, urllib.error.URLError, RuntimeError, subprocess.CalledProcessError) as e:
            print(f"{alias}: ERROR {e}", file=sys.stderr)
            exit_code = 1
            continue

        status = result["status"]
        if status == "rotated":
            ok = result["verify_ok"]
            if not ok:
                exit_code = 1
            print(
                f"{alias}: prefix={result['prefix6']} "
                f"files={len(result['files_rewritten'])} "
                f"units_restarted={result['units_restarted']} "
                f"units_skipped={result['units_skipped_never_restart']} "
                f"verify={'ok' if ok else 'FAILED (' + result['verify_note'] + ')'}"
            )
        elif status == "dry_run":
            print(
                f"{alias}: DRY-RUN would rewrite {len(result['files_would_rewrite'])} file(s), "
                f"would restart {result['units_would_restart']}"
            )
        elif status == "already_rotated":
            print(f"{alias}: already rotated, nothing to do")
        elif status == "alias_not_found":
            print(f"{alias}: no matching key_alias on proxy — skipped", file=sys.stderr)
            exit_code = 1
        elif status == "old_value_not_found_on_disk":
            print(f"{alias}: current key not found on disk under search roots — skipped, needs manual handling", file=sys.stderr)
            exit_code = 1
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
