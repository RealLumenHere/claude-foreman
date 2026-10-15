#!/usr/bin/env python3
"""wq#4018: rotate the OpenHands LiteLLM virtual key wq#4007 burned (edce1aefdbdd).

Privileged lane only: reads the LiteLLM master key and recreates a container, both
denied to BUILD workers by guard.sh. Never prints a key; every value is named by the
same body sha256[:12] mask_replay reports.

  1. confirm the compose's LLM_API_KEY is the burned value (by hash)
  2. mint a replacement mirroring the old key's alias/models/budget (/key/info)
  3. move the key out of the compose: LLM_API_KEY=${OPENHANDS_LLM_API_KEY}, value in
     a 0600 .env beside it (compose reads it for interpolation)
  4. docker compose up -d --force-recreate openhands
  5. new key 200, delete the old key, old key 401
  6. mask_replay.py record-rotated --sha256-12 edce1aefdbdd --ticket 4018

--dry-run stops after step 2's lookup and changes nothing.
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import urllib.error
import urllib.request

sys.path.insert(0, "/home/lumen/bin")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from lumen_env import env_value  # noqa: E402
from probe_key import BURNED_SHA12, COMPOSE, GATEWAY, compose_env, gateway_status, sha12  # noqa: E402

MASTER_ENV = "/home/lumen/.secrets/litellm-master.env"
DOTENV = os.path.join(os.path.dirname(COMPOSE), ".env")
BACKUP = COMPOSE + ".bak.wq4018"
ENV_VAR = "OPENHANDS_LLM_API_KEY"
MASK_REPLAY = "/home/lumen/foreman/harness/mask_replay.py"
MIRRORED = ("models", "max_budget", "budget_duration", "tpm_limit", "rpm_limit", "user_id", "team_id")


def call(path, master, payload=None):
    req = urllib.request.Request(
        f"{GATEWAY}{path}", data=json.dumps(payload).encode() if payload is not None else None,
        headers={"Authorization": f"Bearer {master}", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return resp.status, json.loads(resp.read() or b"{}")
    except urllib.error.HTTPError as e:
        return e.code, {}


def rewrite_compose(path=COMPOSE):
    """Swap the inline literal for an env reference; returns the line number touched."""
    with open(path) as fh:
        lines = fh.readlines()
    hits = [i for i, l in enumerate(lines) if re.match(r"\s*-?\s*LLM_API_KEY\s*[=:]", l)]
    if len(hits) != 1:
        raise SystemExit(f"expected one LLM_API_KEY line in {path}, found {len(hits)}")
    i = hits[0]
    indent = re.match(r"(\s*-?\s*)", lines[i]).group(1)
    lines[i] = f"{indent}LLM_API_KEY=${{{ENV_VAR}}}\n"
    backup = path + ".bak.wq4018"
    if os.path.exists(backup):
        raise SystemExit(f"{backup} exists: a prior run rewrote the compose; rerun to resume")
    shutil.copy2(path, backup)
    os.chmod(backup, 0o600)
    with open(path, "w") as fh:
        fh.writelines(lines)
    return i + 1


def write_dotenv(key, path=DOTENV):
    kept = []
    if os.path.exists(path):
        with open(path) as fh:
            kept = [l for l in fh if not l.startswith(f"{ENV_VAR}=")]
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as fh:
        fh.writelines(kept + [f"{ENV_VAR}={key}\n"])
    os.chmod(path, 0o600)


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    old = compose_env().get("LLM_API_KEY", "")
    resumed = sha12(old) != BURNED_SHA12 and os.path.exists(BACKUP)
    if resumed:
        # A prior run got past step 3: the compose is rewritten, the burned key is in
        # the backup and the replacement in .env. Resume at the recreate.
        old = compose_env(BACKUP).get("LLM_API_KEY", "")
    if sha12(old) != BURNED_SHA12:
        print(f"compose LLM_API_KEY is {sha12(old) if old else 'absent'}, not {BURNED_SHA12}: nothing to rotate")
        return 0
    master = env_value(MASTER_ENV, "LITELLM_MASTER_KEY")
    status, info = call(f"/key/info?key={old}", master)
    info = info.get("info", {})
    if status != 200:
        raise SystemExit(f"/key/info for the burned key returned {status}; is it already deleted?")
    alias = info.get("key_alias") or "openhands"
    print(f"burned key {BURNED_SHA12}: alias={alias} models={info.get('models')} "
          f"max_budget={info.get('max_budget')} resumed={resumed}")
    if args.dry_run:
        return 0

    if resumed:
        new = env_value(DOTENV, ENV_VAR)
    else:
        payload = {k: info[k] for k in MIRRORED if info.get(k) is not None}
        payload["key_alias"] = f"{alias}-wq4018"
        payload["metadata"] = {**(info.get("metadata") or {}), "rotated_from": alias, "ticket": "wq4018"}
        status, gen = call("/key/generate", master, payload)
        new = gen.get("key")
        if status != 200 or not new:
            raise SystemExit(f"/key/generate returned {status}; nothing changed")
        print(f"minted {sha12(new)} as {payload['key_alias']}")
        write_dotenv(new)
        line = rewrite_compose()
        print(f"compose line {line} -> LLM_API_KEY=${{{ENV_VAR}}}; value in {DOTENV} (0600)")
    rc = subprocess.run(["docker", "compose", "-f", COMPOSE, "up", "-d", "--force-recreate"],
                        cwd=os.path.dirname(COMPOSE)).returncode
    if rc:
        raise SystemExit(f"compose up rc={rc}; old key NOT deleted, fix and rerun from step 4")
    if gateway_status(new) != 200:
        raise SystemExit("new key not accepted by the gateway; old key NOT deleted")

    status, _ = call("/key/delete", master, {"keys": [old]})
    old_status = gateway_status(old)
    print(f"/key/delete {status}; burned key now -> {old_status}")
    if old_status not in (401, 403):
        raise SystemExit("burned key still accepted; not recording the rotation")
    return subprocess.run([sys.executable, MASK_REPLAY, "record-rotated", "--sha256-12", BURNED_SHA12,
                           "--ticket", "4018", "--note",
                           "OpenHands LiteLLM virtual key (containers/openhands compose LLM_API_KEY); "
                           "replacement in 0600 .env, old key deleted, 401 confirmed"]).returncode


if __name__ == "__main__":
    sys.exit(main())
