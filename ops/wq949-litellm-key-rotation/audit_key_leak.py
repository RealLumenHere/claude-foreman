#!/usr/bin/env python3
"""wq#949 — assert the litellm container log holds no cleartext service key.

wq#948 stopped `litellm-budget-alert` writing `GET /key/info?key=sk-...` URLs, but the
uvicorn access lines it already produced stay in the container's json log until that log
is truncated or the container is recreated. This is the acceptance check for the purge:

    python3 audit_key_leak.py           # exit 0 clean, 1 leaking
    python3 audit_key_leak.py --json
    python3 audit_key_leak.py --container litellm

Never prints a key. Matches are reported as a count, a distinct-key count derived from a
salt-free digest, and the line numbers of each cluster, so the output is safe to paste
into a ticket.
"""
import argparse, hashlib, json, re, subprocess, sys

# ?key=sk-... or &api_key=sk-... in a request line.
LEAK = re.compile(rb"[?&](?:key|api_key)=(sk-[A-Za-z0-9_-]+)")


def container_log(name):
    """Both streams: uvicorn access lines land on stdout, tracebacks on stderr."""
    p = subprocess.run(
        ["docker", "logs", name],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=120,
    )
    if p.returncode != 0:
        raise SystemExit(f"docker logs {name} failed: {p.stderr.decode()[:300]}")
    return p.stdout + p.stderr


def clusters(lines, gap=20):
    """Group leaking line numbers into sweeps — one budget sweep prints every key at once."""
    out = []
    for n in lines:
        if out and n - out[-1][1] <= gap:
            out[-1][1] = n
        else:
            out.append([n, n])
    return [f"{a}-{b}" if a != b else str(a) for a, b in out]


def audit(name):
    log = container_log(name)
    hits, keys = [], set()
    for i, line in enumerate(log.splitlines(), 1):
        found = LEAK.findall(line)
        if found:
            hits.append(i)
            keys.update(hashlib.sha256(k).hexdigest()[:12] for k in found)
    return {
        "ok": not hits,
        "container": name,
        "occurrences": len(hits),
        "distinct_keys": len(keys),
        "clusters": clusters(hits),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--container", default="litellm")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    r = audit(a.container)
    if a.json:
        print(json.dumps(r, indent=2))
    elif r["ok"]:
        print(f"OK: no cleartext key in `docker logs {r['container']}`")
    else:
        print(
            f"LEAKING: {r['occurrences']} request lines in `docker logs {r['container']}` "
            f"expose {r['distinct_keys']} distinct keys (lines {', '.join(r['clusters'])})"
        )
    return 0 if r["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
