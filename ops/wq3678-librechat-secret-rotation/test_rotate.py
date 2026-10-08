"""wq#3678: rotate.py against a fixture compose and a fake docker.

Fixture values are built at runtime (wq#2795/wq#2839): a `KEY: <literal>` line in
this file would fire the very kv-secret tripwire the runbook exists to settle.
"""
import importlib.util
import json
import os
import re
import stat
import types

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
_spec = importlib.util.spec_from_file_location("wq3678_rotate", os.path.join(HERE, "rotate.py"))
rotate = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rotate)

OLD = {"MONGO_INITDB_ROOT_PASSWORD": "m" * 10,
       "JWT_SECRET": "j" * 27,
       "JWT_REFRESH_SECRET": "r" * 35}
EXPECTED = {k: rotate.sha12(v) for k, v in OLD.items()}


def compose_text():
    pw = OLD["MONGO_INITDB_ROOT_PASSWORD"]
    return "\n".join([
        "services:",
        "  api:",
        "    environment:",
        "      MONGO_URI: mongodb://lumen:" + pw + "@mongodb:27017/librechat?authSource=admin",
        "      JWT_SECRET" + ": " + OLD["JWT_SECRET"],
        "      JWT_REFRESH_SECRET" + ": '" + OLD["JWT_REFRESH_SECRET"] + "'",
        "  rag:",
        "    environment:",
        "      - MONGO_URI=mongodb://lumen:" + pw + "@mongodb:27017/rag",
        "  mongodb:",
        "    environment:",
        "      MONGO_INITDB_ROOT_USERNAME: lumen",
        "      MONGO_INITDB_ROOT_PASSWORD" + ": " + pw + "  # root",
        "",
    ])


class FakeDocker:
    def __init__(self, pw, config_rc=0, up_rc=0):
        self.pw, self.config_rc, self.up_rc = pw, config_rc, up_rc
        self.calls = []

    def __call__(self, cmd, stdin=None):
        self.calls.append((cmd, stdin))
        ok = types.SimpleNamespace(returncode=0, stdout="", stderr="")
        if cmd[-3:-1] == ["ps", "-q"]:
            ok.stdout = "cid123\n"
        elif cmd[-2:] == ["config", "-q"]:
            ok.returncode = self.config_rc
        elif cmd[-2:] == ["up", "-d"]:
            ok.returncode = self.up_rc
        elif "mongosh" in cmd:
            auth = json.loads(re.search(r"a\.auth\(u, (\"[^\"]*\")\)", stdin).group(1))
            if auth != self.pw:
                return types.SimpleNamespace(returncode=3, stdout="AUTH_FAIL\n", stderr="")
            change = re.search(r"changeUserPassword\(u, (\"[^\"]*\")\)", stdin)
            if change:
                self.pw = json.loads(change.group(1))
            ok.stdout = "AUTH_OK\n"
        return ok


@pytest.fixture
def env(tmp_path):
    compose = tmp_path / "docker-compose.yml"
    compose.write_text(compose_text())
    os.chmod(compose, 0o644)
    (tmp_path / ".env").write_text("UNRELATED=keep\n")
    counter = iter(range(100))
    gen = lambda n: "%02d" % next(counter) * n  # noqa: E731 — distinct, deterministic
    return types.SimpleNamespace(compose=str(compose), dir=tmp_path, gen=gen,
                                 ledger=str(tmp_path / "ledger.json"))


def run(env, docker, apply=True):
    return rotate.rotate(env.compose, apply, runner=docker, ledger_path=env.ledger,
                         gen=env.gen, expected=EXPECTED)


def test_dry_run_names_hashes_and_touches_nothing(env):
    docker = FakeDocker(OLD["MONGO_INITDB_ROOT_PASSWORD"])
    report = run(env, docker, apply=False)
    assert report["status"] == "dry_run"
    assert report["burned"] == EXPECTED
    assert report["mongo_service"] == "mongodb"
    assert docker.calls == []
    assert open(env.compose).read() == compose_text()
    for value in OLD.values():
        assert value not in json.dumps(report)


def test_apply_rotates_moves_values_out_and_records_ledger(env):
    docker = FakeDocker(OLD["MONGO_INITDB_ROOT_PASSWORD"])
    report = run(env, docker)
    assert report["status"] == "rotated"

    text = open(env.compose).read()
    for value in OLD.values():
        assert value not in text
    assert text.count("${LIBRECHAT_MONGO_PASSWORD}") == 3
    assert "JWT_SECRET: ${LIBRECHAT_JWT_SECRET}" in text
    assert "JWT_REFRESH_SECRET: '${LIBRECHAT_JWT_REFRESH_SECRET}'" in text
    assert "# root" in text
    assert stat.S_IMODE(os.stat(env.compose).st_mode) == 0o644

    env_file = env.dir / ".env"
    assert stat.S_IMODE(os.stat(env_file).st_mode) == 0o600
    values = dict(line.split("=", 1) for line in env_file.read_text().splitlines())
    assert values["UNRELATED"] == "keep"
    assert docker.pw == values["LIBRECHAT_MONGO_PASSWORD"] != OLD["MONGO_INITDB_ROOT_PASSWORD"]
    assert len({values[v] for v, _ in rotate.KEYS.values()}) == 3

    ledger = {r["value_sha256_12"]: r for r in json.load(open(env.ledger))}
    assert set(ledger) == set(EXPECTED.values())
    assert all(r["ticket"] == "3678" for r in ledger.values())
    assert not os.path.exists(env.compose + ".pre-wq3678")

    secrets_ = list(OLD.values()) + list(values.values())
    for cmd, _ in docker.calls:
        assert not any(s in arg for s in secrets_ for arg in cmd), cmd
    assert ["docker", "compose", "-f", env.compose, "up", "-d"] in [c for c, _ in docker.calls]


def test_rerun_after_apply_is_a_no_op(env):
    docker = FakeDocker(OLD["MONGO_INITDB_ROOT_PASSWORD"])
    run(env, docker)
    calls = len(docker.calls)
    assert run(env, docker)["status"] == "already_migrated"
    assert len(docker.calls) == calls


@pytest.mark.parametrize("docker_kwargs, mongo_pw", [
    ({"config_rc": 1}, OLD["MONGO_INITDB_ROOT_PASSWORD"]),
    ({}, "s" * 10),  # Mongo refuses the compose's password: old-auth fails
])
def test_failure_before_mongo_change_restores_compose(env, docker_kwargs, mongo_pw):
    docker = FakeDocker(mongo_pw, **docker_kwargs)
    with pytest.raises(rotate.RotateError, match="compose restored"):
        run(env, docker)
    assert open(env.compose).read() == compose_text()
    assert docker.pw == mongo_pw
    assert not os.path.exists(env.ledger)


def test_up_failure_keeps_new_values_and_records_nothing(env):
    docker = FakeDocker(OLD["MONGO_INITDB_ROOT_PASSWORD"], up_rc=1)
    with pytest.raises(rotate.RotateError, match="re-run"):
        run(env, docker)
    assert OLD["JWT_SECRET"] not in open(env.compose).read()
    assert not os.path.exists(env.ledger)


def test_values_that_are_not_the_burned_ones_are_refused(env):
    docker = FakeDocker(OLD["MONGO_INITDB_ROOT_PASSWORD"])
    with pytest.raises(rotate.RotateError, match="not the burned ones"):
        rotate.rotate(env.compose, True, runner=docker, ledger_path=env.ledger, gen=env.gen)
    assert docker.calls == []
    assert open(env.compose).read() == compose_text()


def test_uri_password_disagreeing_with_root_password_is_refused(env):
    text = compose_text().replace("lumen:" + OLD["MONGO_INITDB_ROOT_PASSWORD"] + "@mongodb:27017/rag",
                                  "lumen:" + "z" * 10 + "@mongodb:27017/rag")
    open(env.compose, "w").write(text)
    with pytest.raises(rotate.RotateError, match="differs"):
        run(env, FakeDocker(OLD["MONGO_INITDB_ROOT_PASSWORD"]))


def test_merge_env_replaces_in_place_and_dedupes():
    out = rotate.merge_env("A=1\nLIBRECHAT_JWT_SECRET=old\n# c\nLIBRECHAT_JWT_SECRET=dup\n",
                           {"LIBRECHAT_JWT_SECRET": "new", "B": "2"})
    assert out == "A=1\nLIBRECHAT_JWT_SECRET=new\n# c\nB=2\n"
