#!/usr/bin/env python3
"""wq#949 — who reads each LiteLLM service key, and what a rotation therefore costs.

`--rotate <alias>` deletes the old gateway key the moment it writes the replacement, so
anything holding that key in memory starts getting 401s until it is restarted. This maps
each alias to its consumers and splits them by how they pick the new key up:

  restart   an active unit with EnvironmentFile= — the key is in its process env
  next-run  an inactive/timer-driven unit — systemd re-reads EnvironmentFile= on start
  runtime   a script that opens the key file per invocation — nothing to do
  remote    a consumer on another host — the new key has to be pushed there first

    python3 consumer_map.py               # every alias
    python3 consumer_map.py lumen-agent
    python3 consumer_map.py --json

Derived from the live host (drop-ins under /etc/systemd/system, `systemctl is-active`,
and a grep for the secrets dir) so it cannot drift from the runbook beside it.
"""
import argparse, json, pathlib, re, subprocess, sys

sys.path.insert(0, str(pathlib.Path(__file__).parent))
import smoke_alias

SECRETS_DIR = pathlib.Path("/home/lumen/.secrets/litellm-service-keys")
UNIT_DIR = pathlib.Path("/etc/systemd/system")
CODE_ROOTS = ["/home/lumen/pokemon", "/home/lumen/services", "/home/lumen/research",
              "/home/lumen/minerva", "/home/lumen/sentinel", "/home/lumen/duet"]
# Consumers systemd and the local filesystem cannot see. wq#903 put sia-news on host sia;
# its container reads the key from a .env there, so a local rotation is only half of it.
REMOTE = {"sia-news": ["sia:/home/sia/sia-news/.env (sia-news container)"]}

ENVFILE = re.compile(r"EnvironmentFile=.*/litellm-service-keys/env/([a-z0-9-]+)\.env")


def systemd_consumers():
    out = {}
    for conf in UNIT_DIR.glob("*.service.d/*.conf"):
        m = ENVFILE.search(conf.read_text())
        if not m:
            continue
        unit = conf.parent.name[: -len(".service.d")]
        active = subprocess.run(
            ["systemctl", "is-active", unit], capture_output=True, text=True
        ).stdout.strip() == "active"
        out.setdefault(m.group(1), []).append((unit, "restart" if active else "next-run"))
    return out


def runtime_consumers():
    """Scripts that read the key file per invocation — a rotation needs nothing from them."""
    roots = [r for r in CODE_ROOTS if pathlib.Path(r).is_dir()]
    hits = subprocess.run(
        ["grep", "-rlI", "litellm-service-keys", *roots], capture_output=True, text=True
    ).stdout.split()
    out = {}
    for path in hits:
        text = pathlib.Path(path).read_text(errors="replace")
        for alias in re.findall(r"litellm-service-keys/(?:env/)?([a-z0-9-]+)\.(?:key|env)",
                                text):
            out.setdefault(alias, []).append(path)
    return {k: sorted(set(v)) for k, v in out.items()}


def build():
    sysd, runtime = systemd_consumers(), runtime_consumers()
    # Seed from SERVICES so an alias with no consumer at all still shows up — that is the
    # useful answer for ops-emergency (manual) and lumen-dispatch (disarmed): rotate freely.
    aliases = sorted(set(smoke_alias.services()) | set(sysd) | set(runtime) | set(REMOTE))
    m = {}
    for alias in aliases:
        units = sorted(sysd.get(alias, []))
        m[alias] = {
            "restart": [u for u, k in units if k == "restart"],
            "next_run": [u for u, k in units if k == "next-run"],
            "runtime": runtime.get(alias, []),
            "remote": REMOTE.get(alias, []),
        }
    return m


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("aliases", nargs="*")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    m = build()
    if a.aliases:
        m = {k: v for k, v in m.items() if k in a.aliases}
    if a.json:
        print(json.dumps(m, indent=2))
        return 0
    for alias, c in m.items():
        print(f"{alias}")
        for kind in ("restart", "next_run", "runtime", "remote"):
            for item in c[kind]:
                print(f"  {kind:<9} {item}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
