#!/usr/bin/env python3
"""wq#2172 — is the deployed lumen-mailbot worker at or past the merged mailbot HEAD?

Read-only probe, no install step. Needs CLOUDFLARE_API_TOKEN in the environment, so it
cannot run from a build worker; run it from a shell that has the token:

    CLOUDFLARE_API_TOKEN=... python3 ops/wq2172-mailbot-liveness/check_live.py

Exit codes: 0 CURRENT, 1 STALE (run /home/lumen/mailbot/deploy.sh), 3 UNCONFIRMED
(no token, wrangler failed, or no deployments returned). Never prints the token.

The worker exposes no version endpoint, so "current" is judged by time: the newest
deployment must be at or after the last commit touching a deploy input (src/,
wrangler.toml, package*.json). If the newest deployment's message or tag carries that
commit's sha (`wrangler deploy --message <sha>`), the match is exact instead. Limit of
the time rule: a deploy cut from an older tree after the commit lands reads CURRENT.
"""
import argparse
import json
import os
import re
import subprocess
import sys
from datetime import datetime

DEPLOY_INPUTS = ("src", "wrangler.toml", "package.json", "package-lock.json")
SHA_RE = re.compile(r"\b[0-9a-f]{7,40}\b")


def parse_time(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def parse_deployments(out):
    """wrangler may print banner lines before the JSON array; take from the first '['."""
    start = next((i for i, ln in enumerate(out.splitlines(True)) if ln.lstrip().startswith("[")), None)
    if start is None:
        raise ValueError("no JSON array in wrangler output")
    text = "".join(out.splitlines(True)[start:])
    deployments, _ = json.JSONDecoder().raw_decode(text.lstrip())
    return deployments


def trigger(deployment):
    return (deployment.get("annotations") or {}).get("workers/triggered_by")


def newest(deployments):
    """Newest deployment that could have changed code. deploy.sh runs `wrangler secret put`
    before `wrangler deploy`, and a secret change cuts a deployment of the *previous* code, so
    a deploy that died between the two would otherwise read CURRENT."""
    code = [d for d in deployments if trigger(d) != "secret"]
    if not code:
        raise ValueError("only secret-change deployments returned, no code upload to judge")
    return max(code, key=lambda d: parse_time(d["created_on"]))


def stamps(deployment):
    ann = deployment.get("annotations") or {}
    return " ".join(str(ann.get(k, "")) for k in ("workers/message", "workers/tag"))


def last_deploy_commit(repo, rev):
    out = subprocess.run(
        ["git", "-C", repo, "log", "-1", "--format=%H %cI", rev, "--", *DEPLOY_INPUTS],
        capture_output=True, text=True, check=True,
    ).stdout.split()
    if not out:
        raise ValueError(f"no commit at {rev} touches {DEPLOY_INPUTS}")
    return out[0], parse_time(out[1])


def judge(deployment, sha, committed):
    """Return (verdict, reason)."""
    if trigger(deployment) == "rollback":
        raise ValueError("newest code deployment is a rollback; the live version is unknown")
    if any(sha.startswith(s) or s.startswith(sha) for s in SHA_RE.findall(stamps(deployment))):
        return "CURRENT", f"deployment stamp names {sha[:12]}"
    deployed = parse_time(deployment["created_on"])
    if deployed >= committed:
        return "CURRENT", f"deployed {deployed.isoformat()} >= commit {committed.isoformat()}"
    return "STALE", f"deployed {deployed.isoformat()} < commit {committed.isoformat()}"


def run_wrangler(repo, script):
    wrangler = os.path.join(repo, "node_modules", ".bin", "wrangler")
    proc = subprocess.run(
        [wrangler, "deployments", "list", "--name", script, "--json"],
        cwd=repo, capture_output=True, text=True, timeout=40,  # ops_verified kills --verify at 60s
    )
    if proc.returncode != 0:
        raise RuntimeError(f"wrangler exited {proc.returncode}: {proc.stderr.strip()[-300:]}")
    return proc.stdout


def main(argv=None, runner=run_wrangler):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--repo", default="/home/lumen/mailbot")
    ap.add_argument("--script", default="lumen-mailbot")
    ap.add_argument("--rev", default="HEAD", help="mailbot revision the worker should include")
    args = ap.parse_args(argv)

    if not os.environ.get("CLOUDFLARE_API_TOKEN"):
        print("UNCONFIRMED: CLOUDFLARE_API_TOKEN is not set; export it and rerun")
        return 3
    try:
        sha, committed = last_deploy_commit(args.repo, args.rev)
        deployments = parse_deployments(runner(args.repo, args.script))
        if not deployments:
            print(f"UNCONFIRMED: no deployments returned for {args.script}")
            return 3
        latest = newest(deployments)
        verdict, reason = judge(latest, sha, committed)
    except (ValueError, RuntimeError, KeyError, OSError, subprocess.SubprocessError) as e:
        print(f"UNCONFIRMED: {e}")
        return 3
    print(f"{verdict}: {args.script} vs {args.rev} (last deploy-input commit {sha[:12]}): {reason}")
    if verdict == "STALE":
        print(f"  fix: {args.repo}/deploy.sh   (runs the evals/run.sh gate first)")
    return 0 if verdict == "CURRENT" else 1


if __name__ == "__main__":
    sys.exit(main())
