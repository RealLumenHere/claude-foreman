#!/usr/bin/env python3
"""wq#3678 — rotate the three LibreChat secrets inlined in its compose file.

wq#3090 and wq#3665 each Read /home/lumen/docker/docker-compose.yml whole and
burned the Mongo root password (also inside the mongodb:// URI, 6bc6f59051e9),
JWT_SECRET (19dc5bec2297) and JWT_REFRESH_SECRET (7d555026ff55). This rotates
all three AND closes the leak path: the compose file ends up holding only
`${LIBRECHAT_*}` references, the values live in the compose dir's `.env` (0600,
which compose auto-loads and guard.sh already denies as `*.env`).

Needs docker, so it runs from a privileged session, not a fworker BUILD worker
(ISSUE-LOG 797/1028). Dry-run by default; `--apply` to act. Never prints a
value — only key names, sha256[:12] fingerprints and paths. Secrets reach
mongosh on stdin, never argv.
"""
import argparse
import hashlib
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
HARNESS = os.path.normpath(os.path.join(HERE, "..", "..", "harness"))

COMPOSE = "/home/lumen/docker/docker-compose.yml"
TICKET = "3678"

# compose key -> (.env variable, burned sha256[:12] from mask_replay 3665)
KEYS = {
    "MONGO_INITDB_ROOT_PASSWORD": ("LIBRECHAT_MONGO_PASSWORD", "6bc6f59051e9"),
    "JWT_SECRET": ("LIBRECHAT_JWT_SECRET", "19dc5bec2297"),
    "JWT_REFRESH_SECRET": ("LIBRECHAT_JWT_REFRESH_SECRET", "7d555026ff55"),
}
URI_RE = re.compile(r"(?P<lead>mongodb(?:\+srv)?://(?P<user>[^:/@\s]+):)"
                    r"(?P<val>[^@\s\"']+)(?P<trail>@(?P<host>[^:/\s\"']+))")


class RotateError(Exception):
    pass


def sha12(value):
    return hashlib.sha256(value.encode()).hexdigest()[:12]


def key_re(key):
    # map form `KEY: v` and list form `- KEY=v`, optionally quoted, optional comment
    return re.compile(r"^(?P<lead>[ \t]*(?:-[ \t]*)?" + re.escape(key) + r"[ \t]*[:=][ \t]*)"
                      r"(?P<q>[\"']?)(?P<val>[^\"'\s#]+)(?P=q)", re.M)


def is_ref(value):
    return value.startswith("${")


def scan(text):
    """{compose key -> set of current values}, plus the URI's user, host and passwords."""
    found = {key: {m.group("val") for m in key_re(key).finditer(text)} for key in KEYS}
    uris = list(URI_RE.finditer(text))
    found["_uri_pw"] = {m.group("val") for m in uris}
    found["_uri_user"] = {m.group("user") for m in uris}
    found["_uri_host"] = {m.group("host") for m in uris}
    return found


def plan(text):
    """Validate the compose text and return the old values keyed by compose key.

    Returns None when every reference is already `${...}` (nothing to rotate).
    """
    found = scan(text)
    for key in KEYS:
        if len(found[key]) != 1:
            raise RotateError(f"{key}: expected exactly one value, found {len(found[key])}")
    if not found["_uri_pw"]:
        raise RotateError("no mongodb:// URI with a password")
    if len(found["_uri_user"]) != 1 or len(found["_uri_host"]) != 1:
        raise RotateError("mongodb:// URIs disagree on user or host")
    olds = {key: next(iter(found[key])) for key in KEYS}
    refs = [is_ref(v) for v in list(olds.values()) + list(found["_uri_pw"])]
    if all(refs):
        return None
    if any(refs):
        raise RotateError("half-migrated compose: some values are ${...} refs, some literal")
    if found["_uri_pw"] != {olds["MONGO_INITDB_ROOT_PASSWORD"]}:
        raise RotateError("mongodb:// URI password differs from MONGO_INITDB_ROOT_PASSWORD")
    return {"olds": olds, "user": next(iter(found["_uri_user"])),
            "mongo_service": next(iter(found["_uri_host"]))}


def rewrite(text):
    """The compose text with every rotated literal replaced by its `${VAR}` ref."""
    for key, (var, _) in KEYS.items():
        text = key_re(key).sub(lambda m, v=var: m.group("lead") + m.group("q") + "${" + v + "}"
                               + m.group("q"), text)
    var = KEYS["MONGO_INITDB_ROOT_PASSWORD"][0]
    return URI_RE.sub(lambda m: m.group("lead") + "${" + var + "}" + m.group("trail"), text)


def merge_env(existing, values):
    """`.env` text with `values` set, every unrelated line kept as it was."""
    out, seen = [], set()
    for line in existing.splitlines():
        name = line.split("=", 1)[0].strip()
        if name in values:
            if name not in seen:
                out.append(f"{name}={values[name]}")
            seen.add(name)
        else:
            out.append(line)
    out += [f"{name}={value}" for name, value in values.items() if name not in seen]
    return "\n".join(out) + "\n"


def write_private(path, text, mode=0o600):
    """Atomic write; the temp file is chmod'd before any content lands in it."""
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), prefix=".wq3678.")
    try:
        os.fchmod(fd, mode)
        with os.fdopen(fd, "w") as fh:
            fh.write(text)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def mongo_script(user, auth_pw, new_pw=None):
    # JSON literals are valid JS, so quoting is never hand-rolled
    lines = [f"const u = {json.dumps(user)};",
             "const a = db.getSiblingDB('admin');",
             f"try {{ a.auth(u, {json.dumps(auth_pw)}); }} catch (e) {{ print('AUTH_FAIL'); quit(3); }}"]
    if new_pw is not None:
        lines.append(f"a.changeUserPassword(u, {json.dumps(new_pw)});")
    lines.append("print('AUTH_OK');")
    return "\n".join(lines) + "\n"


def run(cmd, stdin=None):
    return subprocess.run(cmd, input=stdin, capture_output=True, text=True)


def mongosh(runner, cid, script):
    res = runner(["docker", "exec", "-i", cid, "mongosh", "--quiet", "--norc"], script)
    return res.returncode == 0 and "AUTH_OK" in res.stdout


def rotate(compose, apply, runner=run, ledger_path=None, gen=secrets.token_hex, expected=None):
    expected = expected or {key: h for key, (_, h) in KEYS.items()}
    with open(compose) as fh:
        text = fh.read()
    p = plan(text)
    if p is None:
        return {"status": "already_migrated", "compose": compose}
    olds = p["olds"]
    burned = {key: sha12(v) for key, v in olds.items()}
    mismatch = {k: h for k, h in burned.items() if h != expected[k]}
    if mismatch:
        raise RotateError(f"live values are not the burned ones (already rotated by hand?): {mismatch}")
    report = {"status": "dry_run", "compose": compose, "mongo_service": p["mongo_service"],
              "burned": burned}
    if not apply:
        return report

    news = {KEYS[key][0]: gen(32) for key in KEYS}
    new_pw = news[KEYS["MONGO_INITDB_ROOT_PASSWORD"][0]]
    old_pw = olds["MONGO_INITDB_ROOT_PASSWORD"]
    dc = ["docker", "compose", "-f", compose]

    cid = runner(dc + ["ps", "-q", p["mongo_service"]]).stdout.strip()
    if not cid:
        raise RotateError(f"no running container for service {p['mongo_service']}")

    env_path = os.path.join(os.path.dirname(compose), ".env")
    backup = compose + ".pre-wq3678"
    existing = open(env_path).read() if os.path.exists(env_path) else ""
    compose_mode = os.stat(compose).st_mode & 0o777
    shutil.copy2(compose, backup)
    os.chmod(backup, 0o600)

    def restore(why):
        shutil.copy2(backup, compose)
        os.chmod(compose, compose_mode)
        raise RotateError(why + "; compose restored, Mongo untouched")

    write_private(env_path, merge_env(existing, news))
    write_private(compose, rewrite(text), compose_mode)
    if runner(dc + ["config", "-q"]).returncode != 0:
        restore("`docker compose config -q` rejected the rewritten file")
    if not mongosh(runner, cid, mongo_script(p["user"], old_pw, new_pw)):
        restore("mongosh changeUserPassword failed")

    # Compose and Mongo now both carry the new values: from here a failure is
    # converged by re-running `up -d`, never by restoring the burned literals.
    if runner(dc + ["up", "-d"]).returncode != 0:
        raise RotateError("Mongo + compose rotated, but `docker compose up -d` failed; re-run it")
    new_ok = mongosh(runner, cid, mongo_script(p["user"], new_pw))
    if not new_ok:
        raise RotateError("new Mongo password does not authenticate")
    if mongosh(runner, cid, mongo_script(p["user"], old_pw)):
        raise RotateError("old Mongo password still authenticates")

    sys.path.insert(0, HARNESS)
    import mask_replay  # noqa: E402
    note = ("LibreChat compose secret moved to 0600 .env as ${%s}, rotated by "
            "ops/wq3678-librechat-secret-rotation; old Mongo pw refused, JWT sessions reset")
    for key, h in burned.items():
        mask_replay.record_rotated(h, TICKET, note=note % KEYS[key][0], path=ledger_path)
    os.unlink(backup)
    report.update(status="rotated", env_file=env_path,
                  new=[sha12(v) for v in news.values()])
    return report


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0],
                                 epilog="Example: rotate.py --apply")
    ap.add_argument("--compose", default=COMPOSE)
    ap.add_argument("--apply", action="store_true", help="act (default is a dry run)")
    ap.add_argument("--rotated-ledger", default=None)
    args = ap.parse_args(argv)
    try:
        report = rotate(args.compose, args.apply, ledger_path=args.rotated_ledger)
    except RotateError as exc:
        print(json.dumps({"status": "error", "error": str(exc)}))
        return 1
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
