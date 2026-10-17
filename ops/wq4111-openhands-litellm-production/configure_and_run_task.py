#!/usr/bin/env python3
"""wq#4111: wire the OpenHands container to LiteLLM, select a model, and run one
headless test task end-to-end. Privileged lane only — every step here needs either
the docker socket or the LiteLLM master key, both denied to BUILD workers by
guard.sh/the sandbox (confirmed live: `docker ps` itself was denied outright, same
failure signature wq#4007 hit — see /home/lumen/foreman/_runs/wq4007/failure.json).
Run from a container-capable operator lane, same posture as
ops/wq4018-openhands-key-rotation/rotate.py.

Root cause this fixes (wq#4007 diagnosis, research/openhands-deployment.md): the
openhands container sits on the `openhands_default` compose network and points at
`host.docker.internal:4000`, but LiteLLM is published only on 127.0.0.1 + the tailnet
IP — host.docker.internal never resolves to something that reaches it from inside
that network. A throwaway container on `docker_default` reached LiteLLM fine over the
container-DNS name `litellm` (38 models, deepseek-v4-pro completion OK). openhands.db
had 0 conversations/0 sandboxes: no task had ever completed.

Steps:
  1. Inspect the live container's networks (docker inspect). If `docker_default` is
     already attached and LLM_BASE_URL already targets `http://litellm:4000/v1`,
     skip straight to step 5 (idempotent).
  2. `docker network connect docker_default openhands` (does NOT touch LiteLLM's own
     publish bindings — do not widen those, wq#4007 was explicit about this).
  3. Write LLM_BASE_URL/LLM_MODEL into the .env beside the compose (never inline in
     compose, never printed here) and recreate: `docker compose up -d --force-recreate`.
  4. Poll probe.py's checks (imported, not re-implemented) until both services are up.
  5. Verify the container's own network namespace can resolve+reach LiteLLM: `docker
     exec openhands curl -sf http://litellm:4000/health/readiness` must exit 0. This
     is the check wq#4007 never got to — host-side reachability (what probe.py checks)
     proves nothing about the container's namespace.
  6. Discover the installed headless entrypoint by running it with `--help` first
     (verify, don't guess the invocation for an unpinned image version) and run ONE
     bounded test task through it: capped max_iterations, confirmation mode off
     (the overnight-autonomous posture wq#4111 needs to verify), a wall-clock timeout.
  7. Confirm the task is recorded: conversation/sandbox row count in openhands.db goes
     from N to N+1 (checked via the bind mount if one exists, else `docker exec sqlite3`).

--dry-run stops after step 1's inspection and changes nothing.
"""
import argparse
import json
import os
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import probe  # noqa: E402

CONTAINER = "openhands"
COMPOSE_DIR = "/home/lumen/containers/openhands"
COMPOSE = os.path.join(COMPOSE_DIR, "docker-compose.yml")
DOTENV = os.path.join(COMPOSE_DIR, ".env")
TARGET_NETWORK = "docker_default"
TARGET_BASE_URL = "http://litellm:4000/v1"
# wq#4007 verified this alias answers completion requests through LiteLLM from a
# throwaway container on the same network; not a fresh guess.
TARGET_MODEL = "deepseek-v4-pro"
MAX_ITERATIONS = 15
TASK_TIMEOUT_S = 600
TEST_PROMPT = "Create a file named wq4111_probe.txt containing the text 'wq4111 ok' and nothing else."


def run(cmd, **kw):
    print(f"+ {' '.join(cmd)}")
    return subprocess.run(cmd, capture_output=True, text=True, **kw)


def docker_json(*args):
    r = run(["docker", *args])
    if r.returncode != 0:
        raise SystemExit(f"docker {' '.join(args)} failed: {r.stderr.strip()}")
    return json.loads(r.stdout)


def inspect_container():
    info = docker_json("inspect", CONTAINER)[0]
    networks = list(info["NetworkSettings"]["Networks"].keys())
    env = {kv.split("=", 1)[0]: kv.split("=", 1)[1] for kv in info["Config"]["Env"] if "=" in kv}
    return networks, env


def already_wired(networks, env):
    return TARGET_NETWORK in networks and env.get("LLM_BASE_URL") == TARGET_BASE_URL


def attach_network():
    r = run(["docker", "network", "connect", TARGET_NETWORK, CONTAINER])
    if r.returncode != 0 and "already exists" not in r.stderr:
        raise SystemExit(f"network connect failed: {r.stderr.strip()}")


def write_dotenv(updates, path=DOTENV):
    kept = []
    if os.path.exists(path):
        with open(path) as fh:
            kept = [l for l in fh if not any(l.startswith(f"{k}=") for k in updates)]
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as fh:
        fh.writelines(kept + [f"{k}={v}\n" for k, v in updates.items()])
    os.chmod(path, 0o600)


def recreate():
    r = run(["docker", "compose", "-f", COMPOSE, "up", "-d", "--force-recreate"], cwd=COMPOSE_DIR)
    if r.returncode != 0:
        raise SystemExit(f"compose up failed: {r.stderr.strip()}")


def wait_host_probes(attempts=12, sleep_s=5):
    for i in range(attempts):
        if probe.check_litellm_ready() and probe.check_openhands_up():
            return True
        time.sleep(sleep_s)
    return False


def container_can_reach_litellm():
    r = run(["docker", "exec", CONTAINER, "curl", "-sf", "-o", "/dev/null",
             "-w", "%{http_code}", "http://litellm:4000/health/readiness"])
    return r.returncode == 0 and r.stdout.strip() == "200"


def discover_headless_entrypoint():
    """Verify the CLI shape before relying on it (doctrine: never guess an
    invocation). Tries the two shapes OpenHands has shipped; returns the argv
    PREFIX (task text appended by the caller) or raises if neither matches."""
    candidates = [
        ["python", "-m", "openhands.core.main"],
        ["openhands", "run"],
    ]
    for prefix in candidates:
        r = run(["docker", "exec", CONTAINER, *prefix, "--help"])
        if r.returncode == 0 and ("-t" in r.stdout or "--task" in r.stdout):
            return prefix
    raise SystemExit(
        "neither `python -m openhands.core.main --help` nor `openhands run --help` "
        "exposed a task flag inside the container; the installed version's CLI has "
        "moved — read it fresh instead of trusting this candidate list")


def conversation_count():
    bind = os.path.join(COMPOSE_DIR, "openhands.db")
    if os.path.exists(bind):
        r = run(["sqlite3", bind, "select count(*) from conversation;"])
    else:
        r = run(["docker", "exec", CONTAINER, "sqlite3", "/app/openhands.db",
                 "select count(*) from conversation;"])
    if r.returncode != 0:
        raise SystemExit(f"conversation count query failed: {r.stderr.strip()}")
    return int(r.stdout.strip())


def run_test_task(entrypoint_prefix):
    before = conversation_count()
    argv = ["docker", "exec", CONTAINER, *entrypoint_prefix,
            "-t", TEST_PROMPT,
            "--max-iterations", str(MAX_ITERATIONS),
            "--no-auto-continue"]
    r = run(argv, timeout=TASK_TIMEOUT_S)
    after = conversation_count()
    ok = r.returncode == 0 and after == before + 1
    print(f"test task exit={r.returncode} conversations {before}->{after}")
    return ok


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    networks, env = inspect_container()
    print(f"networks={networks} LLM_BASE_URL={env.get('LLM_BASE_URL')!r} LLM_MODEL={env.get('LLM_MODEL')!r}")
    if already_wired(networks, env):
        print("already wired to docker_default/litellm; skipping to verification")
    elif args.dry_run:
        print("--dry-run: would attach docker_default, set LLM_BASE_URL/LLM_MODEL, recreate")
        return 0
    else:
        attach_network()
        write_dotenv({"LLM_BASE_URL": TARGET_BASE_URL, "LLM_MODEL": TARGET_MODEL})
        recreate()

    if args.dry_run:
        return 0
    if not wait_host_probes():
        raise SystemExit("host-side probes never went green after recreate")
    if not container_can_reach_litellm():
        raise SystemExit("container cannot reach litellm:4000 from its own network namespace")

    entrypoint = discover_headless_entrypoint()
    print(f"headless entrypoint verified: {' '.join(entrypoint)}")
    ok = run_test_task(entrypoint)
    if not ok:
        raise SystemExit("test task did not complete end-to-end; not production ready")
    print("wq#4111: one task completed end-to-end against the live OpenHands/LiteLLM pair")
    return 0


if __name__ == "__main__":
    sys.exit(main())
