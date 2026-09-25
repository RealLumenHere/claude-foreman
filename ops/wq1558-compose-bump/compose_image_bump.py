#!/usr/bin/env python3
"""compose_image_bump.py — deterministic applier for service_change plans
against compose-managed containers (wq#1558).

Gap this fills: minerva-factory drafted yellow service_change plans as an LLM
"inspect-and-recreate" `docker run` rebuilt from `docker inspect` (plan
741a2aeb6819 glance, a6fef713ac123 actual-budget). Both target services are
compose-managed (/home/lumen/glance, /home/lumen/actual-budget) with
digest-pinned images (wq#856). Applying a rebuilt `docker run` would (a) drift
the container out from under compose — the next `compose up` recreates the OLD
image, (b) drop the digest pin, (c) hit CAPABILITY_DENIED in an fworker anyway
(wq#1507 block note). The LLM draft also left the tag unvalidated (Actual
proposal said v26.9.0; the real tag is 26.9.0, pull failed).

This module replaces that draft path for compose-managed targets:
  1. resolve_compose_project() reads the running container's compose labels
     (com.docker.compose.project.working_dir / .service) instead of guessing
     a path.
  2. validate_tag_exists() checks the registry has the proposed tag BEFORE
     anything is staged — registry validation belongs in PLAN, not APPLY.
  3. bump() edits only the image line (backing up the compose file first),
     re-pins to the digest actually pulled, runs `compose up -d <service>`,
     health-probes, and reverts the compose file + redeploys the old image on
     any failure.

route_service_change_plan() is the seam minerva-factory's plan builder calls:
for a compose-managed target it returns a plan whose apply_cmd invokes this
module instead of an LLM-authored `docker run`.

Reference shell prototype: /home/lumen/uplift/compose-bump/bump.sh
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import time
import urllib.request

BACKUP_DIR = "/home/lumen/backups"


class ComposeBumpError(RuntimeError):
    pass


def _run(cmd: list[str], **kw) -> str:
    return subprocess.run(cmd, check=True, capture_output=True, text=True, **kw).stdout.strip()


def resolve_compose_project(container: str) -> tuple[str, str]:
    """Return (compose_working_dir, service_name) from the running container's
    own compose labels, instead of a guessed/hardcoded path."""
    try:
        raw = _run([
            "docker", "inspect", container,
            "--format", "{{index .Config.Labels \"com.docker.compose.project.working_dir\"}}"
                        "|{{index .Config.Labels \"com.docker.compose.service\"}}",
        ])
    except subprocess.CalledProcessError as e:
        raise ComposeBumpError(f"docker inspect failed for {container}: {e.stderr}") from e
    working_dir, _, service = raw.partition("|")
    if not working_dir or not service:
        raise ComposeBumpError(
            f"{container} is not compose-managed (missing compose labels): {raw!r}"
        )
    return working_dir, service


def is_compose_managed(container: str) -> bool:
    try:
        resolve_compose_project(container)
        return True
    except ComposeBumpError:
        return False


def validate_tag_exists(image_repo: str, tag: str, timeout: float = 10.0) -> bool:
    """Registry-side existence check for a proposed tag. Called from PLAN,
    before a service_change plan is staged — not from APPLY."""
    if "/" in image_repo and "." in image_repo.split("/", 1)[0]:
        registry, repo = image_repo.split("/", 1)
    else:
        registry, repo = "registry-1.docker.io", (
            image_repo if "/" in image_repo else f"library/{image_repo}"
        )
    url = f"https://{registry}/v2/{repo}/manifests/{tag}"
    req = urllib.request.Request(url, method="HEAD")
    req.add_header(
        "Accept",
        "application/vnd.docker.distribution.manifest.v2+json,"
        "application/vnd.oci.image.manifest.v1+json",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status == 200
    except urllib.error.HTTPError as e:
        if e.code == 401:
            # anonymous token flow (Docker Hub) — fall back to `docker manifest inspect`
            try:
                _run(["docker", "manifest", "inspect", f"{image_repo}:{tag}"])
                return True
            except subprocess.CalledProcessError:
                return False
        return False
    except urllib.error.URLError:
        return False


def bump(compose_dir: str, service: str, new_image_ref: str, health_url: str,
          data_dir: str | None = None) -> dict:
    """Deterministic apply: edit the pinned image line, `compose up -d`,
    health probe, revert compose file + redeploy old image on failure."""
    repo, _, tag = new_image_ref.rpartition(":")
    if not validate_tag_exists(repo, tag):
        raise ComposeBumpError(f"tag validation failed for {new_image_ref} — refusing to apply")

    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    compose_file = os.path.join(compose_dir, "docker-compose.yml")
    backup_file = f"{compose_file}.bak.{stamp}"

    if data_dir:
        os.makedirs(BACKUP_DIR, exist_ok=True)
        tar_path = os.path.join(BACKUP_DIR, f"{service}-data-pre-{tag}-{stamp}.tgz")
        with tarfile.open(tar_path, "w:gz") as tf:
            tf.add(data_dir, arcname=os.path.basename(data_dir))

    with open(compose_file) as f:
        original = f.read()
    shutil.copy2(compose_file, backup_file)

    old_match = re.search(
        rf"^(\s*services:.*?{re.escape(service)}:.*?image:\s*)(\S+)",
        original, re.DOTALL | re.MULTILINE,
    )
    if not old_match:
        raise ComposeBumpError(f"could not find image: line for service {service!r}")
    old_image = old_match.group(2)

    _run(["docker", "pull", "-q", new_image_ref])
    digest = _run([
        "docker", "inspect", "--format", "{{index .RepoDigests 0}}", new_image_ref,
    ]).split("@", 1)[-1]
    new_pinned = f"{new_image_ref}@{digest}"

    assert original.count(f"image: {old_image}") == 1, "ambiguous image: line, refusing edit"
    edited = original.replace(
        f"image: {old_image}",
        f"image: {new_pinned}  # bumped {stamp[:8]} by compose_image_bump (wq#1558), was {old_image}",
    )
    with open(compose_file, "w") as f:
        f.write(edited)

    def revert():
        shutil.copy2(backup_file, compose_file)
        subprocess.run(["docker", "compose", "up", "-d", service], cwd=compose_dir,
                        capture_output=True, text=True)

    try:
        subprocess.run(["docker", "compose", "up", "-d", service], cwd=compose_dir,
                        check=True, capture_output=True, text=True)
    except subprocess.CalledProcessError as e:
        revert()
        raise ComposeBumpError(f"compose up failed, reverted: {e.stderr}") from e

    ok, code = False, None
    for _ in range(12):
        time.sleep(5)
        try:
            with urllib.request.urlopen(health_url, timeout=8) as resp:
                code = resp.status
        except urllib.error.HTTPError as e:
            code = e.code
        except Exception:
            code = None
        if code in (200, 302, 401):
            ok = True
            break
    if not ok:
        revert()
        raise ComposeBumpError(f"health probe failed (last code={code}), reverted")

    return {
        "service": service, "old_image": old_image, "new_image": new_pinned,
        "health_code": code, "backup": backup_file,
    }


def route_service_change_plan(plan: dict) -> dict:
    """Seam for minerva-factory's plan builder: if the plan's target container
    is compose-managed, replace the LLM-drafted `docker run` apply_cmd with an
    invocation of this deterministic applier. Non-compose targets pass through
    unchanged (still the LLM-draft path this ticket does not touch)."""
    container = plan.get("target_container")
    if not container or not is_compose_managed(container):
        return plan
    working_dir, service = resolve_compose_project(container)
    new_ref = plan["proposed_image_ref"]
    plan = dict(plan)
    plan["applier"] = "compose_image_bump"
    plan["apply_cmd"] = [
        sys.executable, os.path.abspath(__file__),
        "--compose-dir", working_dir,
        "--service", service,
        "--new-image", new_ref,
        "--health-url", plan.get("health_url", ""),
    ]
    return plan


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--compose-dir", required=True)
    ap.add_argument("--service", required=True)
    ap.add_argument("--new-image", required=True)
    ap.add_argument("--health-url", required=True)
    ap.add_argument("--data-dir")
    args = ap.parse_args()
    try:
        result = bump(args.compose_dir, args.service, args.new_image, args.health_url, args.data_dir)
    except ComposeBumpError as e:
        print(json.dumps({"ok": False, "error": str(e)}))
        return 1
    print(json.dumps({"ok": True, **result}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
